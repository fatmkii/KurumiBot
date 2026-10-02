"""Resumable OCR and image-aware descriptions for confirmed crop boxes."""
import base64
import io
import json
import threading
from copy import deepcopy
from datetime import datetime, timezone

from PIL import Image

import correction_store as store
from material_state import META_DONE, OCR_DONE, metadata_complete
from model_client import MODEL, client

JOB = None


class FatalAPIError(RuntimeError):
    pass


def request(image, prompt, validate):
    block = {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + base64.b64encode(image).decode(), 'detail': 'high'}}
    with client() as connection:
        for attempt in range(2):
            response = connection.post('/chat/completions', json={
                'model': MODEL, 'thinking': {'type': 'disabled'}, 'response_format': {'type': 'json_object'},
                'max_tokens': 2500, 'messages': [{'role': 'user', 'content': [{'type': 'text', 'text': prompt}, block]}]})
            if response.status_code in {400, 401, 402, 403, 404, 422}:
                raise FatalAPIError(f'API HTTP {response.status_code}，批量任务已停止')
            try:
                response.raise_for_status()
                data = json.loads(response.json()['choices'][0]['message']['content'])
                validate(data)
                return data
            except (ValueError, KeyError, TypeError):
                if attempt:
                    raise ValueError('模型输出字段无效，请重试') from None
    raise RuntimeError('识别失败')


def ocr(image):
    def validate(data):
        if not isinstance(data.get('quote'), str) or not data['quote'].strip() or len(data['quote']) > 10000 or type(data.get('uncertain')) is not bool:
            raise ValueError('OCR quote missing')
    return request(image, '''这是人工确认的久留美素材裁剪图。识别她的完整繁体原台词，保持原字与标点，多气泡按漫画阅读顺序合并。
仅提取久留美的对白或明确内心独白，排除他人对白、旁白、拟声词。不要补写看不清或裁剪之外的文字。
若角色或文字归属不确定、文字不完整则 uncertain=true。无可识别台词返回空 quote。
仅输出 JSON {"quote":"繁体原文","uncertain":false}。''', validate)


def describe(image, quote, volume=None):
    def validate(data):
        if (not isinstance(data.get('emotion_tags'), list) or not 1 <= len(data['emotion_tags']) <= 10
                or any(not isinstance(t, str) or not t.strip() or len(t) > 100 for t in data['emotion_tags'])
                or any(not isinstance(data.get(f), str) or not data[f].strip() or len(data[f]) > 10000 for f in ('meaning', 'scenarios'))):
            raise ValueError('description missing')
    background = (store.HERE / '作品与角色背景.md').read_text().split('## 核对来源')[0]
    if volume is not None:
        context_path = store.HERE / 'volume-context' / f'{volume:02d}.md'
        if context_path.exists():
            background += '\n\n' + context_path.read_text().split('## 核对来源')[0]
    return request(image, background + '\n\n' + '''结合裁剪图的表情、动作与下方台词，生成用于聊天选图的简体中文说明。
台词以提供的文本为准，不能改写或凭空补充剧情。emotion_tags 是1至6个简短情绪标签；meaning 解释字面含义及语气；
scenarios 说明适合回复什么样的话、情绪或玩梗场景，必要时指出不适用场景。不要把推测的漫画剧情写成事实。
只输出 JSON {"emotion_tags":["惊讶"],"meaning":"台词含义与语气","scenarios":"适用聊天场景"}。
已保存台词：''' + json.dumps(quote, ensure_ascii=False), validate)


def job_path():
    return store.CORRECTIONS / 'enrichment_job.json'


def progress():
    status = store.read(job_path(), {'status': 'idle', 'total': 0, 'completed': 0, 'failed': 0, 'skipped': 0})
    status['running'] = bool(JOB and JOB.is_alive())
    if status.get('status') == 'running' and not status['running']:
        status['status'] = 'interrupted'
    return status


def eligible(item):
    return item.get('box_reviewed') and item.get('speaker') == 'confirmed'


def start(pages, mode='all', page_id=None, item_id=None):
    global JOB
    if mode not in {'all', 'ocr', 'metadata'}:
        raise ValueError('处理模式无效')
    with store.LOCK:
        if JOB and JOB.is_alive():
            raise RuntimeError('已有素材处理任务运行，请等待完成')
        tasks = []
        for page in pages:
            if page_id and page['id'] != page_id:
                continue
            if not (store.CORRECTIONS / 'pages' / f"{page['id']}.json").exists():
                continue
            for item in store.page_state(page)['items']:
                if item_id and item['id'] != item_id:
                    continue
                if eligible(item):
                    tasks.append((page, item['id']))
        if not tasks:
            raise ValueError('没有已保存且框体／说话人已确认的素材，请先保存复核结果')
        state = {'status': 'running', 'total': len(tasks), 'completed': 0, 'failed': 0, 'skipped': 0,
                 'current': '', 'mode': mode, 'error': '', 'started_at': datetime.now(timezone.utc).isoformat()}
        store.write(job_path(), state)
        JOB = threading.Thread(target=run, args=(tasks, mode, bool(item_id), state), daemon=True)
        JOB.start()
        return progress()


