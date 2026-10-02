"""Round 2: contrast references, bubble attribution, independent verification."""
import argparse
import base64
import io
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from PIL import Image

from correction_store import AUTO, CORRECTIONS, HERE, catalogue, human_review, read, source_bytes, valid_box, write
from model_client import ROOT, MODEL, client, image_block

LOCATE = """最后一张是待处理漫画页，之前的图仅作外观参考，不是待提取页面。
正参考是久留美；负参考分别是黑发双马尾、白发短发、长发角色，均不是久留美。
从本页找出可能属于久留美的聊天回复素材。先定位人物，再逐个判断气泡说话人。
不能因为气泡靠近久留美、包含她的名字、或人物发型相似就归给她；结合气泡尾巴、电话另一端、阅读顺序。
保留明确内心独白；排除纯叫声、拟声词、图表文字。局部人物或背影可辨即可，不可辨就注明不确定。
同一格输出一条，同格多个属于她的气泡不能拆开。不同格不能合并为一条。
panel_bbox须覆盖整个分镜而非气泡；character_bbox须含可辨人物；每个气泡单独给bbox，含越界气泡完整文字。
繁体文字忠实识别，气泡按原阅读顺序输出。存在归属疑问必须标uncertain。
坐标都为原页归一化0–1000的[x1,y1,x2,y2]。
仅输出JSON {"candidates":[{"panel_id":"p1","identity":"kurumi|other|uncertain",
"identity_evidence":"人物身份依据","panel_bbox":[0,0,1,1],"character_bbox":[0,0,1,1],
"bubbles":[{"text":"繁体原文","bbox":[0,0,1,1],"speaker":"kurumi|other|uncertain",
"kind":"speech|thought|sound","evidence":"说话人证据"}],"issues":[]}]}。
没有素材输出空列表，不补写台词。"""
VERIFY = """独立复核最后一张原页与下方初步提取，仍以上方正负参考为身份依据。
逐条检查人物是否久留美；气泡尾巴是否指向她，电话框是否来自别人，名字是否被别人呼喊。
复核同格全部对白是否遗漏、文字顺序、气泡越界、panel_bbox是否真为整个格子，人物范围是否足够可辨。
纠正错误、剔除other人物、合并重复同格项，补充初步遗漏素材。不确定的身份和说话人保留uncertain。
排除纯叫声和音效，保留明确内心独白。不能将别人的台词归给久留美。
输出与初步相同的JSON结构（candidates），每个气泡提供完整bbox，不返回其他文字。
初步提取：\n"""
FATAL = threading.Event()


def references():
    blocks = []
    for label, path in [
        ("正参考：久留美", ROOT / "pics_sample/1.png"),
        ("正参考：久留美", ROOT / "pics_sample/2.jpg"),
        ("负参考：黑发双马尾角色，不是久留美", HERE / "references/black-twin-tail.png"),
        ("负参考：白发角色，不是久留美", HERE / "references/white-short-hair.png"),
        ("负参考：长发角色，不是久留美", HERE / "references/long-hair.png")]:
        blocks += [{"type": "text", "text": label}, image_block(path)]
    return blocks


