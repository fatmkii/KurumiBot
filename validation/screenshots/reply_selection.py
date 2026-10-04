"""Reply-oriented candidate prediction learned from the user's reviewed crops."""
import argparse
import io
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from PIL import Image, ImageDraw, ImageFont

import correction_store as store
from auto_v2 import FATAL, request
from model_client import MODEL, client, image_block

POLICY = 'short-replies-v2'
PROFILE = store.HERE / 'artifacts/reply-selection'
LOCATE = '''你为《FX战士久留美》娱乐 QQ bot 筛选能一针见血回复聊天的台词图片。
之前的图片是用户已保留的选材示例；只有标记为“待处理原页”的最后一张图片需要提取。绝对不能将示例台词复制到待处理页。
选材规则：
1. 不限角色：久留美、芽吹、萌智子、やす子及其他人物的台词均可；也可选明确内心独白。不得因不是久留美而剔除。
2. 优先短而有冲击力、有名台词／梗潜力的片段：崩溃、吐槽、反问、嘲讽、逞强、自信过头、愤怒、求饶、后悔、暴富幻想等。
3. 必须是完整意思的回复，不是从一句话或同一连续句中拆下来的半截。比如“听起来”“这样一来不管上涨还是下跌”“只要有持续入金的能力”均是未说完的条件/修饰语，不能单独选；相邻气泡延续同一句必须全部包含，否则放弃。单独的数字价格、“限价止盈订单”等技术名词、平淡“真突然啊”、普通询问“需要多少？”不因字少就入选。纯“呜/啊/欸？”也不选。
脱离漫画上下文仍能对日常留言作有趣反应；通常一两句、约5至25字最好，但不是硬字数限制。平淡寒暄、流水账、长篇解释、技术分析和需要前后文才能看懂的片段一般不选。宁缺毋滥，不凑数量。
4. 从整页所有人物中找，不能只修旧候选。不要提取封面标题、目录、广告、图表注释或纯拟声词。无合适片段返回空列表。
5. 先定位人物面部或身体范围，再定位所选完整气泡，bbox是这两者的并集外扩少量边距。必须包含表情可见的脸或有表现力的动作，只有文字气泡的窄框不合格。不能为了省范围把脸切掉，人物与气泡分隔较远时扩大框或放弃。
框包含目标台词的完整气泡和表达情绪的脸／动作，尽量贴近用户示例的独立分镜。气泡越界也要完整保留。
可以从同格选一个能独立理解的完整气泡，不强行并入该格所有长对白；绝不从气泡内删字改意，也不拼接不同格。不要只裁文字，也不要为了保全画面输出整页或大片无关长文。
6. 不混入其他气泡导致回复意义改变；同一片段只输出一次。角色名字不确定可写“不确定”，不影响入选。
坐标为原页归一化0–1000 [x1,y1,x2,y2]，不是像素。台词忠实保留繁体及标点，不补写不可见内容。
仅输出 JSON {"candidates":[{"bbox":[0,0,1,1],"character_bbox":[0,0,1,1],"bubble_bbox":[0,0,1,1],"complete":true,"chat_score":5,"quote":"所选范围内完整目标台词","character":"可辨说话人或不确定","reason":"这句的情绪、冲击力与可独立使用的理由","scene":"一两类适合接话的场景","issues":[]}]}。
character_bbox定位表情人物，bubble_bbox包围目标句全部气泡，bbox必须覆盖二者。complete只有完整意思才能为true，chat_score是独立聊天回复价值1到5，普通询问/技术说明/半句为1或2；只保留4或5分。宁可整页零条。
没有素材返回 {"candidates":[]}。'''
VERIFY = '''你是严格选材审核员。复核标记的待处理原页与每条候选的实际裁剪图。这些候选可能是错误的，不能默认接纳。
看实际裁剪图：字是否完整，是否有表现力的人脸/动作，是否确有短句娱乐性；仅能充当普通对话不能打4分。纠正气泡与人物坐标，并只保留complete=true且chat_score>=4的素材。
优先检查：是否真为本页文字（不能把示例文字复制进来）；短句冲击力与独立聊天回复价值；框是否完整包含选中的气泡、情绪人物且没有无关长文；是否坐标越界、重复或跨格乱拼。
所有人物都允许，不按久留美身份筛掉。长篇台词若存在完整且可独立接话的短气泡，可以重新定位到该气泡及对应人物，不能删改气泡文字。
逐条严格淘汰：纯叫声，数字价格，技术名词，平淡问答，未说完的条件句（如“只要有持续入金的能力”）、接头语（如“听起来”）；不可为了保留候选强行编造情绪。必须检查bbox确有可见脸/动作而非只框文字。原台词若跨气泡续句，完整包含所有续句；完成后太长或缺乏冲击力就删掉，禁止截半句。
剔除普通叙事和技术说明，纠正文字与坐标，必要时补充遗漏的精彩短句。别为了补候选而扩大成整页。
返回与初步完全相同的 candidates JSON 结构，只有原图确实存在的素材才能输出。
初步候选：\n'''
RUN_LOCK = threading.Lock()


