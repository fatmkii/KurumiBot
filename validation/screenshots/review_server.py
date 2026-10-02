"""Local screenshot review UI; no model calls or credential access."""

import argparse
import json
from datetime import datetime, timezone
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

HERE = Path(__file__).resolve().parent


def read(path):
    return json.loads((HERE / path).read_text())


def dataset():
    baseline = {p["page_id"]: p for p in read("results/visual_baseline.json")["pages"]}
    reviews = {c["id"]: c for c in read("results/candidate_review.json")}
    candidates = read("artifacts/crop_catalog.json")
    pages = []
    for sample in read("results/sample_manifest.json"):
        page = {**sample, "baseline": baseline[sample["id"]], "candidates": []}
        for candidate in candidates:
            if candidate["page_id"] == sample["id"]:
                page["candidates"].append({**candidate, "assistant_review": reviews[candidate["id"]]})
        pages.append(page)
    return {"id": "screenshots-2026-10-01-30-pages", "pages": pages}


class Handler(SimpleHTTPRequestHandler):
    def json_response(self, value, status=200):
        data = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = unquote(urlsplit(self.path).path)
        if path == "/":
            self.send_response(302)
            self.send_header("Location", "/web/")
            self.end_headers()
        elif path == "/api/data":
            self.json_response(dataset())
        elif path == "/api/review":
            saved = HERE / "human-review/review.json"
            self.json_response(json.loads(saved.read_text()) if saved.exists() else None)
        elif path.startswith(("/web/", "/artifacts/pages/", "/artifacts/crops/")) and ".." not in Path(path).parts:
            super().do_GET()
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path != "/api/review":
            self.send_error(404)
            return
        origin = self.headers.get("Origin")
        if origin and origin != f"http://{self.headers.get('Host')}":
            self.json_response({"error": "请求来源不匹配"}, 403)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 5_000_000:
                raise ValueError("报告大小无效")
            payload = json.loads(self.rfile.read(length))
            review, report = payload["review"], payload["report"]
            if review.get("dataset_id") != dataset()["id"] or not isinstance(report, str):
                raise ValueError("报告不属于当前样本")
            if not isinstance(review.get("pages"), dict) or not isinstance(review.get("candidates"), dict):
                raise ValueError("审核记录格式无效")
        except (ValueError, KeyError, TypeError, AttributeError):
            self.json_response({"error": "无法保存，请检查审核数据"}, 400)
            return
        folder = HERE / "human-review"
        folder.mkdir(exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        body = json.dumps(review, ensure_ascii=False, indent=2) + "\n"
        # Keep snapshots as well as the latest result, so interrupted sessions are recoverable.
        (folder / f"review-{stamp}.json").write_text(body)
        (folder / "review.json").write_text(body)
        (folder / "人工截图审核报告.md").write_text(report)
        self.json_response({"saved": "validation/screenshots/human-review/人工截图审核报告.md"})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    dataset()  # Fail early if the pilot artifacts are missing.
    server = ThreadingHTTPServer(("127.0.0.1", args.port), partial(Handler, directory=str(HERE)))
    print(f"审核页面：http://127.0.0.1:{args.port} （Ctrl+C 停止）", flush=True)
    server.serve_forever()
