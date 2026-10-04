"""Regression checks for all-character reply selection and protected human data."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import correction_store as store
import reply_selection as selection


class ReplySelectionTests(unittest.TestCase):
    def candidate(self, quote='我…生氣了…', character='萌智子'):
        return {'bbox':[300,400,700,800],'character_bbox':[350,450,650,750],'bubble_bbox':[310,410,690,790],'complete':True,'chat_score':5,'quote':quote,'character':character,'reason':'短句强烈愤怒','scene':'被冒犯时','issues':[]}

    def test_non_kurumi_short_phrase_is_retained_without_panel_union(self):
        item=selection.normalize([self.candidate()])[0]
        self.assertEqual(item['bbox'],[280,380,720,820])
        self.assertEqual(item['character'],'萌智子')
        self.assertEqual(item['speaker'],'uncertain')
        self.assertEqual(item['quote'],'我…生氣了…')

    def test_incomplete_or_low_value_phrase_is_rejected(self):
        candidate=self.candidate('只要有持續入金的能力');candidate['complete']=False
        self.assertEqual(selection.normalize([candidate]),[])
        candidate['complete']=True;candidate['chat_score']=2
        self.assertEqual(selection.normalize([candidate]),[])

    def test_duplicate_and_invalid_crop(self):
        candidate=self.candidate()
        self.assertEqual(len(selection.normalize([candidate,candidate])),1)
        candidate['bbox']=[0,0,1001,500]
        with self.assertRaises(ValueError):selection.normalize([candidate])
        self.assertEqual(selection.normalize([]),[])

    def test_crop_includes_outlying_bubble_and_character_with_clamped_margin(self):
        candidate=self.candidate();candidate['character_bbox']=[0,400,300,700];candidate['bubble_bbox']=[700,800,1000,990]
        self.assertEqual(selection.normalize([candidate])[0]['bbox'],[0,380,1000,1000])
        candidate['character_bbox']=None
        with self.assertRaises(ValueError):selection.normalize([candidate])

    def test_failed_prediction_does_not_replace_workbench_candidates(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            with patch.object(selection,'PROFILE',root/'new'),patch.object(store,'AUTO',root/'old'),patch.object(store,'CORRECTIONS',root/'human'):
                page={'id':'v04-p022','volume':4};original={'status':'ok','items':[{'id':'old'}]}
                store.write(store.AUTO/'pages/v04-p022.json',original)
                with patch.object(selection,'predict',return_value={'status':'failed'}):selection.run_page(page)
                self.assertEqual(store.read(store.AUTO/'pages/v04-p022.json'),original)

    def test_completed_or_draft_human_page_never_overwritten(self):
        for reviewed in (False,True):
            with tempfile.TemporaryDirectory() as folder:
                root=Path(folder)
                with patch.object(selection,'PROFILE',root/'new'),patch.object(store,'AUTO',root/'old'),patch.object(store,'CORRECTIONS',root/'human'):
                    page={'id':'v04-p022','volume':4}
                    original={'status':'ok','items':[{'id':'old'}]}
                    correction={'reviewed':reviewed,'items':[{'id':'human'}]}
                    store.write(store.AUTO/'pages/v04-p022.json',original)
                    store.write(store.CORRECTIONS/'pages/v04-p022.json',correction)
                    result={'page_id':page['id'],'status':'ok','items':selection.normalize([self.candidate()])}
                    with patch.object(selection,'predict',return_value=result):selection.run_page(page)
                    self.assertEqual(store.read(store.CORRECTIONS/'pages/v04-p022.json'),correction)
                    self.assertEqual(store.read(store.AUTO/'pages/v04-p022.json'),original)
                    self.assertEqual(store.read(selection.PROFILE/'pages/v04-p022.json'),result)

    def test_new_proposal_installed_with_prior_prediction_backup(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            with patch.object(selection,'PROFILE',root/'new'),patch.object(store,'AUTO',root/'old'),patch.object(store,'CORRECTIONS',root/'human'):
                page={'id':'v04-p022','volume':4};original={'status':'ok','items':[{'id':'old'}]}
                store.write(store.AUTO/'pages/v04-p022.json',original)
                result={'page_id':page['id'],'status':'ok','items':selection.normalize([self.candidate()])}
                with patch.object(selection,'predict',return_value=result):selection.run_page(page)
                self.assertEqual(store.read(store.AUTO/'pages/v04-p022.json'),result)
                self.assertEqual(store.read(selection.PROFILE/'previous/v04-p022.json'),original)

    def test_blind_crop_audit_has_no_suggested_quote_and_reads_visible_text(self):
        import io
        from PIL import Image
        image=Image.new('RGB',(100,100),'white');buffer=io.BytesIO();image.save(buffer,format='PNG')
        item=selection.normalize([self.candidate('不应传入模型的预设台词')])[0]
        decisions=[{'parsed':{'candidates':[{'index':0,'keep':True,'visible_quote':'我生氣了！','reason':'完整短句'}]}}]
        with patch.object(selection,'request',return_value=decisions) as request:
            kept,_=selection.audit_crops(None,buffer.getvalue(),[item])
        self.assertEqual(kept[0]['quote'],'我生氣了！')
        blocks=request.call_args.args[1]
        self.assertNotIn(item['quote'],str(blocks))
        self.assertEqual(item['quote'],'不应传入模型的预设台词')
        validate=request.call_args.kwargs['validator']
        with self.assertRaises(ValueError):validate([])
        with self.assertRaises(ValueError):validate([{'index':0,'keep':True,'visible_quote':''}])

    def test_only_volumes_4_to_6_are_submitted(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(selection,'PROFILE',Path(folder)),patch.object(selection,'run_page',return_value='skipped') as run_page:
            selection.run([{'id':f'v{v:02d}-p001','volume':v} for v in range(1,7)])
            self.assertEqual({call.args[0]['volume'] for call in run_page.call_args_list},{4,5,6})


if __name__=='__main__':unittest.main()
