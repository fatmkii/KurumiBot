# KurumiBot 正式素材库（格式 v1）

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
