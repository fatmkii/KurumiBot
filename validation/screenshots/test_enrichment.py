"""Network-free tests for enrichment, preservation and concurrent human edits."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import correction_store as store
import enrichment
from material_state import ready


class EnrichmentTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(dir=store.HERE / 'artifacts')
        self.override = patch.object(store, 'CORRECTIONS', Path(self.folder.name))
        self.override.start()
        self.page = next(p for p in store.catalogue() if p['id'] == 'v01-p009')
        self.payload = {'revision': 0, 'source_round': 'pending', 'original_items': [], 'reviewed': True,
                        'items': [{'id': 'manual-test', 'bbox': [500, 600, 940, 940],
                                   'quote': '', 'speaker': 'confirmed', 'notes': ''}]}
        self.ocr = patch.object(enrichment, 'ocr', return_value={'quote': '媽媽！？', 'uncertain': False})
        self.describe = patch.object(enrichment, 'describe', return_value={'emotion_tags': ['惊讶'], 'meaning': '惊呼母亲', 'scenarios': '出乎意料时'})
        self.ocr_mock = self.ocr.start()
        self.meta_mock = self.describe.start()

    def tearDown(self):
        self.ocr.stop()
        self.describe.stop()
        self.override.stop()
        self.folder.cleanup()

    def save(self):
        return store.save_correction(self.page, self.payload)

    def item(self):
        return store.page_state(self.page)['items'][0]

    def test_empty_quote_can_review_then_complete_and_resume(self):
        self.save()
        self.assertFalse(ready(self.item()))
        self.assertEqual(enrichment.process(self.page, 'manual-test', 'all'), 'completed')
        item = self.item()
        self.assertEqual(item['quote_simplified'], '妈妈！？')
        self.assertEqual(item['ocr_status'], 'recognized')
        self.assertEqual(item['metadata_status'], 'generated')
        self.assertTrue(ready(item))
        self.assertEqual(enrichment.process(self.page, 'manual-test', 'all'), 'skipped')
        self.ocr_mock.assert_called_once()
        self.assertEqual(store.export_report()['ready_items'], 1)

    def test_human_text_and_annotations_not_overwritten(self):
        self.payload['items'][0].update(quote='人工確認台詞', emotion_tags=['自定义'], meaning='人工含义', scenarios='人工场景')
        self.save()
        self.assertEqual(enrichment.process(self.page, 'manual-test', 'all'), 'completed')
        item = self.item()
        self.assertEqual(item['quote'], '人工確認台詞')
        self.assertEqual(item['quote_simplified'], '人工确认台词')
        self.assertEqual(item['ocr_text'], '媽媽！？')
        self.assertEqual(item['meaning'], '人工含义')
        self.meta_mock.assert_not_called()

    def test_empty_ocr_is_valid_and_clears_automatic_guess_without_description(self):
        self.save()
        saved = store.page_state(self.page)
        saved['items'][0].update(quote='旧自动猜测', quote_source='auto')
        store.save_correction(self.page, saved, generated=True)
        record = self.item()
        self.assertEqual(record['quote_source'], 'auto')
        self.ocr_mock.return_value = {'quote': '', 'uncertain': False}
        self.assertEqual(enrichment.process(self.page, 'manual-test', 'all'), 'failed')
        item = self.item()
        self.assertEqual(item['quote'], '')
        self.assertEqual(item['ocr_status'], 'needs_review')
        self.assertEqual(item['ocr_error'], '')
        self.assertFalse(ready(item))
        self.meta_mock.assert_not_called()

    def test_ocr_prompt_accepts_no_visible_dialogue(self):
        self.ocr.stop()
        def no_dialogue(image, prompt, validate):
            data = {'quote': '', 'uncertain': False}
            validate(data)
            return data
        with patch.object(enrichment, 'request', side_effect=no_dialogue):
            self.assertEqual(enrichment.ocr(b'not-sent')['quote'], '')

    def test_changed_crop_invalidates_and_requires_review(self):
        self.save()
        enrichment.process(self.page, 'manual-test', 'all')
        record = store.page_state(self.page)
        record['reviewed'] = False
        record['items'][0].update(bbox=[490, 600, 940, 940], box_reviewed=False)
        store.save_correction(self.page, record)
        self.assertEqual(self.item()['ocr_status'], 'stale')
        self.assertEqual(self.item()['metadata_status'], 'stale')
        self.assertEqual(enrichment.process(self.page, 'manual-test', 'all'), 'skipped')
        self.assertEqual(store.export_report()['ready_items'], 0)

    def test_ocr_result_cannot_replace_concurrent_human_edit(self):
        self.save()
        def human_edits(_):
            record = store.page_state(self.page)
            record['items'][0]['quote'] = '人工修正'
            store.save_correction(self.page, record)
            return {'quote': 'AI舊結果', 'uncertain': False}
        self.ocr_mock.side_effect = human_edits
        self.assertEqual(enrichment.process(self.page, 'manual-test', 'all'), 'skipped')
        self.assertEqual(self.item()['quote'], '人工修正')
        self.assertEqual(self.item()['ocr_status'], 'confirmed')

    def test_failures_retry_and_unreviewed_material_skips(self):
        self.payload['reviewed'] = False
        self.save()
        self.assertEqual(enrichment.process(self.page, 'manual-test', 'all'), 'skipped')
        record = store.page_state(self.page)
        record['reviewed'] = True
        store.save_correction(self.page, record)
        self.ocr_mock.side_effect = ValueError('invalid output')
        with self.assertRaises(ValueError):
            enrichment.process(self.page, 'manual-test', 'all')
        self.assertEqual(self.item()['ocr_status'], 'failed')
        self.ocr_mock.side_effect = None
        self.assertEqual(enrichment.process(self.page, 'manual-test', 'all'), 'completed')

    def test_partial_manual_annotations_are_filled_without_replacing_tags(self):
        self.payload['items'][0]['emotion_tags'] = ['人工标签']
        self.save()
        enrichment.process(self.page, 'manual-test', 'all')
        self.assertEqual(self.item()['emotion_tags'], ['人工标签'])
        self.assertTrue(self.item()['meaning'])

    def test_uncertain_ocr_requires_confirmation_before_description(self):
        self.save()
        self.ocr_mock.return_value['uncertain'] = True
        self.assertEqual(enrichment.process(self.page, 'manual-test', 'all'), 'failed')
        self.assertEqual(self.item()['ocr_status'], 'needs_review')
        self.meta_mock.assert_not_called()

    def test_fatal_api_error_stops_batch_and_preserves_retry_state(self):
        self.save()
        self.ocr_mock.side_effect = enrichment.FatalAPIError('API HTTP 402，批量任务已停止')
        state = {'status': 'running', 'total': 2, 'completed': 0, 'failed': 0, 'skipped': 0}
        enrichment.run([(self.page, 'manual-test'), (self.page, 'manual-test')], 'all', False, state)
        self.assertEqual(state['status'], 'stopped')
        self.assertEqual(state['failed'], 1)
        self.ocr_mock.assert_called_once()
        self.assertEqual(self.item()['ocr_status'], 'failed')
        self.assertEqual(store.read(enrichment.job_path())['status'], 'stopped')

    def test_only_matching_volume_context_is_sent(self):
        self.describe.stop()
        for volume in range(1, 7):
            with patch.object(enrichment, 'request', return_value={}) as request:
                enrichment.describe(b'not-sent', '台詞', volume)
            prompt = request.call_args.args[1]
            self.assertIn(f'第 {volume} 卷背景', prompt)
            for other in range(1, 7):
                if other != volume:
                    self.assertNotIn(f'第 {other} 卷背景', prompt)

    def test_background_is_included_in_description_prompt(self):
        self.describe.stop()
        image = b'not-sent'
        with patch.object(enrichment, 'request', return_value={}) as request:
            enrichment.describe(image, '測試台詞')
        prompt = request.call_args.args[1]
        self.assertIn('作品简介', prompt)
        self.assertIn('久留美简介', prompt)
        self.assertIn('2,000', prompt)
        self.assertIn('不能冒充漫画原剧情', prompt)
        self.assertIn('測試台詞', prompt)


if __name__ == '__main__':
    unittest.main()
