import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from PIL import Image

import package_library as package


class PackageLibraryTests(unittest.TestCase):
    def test_standalone_package_and_enabled_filter(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            crop = root / 'corrections/crops/page/1/item.png'
            crop.parent.mkdir(parents=True)
            Image.new('RGB', (30, 40), 'white').save(crop)
            item = dict(id='auto-01', crop_path=str(crop.relative_to(root)),
                        quote='放棄FX', quote_simplified='放弃FX', emotion_tags=['无奈'],
                        meaning='放弃交易', scenarios='决定放弃时', bbox=[0, 0, 1000, 1000],
                        quote_source='human', ocr_status='confirmed',
                        annotation_source='ai', metadata_status='generated')
            records = [(dict(id='v01-p041', volume=1, archive='source.zip', source_file='41.jpg'),
                        dict(revision=14, reviewed=False), item)]
            with patch.object(package, 'HERE', root):
                result = package.build(records, root / 'library-v1')
            with ZipFile(result['archive']) as zipped:
                self.assertIsNone(zipped.testzip())
                zipped.extractall(root / 'deploy')
            # The deployed library remains usable after all source crops disappear.
            crop.unlink()
            deployed = root / 'deploy/library-v1'
            catalog = json.loads((deployed / 'catalog.json').read_text())
            self.assertEqual(catalog[0]['id'], 'v01-p041-auto-01')
            self.assertEqual(catalog[0]['quote_traditional'], '放棄FX')
            self.assertEqual(package.digest(deployed / catalog[0]['image_path']), catalog[0]['sha256'])
            with sqlite3.connect(deployed / 'library.sqlite3') as db:
                self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
                self.assertEqual(db.execute('SELECT COUNT(*) FROM materials WHERE enabled=1').fetchone()[0], 1)
                db.execute('UPDATE materials SET enabled=0 WHERE id=?', ('v01-p041-auto-01',))
                self.assertEqual(db.execute('SELECT COUNT(*) FROM materials WHERE enabled=1').fetchone()[0], 0)
            self.assertEqual(result['individually_reviewed_pages'], ['v01-p041'])

    def test_incomplete_frames_cannot_be_packaged(self):
        page = dict(id='v01-p001')
        state = dict(reviewed=True, items=[dict(id='auto-01', box_reviewed=False)])
        with patch.object(package, 'catalogue', return_value=[page]), patch.object(package, 'page_state', return_value=state):
            with self.assertRaisesRegex(ValueError, '尚未完成素材处理'):
                package.collect(include_reviewed_frames=True)

    def test_partial_page_requires_explicit_option(self):
        page = dict(id='v01-p001')
        item = dict(id='auto-01', box_reviewed=True, speaker='confirmed', quote='台詞',
                    ocr_status='confirmed', emotion_tags=['激动'], meaning='含义',
                    scenarios='场景', metadata_status='generated')
        state = dict(reviewed=False, items=[item])
        with patch.object(package, 'catalogue', return_value=[page]), patch.object(package, 'page_state', return_value=state):
            with self.assertRaisesRegex(ValueError, '尚未完成整页复核'):
                package.collect()
            self.assertEqual(len(package.collect(include_reviewed_frames=True)), 1)


if __name__ == '__main__':
    unittest.main()
