"""Recompute the pilot scores from frozen responses and assistant review."""

import csv
import hashlib
import json
import shutil
import unicodedata
from collections import Counter
from zipfile import ZipFile

from PIL import Image

from probe import HERE, OUT, ROOT, api_key, load, save


def normalize(text):
    return "".join(c for c in unicodedata.normalize("NFKC", text)
                   if not c.isspace() and not unicodedata.category(c).startswith("P"))


samples = load(OUT / "sample_manifest.json")
baseline = load(OUT / "visual_baseline.json")
gold = {page["page_id"]: page for page in baseline["pages"]}
catalog = {row["id"]: row for row in load(OUT / "crop_catalog.json")}
with (HERE / "review.csv").open() as source:
    reviews = list(csv.DictReader(source))
assert len(reviews) == len(catalog) == 83
seen, matched, exact_matches = set(), set(), set()
scored = []
for row in reviews:
    candidate_id = f"{row['page']}-c{int(row['candidate']):02d}"
    assert candidate_id not in seen
    seen.add(candidate_id)
    entry = catalog[candidate_id]
    indexes = [int(i) for i in row["gold_indexes"].split("|") if i]
    exact = (row["role"] == "correct" and len(indexes) == 1
             and normalize(entry["candidate"]["quote"]) == normalize(gold[row["page"]]["quotes"][indexes[0]]))
    if row["role"] == "correct":
        matched.update((row["page"], index) for index in indexes)
    if exact:
        exact_matches.add((row["page"], indexes[0]))
    scored.append({"id": candidate_id, "page_id": row["page"], "role_status": row["role"],
                   "gold_indexes": indexes, "quote": entry["candidate"]["quote"],
                   "quote_complete_correct": exact, "A": row["A"], "B": row["B"], "notes": row["notes"]})
    for version in ("A", "B"):
        crop = entry["crops"][version]
        box = crop["pixels"]
        with Image.open(HERE / crop["path"]) as image:
            image.verify()
        with Image.open(HERE / crop["path"]) as image:
            assert image.size == (box[2] - box[0], box[3] - box[1])
assert seen == set(catalog)

usage = Counter()
seconds, page_rows = [], []
for sample in samples:
    with ZipFile(ROOT / "comics" / sample["archive"]) as archive:
        original = archive.read(sample["source_file"])
    assert hashlib.sha256(original).hexdigest() == sample["sha256"]
    assert (HERE / sample["path"]).read_bytes() == original
    attempt = load(OUT / "raw" / f"{sample['id']}.json")["attempts"]
    assert len(attempt) == 1 and attempt[0]["status"] == 200
    attempt = attempt[0]
    for name in ("prompt_tokens", "completion_tokens", "total_tokens", "prompt_cache_hit_tokens", "prompt_cache_miss_tokens"):
        usage[name] += attempt["response"]["usage"][name]
    seconds.append(attempt["elapsed_seconds"])
    page_rows.append({"id": sample["id"], "negative": sample["negative"],
                      "source_file": sample["source_file"], "baseline_count": len(gold[sample["id"]]["quotes"]),
                      "output_count": len(attempt["parsed"]["candidates"]),
                      "seconds": attempt["elapsed_seconds"], "time": attempt["time"]})

role_counts = Counter(row["role_status"] for row in scored)
quality = {version: dict(Counter(row[version] for row in scored)) for version in ("A", "B")}
direct_unique = {version: len({(row["page_id"], index) for row in scored if row[version] == "direct"
                              for index in row["gold_indexes"]}) for version in ("A", "B")}
ready_unique = {version: len({(row["page_id"], index) for row in scored
                             if row[version] == "direct" and row["quote_complete_correct"]
                             for index in row["gold_indexes"]}) for version in ("A", "B")}
full = sum(row["quote_complete_correct"] for row in scored)
cost = (usage["prompt_cache_miss_tokens"] + usage["prompt_cache_hit_tokens"] * .02 + usage["completion_tokens"] * 4) / 1_000_000
summary = {"reviewer": "assistant_visual_review", "human_verified": False,
           "human_review_seconds": None, "human_modification_seconds": None,
           "page_count": len(samples), "gold_count": sum(len(p["quotes"]) for p in gold.values()),
           "candidate_count": len(scored), "role_counts": dict(role_counts),
           "detected_gold_count": len(matched), "complete_quote_count": full,
           "complete_quote_unique_gold_count": len(exact_matches),
           "crop_quality": quality, "direct_crop_unique_gold_count": direct_unique,
           "ready_asset_unique_gold_count": ready_unique,
           "negative_false_positive_pages": sum(p["negative"] and p["output_count"] > 0 for p in page_rows),
           "negative_output_count": sum(p["output_count"] for p in page_rows if p["negative"]),
           "same_A_B_coordinates": sum(p["candidate"]["panel_bbox"] == p["candidate"]["reply_bbox"] for p in catalog.values()),
           "usage": dict(usage), "api_seconds_sum": round(sum(seconds), 3),
           "api_seconds_mean": sum(seconds) / len(seconds),
           "estimated_offpeak_cny": cost, "estimated_peak_cny": 2 * cost,
           "projected_920_pages_cny": cost / len(samples) * 920,
           "projected_920_pages_api_minutes": sum(seconds) / len(samples) * 920 / 60,
           "pages": page_rows}
results = HERE / "results"
save(results / "summary.json", summary)
save(results / "candidate_review.json", scored)
for name in ("sample_manifest.json", "visual_baseline.json", "sampling_notes.json"):
    shutil.copyfile(OUT / name, results / name)

# Keep before/after examples; these are assistant coordinate corrections, not timed human work.
repairs = [{"id": "v01-p009-c02", "bbox": [500, 600, 940, 940], "notes": "扩大左侧和下侧范围保留妈妈气泡及脸"},
           {"id": "v01-p032-c01", "bbox": [0, 240, 1000, 1000], "notes": "合并同格对白并保留越界人物及气泡"}]
for repair in repairs:
    sample = next(p for p in samples if p["id"] == catalog[repair["id"]]["page_id"])
    destination = OUT / "repairs" / f"{repair['id']}-B.png"
    destination.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(HERE / sample["path"]) as original:
        box = repair["bbox"]
        pixels = [round(box[0] * original.width / 1000), round(box[1] * original.height / 1000),
                  round(box[2] * original.width / 1000), round(box[3] * original.height / 1000)]
        original.crop(pixels).save(destination)
    repair["path"] = str(destination.relative_to(HERE))
save(results / "assistant_repairs.json", {"human_modification_seconds": None, "repairs": repairs})

# Scan generated text artifacts without printing credentials.
secret = api_key()
for folder in (results, OUT / "raw"):
    for path in folder.glob("*.json"):
        assert secret not in path.read_text(), f"credential in {path.name}"
print(json.dumps({key: value for key, value in summary.items() if key != "pages"}, ensure_ascii=False, indent=2))
print("Verified 30 source hashes, 30 successful responses, 83 reviews and 166 crop dimensions; no credential in JSON artifacts.")