def request(connection, blocks, prompt, validate=False):
    attempts = []
    for attempt in range(2):
        start = time.perf_counter()
        record = {"time": datetime.now(timezone.utc).isoformat(), "attempt": attempt + 1}
        try:
            response = connection.post("/chat/completions", json={"model": MODEL, "thinking": {"type": "disabled"},
                "response_format": {"type": "json_object"}, "max_tokens": 8000,
                "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}] + blocks}]})
            record.update(status=response.status_code, response=response.json())
            if response.status_code in {400, 401, 402, 403, 404, 422}:
                FATAL.set()
            if response.is_success:
                parsed = json.loads(record["response"]["choices"][0]["message"]["content"])
                if not isinstance(parsed.get("candidates"), list):
                    raise ValueError("candidates missing")
                if validate:
                    normalize(parsed["candidates"])
                record["parsed"] = parsed
        except Exception as error:
            record["error_type"] = type(error).__name__
        record["seconds"] = round(time.perf_counter() - start, 3)
        attempts.append(record)
        if "parsed" in record or FATAL.is_set():
            break
        prompt += "\n前次输出无效。所有坐标必须使用0–1000归一化值，不是原图像素；每个框宽高为正，不能超出1000。请重新检查输出。"
    return attempts


def normalize(candidates):
    grouped = {}
    for c in candidates:
        if c.get("identity") not in {"kurumi", "uncertain"}:
            continue
        bubbles = [b for b in c.get("bubbles", []) if b.get("speaker") in {"kurumi", "uncertain"}
                   and b.get("kind") in {"speech", "thought"} and isinstance(b.get("text"), str) and b["text"].strip()]
        if not bubbles:
            continue
        boxes = [c.get("panel_bbox"), c.get("character_bbox")] + [b.get("bbox") for b in bubbles]
        if not all(valid_box(b) for b in boxes):
            raise ValueError("invalid component coordinates")
        key = str(c.get("panel_id") or c["panel_bbox"])
        group = grouped.setdefault(key, {"boxes": [], "bubbles": [], "issues": [], "uncertain": False})
        group["boxes"].extend(boxes)
        group["uncertain"] |= c["identity"] == "uncertain" or any(b["speaker"] == "uncertain" for b in bubbles)
        group["issues"].extend(str(x) for x in c.get("issues", []))
        for bubble in bubbles:
            if not any(b["text"] == bubble["text"] and b["bbox"] == bubble["bbox"] for b in group["bubbles"]):
                group["bubbles"].append(bubble)
    items = []
    for index, group in enumerate(grouped.values(), 1):
        boxes = group["boxes"]
        bbox = [max(0, min(b[0] for b in boxes) - 8), max(0, min(b[1] for b in boxes) - 8),
                min(1000, max(b[2] for b in boxes) + 8), min(1000, max(b[3] for b in boxes) + 8)]
        issues = group["issues"] + (["模型身份／归属仍不确定，请重点确认"] if group["uncertain"] else [])
        items.append({"id": f"auto-{index:02d}", "bbox": bbox,
                      "quote": "".join(b["text"] for b in group["bubbles"]),
                      "speaker": "uncertain", "notes": "；".join(issues), "components": group})
    if human_review().get("rules", {}).get("reaction") == "exclude":
        import re
        items = [item for item in items if not re.fullmatch(r"[嗚呜啊呀哇哦唔嗯呃…！!？?～~ー\s]+", item["quote"])]
    return items


def run_page(page):
    path = AUTO / "pages" / f"{page['id']}.json"
    if FATAL.is_set():
        return
    previous = read(path)
    if previous and previous.get("status") == "ok":
        return
    if previous:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        write(AUTO / "history" / f"{page['id']}-{stamp}.json", previous)
    if read(CORRECTIONS / "pages" / f"{page['id']}.json", {}).get("reviewed"):
        return
    result = {"page_id": page["id"], "model": MODEL, "status": "running", "phases": []}
    data = source_bytes(page)
    with Image.open(io.BytesIO(data)) as image:
        mime = Image.MIME[image.format]
        dimensions = image.size
    blocks = references() + [{"type": "image_url", "image_url": {"url": f"data:{mime};base64," + base64.b64encode(data).decode(), "detail": "high"}}]
    # Only general rules and error categories enter the prompt; human gold quotes are never supplied.
    review = human_review()
    rules = review.get("rules", {})
    prompt = LOCATE + "\n本轮人工规则：" + json.dumps(rules, ensure_ascii=False) + f"\n待处理原图尺寸{dimensions[0]}×{dimensions[1]}像素，但输出仍必须归一化至0–1000。"
    try:
        with client() as connection:
            phase = request(connection, blocks, prompt)
            result["phases"].append(phase)
            write(path, result)
            if "parsed" not in phase[-1]:
                return
            verification = request(connection, blocks, VERIFY + json.dumps(phase[-1]["parsed"], ensure_ascii=False), validate=True)
            result["phases"].append(verification)
            if "parsed" in verification[-1]:
                result["items"] = normalize(verification[-1]["parsed"]["candidates"])
                result["status"] = "ok"
    except Exception as error:
        result["error_type"] = type(error).__name__
    finally:
        if result["status"] == "running":
            result["status"] = "failed"
        write(path, result)
        print(json.dumps({"page": page["id"], "status": result["status"], "items": len(result.get("items", []))}, ensure_ascii=False), flush=True)


def run(pages, workers=2):
    FATAL.clear()
    AUTO.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(run_page, pages))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, choices=[1, 2, 3], default=2)
    args = parser.parse_args()
    run(catalogue(), args.workers)
