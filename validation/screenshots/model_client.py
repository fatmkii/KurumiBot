"""Shared DeepSeek connection and image blocks for the workbench."""
import base64

import httpx

from correction_store import ROOT

MODEL = "deepseek-flash"
API = "https://api.deepseek.com"


def api_key():
    for line in (ROOT / ".env").read_text().splitlines():
        line = line.strip().removeprefix("export ")
        if line.startswith("DEEPSEEK_API_KEY="):
            key = line.split("=", 1)[1].strip().strip("\"'")
            if key:
                return key
    raise RuntimeError("DEEPSEEK_API_KEY is missing or empty")


def client():
    return httpx.Client(base_url=API, headers={"Authorization": f"Bearer {api_key()}"}, timeout=120, follow_redirects=False)


def image_block(path):
    mime = "png" if path.suffix.lower() == ".png" else "jpeg"
    return {"type": "image_url", "image_url": {"url": f"data:image/{mime};base64," + base64.b64encode(path.read_bytes()).decode(), "detail": "high"}}
