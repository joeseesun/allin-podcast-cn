#!/usr/bin/env python3
"""每日追新：只处理新期，默认 45 分钟内结束；历史积压（ASR、精读）交给 tools/backlog.py。

流程：
1. 取全站锁（与 backlog / gen_zh / fetch_transcripts 互斥）；拿不到锁直接退出，返回码 0
2. fetch_meta.py：RSS + 官网 API + YouTube 合并，找出新期；ASR / 字幕期若官网补发了官方文稿，也算「本次要处理」
3. 只为这些期补标题、章节翻译，拉文稿（官方 > 字幕 > ASR），生成中文精读
4. 有变化就构建 + rsync 部署；最后提交新精读与元数据并推送 main（推送失败不算失败）

返回码：0 = 成功（含没有新期、锁被占用、部分工作因时间预算顺延）；1 = 失败（元数据抓取、构建或部署出错）
日志：~/Library/Logs/allin-site/sync-YYYYMMDD.log（同时输出到 stdout）

用法：/opt/homebrew/bin/python3 /Users/joe/Documents/ChatGPT/allin/tools/sync_new.py [--max-minutes=45] [--no-asr] [--no-push]
"""
import json
import sys
import traceback
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pipeline as P  # noqa: E402  （导入即补齐 launchd 环境的 PATH）


class Tee:
    def __init__(self, *s):
        self.s = s

    def write(self, x):
        for f in self.s:
            f.write(x)
            f.flush()

    def flush(self):
        for f in self.s:
            f.flush()


def arg(k, default=None):
    return next((a.split('=', 1)[1] for a in sys.argv if a.startswith(f'--{k}=')), default)


def upgrade_to_official(master):
    """之前只能用字幕 / ASR 的期，官网补发了官方文稿：删掉旧文稿与精读，让本次按官方版重做。"""
    src_p = P.TOOLS / 'transcript_sources.json'
    src = json.loads(src_p.read_text()) if src_p.exists() else {}
    up = [e['slug'] for e in master if e.get('transcriptURL') and src.get(e['slug']) in ('asr', 'yt', 'ytauto')]
    for slug in up:
        (P.SITE / 'transcripts' / f'{slug}.txt').unlink(missing_ok=True)
        zh = P.SITE / 'zh' / f'{slug}.html'
        if zh.exists():
            zh.rename(zh.with_suffix('.html.pre-official'))
    if up:
        P.log(f'官网补发官方文稿，改用官方版并重写精读：{up}')
    return up


def main():
    P.LOG_DIR.mkdir(parents=True, exist_ok=True)
    logf = open(P.LOG_DIR / f'sync-{datetime.now():%Y%m%d}.log', 'a')
    sys.stdout = Tee(sys.__stdout__, logf)
    sys.stderr = Tee(sys.__stderr__, logf)
    budget = float(arg('max-minutes', 45))
    dl = P.Deadline(budget)
    P.log(f'== sync_new start（预算 {budget:.0f} 分钟）==')
    if not P.acquire_lock('sync_new.py'):
        P.log('== sync_new 退出：另一个流程正在运行（返回码 0）==')
        return 0

    mp = P.TOOLS / 'allin-master.json'
    before = {e['slug'] for e in json.loads(mp.read_text())} if mp.exists() else set()
    ok, _ = P.run_step([P.PY, str(P.TOOLS / 'fetch_meta.py')], min(dl.left() - 900, 900), 'fetch_meta 元数据合并')
    if not ok:
        P.log('== sync_new 失败：元数据抓取出错 ==')
        return 1
    master = json.loads(mp.read_text())
    new = [e['slug'] for e in master if e['slug'] not in before]
    upgraded = upgrade_to_official(master)
    targets = list(dict.fromkeys(new + upgraded))
    P.log(f'新期 {len(new)}：{new or "无"}')

    changed = bool(targets)
    if targets:
        P.run_step([P.PY, str(P.TOOLS / 'translate_titles.py')], min(dl.left() - 600, 600), '标题翻译（只补缺）')
        P.run_step([P.PY, str(P.TOOLS / 'translate_chapters.py')], min(dl.left() - 600, 600), '章节翻译（只补缺）')
        tr_args = [P.PY, str(P.TOOLS / 'fetch_transcripts.py'), f'--only={",".join(targets)}',
                   f'--max-minutes={max(1, dl.minutes_left() - 15):.0f}']
        if '--no-asr' in sys.argv:
            tr_args.append('--no-asr')
        P.run_step(tr_args, dl.left() - 600, '文稿（官方 > 字幕 > ASR）')
        P.run_step([P.PY, str(P.TOOLS / 'gen_zh.py'), f'--only={",".join(targets)}', '--no-deploy',
                    f'--max-minutes={max(1, dl.minutes_left() - 6):.0f}'],
                   dl.left() - 300, '中文精读（仅本次的期）')
        missing = [s for s in targets if not (P.SITE / 'zh' / f'{s}.html').exists()]
        if missing:
            P.log(f'未完成精读（顺延给 backlog）：{missing}')

    # 首次运行或上次部署失败时也要能补上：有变化或站点产物缺失就部署
    if changed or not (P.SITE / 'ep').exists():
        try:
            P.build_and_deploy()
        except Exception as e:
            P.log(f'== sync_new 失败：{e} ==')
            return 1

    if '--no-push' not in sys.argv:
        n_zh = sum(1 for s in targets if (P.SITE / 'zh' / f'{s}.html').exists())
        P.git_publish(f'每日同步：新增 {len(new)} 期 / {n_zh} 篇精读')
    P.log(f'== sync_new 完成：{len(new)} 新期，{len(upgraded)} 期换官方文稿，用时 {budget - dl.minutes_left():.1f} 分钟 ==')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        sys.exit(1)
