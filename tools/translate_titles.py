#!/usr/bin/env python3
"""批量翻译 All-In 期标题：Magpie 订阅路由（trae→opencode→workbuddy 轮换，不走官方 deepseek 路由）。
输出 tools/translations.json（slug → {zh, guest, desc}），增量保存，断点续跑。"""
import json, re, subprocess, sys, time, urllib.error, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / 'translations.json'
BATCH = 12

API = 'https://magpie.qiaomu.ai/v1/chat/completions'
MODELS = ['trae-cn/deepseek-v4.1-flash', 'opencode-go/deepseek-v4.1-flash', 'workbuddy/deepseek-v4.1-flash']
master = json.load(open(ROOT / 'allin-master.json'))
done = json.load(open(OUT)) if OUT.exists() else {}
todo = [e for e in master if e['slug'] not in done]
print(f'total {len(master)}, done {len(done)}, todo {len(todo)}', flush=True)
if not todo:
    sys.exit(0)
sys.path.insert(0, str(ROOT))
import pipeline  # noqa: E402
KEY = pipeline.magpie_key()
if not KEY:
    print('缺少 Magpie API key（环境变量 MAGPIE_API_KEY 或 macOS Keychain），跳过标题翻译', flush=True)
    sys.exit(2)

PROMPT = """你是资深财经科技播客的中文编辑。下面是 All-In 播客若干期的英文标题（JSON 数组，项含 slug/num/title/desc/chapters）。
All-In 由杰森·卡拉卡尼斯、查马斯·帕里哈皮蒂亚、大卫·萨克斯、大卫·弗里德伯格四位投资人主持：周播按期号编号，一期聊多个话题；另有不编号的嘉宾访谈和峰会对谈。
为每一项生成三个字段：
- zh：标题的简体中文翻译，信、达、雅，像中文财经媒体的标题。人名用通行中文译名（Elon Musk→埃隆·马斯克，Jensen Huang→黄仁勋，较冷门人物保留英文原名亦可），公司名用中文圈通行叫法（Anthropic、OpenAI、Nvidia→英伟达），去掉「E123:」和「| All-In Summit 2024」这类前后缀。
- guest：卡片上的短主标题（6-20 字）。周播概括本期最主要的一两个话题，如「CoreWeave 暴跌与 AI 交易裂痕」；嘉宾访谈写「嘉宾名：核心话题」，如「黄仁勋：超级智能已经到来」。
- desc：一句话中文简介（30-60 字），说清这期聊了哪几件事或嘉宾的核心观点；不要「本期」「节目中」这类字眼，不要营销腔和感叹号。
只输出 JSON 对象：{"items":[{"slug":"...","zh":"...","guest":"...","desc":"..."}]}，不要输出其他任何文字。"""


def call(items, attempt=1):
    model = MODELS[(attempt - 1) % len(MODELS)]
    payload = {
        'model': model,
        'messages': [{'role': 'user', 'content': PROMPT + '\n\n输入：\n' + json.dumps(items, ensure_ascii=False)}],
        'max_tokens': 6000,
        'temperature': 0.3,
    }
    try:
        return _post({**payload, 'response_format': {'type': 'json_object'}})
    except urllib.error.HTTPError as e:
        if e.code == 400:  # 该路由不认 response_format，去掉重试
            return _post(payload)
        raise


def _post(body_obj):
    body = json.dumps(body_obj).encode()
    req = urllib.request.Request(API, data=body, headers={
        'Authorization': f'Bearer {KEY}', 'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=180) as r:
        resp = json.load(r)
    content = resp['choices'][0]['message']['content']
    data = json.loads(content)
    got = {}
    for it in data.get('items', []):
        slug = it.get('slug')
        if slug and it.get('zh') and it.get('guest') and it.get('desc'):
            got[slug] = {'zh': it['zh'].strip(), 'guest': it['guest'].strip(), 'desc': it['desc'].strip()}
    return got


import threading
from concurrent.futures import ThreadPoolExecutor
_lock = threading.Lock()


def run_batch(bi):
    chunk = todo[bi:bi + BATCH]
    items = [{'slug': e['slug'], 'num': e.get('num'), 'title': e['title'], 'desc': (e.get('desc') or '')[:300],
              'chapters': [c[1] for c in e.get('chapters') or []][:8]} for e in chunk]
    for attempt in range(1, 4):
        try:
            got = call(items, attempt + bi // BATCH)   # 批次错开起始模型，重试再换下一家
            if got:
                with _lock:
                    done.update(got)
                    OUT.write_text(json.dumps(done, ensure_ascii=False, indent=0))
                print(f'batch {bi//BATCH+1}: +{len(got)}/{len(chunk)} (total {len(done)})', flush=True)
                return
            raise ValueError('no items parsed')
        except Exception as e:
            print(f'batch {bi//BATCH+1} attempt {attempt} failed: {type(e).__name__} {e}', flush=True)
            time.sleep(3 * attempt)
    print('  STILL MISSING:', [e['slug'] for e in chunk if e['slug'] not in done], flush=True)


with ThreadPoolExecutor(6) as ex:
    list(ex.map(run_batch, range(0, len(todo), BATCH)))

miss = [e['slug'] for e in master if e['slug'] not in done]
print(f'FINISHED total={len(done)} missing={len(miss)} {miss[:10]}')