def normalize(candidates):
    if not isinstance(candidates, list) or len(candidates) > 30:
        raise ValueError('素材列表无效')
    items = []
    for candidate in candidates:
        box, quote = candidate.get('bbox'), candidate.get('quote')
        if candidate.get('complete') is not True or not isinstance(candidate.get('chat_score'), (int,float)) or candidate['chat_score'] < 4:
            continue
        if not store.valid_box(box) or not isinstance(quote, str) or not quote.strip() or len(quote) > 3000:
            raise ValueError('台词或归一化裁剪坐标无效')
        components = [box, candidate.get('character_bbox'), candidate.get('bubble_bbox')]
        if not all(store.valid_box(b) for b in components):
            raise ValueError('缺少完整气泡或人物坐标')
        box = [max(0,min(b[0] for b in components)-20),max(0,min(b[1] for b in components)-20),
               min(1000,max(b[2] for b in components)+20),min(1000,max(b[3] for b in components)+20)]
        # Drop duplicate predictions of the same phrase and nearly identical crop.
        if any(''.join(i['quote'].split()) == ''.join(quote.split()) and all(abs(a-b) < 35 for a,b in zip(i['bbox'],box)) for i in items):
            continue
        reason, scene = str(candidate.get('reason', '')), str(candidate.get('scene', ''))
        issues = candidate.get('issues', [])
        if not isinstance(issues, list):
            raise ValueError('问题列表无效')
        notes = '；'.join([f'选材理由：{reason}', f'建议接话：{scene}'] + [str(i) for i in issues])
        items.append({'id': f'reply-{len(items)+1:02d}', 'bbox': box, 'quote': quote,
                      'speaker': 'uncertain', 'notes': notes, 'character': str(candidate.get('character','不确定')),
                      'selection_policy': POLICY})
    return items


def references():
    examples = store.read(PROFILE / 'examples.json')
    if not examples:
        raise ValueError('缺少1～3卷人工选材示例')
    blocks = []
    # Mix short sharp lines with manually added non-Kurumi crops. No stale OCR text is supplied.
    for index in (0, 7, 9):
        row = examples[index]
        blocks += [{'type':'text','text':f'用户已保留的选材示例{index+1}（仅供风格与裁剪参考，不是待提取页）'},
                   image_block(store.HERE / row['example_path'])]
    return blocks


