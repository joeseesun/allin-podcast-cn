#!/usr/bin/env python3
"""英文文稿获取，按来源优先级逐级兜底，来源记入 tools/transcript_sources.json：

  ot      官网 allin.com 发布的官方 Transcript（podcastai / Deepgram 转写，含说话人分轨）
  yt      YouTube 人工字幕
  ytauto  YouTube 自动字幕
  asr     本站下载官方音频，用本地 mlx-whisper 转写

输出统一为「说话人 (HH:MM:SS): 正文」逐段文本（字幕与 ASR 没有说话人，只有时间戳），
写到 tools/official/{slug}.txt（ot）或 site/transcripts/{slug}.txt（其余）。两处都不入库。

官方文稿的说话人只有编号；官网 API 给出每位说话人的发言总时长，与分轨统计的时长一一对应，
据此把编号还原成真名（Jason Calacanis / Chamath Palihapitiya / David Sacks / David Friedberg / 嘉宾）。

用法：python3 tools/fetch_transcripts.py [--only=slug] [--no-asr] [--limit=N]
断点续跑：已有文稿的期跳过。
"""
import json
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SITE = ROOT.parent / 'site'
OT = ROOT / 'official'
TR = SITE / 'transcripts'
SRC_FILE = ROOT / 'transcript_sources.json'
UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/126 Safari/537.36'
TMP = Path(tempfile.gettempdir()) / 'allinsite'
_lock = threading.Lock()


def log(*a):
    print(*a, flush=True)


def ts(sec):
    sec = int(sec)
    return f'{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}'


def load_sources():
    return json.loads(SRC_FILE.read_text()) if SRC_FILE.exists() else {}


def set_source(slug, tag):
    with _lock:
        s = load_sources()
        s[slug] = tag
        SRC_FILE.write_text(json.dumps(s, ensure_ascii=False, indent=0, sort_keys=True))


def speaker_names(paras, speakers):
    """按发言总时长把 Deepgram 的说话人编号对到官网给出的真名。"""
    dur = {}
    for p in paras:
        dur[p['speaker']] = dur.get(p['speaker'], 0) + p['end'] - p['start']
    names, left = {}, [s for s in speakers if s.get('dur')]
    for idx, d in sorted(dur.items(), key=lambda t: -t[1]):
        if not left:
            break
        best = min(left, key=lambda s: abs(s['dur'] - d))
        if abs(best['dur'] - d) <= max(5, d * 0.03):
            names[idx] = best['name'] or None
            left.remove(best)
    return names


def official(e):
    raw = json.loads(urllib.request.urlopen(urllib.request.Request(e['transcriptURL'], headers={'User-Agent': UA}),
                                            timeout=120).read())
    alt = raw['results']['channels'][0]['alternatives'][0]
    paras = (alt.get('paragraphs') or {}).get('paragraphs') or []
    if not paras:
        return None
    names = speaker_names(paras, e.get('speakers') or [])
    out, cur, buf, t0 = [], None, [], 0
    for p in paras:
        sp = p['speaker']
        text = ' '.join(s['text'] for s in p.get('sentences', []))
        if sp != cur or sum(len(b) for b in buf) > 1800:   # 同一人连续发言合并成段，过长再切
            if buf:
                out.append((cur, t0, ' '.join(buf)))
            cur, buf, t0 = sp, [], p['start']
        buf.append(text)
    if buf:
        out.append((cur, t0, ' '.join(buf)))
    lines = []
    for sp, t, text in out:
        who = names.get(sp) or f'Speaker {sp + 1}'
        lines.append(f'{who} ({ts(t)}): {text}')
    return '\n\n'.join(lines) + '\n'