def update(page, item_id, expected, changes):
    with store.LOCK:
        record = store.page_state(page)
        item = next((i for i in record['items'] if i['id'] == item_id), None)
        # API responses must not overwrite edits/deletions made while the request was running.
        if not item or not eligible(item) or any(item.get(f) != expected.get(f) for f in ('bbox', 'quote', 'quote_source', 'emotion_tags', 'meaning', 'scenarios', 'annotation_source')):
            return None
        item.update(changes)
        saved = store.save_correction(page, record, generated=True)
        return next(i for i in saved['items'] if i['id'] == item_id)


def process(page, item_id, mode, force=False):
    with store.LOCK:
        item = next((deepcopy(i) for i in store.page_state(page)['items'] if i['id'] == item_id), None)
    if not item or not eligible(item):
        return 'skipped'
    with Image.open(io.BytesIO(store.source_bytes(page))) as original:
        b = item['bbox']
        pixels = [round(b[0]*original.width/1000), round(b[1]*original.height/1000), round(b[2]*original.width/1000), round(b[3]*original.height/1000)]
        output = io.BytesIO()
        original.crop(pixels).save(output, format='PNG')
        image = output.getvalue()
    did_work = False
    if mode in {'all', 'ocr'} and (force or item['ocr_status'] not in OCR_DONE or not item.get('ocr_bbox')):
        expected = update(page, item_id, item, {'ocr_status': 'running', 'ocr_error': ''})
        if not expected:
            return 'skipped'
        try:
            result = ocr(image)
            changes = {'ocr_text': result['quote'], 'ocr_bbox': item['bbox'],
                       'ocr_status': 'needs_review' if result['uncertain'] else 'recognized'}
            if expected['quote_source'] == 'human' and expected['quote'].strip():
                changes['ocr_status'] = 'confirmed' if not result['uncertain'] else 'needs_review'
            else:
                changes.update(quote=result['quote'], quote_source='ocr')
                if result['quote'] != expected['quote'] and metadata_complete(expected):
                    changes['metadata_status'] = 'stale'
            item = update(page, item_id, expected, changes)
            if not item:
                return 'skipped'
            did_work = True
        except Exception as error:
            update(page, item_id, expected, {'ocr_status': 'failed', 'ocr_error': str(error) if isinstance(error, (FatalAPIError, ValueError)) else type(error).__name__})
            raise
    if mode in {'all', 'metadata'}:
        if not item['quote'].strip() or item['ocr_status'] not in OCR_DONE:
            return 'failed' if did_work else 'skipped'
        # Human annotations are never regenerated by a batch job.
        if (item['annotation_source'] != 'human' and (force or item['metadata_status'] not in META_DONE)) or not metadata_complete(item):
            expected = update(page, item_id, item, {'metadata_status': 'running', 'metadata_error': ''})
            if not expected:
                return 'skipped'
            try:
                result = describe(image, item['quote'], page['volume'])
                if expected['annotation_source'] == 'human':
                    result = {f: expected[f] or result[f] for f in ('emotion_tags', 'meaning', 'scenarios')}
                if not update(page, item_id, expected, {**result, 'annotation_source': expected['annotation_source'], 'metadata_status': 'generated'}):
                    return 'skipped'
                did_work = True
            except Exception as error:
                update(page, item_id, expected, {'metadata_status': 'failed', 'metadata_error': str(error) if isinstance(error, (FatalAPIError, ValueError)) else type(error).__name__})
                raise
    return 'completed' if did_work else 'skipped'


def run(tasks, mode, force, state):
    try:
        for page, item_id in tasks:
            state['current'] = page['id'] + '/' + item_id
            store.write(job_path(), state)
            try:
                outcome = process(page, item_id, mode, force)
            except FatalAPIError as error:
                state['failed'] += 1
                state.update(status='stopped', error=str(error))
                break
            except Exception as error:
                outcome = 'failed'
                state['error'] = type(error).__name__
            with store.LOCK:
                record = store.page_state(page)
                item = next((i for i in record['items'] if i['id'] == item_id), None)
                if item and any(item[f] == 'running' for f in ('ocr_status', 'metadata_status')):
                    for field in ('ocr_status', 'metadata_status'):
                        if item[field] == 'running':
                            item[field] = 'stale'
                    store.save_correction(page, record, generated=True)
            state[outcome] += 1
            store.write(job_path(), state)
        else:
            state['status'] = 'finished'
    finally:
        state['finished_at'] = datetime.now(timezone.utc).isoformat()
        store.write(job_path(), state)
