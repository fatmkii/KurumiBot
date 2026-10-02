"""No-network tests for crop integrity and the human approval boundary."""
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageChops

import correction_store as store
from auto_v2 import normalize


class CorrectionTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(dir=store.HERE / "artifacts")
        self.override = patch.object(store, "CORRECTIONS", Path(self.folder.name))
        self.override.start()
        self.page = next(p for p in store.catalogue() if p["id"] == "v01-p009")

    def tearDown(self):
        self.override.stop()
        self.folder.cleanup()

    def payload(self):
        return {"revision": 0, "source_round": "pending", "original_items": [], "reviewed": False,
                "items": [{"id": "manual-test", "bbox": [500, 600, 940, 940],
                           "quote": "媽媽！？", "speaker": "confirmed", "notes": ""}]}

    def test_exact_pixels_and_only_approved_export(self):
        payload = self.payload()
        draft = store.save_correction(self.page, payload)
        self.assertEqual(store.export_report()["items"], 0)
        self.assertEqual(draft["original_items"], [])
        self.assertEqual(draft["source_round"], "pending")
        with Image.open(io.BytesIO(store.source_bytes(self.page))) as original:
            b = payload["items"][0]["bbox"]
            pixel_box = [round(b[0]*original.width/1000), round(b[1]*original.height/1000),
                         round(b[2]*original.width/1000), round(b[3]*original.height/1000)]
            with Image.open(store.HERE / draft["items"][0]["crop_path"]) as crop:
                self.assertIsNone(ImageChops.difference(original.crop(pixel_box), crop).getbbox())
        payload.update(revision=1, reviewed=True)
        approved = store.save_correction(self.page, payload)
        self.assertEqual(approved["revision"], 2)
        self.assertEqual(store.export_report()["items"], 1)
        with self.assertRaises(RuntimeError):
            store.save_correction(self.page, payload)

    def test_incomplete_or_invalid_material_never_approved(self):
        payload = self.payload()
        payload["reviewed"] = True
        payload["items"][0]["speaker"] = "uncertain"
        with self.assertRaises(ValueError):
            store.save_correction(self.page, payload)
        payload["items"][0]["speaker"] = "confirmed"
        payload["items"][0]["bbox"] = [0, 0, 1001, 100]
        with self.assertRaises(ValueError):
            store.save_correction(self.page, payload)
        self.assertEqual(store.export_report()["items"], 0)

    def test_cross_frame_bubble_retained_and_other_speaker_excluded(self):
        candidate = {"panel_id": "p1", "identity": "kurumi", "panel_bbox": [600, 600, 900, 900],
                     "character_bbox": [650, 650, 850, 850], "bubbles": [
                         {"text": "媽媽！？", "bbox": [500, 620, 700, 820], "speaker": "kurumi", "kind": "speech"},
                         {"text": "其他人的話", "bbox": [100, 100, 200, 200], "speaker": "other", "kind": "speech"}]}
        items = normalize([candidate, candidate])
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["quote"], "媽媽！？")
        self.assertLessEqual(items[0]["bbox"][0], 500)
        self.assertGreaterEqual(items[0]["bbox"][2], 900)
        self.assertEqual(items[0]["speaker"], "uncertain")


if __name__ == "__main__":
    unittest.main()
