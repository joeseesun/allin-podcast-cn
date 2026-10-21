"""每日追新（sync_new.py）与历史积压（backlog.py）共用的基础设施。

- 全站一把锁（fcntl.flock，进程退出自动释放）：同一时刻只允许一个会写 site/ 与 tools/*.json 的流程在跑；
  子进程（gen_zh / fetch_transcripts）由持锁的父进程拉起时通过环境变量 ALLIN_LOCK_HELD=1 跳过取锁
- launchd 等非交互环境：PATH 很精简，这里补齐 Homebrew 等常用目录，外部命令一律解析成绝对路径
- 构建部署、提交推送：推送失败只记日志，不算整体失败
"""
import fcntl
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
REPO = TOOLS.parent
SITE = REPO / 'site'
LOG_DIR = Path.home() / 'Library' / 'Logs' / 'allin-site'
LOCK_FILE = LOG_DIR / 'allin.lock'
REMOTE = 'qiaomu-hostinger:/www/wwwroot/allin.qiaomu.ai/'
GIT_BRANCH = 'main'

# launchd 的 PATH 只有 /usr/bin:/bin:/usr/sbin:/sbin，补上 Homebrew（yt-dlp、mlx_whisper、gh）等目录
_EXTRA_PATH = ['/opt/homebrew/bin', '/opt/homebrew/sbin', '/usr/local/bin', '/usr/bin', '/bin', '/usr/sbin', '/sbin']
os.environ['PATH'] = ':'.join(dict.fromkeys(os.environ.get('PATH', '').split(':') + _EXTRA_PATH).keys()).strip(':')
os.environ.setdefault('HOME', str(Path.home()))
os.environ.setdefault('LANG', 'en_US.UTF-8')
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')


def which(cmd):
    """外部命令的绝对路径；找不到时原样返回，让调用处报出清楚的错误。"""
    return shutil.which(cmd) or cmd


PY = sys.executable


def log(msg):
    print(f'[{datetime.now().strftime("%H:%M:%S")}] {msg}', flush=True)


_lock_fd = None


def acquire_lock(who):
    """非阻塞取锁。拿到返回 True；被占用返回 False（调用方应以返回码 0 退出）。"""
    global _lock_fd
    if os.environ.get('ALLIN_LOCK_HELD') == '1':
        return True
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    fd = open(LOCK_FILE, 'a+')
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fd.seek(0)
        holder = fd.read().strip() or '未知进程'
        fd.close()
        log(f'锁被占用（{holder}），{who} 本次跳过')
        return False
    fd.seek(0)
    fd.truncate()
    fd.write(f'{who} pid={os.getpid()} since={datetime.now().isoformat(timespec="seconds")}')
    fd.flush()
    _lock_fd = fd
    os.environ['ALLIN_LOCK_HELD'] = '1'   # 子进程继承：不再重复取锁
    return True


class Deadline:
    def __init__(self, minutes):
        self.end = time.time() + minutes * 60

    def left(self):
        return max(0, self.end - time.time())

    def minutes_left(self):
        return self.left() / 60

    def over(self, reserve=0):
        return self.left() <= reserve


def run_step(args, timeout, name):
    """跑一个子步骤，超时即终止该子进程（含其子进程）。返回 (ok, timed_out)。"""
    if timeout <= 0:
        log(f'{name}：时间预算已用完，跳过')
        return False, True
    log(f'→ {name}（上限 {timeout / 60:.0f} 分钟）')
    p = subprocess.Popen(args, cwd=REPO, start_new_session=True)
    try:
        rc = p.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        log(f'{name} 超时，终止（剩余工作留给 backlog）')
        try:
            os.killpg(p.pid, 15)
            p.wait(timeout=20)
        except Exception:
            os.killpg(p.pid, 9)
        return False, True
    if rc != 0:
        log(f'{name} 返回码 {rc}')
    return rc == 0, False


def build_and_deploy():
    """全量构建 + rsync 部署。失败抛异常（属于整体失败）。"""
    log('构建站点 …')
    r = subprocess.run([PY, str(TOOLS / 'build.py')], cwd=REPO, capture_output=True, text=True, timeout=1800)
    if r.returncode != 0:
        raise RuntimeError(f'build.py 失败：{(r.stderr or r.stdout)[-800:]}')
    log('rsync 部署 …')
    r = subprocess.run([which('rsync'), '-az', '--delete', '--exclude', '.well-known',
                        '-e', f'{which("ssh")} -o BatchMode=yes -o ConnectTimeout=20',
                        'site/', REMOTE], cwd=REPO, capture_output=True, text=True, timeout=1800)
    if r.returncode != 0:
        raise RuntimeError(f'rsync 失败：{(r.stderr or r.stdout)[-800:]}')
    log('部署完成')


PUBLISH_PATHS = ['site/zh', 'site/data/zh-index.json', 'site/index.html',
                 'tools/allin-master.json', 'tools/translations.json',
                 'tools/transcript_sources.json', 'tools/og-faces.json', 'tools/asr_failed.json']


def git_publish(message):
    """提交新精读与元数据并推送 main。任何一步失败只记日志、返回 False，不影响整体返回码。"""
    git = which('git')

    def g(*a, timeout=120):
        return subprocess.run([git, *a], cwd=REPO, capture_output=True, text=True, timeout=timeout)
    try:
        br = g('rev-parse', '--abbrev-ref', 'HEAD').stdout.strip()
        if br != GIT_BRANCH:
            log(f'git：当前分支是 {br} 而非 {GIT_BRANCH}，跳过提交推送')
            return False
        paths = [p for p in PUBLISH_PATHS if (REPO / p).exists()]
        g('add', '--', *paths)
        if g('diff', '--cached', '--quiet').returncode == 0:
            log('git：没有需要提交的变更')
        else:
            r = g('-c', 'user.name=joeseesun', '-c', 'user.email=joe@32kw.com', 'commit', '-q', '-m',
                  message + '\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>')
            if r.returncode != 0:
                log(f'git commit 失败：{(r.stderr or r.stdout)[-400:]}')
                return False
            log(f'git：已提交 {g("rev-parse", "--short", "HEAD").stdout.strip()}')
        ahead = g('rev-list', '--count', f'origin/{GIT_BRANCH}..HEAD').stdout.strip()
        if ahead in ('', '0'):
            return True
        env = dict(os.environ, GIT_TERMINAL_PROMPT='0', GIT_SSH_COMMAND=f'{which("ssh")} -o BatchMode=yes')
        r = subprocess.run([git, 'push', 'origin', GIT_BRANCH], cwd=REPO, capture_output=True, text=True,
                           timeout=180, env=env)
        if r.returncode != 0:
            log(f'git push 失败（不影响本次结果，下次运行会补推）：{(r.stderr or r.stdout)[-600:]}')
            return False
        log(f'git：已推送 {ahead} 个提交到 origin/{GIT_BRANCH}')
        return True
    except Exception as e:
        log(f'git 提交推送异常（不影响本次结果）：{type(e).__name__} {e}')
        return False
