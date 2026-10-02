"""Repeatable, sequential screenshot pilot. Credentials are never persisted."""

import argparse
import base64
import io
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from zipfile import ZipFile

import httpx
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUT = HERE / "artifacts"
MODEL = "deepseek-flash"
API = "https://api.deepseek.com"
FONT = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 18)
PROMPT = """分析最后一张繁体中文漫画页。前两张图片仅是久留美角色外观参考。
识别本页久留美及由她说出的对白，逐一提取能用于聊天回复的候选。
只选择确实能判断说话人为久留美的内容，不要把其他角色的对白、旁白、背景文字、拟声词归给她。
每个候选是一格中的久留美及她的完整对白；同一格的多个气泡按阅读顺序合并，不重复输出同一格。
对白忠实保留繁体原文，不简体化、不翻译、不补写；疑似错误或不确定处在issues说明。
给出A：严格一格范围panel_bbox，以及B：保持角色及对白完整、适合独立回复的范围reply_bbox。
两种范围均为矩形[x1,y1,x2,y2]，左上原点，坐标归一化到0–1000。B必要时可含相邻分镜，但不得直接使用整页。
A不能保留完整对白时仍给出该格范围，并将panel_complete设为false。B也无法完整理解时将reply_complete设为false。
优先保留清楚可辨的脸及完整气泡，避免截断汉字。没有符合条件候选返回空列表。
只返回JSON对象：{"candidates":[{"quote":"繁体对白","emotion":"情绪","scene":"适用场景",
"speaker_confidence":"high或uncertain","panel_bbox":[0,0,1,1],"reply_bbox":[0,0,1,1],
"panel_complete":true,"reply_complete":true,"issues":[]}],"page_notes":[]}。
"""


