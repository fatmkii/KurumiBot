import asyncio
import json
import logging
import re
import signal
import threading

import httpx
from qqbot_agent_sdk import (
    EventParser, MediaInfo, MediaUploader, MessageToCreate, QQApiClient,
    QQMessageType, QQWebSocket, WSCallbacks, MEDIA_TYPE_IMAGE,
)
from qqbot_agent_sdk.utils import is_fatal_send_error

from .history import now
from .service import BotService
from .selection import Selector


def emit(event, **fields):
    print(json.dumps({"time": now(), "event": event, **fields}, ensure_ascii=False), flush=True)


def parse_inbound(event_type, raw):
    if event_type not in ("C2C_MESSAGE_CREATE", "GROUP_AT_MESSAGE_CREATE"):
        return None
    event = EventParser.parse(event_type, raw)
    if event and event.chat_scope == "group":
        # The SDK strips plain @name prefixes; also accept QQ's <@!id> notation.
        event.content = re.sub(r"^<@!?[^<>]+>\s*", "", event.content).strip()
    return event


class QQSender:
    def __init__(self, api, uploader):
        self.api, self.uploader = api, uploader

    async def send(self, event, image):
        if event.chat_scope not in ("c2c", "group"):
            raise ValueError("unsupported_chat_scope")
        file_info = await self.uploader.upload(
            chat_type=event.chat_scope, chat_id=event.chat_id, source=str(image), file_type=MEDIA_TYPE_IMAGE,
        )
        # Keep the same incoming msg_id and msg_seq when retrying: do not create a new reply identity.
        send = self.api.post_group_message if event.chat_scope == "group" else self.api.post_c2c_message
        result = await send(event.chat_id, MessageToCreate(
            msg_type=QQMessageType.RICH_MEDIA, msg_id=event.message_id,
            msg_seq=1, media=MediaInfo(file_info=file_info),
        ))
        if not isinstance(result, dict) or not result.get("id"):
            raise ValueError("send_response_missing_id")
        return result

    @staticmethod
    def retryable(exc):
        return not isinstance(exc, (FileNotFoundError, ValueError)) and not is_fatal_send_error(str(exc))


async def run(config, library, history):
    # The SDK logs raw openids, presigned URLs and exception response bodies.
    logging.disable(logging.CRITICAL)
    if not config.app_id or not config.app_secret:
        raise ValueError("missing_qq_credentials")
    if not config.default_image.is_file():
        raise ValueError("default_image_missing")
    history.recover()
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    lock = threading.Lock()
    session = [None, None]

    def get_session():
        with lock:
            return tuple(session)

    def set_session(session_id, seq):
        with lock:
            session[:] = [session_id, seq]

    def fatal(code, _message):
        emit("websocket_fatal", code=code)
        loop.call_soon_threadsafe(stop.set)

    async with httpx.AsyncClient(timeout=30) as qq_http, httpx.AsyncClient(timeout=config.ai_timeout) as ai_http:
        api = QQApiClient(app_id=config.app_id, client_secret=config.app_secret)
        api.setup(qq_http)
        service = BotService(config, library, history, Selector(ai_http, config),
                             QQSender(api, MediaUploader(api_client=api, http_client=qq_http)), emit)

        async def on_message(event_type, raw):
            if stop.is_set():
                return
            try:
                event = parse_inbound(event_type, raw)
                if event:
                    await service.handle(event)
            except Exception as exc:
                emit("event_error", error_type=type(exc).__name__)

        ws = QQWebSocket(callbacks=WSCallbacks(
            on_message_event=on_message,
            on_connected=lambda: emit("connected"),
            on_disconnected=lambda: emit("disconnected"),
            on_fatal_error=fatal, get_token=api.ensure_token_sync,
            get_session=get_session, set_session=set_session, clear_token=api.clear_token,
            set_heartbeat_interval=lambda seconds: emit("heartbeat_interval", seconds=seconds),
            fail_pending=lambda _reason: emit("pending_failed"),
            get_gateway_url=api.get_gateway_url_sync,
            on_ready=lambda _ready: emit("ready"),
            on_heartbeat_ack=lambda: emit("heartbeat_ack"),
        ), log_tag="KurumiBot")
        try:
            await api.ensure_token()
            gateway = await api.get_gateway_url()
            emit("bot_start", mode="c2c_and_group_at", candidates=len(library.candidates()), model=config.model)
            ws.start(gateway, loop)
            await stop.wait()
        finally:
            stop.set()
            await ws.async_stop()
            await service.drain()
            emit("bot_stop")
