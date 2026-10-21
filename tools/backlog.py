#!/usr/bin/env python3
"""历史积压：补 ASR 文稿与中文精读，跑满时间预算后构建、部署、提交推送。和每日追新共用一把锁。

适合在夜间或空闲时由调度器调用；拿不到锁（每日追新正在跑）直接退出，返回码 0。
连续失败 2 次的 ASR 会被跳过（记录在 tools/asr_failed.json），加 --retry-failed 可强制重试。

返回码：0 = 成功（含锁被占用、积压已清空）；1 = 构建或部署失败
日志：与 sync_new 同目录（macOS ~/Library/Logs/allin-site/，Linux /var/log/allin-site/），backlog-YYYYMMDD.log

用法：<python> tools/backlog.py [--max-minutes=240] [--no-asr] [--retry-failed] [--no-push]
"""
import sys
import traceback
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pipeline as P  # noqa: E402
from sync_new import Tee, arg  # noqa: E402


def zh_count():
    return len(list((P.SITE / 'zh').glob('*.html')))


def main():
    P.LOG_DIR.mkdir(parents=True, exist_ok=True)
    logf = open(P.LOG_DIR / f'backlog-{datetime.now():%Y%m%d}.log', 'a')
    sys.stdout = Tee(sys.__stdout__, logf)
    sys.stderr = Tee(sys.__stderr__, logf)
    budget = float(arg('max-minutes', 240))
    dl = P.Deadline(budget)
    P.log(f'== backlog start（预算 {budget:.0f} 分钟）==')
    if not P.acquire_lock('backlog.py'):
        P.log('== backlog 退出：另一个流程正在运行（返回码 0）==')
        return 0
    zh0 = zh_count()
    tr = [P.PY, str(P.TOOLS / 'fetch_transcripts.py'), f'--max-minutes={max(1, dl.minutes_left() * 0.5):.0f}']
    tr += [a for a in ('--no-asr', '--retry-failed') if a in sys.argv]
    P.run_step(tr, dl.left() * 0.55, '积压文稿（官方 > 字幕 > ASR）')
    P.run_step([P.PY, str(P.TOOLS / 'gen_zh.py'), '--no-deploy', f'--max-minutes={max(1, dl.minutes_left() - 10):.0f}'],
               dl.left() - 300, '积压精读')
    added = zh_count() - zh0
    P.log(f'本次新增精读 {added} 篇')
    if added:
        try:
            P.build_and_deploy()
        except Exception as e:
            P.log(f'== backlog 失败：{e} ==')
            return 1
    if '--no-push' not in sys.argv:
        P.git_publish(f'积压补齐：新增 {added} 篇精读')
    P.log(f'== backlog 完成，用时 {budget - dl.minutes_left():.1f} 分钟 ==')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        sys.exit(1)
