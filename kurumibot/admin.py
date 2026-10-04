import argparse
import asyncio
import fcntl
import hashlib
import ipaddress
import json
import logging
import os
import secrets
import signal
import socket
import struct
import time
from collections import deque
from pathlib import Path

from aiohttp import web

from .admin_config import ENV_FILE, ensure_login, settings, update_settings
from .admin_data import AdminData
from .config import Config
from .history import History
from .pricing import PRICE_NOTE, PRICE_SOURCE
from .qq import emit

STATIC = Path(__file__).parent / "static"
SESSION_SECONDS = 8 * 3600
SESSION = web.RequestKey("session", dict)


def pagination(request):
    page, size = int(request.query.get("page", "1")), int(request.query.get("size", "20"))
    if page < 1 or not 1 <= size <= 100:
        raise ValueError("分页参数无效")
    return page, size


def bot_running(config):
    path = config.history.with_suffix(".lock")
    if not path.exists():
        return False
    with path.open("r") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        return False


def create_app(config, env_file=ENV_FILE):
    username, password = ensure_login(env_file)
    password_digest = hashlib.sha256(password.encode()).digest()
    data = AdminData(config)
    # Initialize tables without running crash recovery on the live Bot's records.
    History(config.history).close()
    sessions, login_attempts = {}, deque()

    @web.middleware
    async def guard(request, handler):
        try:
            if request.method not in ("GET", "HEAD"):
                origin = request.headers.get("Origin")
                if origin and origin != f"{request.scheme}://{request.host}":
                    raise web.HTTPForbidden(text="请求来源无效")
                if request.content_type != "application/json":
                    raise web.HTTPBadRequest(text="请求必须为 JSON")
            token = request.cookies.get("kurumi_session", "")
            session = sessions.get(token)
            if session and session["expires"] <= time.monotonic():
                sessions.pop(token, None)
                session = None
            request[SESSION] = session
            if request.path.startswith("/api/") and request.path not in ("/api/login", "/api/session"):
                if not session:
                    raise web.HTTPUnauthorized(text="请先登录")
                if request.method not in ("GET", "HEAD") and not secrets.compare_digest(
                    request.headers.get("X-CSRF-Token", "").encode(), session["csrf"].encode()
                ):
                    raise web.HTTPForbidden(text="登录校验已失效，请刷新页面")
            response = await handler(request)
        except web.HTTPException as exc:
            response = web.json_response({"error": exc.text}, status=exc.status)
        except (ValueError, json.JSONDecodeError):
            response = web.json_response({"error": "输入无效，请检查字段格式和长度"}, status=400)
        except Exception as exc:
            emit("admin_error", error_type=type(exc).__name__)
            response = web.json_response({"error": "服务暂时无法处理请求，请稍后再试"}, status=500)
        response.headers.update({
            "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "same-origin",
            "Content-Security-Policy": "default-src 'self'; img-src 'self'; style-src 'self'; script-src 'self'; "
                                       "frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
        })
        return response

    app = web.Application(middlewares=[guard], client_max_size=32 * 1024)

    async def body(request):
        payload = await request.json()
        if not isinstance(payload, dict):
            raise ValueError("invalid_body")
        return payload

    async def login(request):
        current = time.monotonic()
        while login_attempts and login_attempts[0] < current - 60:
            login_attempts.popleft()
        if len(login_attempts) >= 10:
            raise web.HTTPTooManyRequests(text="登录尝试过多，请一分钟后重试")
        login_attempts.append(current)
        payload = await body(request)
        supplied = payload.get("password", "")
        supplied_user = payload.get("username", "")
        if not isinstance(supplied, str) or not isinstance(supplied_user, str):
            raise web.HTTPUnauthorized(text="用户名或密码不正确")
        valid_password = secrets.compare_digest(hashlib.sha256(supplied.encode()).digest(), password_digest)
        valid_user = secrets.compare_digest(supplied_user.encode(), username.encode())
        if not valid_password or not valid_user:
            raise web.HTTPUnauthorized(text="用户名或密码不正确")
        for token in list(sessions):
            if sessions[token]["expires"] <= current:
                del sessions[token]
        if len(sessions) >= 128:
            sessions.pop(next(iter(sessions)))
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        sessions[token] = {"expires": current + SESSION_SECONDS, "csrf": csrf}
        response = web.json_response({"authenticated": True, "csrf": csrf, "username": username})
        response.set_cookie("kurumi_session", token, httponly=True, samesite="Strict", secure=request.secure,
                            max_age=SESSION_SECONDS, path="/")
        return response

    async def session(request):
        value = request[SESSION]
        return web.json_response({"authenticated": bool(value), **({"csrf": value["csrf"], "username": username} if value else {})})

    async def logout(request):
        sessions.pop(request.cookies.get("kurumi_session", ""), None)
        response = web.json_response({"ok": True})
        response.del_cookie("kurumi_session", path="/")
        return response

    async def overview(request):
        result = data.overview()
        result.update(bot_running=bot_running(config), price_note=PRICE_NOTE, price_source=PRICE_SOURCE)
        return web.json_response(result)

    async def materials(request):
        page, size = pagination(request)
        return web.json_response(data.materials(request.query.get("q", "")[:200], request.query.get("enabled", ""),
                                                request.query.get("volume", ""), page, size))

    async def update_material(request):
        payload = await body(request)
        if not data.update_material(request.match_info["id"], payload):
            raise web.HTTPNotFound(text="素材不存在")
        return web.json_response({"ok": True})

    async def material_image(request):
        path = data.material_image(request.match_info["id"])
        if not path or path.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp"):
            raise web.HTTPNotFound(text="图片不存在")
        return web.FileResponse(path)

    async def conversations(request):
        page, size = pagination(request)
        return web.json_response(data.conversations(request.query.get("q", "")[:200], request.query.get("status", ""), page, size))

    async def conversation_image(request):
        path = data.conversation_image(int(request.match_info["id"]))
        if not path:
            raise web.HTTPNotFound(text="图片不存在")
        return web.FileResponse(path)

    async def usage(request):
        page, size = pagination(request)
        result = data.usage(request.query.get("errors") == "1", page, size)
        result.update(price_note=PRICE_NOTE, price_source=PRICE_SOURCE)
        return web.json_response(result)

    async def get_settings(request):
        return web.json_response(settings(env_file))

    async def save_settings(request):
        changed = update_settings(await body(request), env_file)
        return web.json_response({"ok": True, "changed": changed, "restart_required": changed})

    async def static_file(request):
        filename = request.match_info.get("name", "index.html")
        if filename not in ("index.html", "app.js", "style.css", "favicon.svg"):
            raise web.HTTPNotFound()
        return web.FileResponse(STATIC / filename)

    app.add_routes([
        web.get("/", static_file), web.get("/static/{name}", static_file),
        web.post("/api/login", login), web.get("/api/session", session), web.post("/api/logout", logout),
        web.get("/api/overview", overview), web.get("/api/materials", materials),
        web.patch("/api/materials/{id}", update_material), web.get("/api/materials/{id}/image", material_image),
        web.get("/api/conversations", conversations), web.get("/api/conversations/{id}/image", conversation_image),
        web.get("/api/usage", usage), web.get("/api/settings", get_settings), web.post("/api/settings", save_settings),
    ])
    return app


