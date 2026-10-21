#!/usr/bin/env python3
"""每日追新：只处理新期，默认 45 分钟内结束；历史积压（ASR、精读）交给 tools/backlog.py。

流程：
1. 取全站锁（与 backlog / gen_zh / fetch_transcripts 互斥）；拿不到锁直接退出，返回码 0
2. fetch_meta.py：RSS + 官网 API + YouTube 合并，找出新期；ASR / 字幕期若官网补发了官方文稿，也算「本次要处理」
3. 只为这些期补标题、章节翻译，拉文稿（官方 > 字幕 > ASR），生成中文精读
4. 有变化就构建 + rsync 部署；最后提交新精读与元数据并推送 main（推送失败不算失败）

返回码：0 = 成功（含没有新期、锁被占用、部分工作因时间预算顺延）；1 = 失败（元数据抓取、构建或部署出错）
日志：macOS ~/Library/Logs/allin-site/，Linux /var/log/allin-site/（ALLIN_LOG_DIR 可覆盖），文件名 sync-YYYYMMDD.log

环境变量（都可缺省）：MAGPIE_API_KEY、ASR_ENGINE=doubao + DOUBAO_ASR_APP_KEY / DOUBAO_ASR_ACCESS_KEY + PODCAST_OPS、
ALLIN_DEPLOY_TARGET（本地目录或 rsync 远端）、QMREADER_URL（部署后提示刷新 allin-zh 源）

用法：<python> tools/sync_new.py [--max-minutes=45] [--no-asr] [--no-push] [--dry-run]
  --dry-run 演练：抓元数据到临时文件、环境检查、构建到临时副本、rsync --dry-run；不改仓库、不调模型、不部署不推送
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import traceback
import urllib.request
from datetime import datetime
from pathlib import Path

if '--dry-run' in sys.argv:
    os.environ['ALLIN_DRY_RUN'] = '1'   # 须在导入 pipeline 之前设置
sys.path.insert(0, str(Path(__file__).resolve().parent))
import pipeline as P  # noqa: E402  （导入即补齐 launchd / systemd 环境的 PATH）


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


def preflight():
    """不需要凭据也能做的环境检查。返回 (致命问题, 提醒)。"""
    fatal, warn = [], []
    for mod in ('requests', 'PIL'):
        if importlib.util.find_spec(mod) is None:
            fatal.append(f'缺 Python 包 {mod}（装进 {P.PY} 所在的环境）')
    for cmd in ('rsync', 'git'):
        if not shutil.which(cmd):
            fatal.append(f'缺命令 {cmd}')
    if not shutil.which('yt-dlp'):
        warn.append('没有 yt-dlp：新期只能靠官网 API 拿 YouTube ID，RSS 独有的期没有视频')
    engine = os.environ.get('ASR_ENGINE', 'whisper').lower()
    if engine == 'doubao':
        ops = os.environ.get('PODCAST_OPS') or str(P.REPO.parent.parent / 'podcast-ops')
        if not (Path(ops) / 'doubao_asr.py').exists():
            fatal.append(f'ASR_ENGINE=doubao 但找不到 {ops}/doubao_asr.py')
        if not shutil.which('ffmpeg'):
            warn.append('没有 ffmpeg：豆包拒收音频 URL 时无法切段上传')
        if not (os.environ.get('DOUBAO_ASR_APP_KEY') and os.environ.get('DOUBAO_ASR_ACCESS_KEY')):
            warn.append('豆包凭据为空：没有官方文稿的新期暂时无法转写')
    elif not shutil.which('mlx_whisper'):
        warn.append('没有 mlx_whisper 且未设 ASR_ENGINE=doubao：没有官方文稿的新期无法转写')
    if not P.magpie_key():
        warn.append('Magpie key 为空：标题 / 章节翻译与中文精读会跳过')
    target = P.deploy_target()
    if not P.is_remote(target):
        t = Path(target)
        if not t.exists():
            warn.append(f'部署目录 {t} 不存在（首次部署时会创建）')
        elif not os.access(t, os.W_OK):
            fatal.append(f'部署目录 {t} 不可写')
    q = os.environ.get('QMREADER_URL', '').strip()
    if q:
        try:
            urllib.request.urlopen(q, timeout=8)
        except urllib.error.HTTPError:
            pass   # 有响应即可达
        except Exception as e:
            warn.append(f'QMReader {q} 不可达：{type(e).__name__}')
    if not os.access(P.REPO / '.git', os.W_OK):
        warn.append('仓库 .git 不可写：无法提交')
    return fatal, warn


def dry_run():
    """演练：抓元数据到临时文件、列出本次会处理的期、环境检查、构建到临时副本、rsync --dry-run。
    不改仓库、不调用 LLM 与转写、不部署、不提交推送。"""
    P.log(f'== sync_new 演练（--dry-run）：部署目标 {P.deploy_target()}，ASR={os.environ.get("ASR_ENGINE", "whisper")} ==')
    fatal, warn = preflight()
    for w in warn:
        P.log(f'提醒：{w}')
    for f in fatal:
        P.log(f'问题：{f}')
    with tempfile.TemporaryDirectory(prefix='allin-dry-') as td:
        td = Path(td)
        ok, _ = P.run_step([P.PY, str(P.TOOLS / 'fetch_meta.py'), f'--out={td / "master.json"}'], 900, 'fetch_meta（写到临时文件）')
        if not ok:
            P.log('== 演练失败：元数据抓取出错 ==')
            return 1
        old = json.loads((P.TOOLS / 'allin-master.json').read_text())
        new_m = json.loads((td / 'master.json').read_text())
        before = {e['slug'] for e in old}
        new = [e['slug'] for e in new_m if e['slug'] not in before]
        src = json.loads((P.TOOLS / 'transcript_sources.json').read_text())
        up = [e['slug'] for e in new_m if e.get('transcriptURL') and src.get(e['slug']) in ('asr', 'yt', 'ytauto')]
        P.log(f'会处理的新期 {len(new)}：{new or "无"}；会换官方文稿的期 {len(up)}：{up or "无"}')
        site = td / 'site'
        P.log('复制站点到临时目录并构建 …')
        subprocess.run([P.which('rsync'), '-a', f'{P.SITE}/', f'{site}/'], check=True)
        env = dict(os.environ, ALLIN_SITE_DIR=str(site))
        r = subprocess.run([P.PY, str(P.TOOLS / 'build.py')], cwd=P.REPO, env=env, capture_output=True, text=True, timeout=1800)
        if r.returncode != 0:
            P.log(f'== 演练失败：build.py 出错 ==\n{(r.stderr or r.stdout)[-1500:]}')
            return 1
        for ln in r.stdout.strip().splitlines():
            P.log(f'  build: {ln}')
        try:
            P.deploy(src=site)
        except Exception as e:
            P.log(f'== 演练失败：{e} ==')
            return 1
    P.git_publish('')
    if fatal:
        P.log('== 演练结束：有环境问题需要处理（返回码 1）==')
        return 1
    P.log('== 演练通过（返回码 0）==')
    return 0


def main():
    P.LOG_DIR.mkdir(parents=True, exist_ok=True)
    logf = open(P.LOG_DIR / f'sync-{datetime.now():%Y%m%d}.log', 'a')
    sys.stdout = Tee(sys.__stdout__, logf)
    sys.stderr = Tee(sys.__stderr__, logf)
    budget = float(arg('max-minutes', 45))
    dl = P.Deadline(budget)
    P.log(f'== sync_new start（预算 {budget:.0f} 分钟）==')
    if not P.acquire_lock('sync_new.py' + (' --dry-run' if P.DRY_RUN else '')):
        P.log('== sync_new 退出：另一个流程正在运行（返回码 0）==')
        return 0
    if P.DRY_RUN:
        return dry_run()

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
