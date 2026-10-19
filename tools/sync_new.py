#!/usr/bin/env python3
"""同步新一期：RSS + 官网 API + YouTube → allin-master.json → 标题 / 章节翻译 → 文稿 → 精读管线 → 部署。

流程（gen_zh --watch 负责其后的精读生成与增量部署，本脚本保证它有料可扫）：
1. fetch_meta.py：重新合并三路元数据（已有 slug 不变），新期自动入库
2. translate_titles.py / translate_chapters.py：只补缺
3. fetch_transcripts.py：官网官方 Transcript > YouTube 字幕 > 本地 mlx-whisper 转写，只补缺
   官网文稿常在节目上线一两天后才发布：ASR 过的期若后来出现官方文稿，自动换成官方版并重写精读
4. gen_zh --watch 未在跑则拉起；有变更时立即 build + rsync 一次

用法：python3 tools/sync_new.py [--no-asr]
"""
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
SITE = REPO / 'site'
LOG_DIR = Path('/tmp/allinsite')
PY = sys.executable


def log(msg):
    print(msg, flush=True)


def run(script, *args):
    log(f'→ {script} {" ".join(args)}')
    subprocess.run([PY, str(ROOT / script), *args], cwd=REPO)


def upgrade_to_official(master):
    """之前只能用字幕 / ASR 的期，官网补发了官方文稿：换成官方版，并让精读按新文稿重写。"""
    src_p = ROOT / 'transcript_sources.json'
    src = json.loads(src_p.read_text()) if src_p.exists() else {}
    up = [e for e in master if e.get('transcriptURL') and src.get(e['slug']) in ('asr', 'yt', 'ytauto')]
    for e in up:
        (SITE / 'transcripts' / f'{e["slug"]}.txt').unlink(missing_ok=True)
        zh = SITE / 'zh' / f'{e["slug"]}.html'
        if zh.exists():
            zh.rename(zh.with_suffix('.html.pre-official'))
    if up:
        log(f'官方文稿补发，改用官方版: {[e["slug"] for e in up]}')
    return [e['slug'] for e in up]


def ensure_watch():
    r = subprocess.run(['pgrep', '-f', 'gen_zh.py --watch'], capture_output=True, text=True)
    if r.stdout.strip():
        return
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logf = open(LOG_DIR / 'gen_zh.log', 'a')
    subprocess.Popen([PY, '-u', str(ROOT / 'gen_zh.py'), '--watch'], cwd=REPO, stdout=logf, stderr=logf,
                     start_new_session=True)
    log(f'gen_zh --watch 已拉起（日志 {LOG_DIR / "gen_zh.log"}）')


def deploy():
    subprocess.run([PY, 'tools/build.py'], cwd=REPO, check=True, capture_output=True)
    subprocess.run(['rsync', '-az', '--delete', '--exclude', '.well-known',
                    'site/', 'qiaomu-hostinger:/www/wwwroot/allin.qiaomu.ai/'],
                   cwd=REPO, check=True, capture_output=True)
    log('build + rsync 完成')


def main():
    log(f'== sync_new start {datetime.now().isoformat(timespec="seconds")} ==')
    mp = ROOT / 'allin-master.json'
    before = {e['slug'] for e in json.load(open(mp))} if mp.exists() else set()
    run('fetch_meta.py')
    master = json.load(open(mp))
    new = [e['slug'] for e in master if e['slug'] not in before]
    log(f'新期: {new or "无"}')
    upgraded = upgrade_to_official(master)
    run('translate_titles.py')
    run('translate_chapters.py')
    run('fetch_transcripts.py', *(['--no-asr'] if '--no-asr' in sys.argv else []))
    ensure_watch()
    if new or upgraded:
        deploy()
    log(f'== sync done: {len(new)} new, {len(upgraded)} upgraded ==')


if __name__ == '__main__':
    main()
