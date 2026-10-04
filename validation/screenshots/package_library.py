"""Build a standalone runtime library from saved, reviewed screenshot frames."""
import argparse
import hashlib
import json
import shutil
import sqlite3
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from PIL import Image

from correction_store import HERE, ROOT, catalogue, page_state
from material_state import ready


SCHEMA = """
CREATE TABLE materials (
    id TEXT PRIMARY KEY,
    image_path TEXT NOT NULL UNIQUE,
    quote_traditional TEXT NOT NULL,
    quote_simplified TEXT NOT NULL,
    emotion_tags TEXT NOT NULL,
    meaning TEXT NOT NULL,
    scenarios TEXT NOT NULL,
    volume INTEGER NOT NULL,
    page_id TEXT NOT NULL,
    source_archive TEXT NOT NULL,
    source_file TEXT NOT NULL,
    source_item_id TEXT NOT NULL,
    bbox TEXT NOT NULL,
    source_revision INTEGER NOT NULL,
    page_reviewed INTEGER NOT NULL CHECK(page_reviewed IN (0, 1)),
    quote_source TEXT NOT NULL,
    ocr_status TEXT NOT NULL,
    annotation_source TEXT NOT NULL,
    metadata_status TEXT NOT NULL,
    width INTEGER NOT NULL CHECK(width > 0),
    height INTEGER NOT NULL CHECK(height > 0),
    sha256 TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0, 1))
);
CREATE INDEX materials_enabled ON materials(enabled);
PRAGMA user_version = 1;
"""


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def collect(include_reviewed_frames=False):
    records = []
    for page in catalogue():
        state = page_state(page)
        if state['items'] and not state['reviewed'] and not include_reviewed_frames:
            raise ValueError(f"{page['id']} 尚未完成整页复核；请确认后再打包")
        for item in state['items']:
            if not ready(item):
                raise ValueError(f"{page['id']}/{item['id']} 尚未完成素材处理")
            records.append((page, state, item))
    return records


def build(records, output):
    output = Path(output).resolve()
    archive = output.with_suffix('.zip')
    if output.exists() or archive.exists():
        raise FileExistsError(f"目标已存在，请使用新的版本目录：{output}")
    if not records:
        raise ValueError("没有可打包素材")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.library-', dir=output.parent) as temporary:
        staging = Path(temporary) / output.name
        images = staging / 'images'
        images.mkdir(parents=True)
        entries = []
        identifiers = set()
        for page, state, item in records:
            identifier = f"{page['id']}-{item['id']}"
            if identifier in identifiers:
                raise ValueError(f"重复素材 ID：{identifier}")
            identifiers.add(identifier)
            source = (HERE / item['crop_path']).resolve()
            if not source.is_relative_to(HERE / 'corrections/crops'):
                raise ValueError(f"裁剪图路径无效：{identifier}")
            image_path = f"images/{identifier}.png"
            destination = staging / image_path
            shutil.copyfile(source, destination)
            with Image.open(destination) as image:
                width, height = image.size
                image.verify()
            entries.append(dict(
                id=identifier, image_path=image_path,
                quote_traditional=item['quote'], quote_simplified=item['quote_simplified'],
                emotion_tags=item['emotion_tags'], meaning=item['meaning'], scenarios=item['scenarios'],
                volume=page['volume'], page_id=page['id'], source_archive=page['archive'],
                source_file=page['source_file'], source_item_id=item['id'], bbox=item['bbox'],
                source_revision=state['revision'], page_reviewed=bool(state['reviewed']),
                quote_source=item['quote_source'], ocr_status=item['ocr_status'],
                annotation_source=item['annotation_source'], metadata_status=item['metadata_status'],
                width=width, height=height, sha256=digest(destination), enabled=True,
            ))
        database = staging / 'library.sqlite3'
        with sqlite3.connect(database) as connection:
            connection.executescript(SCHEMA)
            columns = list(entries[0])
            rows = [{k: json.dumps(v, ensure_ascii=False) if isinstance(v, list) else v
                     for k, v in entry.items()} for entry in entries]
            connection.executemany(
                f"INSERT INTO materials ({','.join(columns)}) VALUES ({','.join(':'+k for k in columns)})", rows)
            if connection.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('SQLite 完整性检查失败')
        write_json(staging / 'catalog.json', entries)
        manifest = dict(schema_version=1, created_at=datetime.now(timezone.utc).isoformat(),
                        material_count=len(entries), volumes=dict(sorted(Counter(e['volume'] for e in entries).items())),
                        database='library.sqlite3', catalog='catalog.json',
                        bbox_format='[left, top, right, bottom], normalized 0..1000',
                        individually_reviewed_pages=sorted({e['page_id'] for e in entries if not e['page_reviewed']}))
        write_json(staging / 'manifest.json', manifest)
        (staging / 'README.md').write_text(README, encoding='utf-8')
        files = sorted(p for p in staging.rglob('*') if p.is_file())
        checksums = '\n'.join(f"{digest(p)}  {p.relative_to(staging).as_posix()}" for p in files) + '\n'
        (staging / 'SHA256SUMS').write_text(checksums, encoding='utf-8')
        temporary_zip = Path(temporary) / 'package.zip'
        with ZipFile(temporary_zip, 'w', ZIP_DEFLATED) as zipped:
            for path in sorted(staging.rglob('*')):
                if path.is_file():
                    zipped.write(path, f"{output.name}/{path.relative_to(staging).as_posix()}")
        staging.rename(output)
        temporary_zip.rename(archive)
    return dict(directory=str(output), archive=str(archive), **manifest)


