#!/usr/bin/env python3
"""把官方 show notes 的章节标题翻成中文，写入 translations.json 各期的 chap 字段（与 chapters 一一对应）。
增量、断点续跑；与 translate_titles.py 共用 translations.json，二者不要同时运行。"""
import json
import subprocess
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / 'translations.json'
API = 'https://magpie.qiaomu.ai/v1/chat/completions'
MODELS = ['workbuddy/deepseek-v4.1-flash', 'opencode-go/deepseek-v4.1-flash', 'trae-cn/deepseek-v4.1-flash']
KEY = subprocess.run(['security', 'find-generic-password', '-s', 'qiaomu-llm', '-a', 'magpie', '-w'],
                     capture_output=True, text=True).stdout.strip()
BATCH = 10
PROMPT = """把下面 All-In 播客各期的章节标题翻成简体中文（JSON，键为 slug，值为章节标题数组）。
要求：像中文财经杂志的小标题，简洁准确；人名用通行中文译名（Chamath→查马斯，Sacks→萨克斯，Friedberg→弗里德伯格，Jason→杰森，Jensen Huang→黄仁勋），公司名用通行叫法；
「Bestie intros」译作「开场寒暄」，「Intro」译作「开场」；保留数字、金额与百分比；数组长度和顺序必须与输入一致。
只输出 JSON 对象：{"items": {"slug": ["…", "…"]}}，不要输出其他文字。"""
_lock = threading.Lock()


def post(body):
    req = urllib.request.Request(API, data=json.dumps(body).encode(),
                                 headers={'Authorization': f'Bearer {KEY}', 'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=240) as r:
        return json.loads(json.load(r)['choices'][0]['message']['content'])


def run(args):
    k, batch, done = args
    items = {e['slug']: [c[1] for c in e['chapters']] for e in batch}
    for attempt in range(3):
        model = MODELS[(k + attempt) % len(MODELS)]
        try:
            body = {'model': model, 'temperature': 0.2, 'max_tokens': 6000,
                    'messages': [{'role': 'user', 'content': PROMPT + '\n\n' + json.dumps(items, ensure_ascii=False)}]}
            try:
                got = post({**body, 'response_format': {'type': 'json_object'}})
            except urllib.error.HTTPError as e:
                if e.code != 400:
                    raise
                got = post(body)
            got = got.get('items', got)
            n = 0
            with _lock:
                for slug, arr in got.items():
                    if slug in items and isinstance(arr, list) and len(arr) == len(items[slug]):
                        done.setdefault(slug, {})['chap'] = [str(x).strip() for x in arr]
                        n += 1
                OUT.write_text(json.dumps(done, ensure_ascii=False, indent=0))
            print(f'batch {k}: {n}/{len(items)} [{model}]', flush=True)
            if n:
                return
        except Exception as e:
            print(f'batch {k} attempt {attempt} failed: {type(e).__name__} {str(e)[:120]}', flush=True)
            time.sleep(3)


def main():
    master = json.load(open(ROOT / 'allin-master.json'))
    done = json.load(open(OUT)) if OUT.exists() else {}
    todo = [e for e in master if len(e.get('chapters') or []) >= 2 and not (done.get(e['slug']) or {}).get('chap')]
    print(f'todo {len(todo)}', flush=True)
    jobs = [(k, todo[i:i + BATCH], done) for k, i in enumerate(range(0, len(todo), BATCH))]
    with ThreadPoolExecutor(6) as ex:
        list(ex.map(run, jobs))
    print('missing', sum(1 for e in master if len(e.get('chapters') or []) >= 2 and not (done.get(e['slug']) or {}).get('chap')))


if __name__ == '__main__':
    main()
