#!/usr/bin/env python3
"""单集元数据：官方 RSS（libsyn）+ 官网 allin.com 后台（podcastai API）+ YouTube 频道，合并为 tools/allin-master.json。

三路来源各有所长：
- RSS：全部播客单集（含嘉宾访谈、峰会场次），音频直链、时长、带时间戳的章节（show notes）
- podcastai API：官网收录的单集，期号、YouTube ID、官方 Transcript（Deepgram 转写，含说话人）
- YouTube 频道：给 API 没收录的 RSS 单集补视频 ID（标题相似 + 时长接近）

匹配规则：期号（RSS 标题 E123 / 音频文件名 ALLIN-E123）> 标题相似且日期相近 > 时长兜底。
可重复执行；已有条目的 slug 保持不变（slug 一旦发布就是网址）。

用法：python3 tools/fetch_meta.py [--no-yt]
"""
import difflib
import html as H
import json
import re
import shutil
import subprocess
import sys
import urllib.request
from datetime import date
from email.utils import parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MASTER = ROOT / 'allin-master.json'
RSS_URL = 'https://rss.libsyn.com/shows/254861/destinations/1928300.xml'
API = 'https://app.podcastai.com/api/v1/portal/shows/allin/episodes'
UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/126 Safari/537.36'
HOSTS = {'Jason Calacanis', 'Chamath Palihapitiya', 'David Sacks', 'David Friedberg'}


def get(url, timeout=60):
    return urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': UA}), timeout=timeout).read()


def hms(s):
    p = [int(x) for x in s.split(':')]
    return sum(v * 60 ** i for i, v in enumerate(reversed(p)))


def parse_rss():
    xml = get(RSS_URL).decode('utf-8', 'ignore')
    out = []
    for it in re.findall(r'<item>(.*?)</item>', xml, re.S):
        def g(t):
            m = re.search(rf'<{t}[^>]*>(.*?)</{t}>', it, re.S)
            return re.sub(r'^<!\[CDATA\[|\]\]>$', '', m.group(1).strip()) if m else ''
        title = H.unescape(g('title')).strip()
        enc = re.search(r'<enclosure[^>]*url="([^"]+)"', it)
        enc = H.unescape(enc.group(1)) if enc else None
        desc = H.unescape(g('description'))
        chapters = []
        for t, txt in re.findall(r'\((\d{1,2}:\d{2}(?::\d{2})?)\)\s*([^<]+)', desc):
            txt = H.unescape(txt).strip()
            if txt:
                chapters.append([hms(t), txt])
        num = None
        m = re.match(r'^E(\d+)\b', title)
        if m:
            num = int(m.group(1))
        elif enc:
            m = re.search(r'ALLIN-E(\d+)', enc)
            if m:
                num = int(m.group(1))
        dur = g('itunes:duration')
        link = g('link')
        out.append({'title': title, 'date': parsedate_to_datetime(g('pubDate')).date().isoformat(),
                    'audio': enc.split('?')[0] if enc else None, 'num': num,
                    'duration': hms(dur) if re.match(r'^[\d:]+$', dur or '') else None,
                    'chapters': chapters, 'link': link if link.startswith('http') else None,
                    'guid': g('guid')})
    return out


def parse_api():
    out, off = [], 0
    while True:
        d = json.loads(get(f'{API}?offset={off}&limit=100'))['data']
        out += d
        off += len(d)
        if len(d) < 100:
            return out


def norm(s):
    s = re.sub(r'^(e\d+:?|#ais:)\s*', '', (s or '').lower())
    s = s.replace('’', "'").replace('‘', "'")
    return re.sub(r'[^a-z0-9 ]', '', s).split()


def sim(a, b):
    a, b = norm(a), norm(b)
    if not a or not b:
        return 0
    seq = difflib.SequenceMatcher(None, ' '.join(a), ' '.join(b)).ratio()
    jac = len(set(a) & set(b)) / len(set(a) | set(b))
    return max(seq, jac * 1.1)


def days(a, b):
    return abs((date.fromisoformat(a) - date.fromisoformat(b)).days)


def slugify(title, num):
    if num is not None:
        return f'e{num}'
    t = re.sub(r'\s*\|.*$', '', title)                       # 去掉「| All-In Summit 2024」之类后缀
    t = t.lower().replace('’', '').replace("'", '').replace('&', ' and ')
    words = re.sub(r'[^a-z0-9]+', ' ', t).split()
    stop = {'the', 'a', 'an', 'of', 'on', 'and', 'with', 'in', 'to', 'for', 'is', 'at', 'from', 'how', 'why', 'what'}
    keep = [w for w in words if w not in stop][:7] or words[:7]
    return '-'.join(keep)[:64].strip('-') or 'episode'


