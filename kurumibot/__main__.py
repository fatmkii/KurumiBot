import argparse
import asyncio
import fcntl

import httpx

from .config import Config
from .history import History
from .library import Library
from .qq import emit, run
from .selection import Selector


async def main(args):
    config = Config.from_env()
    library = Library(config.library)
    lock = None
    history = None
    try:
        if args.command == "run":
            config.history.parent.mkdir(parents=True, exist_ok=True)
            lock = config.history.with_suffix(".lock").open("a")
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                emit("startup_error", reason="bot_already_running")
                return 1
        history = History(config.history)
        if args.command == "select":
            candidates = library.candidates()
            async with httpx.AsyncClient(timeout=config.ai_timeout) as http:
                result = await Selector(http, config).select(args.text, candidates)
            history.usage(None, result)
            image = library.selected_image(result.material_id, {c["id"] for c in candidates})
            item = next((c for c in candidates if c["id"] == result.material_id), {})
            emit("selection_preview", material_id=result.material_id, quote=item.get("quote_simplified"),
                 scene=result.scene, reason=result.reason, image=str(image or config.default_image),
                 fallback=image is None, error_type=result.error_type,
                 elapsed_seconds=result.elapsed_seconds, usage=result.usage)
            return 1 if result.error_type else 0
        await run(config, library, history)
        return 0
    finally:
        library.close()
        if history:
            history.close()
        if lock:
            lock.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="久留美 QQ 图片 Bot（私聊与群 @）")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("run", help="连接 QQ 并处理私聊与群 @")
    select = commands.add_parser("select", help="真实调用 DeepSeek 预览选图，不向 QQ 发消息")
    select.add_argument("text")
    try:
        raise SystemExit(asyncio.run(main(parser.parse_args())))
    except Exception as exc:
        emit("startup_error", error_type=type(exc).__name__)
        raise SystemExit(1)