def predict(page):
    from base64 import b64encode
    data = store.source_bytes(page)
    with Image.open(io.BytesIO(data)) as image:
        mime, size = Image.MIME[image.format], image.size
        grid=image.convert('RGB')
    draw=ImageDraw.Draw(grid);font=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',max(18,size[0]//55))
    for value in range(0,1001,100):
        x,y=round(value*size[0]/1000),round(value*size[1]/1000)
        draw.line((x,0,x,size[1]),fill=(0,170,210),width=2);draw.line((0,y,size[0],y),fill=(0,170,210),width=2)
        for tx,ty in ((min(x,size[0]-55),2),(min(x,size[0]-55),size[1]-30),(2,min(y,size[1]-30)),(size[0]-55,min(y,size[1]-30))):
            draw.text((tx,ty),str(value),fill=(0,70,120),font=font,stroke_width=2,stroke_fill='white')
    grid_data=io.BytesIO();grid.save(grid_data,format='PNG')
    blocks = references() + [{'type':'text','text':f'待处理原页：第{page["volume"]}卷 {page["id"]}。只提取下面这张图。'},
        {'type':'image_url','image_url':{'url':f'data:{mime};base64,'+b64encode(data).decode(),'detail':'high'}},
        {'type':'text','text':'同一待处理原页的坐标辅助图：青色网格间隔100，顶部/底部为x，左/右为y，直接按网格确定框边界。标签和青线不属于漫画。原图用于识字，网格图用于坐标。'},
        {'type':'image_url','image_url':{'url':'data:image/png;base64,'+b64encode(grid_data.getvalue()).decode(),'detail':'high'}}]
    result = {'page_id':page['id'],'model':MODEL,'selection_policy':POLICY,'status':'running','phases':[]}
    with client() as connection:
        phase = request(connection, blocks, LOCATE + f'\n待处理原图{size[0]}×{size[1]}像素，输出坐标仍为0–1000归一化值。')
        result['phases'].append(phase)
        if 'parsed' not in phase[-1]:
            result['status'] = 'failed'
            return result
        # Review actual proposed crops without preference examples distracting from the target.
        checks = blocks[-4:]
        with Image.open(io.BytesIO(data)) as original:
            for index, candidate in enumerate(phase[-1]['parsed']['candidates'],1):
                box = candidate.get('bbox')
                if not store.valid_box(box):continue
                components=[box]+[candidate[k] for k in ('character_bbox','bubble_bbox') if store.valid_box(candidate.get(k))]
                union=[min(b[0] for b in components),min(b[1] for b in components),max(b[2] for b in components),max(b[3] for b in components)]
                pixels=[round(v*size[i%2]/1000) for i,v in enumerate(union)]
                buffer=io.BytesIO();original.crop(pixels).save(buffer,format='PNG')
                checks += [{'type':'text','text':f'候选{index}的实际裁剪图（检查完整性和表情，不是另一页；坐标仍以原页为准）'},
                    {'type':'image_url','image_url':{'url':'data:image/png;base64,'+b64encode(buffer.getvalue()).decode(),'detail':'high'}}]
        verified = request(connection, checks, VERIFY + json.dumps(phase[-1]['parsed'],ensure_ascii=False), validator=normalize)
        result['phases'].append(verified)
        if 'parsed' in verified[-1]:
            items = normalize(verified[-1]['parsed']['candidates'])
            if items:
                items, audit = audit_crops(connection, data, items)
                result['phases'].append(audit)
                result['crop_audit'] = 'blind-v1'
            result['items'] = items
            result['status'] = 'ok'
        else:
            result['status'] = 'failed'
    return result


