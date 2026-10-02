"""Source catalogue and versioned human corrections for all six volumes."""
import io
import json
import math
import re
import threading
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from zipfile import ZipFile

from PIL import Image
from material_state import material, metadata_complete, ready

LOCK = threading.RLock()

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
AUTO = HERE / "artifacts/round2"
CORRECTIONS = HERE / "corrections"


def read(path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def natural(value):
    return [int(p) if p.isdigit() else p.casefold() for p in re.split(r"(\d+)", value)]


def catalogue():
    path = HERE / "artifacts/all_pages.json"
    if path.exists():
        return read(path)
    pages = []
    for archive in sorted((ROOT / "comics").glob("*.zip")):
        volume = int(re.search(r"卷(\d+)", archive.name).group(1))
        with ZipFile(archive) as zipped:
            names = sorted([n for n in zipped.namelist() if Path(n).suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"} and not n.startswith("__MACOSX/")], key=natural)
            for index, name in enumerate(names, 1):
                pages.append({"id": f"v{volume:02d}-p{index:03d}", "volume": volume,
                              "archive": archive.name, "source_file": name})
    write(path, pages)
    return pages


def source_bytes(page):
    with ZipFile(ROOT / "comics" / page["archive"]) as zipped:
        return zipped.read(page["source_file"])


def valid_box(box):
    return (isinstance(box, list) and len(box) == 4
            and all(type(v) in (float, int) and math.isfinite(v) and 0 <= v <= 1000 for v in box)
            and box[0] < box[2] and box[1] < box[3])


def human_review():
    return read(HERE / "human-review/review.json", {"pages": {}, "candidates": {}, "rules": {}})


def hints(page_id):
    review = human_review()
    p = review.get("pages", {}).get(page_id, {})
    return {"page": p, "candidates": {k: v for k, v in review.get("candidates", {}).items() if k.startswith(page_id + "-")}}


def page_state(page):
    saved = read(CORRECTIONS / "pages" / f"{page['id']}.json")
    if saved:
        for item in saved["items"]:
            if "quote_source" not in item and item.get("quote", "").strip():
                item.update(quote_source="human", ocr_status="confirmed")
        saved["items"] = [material(i, saved["reviewed"]) for i in saved["items"]]
        return saved
    result = read(AUTO / "pages" / f"{page['id']}.json")
    items, seed = [], "pending"
    if result and result.get("status") == "ok":
        seed, items = "round2", result["items"]
    else:
        old = read(HERE / "artifacts/crop_catalog.json", [])
        for entry in old:
            if entry["page_id"] != page["id"]:
                continue
            c = entry["candidate"]
            if valid_box(c.get("reply_bbox")):
                items.append({"id": entry["id"], "bbox": c["reply_bbox"], "quote": c["quote"],
                              "speaker": "uncertain", "notes": "首轮候选，需重新确认说话人与截图"})
        if items:
            seed = "round1"
    items = [material(i) for i in items]
    return {"page_id": page["id"], "revision": 0, "reviewed": False, "source_round": seed,
            "items": items, "original_items": items, "notes": "", "updated_at": ""}


def save_correction(page, payload, generated=False):
    existing = page_state(page)
    if payload.get("revision") != existing["revision"]:
        raise RuntimeError("此页有更新，请重新载入后再保存")
    items = payload.get("items")
    if not isinstance(items, list) or len(items) > 100:
        raise ValueError("素材列表无效")
    ids, normalized = set(), []
    for item in items:
        identifier, box = item.get("id", ""), item.get("bbox")
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", identifier) or identifier in ids or not valid_box(box):
            raise ValueError("素材编号或裁剪坐标无效")
        ids.add(identifier)
        quote, speaker = item.get("quote", ""), item.get("speaker", "uncertain")
        if not isinstance(quote, str) or len(quote) > 10000 or speaker not in {"confirmed", "uncertain"}:
            raise ValueError("台词或说话人状态无效")
        box_reviewed = bool(item.get("box_reviewed", existing["reviewed"]) or payload.get("reviewed"))
        if box_reviewed and speaker != "confirmed":
            raise ValueError("确认裁剪框前，请确认说话人；错误素材请删除")
        previous = next((i for i in existing["items"] if i["id"] == identifier), None)
        current = material({"id": identifier, "bbox": box, "quote": quote,
                            "speaker": speaker, "notes": str(item.get("notes", ""))[:10000],
                            "box_reviewed": box_reviewed})
        for field in ("quote_source", "ocr_status", "ocr_text", "ocr_bbox", "ocr_error",
                      "emotion_tags", "meaning", "scenarios", "annotation_source", "metadata_status", "metadata_error"):
            if field in item:
                current[field] = deepcopy(item[field])
        if current["ocr_status"] not in {"pending", "running", "recognized", "confirmed", "failed", "stale", "needs_review"}:
            raise ValueError("OCR 状态无效")
        if current["metadata_status"] not in {"pending", "running", "generated", "confirmed", "failed", "stale"}:
            raise ValueError("说明状态无效")
        if (not isinstance(current["emotion_tags"], list) or len(current["emotion_tags"]) > 30
                or any(not isinstance(t, str) or len(t) > 100 for t in current["emotion_tags"])
                or any(not isinstance(current[f], str) or len(current[f]) > 10000 for f in ("meaning", "scenarios", "ocr_text"))):
            raise ValueError("情绪标签或说明内容无效")
        if not generated:
            quote_changed = (previous is None and bool(quote.strip())) or (previous is not None and quote != previous["quote"])
            box_changed = previous is not None and box != previous["bbox"]
            if quote_changed:
                current.update(quote_source="human", ocr_status="confirmed" if quote.strip() else "pending")
            if box_changed:
                current["ocr_status"] = "stale"
            annotation_changed = any(current[f] != (previous or material({}))[f] for f in ("emotion_tags", "meaning", "scenarios"))
            if annotation_changed:
                current.update(annotation_source="human", metadata_status="confirmed" if metadata_complete(current) else "pending")
            elif (quote_changed or box_changed) and metadata_complete(current):
                current["metadata_status"] = "stale"
        if current["metadata_status"] in {"generated", "confirmed"} and not metadata_complete(current):
            raise ValueError("说明尚未填写完整")
        normalized.append(current)
    with Image.open(io.BytesIO(source_bytes(page))) as original:
        for item in normalized:
            b = item["bbox"]
            pixels = [round(b[0] * original.width / 1000), round(b[1] * original.height / 1000),
                      round(b[2] * original.width / 1000), round(b[3] * original.height / 1000)]
            if pixels[0] >= pixels[2] or pixels[1] >= pixels[3]:
                raise ValueError("截图小于一个像素，请扩大裁剪框")
    stamp = datetime.now(timezone.utc).isoformat()
    original_items = existing["original_items"]
    source_round = existing["source_round"]
    if existing["revision"] == 0:
        # Automation can finish while the user is editing. Audit against what they actually loaded.
        original_items = payload.get("original_items", original_items)
        source_round = payload.get("source_round", source_round)
        if source_round not in {"pending", "round1", "round2"} or not isinstance(original_items, list):
            raise ValueError("原始候选记录无效")
    record = {"page_id": page["id"], "revision": existing["revision"] + 1,
              "reviewed": bool(payload.get("reviewed")), "source_round": source_round,
              "items": normalized, "original_items": original_items,
              "notes": str(payload.get("notes", ""))[:10000], "updated_at": stamp}
    # Each revision has its own crops; drafts never overwrite a previously approved crop.
    crop_dir = CORRECTIONS / "crops" / page["id"] / str(record["revision"])
    crop_dir.mkdir(parents=True, exist_ok=True)
    with Image.open(io.BytesIO(source_bytes(page))) as original:
        for item in normalized:
            b = item["bbox"]
            pixels = [round(b[0] * original.width / 1000), round(b[1] * original.height / 1000),
                      round(b[2] * original.width / 1000), round(b[3] * original.height / 1000)]
            original.crop(pixels).save(crop_dir / f"{item['id']}.png")
            item["crop_path"] = str((crop_dir / f"{item['id']}.png").relative_to(HERE))
    write(CORRECTIONS / "history" / f"{page['id']}-r{record['revision']}.json", record)
    write(CORRECTIONS / "pages" / f"{page['id']}.json", record)
    return record


def export_report():
    pages = catalogue()
    records = [page_state(p) if (CORRECTIONS / "pages" / f"{p['id']}.json").exists() else None for p in pages]
    approved = [r for r in records if r and r["reviewed"]]
    library = [{"page_id": r["page_id"], **item} for r in approved for item in r["items"]]
    pending = [p["id"] for p, r in zip(pages, records) if not r or not r["reviewed"]]
    report = ["# 人工修正报告", "", f"全部页面：{len(pages)}；已全面复核：{len(approved)}；待复核：{len(pending)}；框已复核素材：{len(library)}；信息齐全可入库：{sum(ready(i) for i in library)}。", "",
              "仅导出已完成整页复核的素材。草稿、未确认说话人及未复核页面均不入库。", "", "## 逐页修正", ""]
    for r in approved:
        before = {i["id"]: i for i in r["original_items"]}
        after = {i["id"]: i for i in r["items"]}
        adjusted = sum(i in before and (before[i]["bbox"] != after[i]["bbox"] or before[i]["quote"] != after[i]["quote"]) for i in after)
        report.append(f"- {r['page_id']}：保留 {len(after)}，追加 {len(after.keys()-before.keys())}，删除 {len(before.keys()-after.keys())}，调整框／台词 {adjusted}。{r['notes']}")
    report.extend(["", "## 下一轮", "", "- corrected_catalog.json 保留全部框已复核素材与处理状态；ready_catalog.json 仅含图片、繁简台词与说明齐全的素材。", "- 按待复核清单逐页检查，零候选页面同样需要确认。", "- 将人工删除的候选作为角色／归属反例，将调整后的框作为定位对照，另选新样本评估泛化。", "", "待复核页面：", ", ".join(pending), ""])
    write(CORRECTIONS / "corrected_catalog.json", library)
    write(CORRECTIONS / "ready_catalog.json", [i for i in library if ready(i)])
    CORRECTIONS.mkdir(exist_ok=True)
    (CORRECTIONS / "人工修正报告.md").write_text("\n".join(report))
    return {"approved_pages": len(approved), "pending_pages": len(pending), "items": len(library),
            "report": "\n".join(report), "catalogue": library, "ready_items": sum(ready(i) for i in library)}
