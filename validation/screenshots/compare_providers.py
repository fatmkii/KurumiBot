"""Isolated, paired vision trial on ten saved human-reviewed crops.

Does not call save_correction or modify the workbench's material records.
"""
import argparse
import base64
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from opencc import OpenCC

import correction_store as store
import enrichment
from model_client import MODEL, client

TRIAL = store.HERE / 'artifacts/provider-comparison/2026-10-04'
CODEX_MODEL = 'gpt-6.1-sol'
SCHEMAS = {
    'ocr': {'quote': {'type': 'string'}, 'uncertain': {'type': 'boolean'}},
    'metadata': {'emotion_tags': {'type': 'array', 'items': {'type': 'string'}},
                 'meaning': {'type': 'string'}, 'scenarios': {'type': 'string'}},
}


def codex_request(image_path, prompt, stage, folder, model=CODEX_MODEL):
    schema = {'type': 'object', 'properties': SCHEMAS[stage],
              'required': list(SCHEMAS[stage]), 'additionalProperties': False}
    store.write(folder / f'{stage}-schema.json', schema)
    environment = dict(os.environ)
    # Ensure a saved ChatGPT login, rather than inherited API credentials, is used.
    for key in ('OPENAI_API_KEY', 'CODEX_API_KEY', 'CODEX_ACCESS_TOKEN'):
        environment.pop(key, None)
    scratch = Path('/tmp/kurumi-codex-vision')
    scratch.mkdir(exist_ok=True)
    command = ['codex', 'exec', '--ignore-user-config', '--ephemeral', '--json',
               '--skip-git-repo-check', '--sandbox', 'read-only', '-C', str(scratch),
               '--model', model, '-c', 'model_reasoning_effort="medium"',
               '-c', 'web_search="disabled"', '--image', str(image_path),
               '--output-schema', str(folder / f'{stage}-schema.json'), '-']
    for feature in ('shell_tool', 'multi_agent', 'apps', 'plugins', 'browser_use',
                    'computer_use', 'image_generation', 'view_image'):
        command += ['--disable', feature]
    run = subprocess.run(command, input=prompt + '\n只分析附带图片，按指定JSON结构回答；不访问文件、不运行命令或其他工具。',
                         text=True, capture_output=True, timeout=240, env=environment)
    (folder / f'{stage}-events.jsonl').write_text(run.stdout)
    (folder / f'{stage}-stderr.txt').write_text(run.stderr)
    events = [json.loads(line) for line in run.stdout.splitlines() if line.startswith('{')]
    usage = next((e.get('usage', {}) for e in reversed(events) if e.get('type') == 'turn.completed'), {})
    messages = [e['item']['text'] for e in events if e.get('type') == 'item.completed'
                and e.get('item', {}).get('type') == 'agent_message']
    if run.returncode or not messages:
        raise RuntimeError(f'Codex exit={run.returncode}; inspect isolated event log')
    if any(e.get('item', {}).get('type') in ('command_execution', 'mcp_tool_call', 'web_search') for e in events):
        raise RuntimeError('Unexpected tool use in image-only trial')
    return json.loads(messages[-1]), usage


def deepseek_request(image_path, prompt, stage, folder):
    block = {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,'
             + base64.b64encode(image_path.read_bytes()).decode(), 'detail': 'high'}}
    with client() as connection:
        response = connection.post('/chat/completions', json={
            'model': MODEL, 'thinking': {'type': 'disabled'},
            'response_format': {'type': 'json_object'}, 'max_tokens': 2500,
            'messages': [{'role': 'user', 'content': [{'type': 'text', 'text': prompt}, block]}]})
    response.raise_for_status()
    data = response.json()
    store.write(folder / f'{stage}-response.json', data)
    return json.loads(data['choices'][0]['message']['content']), data.get('usage', {})


def run_sample(sample, provider, codex_model=CODEX_MODEL):
    name = f'codex-{codex_model}' if provider == 'codex' and codex_model != CODEX_MODEL else provider
    folder = TRIAL / name / f"{sample['sample']:02d}"
    folder.mkdir(parents=True, exist_ok=True)
    output_path = folder / 'result.json'
    previous = store.read(output_path, {})
    if previous.get('status') == 'ok':
        return
    if previous:
        history = folder / 'attempts'
        history.mkdir(exist_ok=True)
        number = len(list(history.glob('*.json'))) + 1
        store.write(history / f'{number:02d}.json', previous)
        raw = folder / 'ocr-response.json'
        if raw.exists():
            store.write(history / f'{number:02d}-ocr-response.json', store.read(raw))
    result = {'sample': sample['sample'], 'page_id': sample['page']['id'],
              'item_id': sample['item']['id'], 'provider': provider,
              'model': codex_model if provider == 'codex' else MODEL,
              'started_at': datetime.now(timezone.utc).isoformat(), 'calls': [], 'status': 'running'}
    image_path = store.HERE / sample['image']
    image = image_path.read_bytes()
    stage = 'ocr'

    def request(_image, prompt, validate):
        (folder / f'{stage}-prompt.txt').write_text(prompt)
        begin = time.perf_counter()
        try:
            if provider == 'codex':
                data, usage = codex_request(image_path, prompt, stage, folder, model=codex_model)
            else:
                data, usage = deepseek_request(image_path, prompt, stage, folder)
        except Exception as error:
            # Preserve usage even when a successful API response contained invalid JSON.
            raw = folder / f'{stage}-response.json'
            usage = store.read(raw, {}).get('usage', {}) if provider == 'deepseek' else {}
            result['calls'].append({'stage': stage, 'seconds': round(time.perf_counter() - begin, 3),
                                    'usage': usage, 'error_type': type(error).__name__})
            raise
        result['calls'].append({'stage': stage, 'seconds': round(time.perf_counter() - begin, 3),
                                'usage': usage, 'data': data})
        store.write(output_path, result)
        validate(data)
        return data

    try:
        with patch.object(enrichment, 'request', side_effect=request):
            ocr = enrichment.ocr(image)
            result.update(quote=ocr['quote'], uncertain=ocr['uncertain'],
                          quote_simplified=OpenCC('t2s').convert(ocr['quote']))
            stage = 'metadata'
            # Trial generates descriptions even for uncertain OCR, to compare all fields.
            # Production still requires OCR review before that step.
            result.update(enrichment.describe(image, ocr['quote'], sample['page']['volume']))
        result['status'] = 'ok'
    except Exception as error:
        result.update(status='failed', error_type=type(error).__name__)
    result['finished_at'] = datetime.now(timezone.utc).isoformat()
    store.write(output_path, result)
    print(json.dumps({'provider': provider, 'sample': sample['sample'], 'status': result['status'],
                      'seconds': round(sum(c['seconds'] for c in result['calls']), 3)}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--provider', required=True, choices=('codex', 'deepseek'))
    parser.add_argument('--codex-model', choices=(CODEX_MODEL, 'gpt-5.6-luna'), default=CODEX_MODEL)
    parser.add_argument('--samples', type=int, nargs='*', help='Limit to manifest sample numbers')
    args = parser.parse_args()
    if args.provider == 'codex':
        login = subprocess.run(['codex', 'login', 'status'], text=True, capture_output=True)
        if 'Logged in using ChatGPT' not in login.stdout + login.stderr:
            raise SystemExit('Codex must already be logged in using ChatGPT; no API fallback')
    for sample in store.read(TRIAL / 'manifest.json'):
        if not args.samples or sample['sample'] in args.samples:
            run_sample(sample, args.provider, args.codex_model)
