"""QQ connectivity and fixed-image probe; load credentials with uv --env-file."""
import argparse
import asyncio
import hashlib
import json
import logging
import os
import platform
import re
import signal
import threading
import time
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from qqbot_agent_sdk import (
    EventParser, MediaInfo, MediaUploader, MessageToCreate, QQApiClient,
    QQMessageType, QQWebSocket, WSCallbacks, MEDIA_TYPE_IMAGE,
)
from qqbot_agent_sdk.constants import TOKEN_URL

# SDK errors can contain raw identifiers or upload credentials. Emit only our
# structured, allowlisted records, never SDK tracebacks or raw response bodies.
logging.disable(logging.CRITICAL)
LOG_LOCK = threading.Lock()


def emit(event, **fields):
    with LOG_LOCK:
        print(json.dumps({"time": datetime.now(timezone.utc).isoformat(),
                          "event": event, **fields}, ensure_ascii=False), flush=True)


def masked(value):
    return hashlib.sha256(value.encode()).hexdigest()[:12]


def error_fields(exc):
    fields = {"error_type": type(exc).__name__}
    chain = []
    while exc is not None:
        chain.append(type(exc).__name__)
        response = getattr(exc, "response", None)
        if response is not None:
            fields["http_status"] = response.status_code
            fields["trace_id"] = response.headers.get("x-tps-trace-id")
            try:
                fields["business_code"] = response.json().get("code")
            except (ValueError, AttributeError):
                pass
        exc = exc.__cause__
    fields["exception_chain"] = chain
    return fields


async def record_http(response):
    await response.aread()
    path = response.request.url.path
    # Suppress group openids and presigned COS URLs, including query strings.
    path = re.sub(r"(/v2/(?:groups|users)/)[^/]+", r"\1<redacted>", path)
    fields = {"method": response.request.method, "http_status": response.status_code,
              "trace_id": response.headers.get("x-tps-trace-id")}
    if response.request.url.host in ("api.sgroup.qq.com", "bots.qq.com", "sandbox.api.sgroup.qq.com"):
        fields["path"] = path
        try:
            fields["business_code"] = response.json().get("code")
        except (ValueError, AttributeError):
            pass
    else:
        fields["path"] = "<media-transfer>"
    emit("http_response", **fields)


