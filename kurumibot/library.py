import json
import random
import sqlite3
from pathlib import Path


class Library:
    def __init__(self, root: Path):
        self.root = root.resolve()
        # mode=ro prevents a typo in the configured path from creating an empty DB.
        self.db = sqlite3.connect((self.root / "library.sqlite3").as_uri() + "?mode=ro", uri=True)
        self.db.row_factory = sqlite3.Row

    def image(self, item):
        path = (self.root / item["image_path"]).resolve()
        if not path.is_relative_to(self.root) or not path.is_file():
            return None
        return path

    def candidates(self):
        result = []
        for row in self.db.execute(
            "SELECT id, image_path, quote_traditional, quote_simplified, emotion_tags, meaning, scenarios "
            "FROM materials WHERE enabled = 1 ORDER BY id"
        ):
            item = dict(row)
            if self.image(item):
                item["emotion_tags"] = json.loads(item["emotion_tags"])
                result.append(item)
        return result

    def selected_image(self, material_id, candidate_ids):
        if material_id not in candidate_ids:
            return None
        row = self.db.execute("SELECT image_path FROM materials WHERE id = ? AND enabled = 1", (material_id,)).fetchone()
        return self.image(row) if row else None

    def random_image(self, recent_ids=()):
        items = [dict(row) for row in self.db.execute(
            "SELECT id, image_path FROM materials WHERE enabled = 1"
        ) if self.image(row)]
        fresh = [item for item in items if item["id"] not in recent_ids]
        pool = fresh or items
        if not pool:
            return None, None
        item = random.choice(pool)
        return item["id"], self.image(item)

    def close(self):
        self.db.close()
