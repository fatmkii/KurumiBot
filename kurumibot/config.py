import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL = "gpt-6-luna-low"
AI_BASE_URL = "http://127.0.0.1:9879/v1"
DEFAULT_IMAGE = "materials/library-v1/images/v06-p088-manual-9c6ecdf5-9300-4006-8a48-c51279382a9e.png"


@dataclass
class Config:
    app_id: str = field(repr=False)
    app_secret: str = field(repr=False)
    api_key: str = field(repr=False)
    model: str = DEFAULT_MODEL
    library: Path = ROOT / "materials/library-v1"
    default_image: Path = ROOT / DEFAULT_IMAGE
    history: Path = ROOT / "data/history.sqlite3"
    ai_timeout: float = 30
    send_timeout: float = 60
    ai_concurrency: int = 2
    user_interval: float = 5
    send_attempts: int = 2

    @classmethod
    def from_env(cls, require_api_key=True):
        def path(name, default):
            value = Path(os.getenv(name, default))
            return value if value.is_absolute() else ROOT / value

        result = cls(
            app_id=os.getenv("QQ_APP_ID") or os.getenv("QQBOT_APP_ID", ""),
            app_secret=os.getenv("QQ_APP_SECRET") or os.getenv("QQBOT_CLIENT_SECRET", ""),
            api_key=os.getenv("CODEX_OAUTH_PROXY_API_KEY", ""),
            model=os.getenv("CODEX_OAUTH_PROXY_MODEL") or DEFAULT_MODEL,
            library=path("KURUMI_LIBRARY", "materials/library-v1"),
            default_image=path("KURUMI_DEFAULT_IMAGE", DEFAULT_IMAGE),
            history=path("KURUMI_HISTORY", "data/history.sqlite3"),
            ai_timeout=float(os.getenv("KURUMI_AI_TIMEOUT", "30")),
            send_timeout=float(os.getenv("KURUMI_SEND_TIMEOUT", "60")),
            ai_concurrency=int(os.getenv("KURUMI_AI_CONCURRENCY", "2")),
            user_interval=float(os.getenv("KURUMI_USER_INTERVAL", "5")),
            send_attempts=int(os.getenv("KURUMI_SEND_ATTEMPTS", "2")),
        )
        if require_api_key and not result.api_key:
            raise ValueError("missing_codex_oauth_proxy_api_key")
        if min(result.ai_timeout, result.send_timeout, result.ai_concurrency, result.send_attempts) <= 0 or result.user_interval < 0:
            raise ValueError("invalid_runtime_limits")
        if result.send_attempts > 3:
            raise ValueError("send_attempts_must_be_at_most_3")
        return result