README = """# KurumiBot 正式素材库（格式 v1）

解压整个目录即可使用，无需漫画 ZIP、复核工作台、OCR 服务或 API 密钥。

- `library.sqlite3`：运行时数据库，`materials` 表每行对应一张图片；初始全部启用。
- `images/`：保留人工复核裁剪尺寸的 PNG 图片。
- `catalog.json`：发布时的数据快照，便于预览、导入或给 AI 提供候选文本。
- `manifest.json`：格式版本、发布时间、数量及来源复核说明。
- `SHA256SUMS`：发布时的文件校验值，在本目录执行 `sha256sum -c SHA256SUMS`。

SQLite 应作为运行时数据的主来源。后台修改 SQLite 后，JSON 快照和校验值不会自动更新。
`image_path` 相对于本目录，部署后用素材库根目录拼接，不能依赖当前工作目录。
`emotion_tags` 和 `bbox` 在 SQLite 中为 JSON 字符串，在 JSON 清单中为数组。
`bbox` 为原页归一化坐标 `[左, 上, 右, 下]`，范围 0～1000。
素材 ID 由页 ID 与原框 ID 组成，跨页面唯一。卷号、原页文件名、原框及修订号用于追溯。
`quote_traditional` 保留已确认的漫画原文；`quote_simplified` 用于检索。
框体均已人工确认；OCR 与说明保留实际来源状态，不将 AI 生成内容标记为逐条人工验收。

读取示例（Python 标准库即可）：

```python
import json
import sqlite3
from pathlib import Path

library_root = Path('/absolute/path/to/library-v1')
with sqlite3.connect(library_root / 'library.sqlite3') as db:
    db.row_factory = sqlite3.Row
    candidates = [dict(row) for row in db.execute(
        'SELECT id, image_path, quote_traditional, quote_simplified, emotion_tags, '
        'meaning, scenarios FROM materials WHERE enabled = 1 ORDER BY id'
    )]
for item in candidates:
    item['emotion_tags'] = json.loads(item['emotion_tags'])
    image = library_root / item['image_path']
    assert image.is_file()
```

给选图 AI 提供候选 ID、台词、标签、含义及场景；返回 ID 后再次检查启用状态和文件存在，再发送图片。
启停可执行 `UPDATE materials SET enabled = ? WHERE id = ?`。正式图库更新应发布新版本。
默认反应图尚未指定：Bot 接入阶段需要另外选择固定兜底图片，不应隐式使用清单第一项。
"""


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'materials/library-v1')
    parser.add_argument('--include-reviewed-frames', action='store_true',
                        help='经确认，允许整页尚未完成但每个保留框均已复核的素材')
    args = parser.parse_args()
    print(json.dumps(build(collect(args.include_reviewed_frames), args.output), ensure_ascii=False, indent=2))
