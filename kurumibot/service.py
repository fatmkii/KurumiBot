import asyncio
import time

from .history import masked


class BotService:
    def __init__(self, config, library, history, selector, sender, emit):
        self.config, self.library, self.history = config, library, history
        self.selector, self.sender, self.emit = selector, sender, emit
        self.active = 0
        self.tasks = set()

    async def handle(self, event):
        if event.chat_scope not in ("c2c", "group") or not all((event.chat_id, event.user_id, event.message_id)):
            return
        # Only configured secret values are redacted; ordinary dialogue stays readable in history.
        content = event.content.strip()
        for secret in (self.config.api_key, self.config.app_secret):
            if secret:
                content = content.replace(secret, "<redacted>")
        conversation_id = self.history.claim(event, content)
        if conversation_id is None:
            self.emit("duplicate_skipped", message=masked(event.message_id))
            return
        started = time.monotonic()
        fields = {"conversation_id": conversation_id}

        def finish(status, **extra):
            self.history.finish(conversation_id, status=status,
                                elapsed_seconds=round(time.monotonic() - started, 3), **extra)
            self.emit("message_finished", **fields, status=status, **{
                k: v for k, v in extra.items() if k in ("material_id", "fallback_reason", "error_type", "attempts")
            })

        # Use recorded arrivals instead of an unbounded in-memory per-user dictionary.
        recent = self.history.db.execute(
            "SELECT received_at FROM conversations WHERE user_hash=? AND id<>? "
            "AND status NOT IN ('rate_limited','busy') ORDER BY id DESC LIMIT 1",
            (masked(event.user_id), conversation_id),
        ).fetchone()
        if recent:
            from datetime import datetime, timezone
            elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(recent[0])).total_seconds()
            if elapsed < self.config.user_interval:
                finish("rate_limited")
                return
        if self.active >= self.config.ai_concurrency:
            finish("busy")
            return
        self.active += 1
        task = asyncio.current_task()
        self.tasks.add(task)
        attempts = 0
        try:
            self.emit("message_received", **fields)
            candidates = self.library.candidates()
            material_id, image = None, None
            fallback_reason = None
            if not content:
                fallback_reason = "empty_message"
            elif not candidates:
                fallback_reason = "no_candidates"
            else:
                selection = await self.selector.select(content, candidates)
                self.history.usage(conversation_id, selection)
                material_id = selection.material_id
                if selection.error_type:
                    fallback_reason = "ai_" + selection.error_type
                elif material_id is None:
                    fallback_reason = "no_match"
                else:
                    image = self.library.selected_image(material_id, {c["id"] for c in candidates})
                    if image is None:
                        fallback_reason = "material_unavailable"
            if image is None:
                material_id = None
                image = self.config.default_image
                if not image.is_file():
                    finish("failed", fallback_reason=fallback_reason, error_type="default_image_missing")
                    return
            self.history.finish(conversation_id, status="processing", material_id=material_id,
                                image_path=str(image), fallback_reason=fallback_reason)
            for attempts in range(1, self.config.send_attempts + 1):
                try:
                    # Each attempt bounds upload + message send together.
                    result = await asyncio.wait_for(self.sender.send(event, image), self.config.send_timeout)
                    finish("sent", attempts=attempts, sent_message_hash=masked(str(result["id"])),
                           material_id=material_id, fallback_reason=fallback_reason)
                    return
                except Exception as exc:
                    if attempts == self.config.send_attempts or not self.sender.retryable(exc):
                        finish("failed", attempts=attempts, error_type=type(exc).__name__,
                               material_id=material_id, fallback_reason=fallback_reason)
                        return
                    await asyncio.sleep(1)
        except asyncio.CancelledError:
            finish("interrupted", attempts=attempts)
            raise
        except Exception as exc:
            finish("failed", attempts=attempts, error_type=type(exc).__name__)
        finally:
            self.active -= 1
            self.tasks.discard(task)

    async def drain(self):
        tasks = list(self.tasks)
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=5)
            for task in pending:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