def vtt_to_text(vtt):
    """YouTube 字幕（含滚动重复的自动字幕）→ 每约 30 秒一段、带时间戳的正文。"""
    cues = []
    for block in re.split(r'\n\n+', vtt):
        m = re.search(r'(\d{2}):(\d{2}):(\d{2})\.\d{3} --> ', block)
        if not m:
            continue
        start = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
        body = block[block.index('\n', m.end()) + 1:] if '\n' in block[m.end():] else ''
        body = re.sub(r'<[^>]+>', '', body)
        for ln in body.splitlines():
            ln = ln.strip()
            if ln:
                cues.append((start, ln))
    words, last = [], ''
    seen_lines = []
    for start, ln in cues:   # 自动字幕每条会重复上一行，去掉与最近几行完全相同的
        if ln in seen_lines[-3:]:
            continue
        seen_lines.append(ln)
        words.append((start, ln))
    paras, cur, t0 = [], [], None
    for start, ln in words:
        if t0 is None:
            t0 = start
        cur.append(ln)
        if start - t0 >= 30 and re.search(r'[.?!]$', ln) or start - t0 >= 60:
            paras.append((t0, ' '.join(cur)))
            cur, t0 = [], None
    if cur:
        paras.append((t0, ' '.join(cur)))
    text = '\n\n'.join(f'({ts(t)}) {re.sub(r"\s+", " ", p).replace("&gt;&gt;", "—").strip()}' for t, p in paras)
    text = text.replace('&amp;', '&').replace('&gt;', '>').replace('&lt;', '<').replace('&#39;', "'")
    return text + '\n'


def youtube(e):
    if not e.get('ytId'):
        return None, None
    d = TMP / 'subs' / e['slug']
    d.mkdir(parents=True, exist_ok=True)
    url = f'https://www.youtube.com/watch?v={e["ytId"]}'
    for tag, flag in (('yt', '--write-subs'), ('ytauto', '--write-auto-subs')):
        for f in d.glob('*.vtt'):
            f.unlink()
        subprocess.run(['yt-dlp', '--skip-download', flag, '--sub-langs', 'en,en-US,en-orig' if tag == 'ytauto' else 'en,en-US,en-GB',
                        '--sub-format', 'vtt', '-o', str(d / '%(id)s.%(ext)s'), url],
                       capture_output=True, text=True, timeout=300)
        vtts = sorted(d.glob('*.vtt'), key=lambda p: ('orig' in p.name, p.name))
        if vtts:
            text = vtt_to_text(vtts[0].read_text(errors='replace'))
            if len(text) > 2000:
                return tag, text
    return None, None


def asr(e):
    if not e.get('audio'):
        return None
    TMP.mkdir(parents=True, exist_ok=True)
    mp3 = TMP / f'{e["slug"]}.mp3'
    log(f'  下载音频 {e["slug"]} …')
    data = urllib.request.urlopen(urllib.request.Request(e['audio'], headers={'User-Agent': UA}), timeout=900).read()
    mp3.write_bytes(data)
    out = TMP / 'asr'
    # 单期超时：turbo 在 Apple Silicon 上约 20 倍实时，给到时长的一半、至少 15 分钟；
    # 关掉 condition-on-previous-text，避免某些音频陷入重复幻听的死循环（2026-10-20 曾卡死 12 小时）
    limit = max(900, int((e.get('duration') or 3600) * 0.5))
    try:
        subprocess.run([shutil.which('mlx_whisper') or 'mlx_whisper', str(mp3),
                        '--model', 'mlx-community/whisper-large-v3-turbo', '--language', 'en',
                        '--condition-on-previous-text', 'False',
                        '--output-dir', str(out), '--output-format', 'json'],
                       check=True, capture_output=True, timeout=limit)
    finally:
        mp3.unlink(missing_ok=True)
    segs = json.loads((out / f'{e["slug"]}.json').read_text())['segments']
    paras, cur, t0 = [], [], None
    for s in segs:
        if t0 is None:
            t0 = s['start']
        cur.append(s['text'].strip())
        if s['end'] - t0 >= 45:
            paras.append((t0, ' '.join(cur)))
            cur, t0 = [], None
    if cur:
        paras.append((t0, ' '.join(cur)))
    return '\n\n'.join(f'({ts(t)}) {p}' for t, p in paras) + '\n'


FAILED_FILE = ROOT / 'asr_failed.json'
ASR_MAX_FAILS = 2


def asr_failed():
    return json.loads(FAILED_FILE.read_text()) if FAILED_FILE.exists() else {}


def mark_asr_failed(slug, why):
    with _lock:
        f = asr_failed()
        f[slug] = {'n': f.get(slug, {}).get('n', 0) + 1, 'why': why[:200]}
        FAILED_FILE.write_text(json.dumps(f, ensure_ascii=False, indent=0, sort_keys=True))


