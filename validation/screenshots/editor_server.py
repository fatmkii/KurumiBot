"""Local full-page crop editor and human correction persistence."""
import argparse
import io
import json
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from PIL import Image

from correction_store import LOCK, AUTO, CORRECTIONS, HERE, catalogue, export_report, hints, page_state, read, save_correction, source_bytes

import enrichment
from material_state import ready
JOB = None
PAGES = {}


def progress():
    rows, materials = [], []
    external_running = False
    for page in PAGES.values():
        result = read(AUTO / "pages" / f"{page['id']}.json", {})
        correction = page_state(page)
        result_path = AUTO / "pages" / f"{page['id']}.json"
        if result.get("status") == "running":
            external_running |= time.time() - result_path.stat().st_mtime < 600
        for index, item in enumerate(correction["items"], 1):
            materials.append({"page_id": page["id"], "id": item["id"], "number": index,
                "quote": item["quote"], "box_reviewed": item["box_reviewed"],
                "ocr_status": item["ocr_status"], "metadata_status": item["metadata_status"],
                "emotion_done": bool(item["emotion_tags"]), "meaning_done": bool(item["meaning"].strip()),
                "scenarios_done": bool(item["scenarios"].strip()), "ready": ready(item),
                "error": item.get("ocr_error", "") or item.get("metadata_error", "")})
        rows.append({**page, "auto_status": result.get("status", "pending"),
                     "auto_count": len(result.get("items", [])), "reviewed": correction.get("reviewed", False),
                     "saved": bool(correction["revision"]), "item_count": len(correction.get("items", []))})
    reply_job = read(HERE / "artifacts/reply-selection/job.json", {})
    external_running |= reply_job.get("status") == "running"
    return {"pages": rows, "materials": materials, "enrichment": enrichment.progress(), "reply_prediction": reply_job,
            "job_running": bool(JOB and JOB.is_alive()) or external_running}


class Handler(SimpleHTTPRequestHandler):
    def respond(self, value, status=200):
        content = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self):
        path = unquote(urlsplit(self.path).path)
        if path == "/":
            self.send_response(302)
            self.send_header("Location", "/editor/")
            self.end_headers()
        elif path == "/api/catalog":
            self.respond(progress())
        elif path.startswith("/api/page/") and path.rsplit("/", 1)[-1] in PAGES:
            page = PAGES[path.rsplit("/", 1)[-1]]
            self.respond({"page": page, "correction": page_state(page), "human_review": hints(page["id"])})
        elif path.startswith("/api/image/") and path.rsplit("/", 1)[-1] in PAGES:
            content = source_bytes(PAGES[path.rsplit("/", 1)[-1]])
            with Image.open(io.BytesIO(content)) as original:
                mime = Image.MIME[original.format]
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        elif path.startswith("/editor/") and ".." not in Path(path).parts:
            super().do_GET()
        else:
            self.send_error(404)

    def do_POST(self):
        global JOB
        origin = self.headers.get("Origin")
        if origin and origin != f"http://{self.headers.get('Host')}":
            self.respond({"error": "请求来源不匹配"}, 403)
            return
        path = urlsplit(self.path).path
        try:
            if path.startswith("/api/page/") and path.rsplit("/", 1)[-1] in PAGES:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length < 2_000_000:
                    raise ValueError("保存内容大小无效")
                payload = json.loads(self.rfile.read(length))
                with LOCK:
                    result = save_correction(PAGES[path.rsplit("/", 1)[-1]], payload)
                self.respond(result)
            elif path == "/api/enrich":
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length < 10000:
                    raise ValueError("处理请求大小无效")
                payload = json.loads(self.rfile.read(length))
                self.respond(enrichment.start(list(PAGES.values()), payload.get("mode", "all"), payload.get("page_id"), payload.get("item_id")))
            elif path == "/api/export":
                with LOCK:
                    self.respond(export_report())
            elif path == "/api/auto":
                from reply_selection import run
                with LOCK:
                    if progress()["job_running"]:
                        self.respond({"running": True})
                        return
                    if not JOB or not JOB.is_alive():
                        JOB = threading.Thread(target=run, args=(list(PAGES.values()), 2), daemon=True)
                        JOB.start()
                self.respond({"running": True})
            else:
                self.send_error(404)
        except RuntimeError as error:
            self.respond({"error": str(error)}, 409)
        except (ValueError, TypeError, KeyError, AttributeError) as error:
            self.respond({"error": str(error)}, 400)
        except OSError:
            self.respond({"error": "保存失败，请检查磁盘空间；浏览器草稿仍保留"}, 500)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8767)
    args = parser.parse_args()
    PAGES = {page["id"]: page for page in catalogue()}
    server = ThreadingHTTPServer(("127.0.0.1", args.port), partial(Handler, directory=str(HERE)))
    print(f"人工修正页面：http://127.0.0.1:{args.port} · {len(PAGES)} 页", flush=True)
    server.serve_forever()
