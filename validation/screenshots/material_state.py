"""Independent review and enrichment state; backwards-compatible with saved crops."""
from copy import deepcopy
from opencc import OpenCC

CONVERTER = OpenCC('t2s')
OCR_DONE = {'recognized', 'confirmed'}
META_DONE = {'generated', 'confirmed'}


def material(item, reviewed=False):
    result = deepcopy(item)
    result.setdefault('box_reviewed', reviewed)
    result.setdefault('quote_source', 'auto')
    result.setdefault('ocr_status', 'pending')
    result.setdefault('ocr_text', '')
    result.setdefault('emotion_tags', [])
    result.setdefault('meaning', '')
    result.setdefault('scenarios', '')
    result.setdefault('annotation_source', 'ai')
    result.setdefault('metadata_status', 'pending')
    result['quote_simplified'] = CONVERTER.convert(result.get('quote', ''))
    return result


def metadata_complete(item):
    return bool(item.get('emotion_tags') and item.get('meaning', '').strip() and item.get('scenarios', '').strip())


def ready(item):
    return (item.get('box_reviewed') and item.get('speaker') == 'confirmed'
            and bool(item.get('quote', '').strip()) and item.get('ocr_status') in OCR_DONE
            and metadata_complete(item) and item.get('metadata_status') in META_DONE)