def lan_host():
    addresses = []
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        for _, interface in socket.if_nameindex():
            try:
                result = fcntl.ioctl(sock.fileno(), 0x8915, struct.pack("256s", interface.encode()[:15]))
                addresses.append(socket.inet_ntoa(result[20:24]))
            except OSError:
                continue
    return next((address for address in addresses if address.startswith("192.168.")), None)


async def run_admin(host, config, env_file=ENV_FILE):
    app = create_app(config, env_file)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    try:
        await web.TCPSite(runner, host, 10963).start()
        emit("admin_ready", host=host, port=10963)
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)
        await stop.wait()
    finally:
        await runner.cleanup()
        emit("admin_stop")


if __name__ == "__main__":
    logging.disable(logging.CRITICAL)
    parser = argparse.ArgumentParser(description="KurumiBot 管理后台，端口 10963")
    parser.add_argument("--host", help="服务器实际的局域网 IPv4；默认自动查找 192.168.x.x")
    args = parser.parse_args()
    host = args.host or os.getenv("KURUMI_ADMIN_HOST") or lan_host()
    if not host:
        emit("admin_startup_error", reason="no_192_168_address_specify_host")
        raise SystemExit(1)
    try:
        address = ipaddress.ip_address(host)
        if address.version != 4 or not address.is_private or address.is_unspecified:
            raise ValueError("host_must_be_private_ipv4")
        asyncio.run(run_admin(host, Config.from_env(require_api_key=False)))
    except Exception as exc:
        emit("admin_startup_error", error_type=type(exc).__name__)
        raise SystemExit(1)
