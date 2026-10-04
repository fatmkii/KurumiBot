import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .config import ROOT
from .pricing import estimate


class AdminData:
    def __init__(self, config):
        self.config = config
        self.root = config.library.resolve()

    @contextmanager
    def database(self, path, writable=False):
        db = sqlite3.connect(path if writable else Path(path).as_uri() + "?mode=ro", uri=not writable, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            yield db
            if writable:
                db.commit()
        finally:
            db.close()

    def materials(self, search, enabled, volume, page, size):
        terms, args = [], []
        if search:
            terms.append("(id LIKE ? OR quote_traditional LIKE ? OR quote_simplified LIKE ? OR emotion_tags LIKE ? OR scenarios LIKE ?)")
            args.extend([f"%{search}%"] * 5)
        if enabled in ("0", "1"):
            terms.append("enabled=?")
            args.append(int(enabled))
        if volume:
            terms.append("volume=?")
            args.append(volume)
        where = " WHERE " + " AND ".join(terms) if terms else ""
        with self.database(self.root / "library.sqlite3") as db:
            total = db.execute("SELECT count(*) FROM materials" + where, args).fetchone()[0]
            rows = db.execute("SELECT id,quote_traditional,quote_simplified,emotion_tags,meaning,scenarios,enabled,"
                              "volume,page_id,source_file,bbox,width,height FROM materials" + where + " ORDER BY id LIMIT ? OFFSET ?",
                              (*args, size, (page - 1) * size)).fetchall()
        items = [dict(row) for row in rows]
        for item in items:
            item["emotion_tags"] = json.loads(item["emotion_tags"])
            item["bbox"] = json.loads(item["bbox"])
        return {"items": items, "total": total, "page": page, "size": size}

    def update_material(self, material_id, payload):
        fields = {"quote_traditional", "quote_simplified", "emotion_tags", "meaning", "scenarios", "enabled"}
        if not payload or set(payload) - fields:
            raise ValueError("素材修改字段无效")
        updates = dict(payload)
        for key, value in updates.items():
            if key == "enabled":
                if type(value) is not bool:
                    raise ValueError("启用状态无效")
                updates[key] = int(value)
            elif key == "emotion_tags":
                if not isinstance(value, list) or not 1 <= len(value) <= 20 or any(
                    not isinstance(tag, str) or not tag.strip() or len(tag) > 40 for tag in value
                ):
                    raise ValueError("请输入 1～20 个情绪标签，每个不超过 40 字")
                updates[key] = json.dumps([tag.strip() for tag in value], ensure_ascii=False)
            elif not isinstance(value, str) or not value.strip() or len(value) > 3000:
                raise ValueError("台词、含义与场景不能为空，每项不超过 3000 字")
            else:
                updates[key] = value.strip()
        with self.database(self.root / "library.sqlite3", writable=True) as db:
            cursor = db.execute(f"UPDATE materials SET {','.join(k + '=?' for k in updates)} WHERE id=?",
                                (*updates.values(), material_id))
            return cursor.rowcount > 0

    def material_image(self, material_id):
        with self.database(self.root / "library.sqlite3") as db:
            row = db.execute("SELECT image_path FROM materials WHERE id=?", (material_id,)).fetchone()
        if not row:
            return None
        image = (self.root / row[0]).resolve()
        return image if image.is_relative_to(self.root) and image.is_file() else None

    def conversations(self, search, status, page, size):
        terms, args = [], []
        if search:
            terms.append("c.content LIKE ?")
            args.append(f"%{search}%")
        if status:
            terms.append("c.status=?")
            args.append(status)
        where = " WHERE " + " AND ".join(terms) if terms else ""
        with self.database(self.config.history) as db:
            total = db.execute("SELECT count(*) FROM conversations c" + where, args).fetchone()[0]
            rows = db.execute(
                "SELECT c.*,a.scene,a.reason,a.elapsed_seconds AS ai_seconds "
                "FROM conversations c LEFT JOIN ai_usage a ON a.id=(SELECT max(id) FROM ai_usage WHERE conversation_id=c.id)"
                + where + " ORDER BY c.id DESC LIMIT ? OFFSET ?", (*args, size, (page - 1) * size),
            ).fetchall()
        items = [dict(row) for row in rows]
        for item in items:
            item.pop("image_path")
            item["has_image"] = self.conversation_image(item["id"]) is not None
        return {"items": items, "total": total, "page": page, "size": size}

    def conversation_image(self, conversation_id):
        with self.database(self.config.history) as db:
            row = db.execute("SELECT image_path FROM conversations WHERE id=?", (conversation_id,)).fetchone()
        if not row or not row[0]:
            return None
        image = Path(row[0]).resolve()
        # Never allow a database path to expose .env or an arbitrary server file.
        allowed = (image.is_relative_to(self.root) or image == self.config.default_image.resolve()
                   or image == (ROOT / "pics_sample/1.png").resolve())
        return image if allowed and image.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp") and image.is_file() else None

    def usage(self, errors, page, size):
        where = " WHERE error_type IS NOT NULL" if errors else ""
        with self.database(self.config.history) as db:
            total = db.execute("SELECT count(*) FROM ai_usage" + where).fetchone()[0]
            rows = db.execute("SELECT * FROM ai_usage" + where + " ORDER BY id DESC LIMIT ? OFFSET ?",
                              (size, (page - 1) * size)).fetchall()
        items = [dict(row) for row in rows]
        for item in items:
            item["usage"] = json.loads(item.pop("usage_json"))
            item["estimated_cost"] = estimate(item["model"], item["usage"])
        return {"items": items, "total": total, "page": page, "size": size}

    def overview(self):
        with self.database(self.root / "library.sqlite3") as db:
            materials = dict(db.execute("SELECT count(*) AS total,coalesce(sum(enabled),0) AS enabled FROM materials").fetchone())
            volumes = [dict(row) for row in db.execute("SELECT volume,count(*) AS total,sum(enabled) AS enabled FROM materials GROUP BY volume")]
        with self.database(self.config.history) as db:
            conversation = dict(db.execute("SELECT count(*) AS total,coalesce(sum(status='sent'),0) AS sent,"
                                           "coalesce(sum(status='failed'),0) AS failed FROM conversations").fetchone())
            rows = db.execute("SELECT model,usage_json,error_type FROM ai_usage").fetchall()
        tokens, cost, unknown = 0, 0, 0
        for row in rows:
            usage = json.loads(row["usage_json"])
            tokens += usage.get("total_tokens", 0)
            value = estimate(row["model"], usage)
            if value is None:
                unknown += 1
            else:
                cost += value
        return {"materials": materials, "volumes": volumes, "conversations": conversation,
                "ai": {"calls": len(rows), "tokens": tokens, "estimated_cost": round(cost, 6), "unknown_cost_calls": unknown},
                "recent": self.conversations("", "", 1, 5)["items"]}