def audit_crops(connection, data, items):
    from base64 import b64encode
    blocks=[]
    with Image.open(io.BytesIO(data)) as original:
        for index,item in enumerate(items):
            pixels=[round(v*original.size[n%2]/1000) for n,v in enumerate(item['bbox'])]
            buffer=io.BytesIO();original.crop(pixels).save(buffer,format='PNG')
            # No suggested text: it could bias the model into inventing missing words.
            blocks += [{'type':'text','text':f'独立素材 index={index}'},
                {'type':'image_url','image_url':{'url':'data:image/png;base64,'+b64encode(buffer.getvalue()).decode(),'detail':'high'}}]
    def validate(candidates):
        if len(candidates)!=len(items) or {d.get('index') for d in candidates}!=set(range(len(items))):
            raise ValueError('最终裁剪检查缺失或重复')
        for decision in candidates:
            if type(decision.get('index')) is not int or type(decision.get('keep')) is not bool or not isinstance(decision.get('visible_quote'),str):
                raise ValueError('最终裁剪检查格式无效')
            if decision['keep'] and (not decision['visible_quote'].strip() or len(decision['visible_quote'])>3000):
                raise ValueError('最终裁剪缺少台词')
    attempts=request(connection,blocks,'''只看这些实际裁剪图进行严格选材，你看不到原页，也没有预设答案。
先忠实逐字识别图中实际可见的繁体原字及标点，不转换简体，不补写被裁掉的字；只选有明确完整意思的目标对白/内心独白，按阅读顺序写visible_quote。
keep=true必须同时满足：目标句完整未切字，含有表现力的人脸/动作，可以成为简短有趣独立聊天回复。纯数字、技术名词、平淡问答、半句、只有字没有人物、单独叫声均false。不因字少就认定有趣；“可现在看来”“但还是恳求你”“要是出这个角色的周边的话”是没说完的半句，不能独立接话。“我是天才吗”可选，但若周围不可避免包含大段普通说明则降低价值。气泡被图片边界切掉或人脸明显不完整时也false。
不能把不同图片文字混合，不提供坐标，只判断实际图。所有角色允许。
输出JSON {"candidates":[{"index":0,"keep":true,"visible_quote":"图中实际看见的完整目标句","reason":"检查结论"}]}。每张图恰好一项。''',validator=validate)
    if 'parsed' not in attempts[-1]:
        raise ValueError('最终裁剪检查失败')
    kept=[]
    for decision in attempts[-1]['parsed']['candidates']:
        if decision['keep']:
            item={**items[decision['index']]};item['quote']=decision['visible_quote'].strip()
            item['notes']+='；无预设台词裁剪检查：'+str(decision.get('reason',''))
            kept.append(item)
    return kept,attempts


def run_page(page):
    destination = PROFILE / 'pages' / f"{page['id']}.json"
    if FATAL.is_set():
        return 'stopped'
    previous = store.read(destination, {})
    if previous.get('status') == 'ok':
        return 'skipped'
    # Saved human drafts as well as completed reviews are kept; the new proposal remains separate.
    result = {'page_id':page['id'],'selection_policy':POLICY,'status':'running'}
    store.write(destination, result)
    try:
        result = predict(page)
    except Exception as error:
        result.update(status='failed',error_type=type(error).__name__)
    store.write(destination, result)
    if result['status'] == 'ok':
        with store.LOCK:
            if not (store.CORRECTIONS / 'pages' / f"{page['id']}.json").exists():
                old = store.read(store.AUTO / 'pages' / f"{page['id']}.json")
                if old and not (PROFILE / 'previous' / f"{page['id']}.json").exists():
                    store.write(PROFILE / 'previous' / f"{page['id']}.json", old)
                store.write(store.AUTO / 'pages' / f"{page['id']}.json", result)
    print(json.dumps({'page':page['id'],'status':result['status'],'items':len(result.get('items',[]))},ensure_ascii=False),flush=True)
    return result['status']


def run(pages, workers=2):
    with RUN_LOCK:
        FATAL.clear()
        selected = [p for p in pages if p['volume'] in (4,5,6)]
        job = {'status':'running','total':len(selected),'completed':0,'failed':0,'started_at':datetime.now(timezone.utc).isoformat()}
        store.write(PROFILE/'job.json',job)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for status in pool.map(run_page,selected):
                if status in ('ok','skipped'):job['completed'] += 1
                elif status == 'failed':job['failed'] += 1
                store.write(PROFILE/'job.json',job)
        job.update(status='stopped' if FATAL.is_set() else 'finished',finished_at=datetime.now(timezone.utc).isoformat())
        store.write(PROFILE/'job.json',job)


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--workers',type=int,choices=range(1,9),default=2)
    parser.add_argument('--pages',nargs='*',help='Optional page IDs for the initial smoke check')
    args=parser.parse_args()
    pages=store.catalogue()
    if args.pages:pages=[p for p in pages if p['id'] in args.pages]
    run(pages,args.workers)
