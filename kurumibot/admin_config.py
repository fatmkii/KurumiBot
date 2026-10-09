import fcntl
import os
import secrets
import tempfile
from pathlib import Path

from dotenv import dotenv_values, set_key

from .config import ROOT, DEFAULT_MODEL

ENV_FILE = ROOT / ".env"
SECRET_KEYS = ("QQ_APP_ID", "QQ_APP_SECRET", "CODEX_OAUTH_PROXY_API_KEY")


def read_env(path=ENV_FILE):
    return {k: v or "" for k, v in dotenv_values(path, interpolate=False).items()}


def save_env(updates, path=ENV_FILE):
    """Preserve unrelated settings/comments and atomically replace a private .env."""
    path = Path(path)
    with path.with_name(path.name + ".lock").open("a") as lock:
        os.chmod(lock.name, 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        descriptor, name = tempfile.mkstemp(prefix=".env-", dir=path.parent)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "w") as stream:
                stream.write(path.read_text() if path.exists() else "")
            for key, value in updates.items():
                set_key(temporary, key, value, quote_mode="always")
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def ensure_login(path=ENV_FILE):
    values = read_env(path)
    updates = {}
    if not values.get("KURUMI_ADMIN_USERNAME"):
        updates["KURUMI_ADMIN_USERNAME"] = "admin"
    if not values.get("KURUMI_ADMIN_PASSWORD"):
        updates["KURUMI_ADMIN_PASSWORD"] = secrets.token_urlsafe(18)
    if updates:
        save_env(updates, path)
        values.update(updates)
    return values["KURUMI_ADMIN_USERNAME"], values["KURUMI_ADMIN_PASSWORD"]


def settings(path=ENV_FILE):
    values = read_env(path)
    aliases = {"QQ_APP_ID": "QQBOT_APP_ID", "QQ_APP_SECRET": "QQBOT_CLIENT_SECRET"}
    return {
        "model": values.get("CODEX_OAUTH_PROXY_MODEL") or DEFAULT_MODEL,
        "secrets": {key: "已配置 · ••••" + (value[-4:] if len(value) > 4 else "") if value else "未配置"
                    for key in SECRET_KEYS
                    for value in [values.get(key) or values.get(aliases.get(key, ""), "")]},
    }


def update_settings(payload, path=ENV_FILE):
    if set(payload) - {*SECRET_KEYS, "CODEX_OAUTH_PROXY_MODEL"}:
        raise ValueError("包含不支持的配置项")
    updates = {}
    for key, value in payload.items():
        if not isinstance(value, str) or len(value) > 512 or any(c in value for c in "\r\n\x00"):
            raise ValueError("配置格式无效")
        value = value.strip()
        if key == "CODEX_OAUTH_PROXY_MODEL":
            if not value or any(c.isspace() for c in value):
                raise ValueError("请填写代理 /v1/models 返回的模型 ID")
            updates[key] = value
        elif value:
            updates[key] = value
    if updates:
        save_env(updates, path)
    return bool(updates)