def yt_list():
    vids = []
    for tab in ('videos', 'streams'):
        try:
            r = subprocess.run([shutil.which('yt-dlp') or '/opt/homebrew/bin/yt-dlp', '--flat-playlist', '-J',
                                f'https://www.youtube.com/@allin/{tab}'],
                               capture_output=True, text=True, timeout=240)
            vids += [{'id': e['id'], 'title': e.get('title') or '', 'duration': e.get('duration')}
                     for e in json.loads(r.stdout).get('entries', [])]
        except Exception as e:
            print('yt list failed', tab, e)
    return vids


def main():
    rss = parse_rss()
    api = parse_api()
    print(f'RSS {len(rss)} items, API {len(api)} episodes')
    by_num = {e['episodeNumber']: e for e in api if e.get('episodeNumber') is not None and e['type'] == 'full'}
    used = set()

    def match_api(r):
        if r['num'] is not None and r['num'] in by_num and by_num[r['num']]['handle'] not in used:
            return by_num[r['num']]
        best, bs = None, 0
        for e in api:
            if e['handle'] in used:
                continue
            dd = days(r['date'], e['publishedAt'][:10])
            gap = abs(r['duration'] - e['duration']) if r['duration'] and e.get('duration') else 9999
            if dd > 6 and not (gap <= 5 and dd <= 60):   # 峰会场次常先上视频、晚几周进播客，时长一致即同一期
                continue
            s = sim(r['title'], e['title'])
            if gap <= 5:
                s += 0.4
            elif gap < 90:
                s += 0.25
            if s > bs:
                best, bs = e, s
        return best if bs >= 0.5 else None

    eps = []
    for r in rss:
        a = match_api(r)
        if a:
            used.add(a['handle'])
        eps.append((r, a))
    for a in api:   # 官网有、RSS 没有（少见：仅视频的访谈）
        if a['handle'] not in used:
            eps.append((None, a))
    print(f'matched {sum(1 for r, a in eps if r and a)}, rss-only {sum(1 for r, a in eps if not a)}, api-only {sum(1 for r, a in eps if not r)}')

    yt = [] if '--no-yt' in sys.argv else yt_list()
    yt_used = {a['ytVideoHandle'] for r, a in eps if a and a.get('ytVideoHandle')}

    old = {}
    if MASTER.exists():
        for e in json.load(open(MASTER)):
            old[e.get('guid') or e.get('pai')] = e

    master, slugs = [], set()
    for r, a in eps:
        num = (a or {}).get('episodeNumber')
        if num is None and r and r['num'] is not None:
            num = r['num']
        if a and a['type'] == 'bonus':
            num = None
        title = (a or {}).get('title') or r['title']
        title = re.sub(r'\s+', ' ', title).strip()
        d = (r or {}).get('date') or a['publishedAt'][:10]
        yid = (a or {}).get('ytVideoHandle')
        if not yid and yt and r:
            best, bs = None, 0
            for v in yt:
                if v['id'] in yt_used:
                    continue
                s = sim(r['title'], v['title'])
                if r['duration'] and v['duration'] and abs(r['duration'] - v['duration']) < 120:
                    s += 0.3
                if s > bs:
                    best, bs = v, s
            if best and bs >= 0.62:
                yid = best['id']
                yt_used.add(yid)
        speakers = [{'name': s['name'], 'dur': s.get('duration'), 'host': bool(s.get('host'))}
                    for s in (a or {}).get('speakers') or []]
        key = (r or {}).get('guid') or (a or {}).get('handle')
        prev = old.get(key) or old.get((a or {}).get('handle')) or {}
        slug = prev.get('slug') or slugify(title, num)
        base, k = slug, 2
        while slug in slugs:
            slug = f'{base}-{k}'
            k += 1
        slugs.add(slug)
        master.append({
            'num': num, 'title': title, 'rssTitle': (r or {}).get('title'), 'date': d, 'slug': slug,
            'type': 'weekly' if num is not None else 'interview',
            'ytId': yid, 'audio': (r or {}).get('audio'),
            'duration': (r or {}).get('duration') or (a or {}).get('duration'),
            'desc': (a or {}).get('description'), 'chapters': (r or {}).get('chapters') or [],
            'speakers': speakers,
            'guests': [s['name'] for s in speakers if s['name'] not in HOSTS and not s['host']],
            'pai': (a or {}).get('handle'), 'transcriptURL': (a or {}).get('transcriptURL'),
            'libsyn': (r or {}).get('link'), 'guid': (r or {}).get('guid'),
            'apple': (a or {}).get('appleEpisodeID'), 'spotify': (a or {}).get('spotifyEpisodeID'),
        })
    master.sort(key=lambda e: (e['date'], e['num'] or 0), reverse=True)
    MASTER.write_text(json.dumps(master, ensure_ascii=False, indent=1) + '\n')
    print(f'master: {len(master)} eps, num={sum(1 for e in master if e["num"] is not None)}, '
          f'yt={sum(1 for e in master if e["ytId"])}, audio={sum(1 for e in master if e["audio"])}, '
          f'official_tr={sum(1 for e in master if e["transcriptURL"])}')


if __name__ == '__main__':
    main()