def save(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def load(path):
    return json.loads(path.read_text())


def natural(value):
    return [int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", value)]


def sheet(items, destination, columns=4, width=350, height=500):
    canvas = Image.new("RGB", (columns * width, ((len(items) + columns - 1) // columns) * height), "#dedede")
    draw = ImageDraw.Draw(canvas)
    for index, (label, path) in enumerate(items):
        x, y = (index % columns) * width, (index // columns) * height
        draw.text((x + 8, y + 5), label, fill="black", font=FONT)
        with Image.open(path) as image:
            image = image.convert("RGB")
            image.thumbnail((width - 12, height - 36))
            canvas.paste(image, (x + (width - image.width) // 2, y + 32))
    destination.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(destination, quality=90)


def preview():
    entries = []
    for archive in sorted((ROOT / "comics").glob("*.zip")):
        volume = int(re.search(r"卷(\d+)", archive.name).group(1))
        with ZipFile(archive) as zipped:
            files = sorted([name for name in zipped.namelist() if Path(name).suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"} and not name.startswith("__MACOSX/")], key=natural)
            indexes = sorted({round(8 + i * (len(files) - 15) / 19) for i in range(20)})
            tiles = []
            for index in indexes:
                name = files[index]
                page_id = f"v{volume:02d}-p{index + 1:03d}"
                path = OUT / "pages" / f"{page_id}.jpg"
                path.parent.mkdir(parents=True, exist_ok=True)
                data = zipped.read(name)
                with Image.open(io.BytesIO(data)) as image:
                    dimensions = list(image.size)
                path.write_bytes(data)
                entries.append({"id": page_id, "volume": volume, "archive": archive.name, "source_file": name, "dimensions": dimensions, "path": str(path.relative_to(HERE))})
                tiles.append((page_id, path))
            sheet(tiles, OUT / "previews" / f"volume-{volume:02d}.jpg")
            print(f"volume {volume}: {len(files)} images, {len(tiles)} preview pages", flush=True)
    save(OUT / "preview_manifest.json", entries)


def api_key():
    for line in (ROOT / ".env").read_text().splitlines():
        line = line.strip().removeprefix("export ")
        if line.startswith("DEEPSEEK_API_KEY="):
            key = line.split("=", 1)[1].strip().strip("\"'")
            if key:
                return key
    raise SystemExit("DEEPSEEK_API_KEY is missing or empty")


def client():
    return httpx.Client(base_url=API, headers={"Authorization": f"Bearer {api_key()}"}, timeout=120, follow_redirects=False)


def models():
    with client() as connection:
        response = connection.get("/models")
        data = response.json()
        save(OUT / "preflight_models.json", {"time": datetime.now(timezone.utc).isoformat(), "status": response.status_code, "data": data})
        print(json.dumps({"status": response.status_code, "models": [item["id"] for item in data.get("data", [])]}, ensure_ascii=False))


def image_block(path):
    return {"type": "image_url", "image_url": {"url": "data:image/" + ("png" if path.suffix.lower() == ".png" else "jpeg") + ";base64," + base64.b64encode(path.read_bytes()).decode(), "detail": "high"}}


def run(smoke=False):
    manifest = load(OUT / "sample_manifest.json") if not smoke else [{"id": "smoke", "path": str(ROOT / "pics_sample/1.png")}]
    references = [image_block(ROOT / "pics_sample/1.png"), image_block(ROOT / "pics_sample/2.jpg")]
    with client() as connection:
        for entry in manifest:
            destination = OUT / "raw" / f"{entry['id']}.json"
            if destination.exists():
                print(entry["id"] + ": already recorded; skipping", flush=True)
                continue
            attempts = []
            for attempt in range(1, 3):
                start = time.perf_counter()
                stamp = datetime.now(timezone.utc).isoformat()
                try:
                    prompt = '识别此图的全部繁体对白，仅返回JSON对象{"text":"原文"}。' if smoke else PROMPT
                    blocks = [{"type": "text", "text": prompt}] + ([] if smoke else references) + [image_block(HERE / entry["path"])]
                    response = connection.post("/chat/completions", json={"model": MODEL, "messages": [{"role": "user", "content": blocks}], "thinking": {"type": "disabled"}, "response_format": {"type": "json_object"}, "max_tokens": 6000, "stream": False})
                    payload = response.json()
                    result = {"attempt": attempt, "time": stamp, "elapsed_seconds": round(time.perf_counter() - start, 3), "status": response.status_code, "response": payload}
                    if response.is_success:
                        content = payload["choices"][0]["message"]["content"]
                        parsed = json.loads(content)
                        if not smoke and not isinstance(parsed.get("candidates"), list):
                            raise ValueError("missing candidates list")
                        result["parsed"] = parsed
                    attempts.append(result)
                except (httpx.HTTPError, ValueError, KeyError, IndexError) as error:
                    # Exception messages can contain request details; record only the type.
                    attempts.append({"attempt": attempt, "time": stamp, "elapsed_seconds": round(time.perf_counter() - start, 3), "error_type": type(error).__name__})
                save(destination, {"page_id": entry["id"], "requested_model": MODEL, "attempts": attempts})
                result = attempts[-1]
                print(json.dumps({"page": entry["id"], "attempt": attempt, "status": result.get("status"), "error_type": result.get("error_type"), "seconds": result["elapsed_seconds"], "candidates": len(result.get("parsed", {}).get("candidates", [])), "usage": result.get("response", {}).get("usage")}, ensure_ascii=False), flush=True)
                if "parsed" in result:
                    break
                if result.get("status") in {400, 401, 402, 403, 404, 422}:
                    raise SystemExit("Non-retryable API error. Response saved; further calls stopped.")
            if "parsed" not in attempts[-1]:
                print(entry["id"] + ": failed after maximum attempts", flush=True)


def crops():
    entries = load(OUT / "sample_manifest.json")
    catalog = []
    for entry in entries:
        raw = OUT / "raw" / f"{entry['id']}.json"
        if not raw.exists():
            continue
        attempts = load(raw)["attempts"]
        candidates = attempts[-1].get("parsed", {}).get("candidates", [])
        tiles = []
        with Image.open(HERE / entry["path"]) as original:
            for number, candidate in enumerate(candidates, 1):
                candidate_id = f"{entry['id']}-c{number:02d}"
                record = {"id": candidate_id, "page_id": entry["id"], "candidate": candidate, "crops": {}}
                for version, field in [("A", "panel_bbox"), ("B", "reply_bbox")]:
                    box = candidate.get(field)
                    valid = isinstance(box, list) and len(box) == 4 and all(type(v) in (int, float) and 0 <= v <= 1000 for v in box) and box[0] < box[2] and box[1] < box[3]
                    if not valid:
                        record["crops"][version] = {"error": "invalid_coordinates"}
                        continue
                    pixels = [round(box[0] * original.width / 1000), round(box[1] * original.height / 1000), round(box[2] * original.width / 1000), round(box[3] * original.height / 1000)]
                    if pixels[0] >= pixels[2] or pixels[1] >= pixels[3]:
                        record["crops"][version] = {"error": "empty_crop"}
                        continue
                    path = OUT / "crops" / f"{candidate_id}-{version}.png"
                    path.parent.mkdir(parents=True, exist_ok=True)
                    original.crop(pixels).save(path)
                    record["crops"][version] = {"path": str(path.relative_to(HERE)), "pixels": pixels}
                    tiles.append((f"c{number:02d}-{version}", path))
                catalog.append(record)
        if tiles:
            sheet(tiles, OUT / "comparisons" / f"{entry['id']}.jpg", columns=2, width=600, height=600)
    save(OUT / "crop_catalog.json", catalog)
    print(f"{len(catalog)} candidates, crops and comparison sheets generated")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["preview", "models", "smoke", "run", "crops"])
    args = parser.parse_args()
    {"preview": preview, "models": models, "smoke": lambda: run(True), "run": run, "crops": crops}[args.command]()