def header(e, tag):
    src = {'ot': 'Official transcript published on allin.com (speaker-diarized).',
           'yt': 'YouTube captions (uploaded by the channel).',
           'ytauto': 'YouTube automatic captions. May contain recognition errors.',
           'asr': 'Machine transcription by Whisper large-v3-turbo (MLX, local). May contain recognition errors.'}[tag]
    num = f'E{e["num"]}: ' if e.get('num') is not None else ''
    return f'All-In Podcast {num}{e["title"]}\n{src}\n\n'


def have(slug):
    return (OT / f'{slug}.txt').exists() or (TR / f'{slug}.txt').exists()


def work(e, do_asr):
    slug = e['slug']
    try:
        if e.get('transcriptURL'):
            text = official(e)
            if text:
                OT.mkdir(exist_ok=True)
                (OT / f'{slug}.txt').write_text(header(e, 'ot') + text)
                set_source(slug, 'ot')
                return slug, 'ot', len(text)
        tag, text = youtube(e)
        if text:
            TR.mkdir(parents=True, exist_ok=True)
            (TR / f'{slug}.txt').write_text(header(e, tag) + text)
            set_source(slug, tag)
            return slug, tag, len(text)
        if do_asr:
            try:
                text = asr(e)
            except Exception as ex:
                mark_asr_failed(slug, f'{type(ex).__name__}: {ex}')
                raise
            if text:
                TR.mkdir(parents=True, exist_ok=True)
                (TR / f'{slug}.txt').write_text(header(e, 'asr') + text)
                set_source(slug, 'asr')
                return slug, 'asr', len(text)
        return slug, None, 0
    except Exception as ex:
        return slug, f'ERR {type(ex).__name__}: {str(ex)[:120]}', 0


def main():
    """--only=a,b 只处理指定期；--max-minutes=N 到点不再开始新的 ASR；--no-asr 只拉官方文稿与字幕；
    --retry-failed 连续失败过 2 次的 ASR 也重试。"""
    sys.path.insert(0, str(ROOT))
    import pipeline
    if not pipeline.acquire_lock('fetch_transcripts.py ' + ' '.join(sys.argv[1:])):
        return 0
    arg = lambda k: next((a.split('=', 1)[1] for a in sys.argv if a.startswith(f'--{k}=')), None)
    only = {x for x in (arg('only') or '').split(',') if x}
    limit = int(arg('limit') or 0) or None
    deadline = time.time() + float(arg('max-minutes')) * 60 if arg('max-minutes') else None
    do_asr = '--no-asr' not in sys.argv
    failed = {} if '--retry-failed' in sys.argv else asr_failed()
    master = json.load(open(ROOT / 'allin-master.json'))
    todo = [e for e in master if (e['slug'] in only if only else not have(e['slug']))][:limit]
    log(f'todo: {len(todo)}')
    # 官方文稿与字幕并发拉取；ASR 吃 GPU，单独串行
    net = [e for e in todo if e.get('transcriptURL') or e.get('ytId')]
    rest = []
    with ThreadPoolExecutor(6) as ex:
        for slug, tag, n in ex.map(lambda e: work(e, False), net):
            log(f'{slug}: {tag} {n}')
            if not tag or tag.startswith('ERR'):
                rest.append(next(e for e in todo if e['slug'] == slug))
    rest += [e for e in todo if not (e.get('transcriptURL') or e.get('ytId'))]
    for e in rest:
        if not do_asr or have(e['slug']):
            continue
        if failed.get(e['slug'], {}).get('n', 0) >= ASR_MAX_FAILS:
            log(f'{e["slug"]}: ASR 已失败 {failed[e["slug"]]["n"]} 次，跳过（--retry-failed 可重试）')
            continue
        need = max(900, int((e.get('duration') or 3600) * 0.5))
        if deadline and time.time() + min(need, 1200) > deadline:
            log(f'时间预算用完，剩余 ASR 留给下次：{e["slug"]} 等')
            break
        slug, tag, n = work(dict(e, transcriptURL=None, ytId=None), True)
        log(f'{slug}: {tag} {n}')
    src = load_sources()
    from collections import Counter
    log('sources:', dict(Counter(src.values())), f'missing: {sum(1 for e in master if not have(e["slug"]))}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