async def main(args):
    app_id = os.getenv("QQ_APP_ID") or os.getenv("QQBOT_APP_ID")
    secret = os.getenv("QQ_APP_SECRET") or os.getenv("QQBOT_CLIENT_SECRET")
    if not app_id or not secret:
        emit("configuration_error", reason="missing_app_id_or_secret")
        return 1
    image = Path(os.getenv("QQBOT_IMAGE_PATH", str(Path(__file__).resolve().parents[2] / "pics_sample/1.png")))
    emit("probe_start", python=platform.python_version(), sdk=version("qqbot-agent-sdk"),
         duration_seconds=args.duration, image=str(image), image_exists=image.is_file())
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    lock = threading.Lock()
    session = [None, None]
    counts = {"connections": 0, "heartbeat_acks": 0, "received": 0, "sent": 0}
    seen = set()
    started = time.monotonic()
    disconnect_at = None

    def get_session():
        with lock:
            return tuple(session)

    def set_session(session_id, seq):
        with lock:
            session[:] = [session_id, seq]

    def connected():
        nonlocal disconnect_at
        with lock:
            counts["connections"] += 1
            number = counts["connections"]
        emit("connected", connection_number=number,
             elapsed_seconds=round(time.monotonic() - started, 3),
             recovery_seconds=round(time.monotonic() - disconnect_at, 3) if disconnect_at else None)
        disconnect_at = None

    def disconnected():
        nonlocal disconnect_at
        disconnect_at = time.monotonic()
        emit("disconnected", resume_session_available=bool(get_session()[0]))

    def heartbeat_ack():
        with lock:
            counts["heartbeat_acks"] += 1
            number = counts["heartbeat_acks"]
        emit("heartbeat_ack", count=number)

    def fatal(code, _message):
        emit("websocket_fatal", code=code)
        loop.call_soon_threadsafe(stop.set)

    async with httpx.AsyncClient(timeout=30, event_hooks={"response": [record_http]}) as http:
        api = QQApiClient(app_id=app_id, client_secret=secret)
        api.setup(http)
        uploader = MediaUploader(api_client=api, http_client=http)

        async def on_message(event_type, raw):
            if event_type not in ("GROUP_AT_MESSAGE_CREATE", "C2C_MESSAGE_CREATE"):
                emit("ignored_event", event_type=event_type)
                return
            event = EventParser().parse(event_type, raw)
            if not event or not event.message_id:
                emit("invalid_message_event", event_type=event_type)
                return
            key = (event.chat_scope, event.chat_id, event.message_id)
            fields = {"chat_scope": event.chat_scope, "chat_id": masked(event.chat_id), "user": masked(event.user_id),
                      "message_id": masked(event.message_id)}
            if key in seen:
                emit("duplicate_skipped", **fields)
                return
            seen.add(key)
            with lock:
                counts["received"] += 1
            emit("group_at_received" if event.chat_scope == "group" else "c2c_received",
                 **fields, content=event.content, platform_time=event.timestamp)
            phase = "upload"
            begin = time.monotonic()
            try:
                if not image.is_file():
                    raise FileNotFoundError(image)
                file_info = await asyncio.wait_for(uploader.upload(
                    chat_type=event.chat_scope, chat_id=event.chat_id,
                    source=str(image), file_type=MEDIA_TYPE_IMAGE), timeout=120)
                emit("upload_success", **fields, seconds=round(time.monotonic() - begin, 3))
                phase = "send"
                begin = time.monotonic()
                send = api.post_group_message if event.chat_scope == "group" else api.post_c2c_message
                result = await asyncio.wait_for(send(event.chat_id, MessageToCreate(
                    msg_type=QQMessageType.RICH_MEDIA, msg_id=event.message_id,
                    msg_seq=1, media=MediaInfo(file_info=file_info))), timeout=30)
                with lock:
                    counts["sent"] += 1
                emit("send_success", **fields, seconds=round(time.monotonic() - begin, 3),
                     sent_message_id=masked(str(result.get("id", ""))), client_visibility="unverified")
            except Exception as exc:
                emit("message_failure", **fields, phase=phase, **error_fields(exc))

        ws = QQWebSocket(callbacks=WSCallbacks(
            on_message_event=on_message, on_connected=connected,
            on_disconnected=disconnected, on_fatal_error=fatal,
            get_token=api.ensure_token_sync, get_session=get_session,
            set_session=set_session, clear_token=api.clear_token,
            set_heartbeat_interval=lambda seconds: emit("heartbeat_interval", seconds=seconds),
            fail_pending=lambda _reason: emit("pending_failed"),
            get_gateway_url=api.get_gateway_url_sync,
            on_ready=lambda _ready: emit("ready"), on_heartbeat_ack=heartbeat_ack), log_tag="Probe")
        phase = "token"
        try:
            await api.ensure_token()
            emit("token_success", seconds=round(time.monotonic() - started, 3))
            phase = "gateway"
            gateway = await api.get_gateway_url()
            emit("gateway_success", host=urlsplit(gateway).hostname)
            phase = "websocket"
            ws.start(gateway, loop)
            try:
                await asyncio.wait_for(stop.wait(), timeout=args.duration)
            except TimeoutError:
                pass
        except Exception as exc:
            emit("connection_failure", phase=phase, **error_fields(exc))
            if phase == "token":
                # SDK drops business codes when a HTTP 200 response lacks a
                # token. Repeat once to preserve allowlisted failure evidence.
                try:
                    response = await http.post(TOKEN_URL, json={
                        "appId": app_id, "clientSecret": secret})
                    data = response.json()
                    message = str(data.get("message", "")).replace(app_id, "<app_id>").replace(secret, "<secret>")
                    emit("token_diagnostic", http_status=response.status_code,
                         business_code=data.get("code"), message=message,
                         has_token=bool(data.get("access_token")),
                         trace_id=response.headers.get("x-tps-trace-id"),
                         app_id_is_decimal=app_id.isdecimal(),
                         app_id_has_outer_whitespace=app_id != app_id.strip(),
                         secret_has_outer_whitespace=secret != secret.strip())
                except Exception as diagnostic_error:
                    emit("token_diagnostic_failure", **error_fields(diagnostic_error))
            return 1
        finally:
            await ws.async_stop()
            emit("probe_end", **counts, elapsed_seconds=round(time.monotonic() - started, 3))
        return 0 if counts["connections"] and counts["heartbeat_acks"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=int, default=120, help="Observation seconds")
    raise SystemExit(asyncio.run(main(parser.parse_args())))
