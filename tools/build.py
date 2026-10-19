#!/usr/bin/env python3
"""allin.qiaomu.ai 静态站构建器。

输入：
  tools/allin-master.json        主数据（fetch_meta.py：RSS + 官网 API + YouTube 合并）
  tools/translations.json        LLM 翻译的中文标题/话题/简介（可缺）
  tools/transcript_sources.json  每期文稿来源（ot / yt / ytauto / asr）
  tools/official/{slug}.txt      官网官方 Transcript（含说话人）
  site/transcripts/{slug}.txt    YouTube 字幕 / 本地 ASR 文稿
  site/zh/{slug}.html            中文精读片段
  site/data/zh-index.json        中文精读索引

输出：
  site/ep/{slug}/index.html  每期一个自包含单集页（可被搜索引擎直接抓取）
  site/data/episodes.json    首页数据（含中英双语标题）
  site/sitemap.xml / llms.txt / feed.xml / archive / 404 / offline / og 分享卡片

断点续跑安全：可重复执行；ASR 批次补完后重跑即可覆盖更新。
"""
import html as H
import json
import re
import shutil
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SITE = ROOT.parent / 'site'
DOMAIN = 'https://allin.qiaomu.ai'
SITE_NAME = 'All-In 播客中文版'
UMAMI = '<script defer src="https://umami.qiaomu.ai/script.js" data-website-id="b62cc787-d58e-44c9-892b-4137e31da048" data-domains="allin.qiaomu.ai"></script>'
TODAY = date.today().isoformat()

CSS_V, APP_V, EP_V = 14, 12, 8

# 阅读偏好（主题 / 字体 / 字号 / 行距 / 版心宽度 / 段首缩进）在 <head> 内提前应用，首屏不闪
THEME_SCRIPT = """<script>
(function () {
  var d = document.documentElement, g = function (k) { try { return localStorage.getItem(k); } catch (e) { return null; } };
  var t = g('allin-theme'), f = g('allin-font'), z = g('allin-size'), lh = g('allin-lh'), w = g('allin-measure');
  var BG = { light: '#ffffff', sepia: '#f5eddc', dark: '#141414' };
  if (!BG[t]) t = (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) ? 'dark' : 'light';
  d.setAttribute('data-theme', t);
  var FONTS = {
    sans: 'https://fonts.googleapis.cn/css2?family=Noto+Sans+SC:wght@400;600&display=swap',
    serif: 'https://fonts.googleapis.cn/css2?family=Noto+Serif+SC:wght@400;600&display=swap',
    kai: 'https://cdn.jsdelivr.net/npm/lxgw-wenkai-screen-webfont@1.7.0/lxgwwenkaiscreen.css',
    wei: 'https://fonts.googleapis.cn/css2?family=ZCOOL+XiaoWei&display=swap',
    song: 'https://cdn.jsdelivr.net/npm/@chinese-fonts/jhlst@3.0.0/dist/%E4%BA%AC%E8%8F%AF%E8%80%81%E5%AE%8B%E4%BD%93v2_002/result.css'
  };
  window.__allinFonts = FONTS;
  window.__allinLoadFont = function (k) {
    if (!FONTS[k] || document.getElementById('allinfont-' + k)) return;
    var l = document.createElement('link'); l.rel = 'stylesheet'; l.href = FONTS[k]; l.id = 'allinfont-' + k;
    document.head.appendChild(l);
  };
  if (!f) f = 'serif'; // 默认正文用思源宋体，杂志感阅读；可在阅读设置里切换
  if (FONTS[f]) { d.setAttribute('data-font', f); window.__allinLoadFont(f); }
  if (z && /^(1[5-9]|2[0-2])$/.test(z)) d.style.setProperty('--read-size', z + 'px');
  if (lh && /^(1\\.\\d+|2(\\.\\d+)?)$/.test(lh)) d.style.setProperty('--read-lh', lh);
  if (w && /^\\d{3}$/.test(w)) d.style.setProperty('--measure', w + 'px');
  var meta = document.querySelector('meta[name="theme-color"]');
  function syncMeta() { if (meta) meta.content = BG[t]; }
  syncMeta();
  window.__allinSetTheme = function (n) {
    if (!BG[n]) return;
    t = n;
    try { localStorage.setItem('allin-theme', t); if (t !== 'dark') localStorage.setItem('allin-light', t); } catch (e) {}
    d.classList.add('theme-anim');
    d.setAttribute('data-theme', t);
    syncMeta();
    setTimeout(function () { d.classList.remove('theme-anim'); }, 320);
    document.dispatchEvent(new CustomEvent('allin-theme', { detail: t }));
  };
  window.__allinThemeToggle = function () { window.__allinSetTheme(t === 'dark' ? (g('allin-light') || 'light') : 'dark'); };
  // 全站通用：主题切换 / 打开与关闭弹窗 / 复制 RSS 地址
  document.addEventListener('click', function (e) {
    var el = e.target.closest ? e.target : e.target.parentNode;
    if (el.closest('[data-theme-toggle]')) return window.__allinThemeToggle();
    var o = el.closest('[data-open]');
    if (o) { var m = document.getElementById(o.getAttribute('data-open')); if (m) { m.hidden = false; var c = m.querySelector('.modal-close'); if (c) c.focus(); } if (window.umami) window.umami.track('open-' + o.getAttribute('data-open')); return; }
    var mm = el.closest('.modal');
    if (mm && (el === mm || el.closest('.modal-close'))) { mm.hidden = true; return; }
    var sh = el.closest('[data-share]');
    if (sh) {
      var data = { title: document.title, url: location.href.split('#')[0] };
      if (navigator.share) { navigator.share(data).catch(function () {}); }
      else if (navigator.clipboard) navigator.clipboard.writeText(data.title + ' ' + data.url).then(function () { var t = sh.textContent; sh.textContent = '链接已复制'; setTimeout(function () { sh.textContent = t; }, 1600); });
      return;
    }
    var cf = el.closest('[data-copy-feed]');
    if (cf) {
      var url = location.origin + '/feed.xml', label = cf.textContent;
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(url).then(function () { cf.textContent = '已复制'; setTimeout(function () { cf.textContent = label; }, 1600); }, function () { location.href = '/feed.xml'; });
      } else location.href = '/feed.xml';
    }
  });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') Array.prototype.forEach.call(document.querySelectorAll('.modal'), function (m) { m.hidden = true; });
  });
  // 图片加载完成再淡入（捕获阶段监听，覆盖后续动态插入的卡片）
  document.addEventListener('load', function (e) { var t = e.target; if (t.tagName === 'IMG') t.classList.add('ok'); }, true);
  document.addEventListener('error', function (e) { var t = e.target; if (t.tagName === 'IMG' && !t.getAttribute('onerror')) t.classList.add('ok'); }, true);
  // 安装为应用：Chrome / Edge 用系统安装框；iOS Safari 给出添加到主屏幕的指引
  var deferred = null;
  function showInstall() { Array.prototype.forEach.call(document.querySelectorAll('[data-install]'), function (b) { b.hidden = false; }); }
  window.addEventListener('beforeinstallprompt', function (e) { e.preventDefault(); deferred = e; showInstall(); });
  var standalone = (window.matchMedia && window.matchMedia('(display-mode: standalone)').matches) || navigator.standalone;
  if (/iphone|ipad|ipod/i.test(navigator.userAgent) && !standalone) document.addEventListener('DOMContentLoaded', showInstall);
  document.addEventListener('click', function (e) {
    var b = e.target.closest && e.target.closest('[data-install]');
    if (!b) return;
    if (deferred) { deferred.prompt(); deferred.userChoice.finally(function () { deferred = null; b.hidden = true; }); }
    else { var m = document.getElementById('installModal'); if (m) m.hidden = false; }
  });
  // PWA：可安装为应用；读过的文章离线可读
  if ('serviceWorker' in navigator && location.protocol === 'https:') {
    window.addEventListener('load', function () { navigator.serviceWorker.register('/sw.js').catch(function () {}); });
  }
})();
</script>"""

# 全站共用：标题字体（西文 Libre Caslon + 中文思源宋体，Google Fonts 国内镜像按需切片加载）+ favicon
HEAD_EXTRA = ('<link rel="preconnect" href="https://fonts.googleapis.cn">'
              '<link rel="preconnect" href="https://fonts.gstatic.cn" crossorigin>'
              '<link rel="preconnect" href="https://i.ytimg.com">'
              '<link rel="stylesheet" href="https://fonts.googleapis.cn/css2?family=Libre+Caslon+Text:ital,wght@0,400;0,700;1,400'
              '&family=Noto+Serif+SC:wght@500;700&display=swap">\n'
              '<link rel="icon" href="/favicon.ico" sizes="32x32">'
              '<link rel="icon" href="/icon.svg" type="image/svg+xml">'
              '<link rel="apple-touch-icon" href="/apple-touch-icon.png">'
              '<link rel="manifest" href="/site.webmanifest">\n'
              '<meta name="apple-mobile-web-app-title" content="All-In 中文">'
              '<meta name="apple-mobile-web-app-capable" content="yes">'
              '<meta name="mobile-web-app-capable" content="yes">'
              '<meta name="apple-mobile-web-app-status-bar-style" content="default">'
              '<meta name="format-detection" content="telephone=no">'
              '<noscript><style>.thumb img,.cont-thumb img,.hero-media img{opacity:1}</style></noscript>')

THEME_BTN = ('<button class="theme-toggle" type="button" data-theme-toggle aria-label="切换浅色 / 深色模式" title="切换浅色 / 深色模式">'
             '<svg class="i-sun" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="4"/>'
             '<path d="M12 2.5v2M12 19.5v2M4.6 4.6l1.4 1.4M18 18l1.4 1.4M2.5 12h2M19.5 12h2M4.6 19.4L6 18M18 6l1.4-1.4"/></svg>'
             '<svg class="i-moon" viewBox="0 0 24 24" aria-hidden="true"><path d="M20.5 14.2A8.5 8.5 0 0 1 9.8 3.5a8.5 8.5 0 1 0 10.7 10.7z"/></svg>'
             '</button>')

ICON_SEARCH = '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="M20 20l-3.6-3.6"/></svg>'

# 内页报头：左返回、中站名（衬线字标）、右搜索 + 主题
SITE_HEADER = ('<header class="site-header slim">\n  <div class="wrap header-inner">\n'
               '    <a class="hdr-back" href="/">← 全部单集</a>\n'
               '    <a class="wordmark" href="/" aria-label="All-In 播客中文版 · 首页">All-In <span>播客中文版</span></a>\n'
               f'    <div class="header-actions"><a class="hdr-icon" href="/#search" aria-label="搜索全部单集" title="搜索">{ICON_SEARCH}</a>{THEME_BTN}</div>\n'
               '  </div>\n</header>')

ICON_PLAY = '<svg class="i-play" viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5.5v13a1 1 0 0 0 1.5.86l10.6-6.5a1 1 0 0 0 0-1.72L9.5 4.64A1 1 0 0 0 8 5.5z"/></svg>'
ICON_PAUSE = '<svg class="i-pause" viewBox="0 0 24 24" aria-hidden="true"><rect x="6" y="5" width="4.2" height="14" rx="1.2"/><rect x="13.8" y="5" width="4.2" height="14" rx="1.2"/></svg>'

FEED_LINK = (f'<link rel="alternate" type="application/rss+xml" '
             f'title="All-In 播客中文版 · 中文精读全文" href="{DOMAIN}/feed.xml">')

SITE_FOOTER = """<footer class="site-footer">
  <div class="wrap">
    <div class="ft-top">
      <a class="ft-mark" href="/">All-In <span>播客中文版</span></a>
      <p class="ft-tag">硅谷四位投资人的每周牌桌，科技、市场与政治，用中文认真读一遍。</p>
    </div>
    <div class="ft-cols">
      <nav class="ft-col" aria-label="浏览"><h2>浏览</h2>
        <a href="/">全部单集</a><a href="/?f=zh">中文精读</a><a href="/archive/">单集目录</a><a href="/#search">搜索</a>
        <button type="button" data-install hidden>安装为应用</button></nav>
      <nav class="ft-col" aria-label="订阅"><h2>订阅</h2>
        <a href="/feed.xml">RSS 全文订阅</a><button type="button" data-copy-feed>复制 RSS 地址</button>
        <a href="https://allin.com/episodes" target="_blank" rel="noopener">官方网站</a></nav>
      <nav class="ft-col" aria-label="关于"><h2>关于</h2>
        <a href="/disclaimer/">免责声明</a><a href="https://github.com/joeseesun/allin-podcast-cn" target="_blank" rel="noopener">开源代码</a>
        <a href="/llms.txt">llms.txt</a><a href="https://x.com/vista8" target="_blank" rel="noopener">作者 X</a></nav>
      <div class="ft-col ft-support"><h2>支持这个项目</h2>
        <p>一个人独立制作，没有广告，也不收费。如果这里的某篇精读让你有所收获，欢迎请作者喝杯咖啡，让它一直做下去。</p>
        <div class="ft-actions"><button type="button" class="ft-btn" data-open="rewardModal">打赏支持</button><button type="button" class="ft-btn ghost" data-open="followModal">关注公众号</button></div></div>
    </div>
    <div class="ft-bottom">
      <span>© 2026 向阳乔木 · 非官方粉丝站点，与 All-In 节目及四位主持人无隶属关系</span>
      <span>音视频与原始文稿版权归原权利方所有，本站不托管媒体文件 · 中文精读由 AI 改写，仅供学习参考</span>
    </div>
  </div>
</footer>

<div id="rewardModal" class="modal" hidden role="dialog" aria-modal="true" aria-labelledby="rewardT">
  <div class="modal-card">
    <button type="button" class="modal-close" aria-label="关闭">×</button>
    <h3 id="rewardT">谢谢你的支持</h3>
    <p class="modal-sub">每一份打赏都会变成更多认真校过的精读</p>
    <img src="/assets/qiaomu_reward_qr.png" alt="打赏二维码" width="220" height="220" loading="lazy">
    <p class="modal-links">微信扫一扫</p>
  </div>
</div>
<div id="installModal" class="modal" hidden role="dialog" aria-modal="true" aria-labelledby="installT">
  <div class="modal-card">
    <button type="button" class="modal-close" aria-label="关闭">×</button>
    <h3 id="installT">添加到主屏幕</h3>
    <p class="modal-sub">像应用一样打开，读过的文章离线也能看</p>
    <ol class="install-steps"><li>点 Safari 底部的「分享」按钮 <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3v12M7.5 7.5L12 3l4.5 4.5M5 12v7a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-7"/></svg></li><li>向下滑，选择「添加到主屏幕」</li><li>点右上角「添加」</li></ol>
  </div>
</div>
<div id="followModal" class="modal" hidden role="dialog" aria-modal="true" aria-labelledby="followT">
  <div class="modal-card">
    <button type="button" class="modal-close" aria-label="关闭">×</button>
    <h3 id="followT">关注向阳乔木</h3>
    <p class="modal-sub">微信公众号：向阳乔木推荐看</p>
    <img src="/assets/qiaomu_wechat_public_account_qr.jpg" alt="公众号二维码" width="220" height="220" loading="lazy">
    <p class="modal-links"><a href="https://github.com/joeseesun/" target="_blank" rel="noopener">GitHub</a> · <a href="https://x.com/vista8" target="_blank" rel="noopener">X</a></p>
  </div>
</div>"""

# 文末「致读者」：交代做法与诚意，给出支持入口，不打扰阅读
COLOPHON = ('<aside class="colophon" aria-label="致读者">'
            '<div class="colo-k">致读者</div>'
            '<p>这篇精读来自 allin.qiaomu.ai，一个人独立维护的非营利项目：从官方音频与文稿出发，经转写、AI 改写与逐篇校验，'
            '把四个人一个半小时的交锋，整理成一篇按话题展开、谁说了什么都交代清楚的中文长文。这里没有广告，也不设付费墙。'
            '如果它让你有所收获，欢迎请作者喝杯咖啡。</p>'
            '<div class="colo-actions"><button type="button" class="ft-btn" data-open="rewardModal">打赏支持</button>'
            '<button type="button" class="ft-btn ghost" data-open="followModal">关注公众号</button>'
            '<button type="button" class="colo-link" data-share>分享这篇</button>'
            '<a class="colo-link" href="/feed.xml">RSS 订阅更新</a></div></aside>')

FOOTER_FEED = ('<p class="footer-feed">订阅本站更新：<a href="/feed.xml">RSS 全文订阅</a>'
               '（中文精读全文 + 官方音频） '
               '<button class="copy-feed" type="button" data-copy-feed>复制地址</button></p>')

master = json.load(open(ROOT / 'allin-master.json'))
trans = json.load(open(ROOT / 'translations.json')) if (ROOT / 'translations.json').exists() else {}
_zp = SITE / 'data' / 'zh-index.json'
zh_index = json.load(open(_zp)) if _zp.exists() else {'slugs': []}
ZH = set(zh_index.get('slugs', []))
_sp = ROOT / 'transcript_sources.json'
SOURCES = json.load(open(_sp)) if _sp.exists() else {}
HOSTS = {'Jason Calacanis': '杰森', 'Chamath Palihapitiya': '查马斯', 'David Sacks': '萨克斯', 'David Friedberg': '弗里德伯格'}


def text_for(slug):
    """返回 (source_tag, text)；官网官方文稿 > YouTube 人工字幕 > 自动字幕 > 本地 ASR。"""
    tag = SOURCES.get(slug)
    p = (ROOT / 'official' / f'{slug}.txt') if tag == 'ot' else (SITE / 'transcripts' / f'{slug}.txt')
    if tag and p.exists():
        return tag, p.read_text()
    return None, None


TR_SOURCE_NOTE = {
    'ot': '文稿来自 All-In 官网（allin.com）发布的官方 Transcript，说话人按官网分轨数据标注，个别段落可能张冠李戴。',
    'yt': '文稿来自 YouTube 频道上传的英文字幕。',
    'ytauto': '文稿来自 YouTube 自动生成字幕，可能有识别错误，没有说话人标注。',
    'asr': '文稿由本站用语音识别模型（Whisper，基于官方发布音频）机器转写，可能有识别错误，没有说话人标注；人名与数字请以音频为准。',
}
TR_LABEL = {'ot': '官方文稿', 'yt': 'YouTube 字幕', 'ytauto': '自动字幕', 'asr': '机器转写文稿'}


def ep_kind(e):
    """单集类型：周播（有期号）/ 峰会 / 嘉宾访谈 / 特别节目。"""
    if e.get('num') is not None:
        return f'第 {e["num"]} 期'
    t = (e.get('title') or '') + ' ' + (e.get('rssTitle') or '')
    if re.search(r'(?i)summit|#AIS', t):
        return '峰会对谈'
    if re.search(r'(?i)special|holiday|spectacular|recap|live from|livestream|predictions', t):
        return '特别节目'
    return '嘉宾访谈'


def fmt_date(d):
    if not d:
        return ''
    return f'{d[:4]} 年 {int(d[5:7])} 月 {int(d[8:10])} 日'


def audio_for(e):
    return e.get('audio')


def norm_zh(zh):
    """去掉译名里残留的期号前缀（E123: / #123）"""
    return pangu(re.sub(r'^(E\d+[:：]?|#\d+)\s*[–—:-]?\s*', '', zh or '').strip())


def build_data():
    eps = []
    for e in master:
        tag, _ = text_for(e['slug'])
        eps.append({
            'n': e.get('num'), 't': e['title'], 's': e['slug'], 'd': e.get('date'),
            'y': e.get('ytId'), 'k': ep_kind(e),
            'zh': norm_zh(trans.get(e['slug'], {}).get('zh')) or None,
            'guest': trans.get(e['slug'], {}).get('guest'),
            'tr': tag, 'au': bool(audio_for(e)),
            'r': 1 if e['slug'] in ZH and (SITE / 'zh' / f"{e['slug']}.html").exists() else 0,
            'ds': trans.get(e['slug'], {}).get('desc'),
        })
    out = {'updated': TODAY, 'episodes': eps}
    (SITE / 'data' / 'episodes.json').write_text(json.dumps(out, ensure_ascii=False, separators=(',', ':')))
    print(f"episodes.json: {len(eps)} eps, "
          f"tr={sum(1 for x in eps if x['tr'])}, zh={len(ZH)}, au={sum(1 for x in eps if x['au'])}, "
          f"zh_title={sum(1 for x in eps if x['zh'])}")
    return eps


EP_SHELL = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="color-scheme" content="dark light">
<meta name="theme-color" content="#ffffff">
<title>{title_tag}</title>
<meta name="description" content="{desc}">
<link rel="canonical" href="{url}/">
<meta property="og:title" content="{og_title}">
<meta property="og:description" content="{desc}">
<meta property="og:type" content="article">
<meta property="og:url" content="{url}/">
<meta property="og:image" content="{og_img}">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:image:alt" content="{og_title}">
<meta property="og:site_name" content="All-In 播客中文版">
<meta property="og:locale" content="zh_CN">
{article_meta}
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{og_title}">
<meta name="twitter:description" content="{desc}">
<meta name="twitter:image" content="{og_img}">
<meta name="robots" content="index, follow, max-image-preview:large, max-snippet:-1, max-video-preview:-1">
{md_link}
<script type="application/ld+json">{ldjson}</script>
{theme_script}
{head_extra}
{feed_link}
<link rel="stylesheet" href="/style.css?v={css_v}">
</head>
<body>
<a class="skip-link" href="#main">跳到主要内容</a>
{site_header}

<main class="wrap ep-main{main_cls}" id="main">
  <article class="ep">
    <header class="ep-head">
      <div class="ep-kicker">{kicker}</div>
      <h1 class="ep-title">{h1}</h1>
      {orig_line}
      {dek}
      <div class="ep-byline">{byline}</div>
    </header>
    {media_html}
    <section class="content-tabs">
      {tab_buttons}
      {panel_zh}
      {panel_tr}
      {no_tr_note}
    </section>
    {resources}
    <nav class="ep-prevnext" aria-label="上一期 / 下一期">
      {prevnext}
    </nav>
  </article>
  <aside class="ep-toc" aria-label="精读目录"></aside>
</main>

{site_footer}

{umami}
<script src="/ep.js?v={ep_v}"></script>
</body>
</html>
"""


def prune(o):
    """去掉 JSON-LD 里的空值，结构化数据更干净。"""
    if isinstance(o, dict):
        return {k: prune(v) for k, v in o.items() if v not in (None, '', [], {})}
    if isinstance(o, list):
        return [prune(v) for v in o]
    return o


def html_to_md(h):
    """精读 HTML（Markdown 渲染结果，结构简单）还原成 Markdown，供 AI 直接读取。"""
    h = re.sub(r'\s*<h2[^>]*>(.*?)</h2>\s*', lambda m: f'\n\n## {m.group(1)}\n\n', h, flags=re.S)
    h = re.sub(r'\s*<h3[^>]*>(.*?)</h3>\s*', lambda m: f'\n\n### {m.group(1)}\n\n', h, flags=re.S)
    h = re.sub(r'\s*<blockquote>(.*?)</blockquote>\s*',
               lambda m: '\n\n' + '\n'.join('> ' + ln for ln in re.sub(r'</?p>', '\n', m.group(1)).strip().splitlines() if ln.strip()) + '\n\n',
               h, flags=re.S)
    h = re.sub(r'\s*<li>(.*?)</li>\s*', lambda m: f'\n- {m.group(1).strip()}', h, flags=re.S)
    h = re.sub(r'</?(ul|ol)>', '\n', h)
    h = re.sub(r'<(strong|b)>(.*?)</\1>', r'**\2**', h, flags=re.S)
    h = re.sub(r'<(em|i)>(.*?)</\1>', r'*\2*', h, flags=re.S)
    h = re.sub(r'<br\s*/?>', '  \n', h)
    h = re.sub(r'\s*<p>(.*?)</p>\s*', lambda m: f'\n\n{m.group(1).strip()}\n\n', h, flags=re.S)
    h = H.unescape(re.sub(r'<[^>]+>', '', h))
    return re.sub(r'\n{3,}', '\n\n', h).strip() + '\n'


def badges_html(tr, zh):
    b = []
    if zh:
        b.append('<span class="badge zh">中文精读</span>')
    if tr:
        b.append('<span class="badge">文稿</span>')
    return f'<span class="badges">{"".join(b)}</span>' if b else ''


def people(e):
    """出场名单：主持人用中文简称，嘉宾用原名；分轨里时长太短的（多为插播片段里的政要）不算。"""
    hosts, guests = [], []
    for sp in e.get('speakers') or []:
        n = sp.get('name')
        if not n:
            continue
        if n in HOSTS:
            if HOSTS[n] not in hosts:   # 分轨偶尔把同一人拆成两路
                hosts.append(HOSTS[n])
        elif (sp.get('dur') or 0) >= 240 and n not in guests:
            guests.append(n)
    order = list(HOSTS.values())
    return sorted(hosts, key=order.index), guests


def fmt_dur(sec):
    if not sec:
        return ''
    h, m = divmod(round(sec / 60), 60)
    return f'{h} 小时 {m} 分' if h else f'{m} 分钟'


def pangu(t):
    """中西文之间加空格，直引号换成中文弯引号（章节译文常见的排版问题）。"""
    t = re.sub(r'([\u4e00-\u9fff])([A-Za-z0-9$])', r'\1 \2', t)
    t = re.sub(r'([A-Za-z0-9%])([\u4e00-\u9fff])', r'\1 \2', t)
    n = [0]

    def q(m):
        n[0] += 1
        return '“' if n[0] % 2 else '”'
    if re.search(r'[\u4e00-\u9fff]', t):
        t = re.sub(r'"', q, t)
    return t


def chapters_html(e, has_audio):
    ch = [c for c in e.get('chapters') or [] if c[1]]
    if len(ch) < 2:
        return ''
    zh = (trans.get(e['slug']) or {}).get('chap') or []
    if len(zh) == len(ch):
        ch = [[c[0], pangu(z) if z else c[1]] for c, z in zip(ch, zh)]
    def ts(sec):
        h, r = divmod(int(sec), 3600)
        return f'{h}:{r // 60:02d}:{r % 60:02d}' if h else f'{r // 60}:{r % 60:02d}'
    def t3(sec):
        return f'{int(sec) // 3600:02d}:{int(sec) % 3600 // 60:02d}:{int(sec) % 60:02d}'
    items = ''.join(
        f'<li><span class="ts-link chap-t" data-t="{t3(sec)}">{ts(sec)}</span><span class="chap-x">{H.escape(txt)}</span></li>'
        if has_audio else
        f'<li><span class="chap-t">{ts(sec)}</span><span class="chap-x">{H.escape(txt)}</span></li>'
        for sec, txt in ch)
    hint = '<span class="chap-hint">点时间可跳到音频对应位置</span>' if has_audio else ''
    return f'<section class="chapters" aria-label="本期章节"><h2>本期章节{hint}</h2><ol>{items}</ol></section>'


def strip_no(t):
    return re.sub(r'^(E\d+[:.]?\d*[:：]?|#AIS:|#\d+)\s*[–—:-]?\s*', '', t or '').strip()


def build_page(i, eps):
    e = master[i]
    slug, num, title = e['slug'], e.get('num'), e['title']
    d = e.get('date') or ''
    y = e.get('ytId')
    audio = audio_for(e)
    tr_tag, tr_text = text_for(slug)
    zh_html_file = SITE / 'zh' / f'{slug}.html'
    has_zh = slug in ZH and zh_html_file.exists()
    tr_info = trans.get(slug, {})
    zh_title = norm_zh(tr_info.get('zh'))
    topic, desc = tr_info.get('guest'), tr_info.get('desc')
    lede = desc
    kind = ep_kind(e)
    hosts, guests = people(e)

    url = f'{DOMAIN}/ep/{slug}'
    title_tag = (f'{zh_title} | All-In 播客第 {num} 期' if num is not None else f'{zh_title} | All-In 播客') if zh_title \
        else f'{title} | All-In 播客'
    desc = desc or (e.get('desc') or f'All-In 播客：{title[:80]}')[:150]
    desc = f'{desc} 含官方音频、视频与英文文稿。'
    og_title = f'{zh_title}｜{strip_no(title)}' if zh_title else title

    zh_raw = zh_html_file.read_text() if has_zh else ''
    zh_chars = len(re.sub(r'\s+', '', H.unescape(re.sub(r'<[^>]+>', '', zh_raw))))
    read_min = max(1, round(zh_chars / 450)) if zh_chars else 0
    zh_mtime = date.fromtimestamp(zh_html_file.stat().st_mtime).isoformat() if has_zh else None
    zh_sections = re.findall(r'<h2[^>]*>(.*?)</h2>', zh_raw)
    zh_sections = [H.unescape(re.sub(r'<[^>]+>', '', x)).strip() for x in zh_sections]

    ld_media = []
    if audio:
        ld_media.append({"@type": "AudioObject", "contentUrl": audio, "encodingFormat": "audio/mpeg",
                         "duration": f'PT{int(e["duration"])}S' if e.get('duration') else None})
    if y:
        ld_media.append({"@type": "VideoObject", "name": title, "embedUrl": f'https://www.youtube.com/embed/{y}',
                         "thumbnailUrl": f'https://i.ytimg.com/vi/{y}/hqdefault.jpg', "uploadDate": d or None})
    actors = [{"@type": "Person", "name": n} for n in [k for k, v in HOSTS.items() if v in hosts] + guests]
    ld = {"@context": "https://schema.org", "@graph": [
        {"@type": "PodcastEpisode", "@id": f'{url}/#episode', "url": f'{url}/',
         "name": title, "alternativeHeadline": zh_title or None, "description": tr_info.get('desc') or e.get('desc') or None,
         "datePublished": d or None, "episodeNumber": num, "inLanguage": "en",
         "timeRequired": f'PT{round(e["duration"] / 60)}M' if e.get('duration') else None,
         "actor": actors or None,
         "partOfSeries": {"@type": "PodcastSeries", "name": "All-In with Chamath, Jason, Sacks & Friedberg",
                          "url": "https://allin.com/"},
         "associatedMedia": ld_media},
        *([{"@type": "Article", "@id": f'{url}/#article', "url": f'{url}/',
             "headline": (zh_title or title)[:110], "alternativeHeadline": title,
             "description": tr_info.get('desc') or None, "inLanguage": "zh-CN",
             "datePublished": zh_mtime, "dateModified": zh_mtime,
             "wordCount": zh_chars, "timeRequired": f'PT{read_min}M',
             "image": f'{DOMAIN}/og/{slug}.jpg',
             "articleSection": zh_sections[:30] or None,
             "isBasedOn": {"@id": f'{url}/#episode'}, "about": {"@id": f'{url}/#episode'},
             "author": {"@type": "Person", "name": "向阳乔木", "url": "https://x.com/vista8"},
             "publisher": {"@id": f'{DOMAIN}/#org'},
             "isAccessibleForFree": True,
             "mainEntityOfPage": f'{url}/'}] if has_zh else []),
        {"@type": "Organization", "@id": f'{DOMAIN}/#org', "name": SITE_NAME, "url": f'{DOMAIN}/',
         "logo": f'{DOMAIN}/icon-512.png',
         "sameAs": ["https://github.com/joeseesun/allin-podcast-cn", "https://x.com/vista8"]},
        {"@type": "BreadcrumbList", "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "首页", "item": f'{DOMAIN}/'},
            {"@type": "ListItem", "position": 2, "name": title_tag.split(' | ')[0], "item": f'{url}/'}]},
    ]}
    ldjson = json.dumps(prune(ld), ensure_ascii=False).replace('<', '\\u003c')

    h1 = H.escape(zh_title or strip_no(title) or title)
    orig_line = (f'<p class="ep-title-en" lang="en">{H.escape(strip_no(title))}</p>' if zh_title else '')
    kicker_bits = [kind]
    if has_zh:
        kicker_bits.append('<span class="k-zh">中文精读</span>')
    kicker = '<span class="k-sep">·</span>'.join(kicker_bits)
    dek = f'<p class="ep-dek">{H.escape(lede)}</p>' if lede else ''
    by = []
    if hosts or guests:
        who = '、'.join(hosts)
        if guests:
            who += ('，嘉宾 ' if hosts else '') + '、'.join(H.escape(g) for g in guests[:4])
        by.append(f'<span>{who}</span>')
    if d:
        by.append(f'<time datetime="{d}">{fmt_date(d)}</time>')
    if e.get('duration'):
        by.append(f'<span>时长 {fmt_dur(e["duration"])}</span>')
    if tr_tag:
        by.append(f'<span>{TR_LABEL[tr_tag]}</span>')
    if read_min:
        wan = f'{zh_chars / 10000:.1f} 万字' if zh_chars >= 10000 else f'{zh_chars} 字'
        by.append(f'<span>精读约 {wan} · 阅读 {read_min} 分钟</span>')
    byline = '<span class="k-sep">·</span>'.join(by)

    audio_html = ''
    if audio:
        audio_html = (f'<div class="audio-player" data-slug="{H.escape(slug)}" data-title="{H.escape(zh_title or title)}">'
                      f'<audio src="{H.escape(audio)}" preload="none"></audio>'
                      f'<button class="ap-play" type="button" aria-label="播放">{ICON_PLAY}{ICON_PAUSE}</button>'
                      f'<div class="ap-mid">'
                      f'<div class="ap-line1"><span class="ap-label">官方音频</span>'
                      f'<span class="ap-time"><span class="ap-cur">00:00</span> / <span class="ap-dur">--:--</span></span></div>'
                      f'<div class="ap-bar" role="slider" aria-label="播放进度" tabindex="0">'
                      f'<div class="ap-buf"></div><div class="ap-fill"></div></div>'
                      f'<div class="ap-line2">'
                      f'<button type="button" class="ap-skip" data-s="-15" aria-label="后退 15 秒">−15s</button>'
                      f'<button type="button" class="ap-skip" data-s="15" aria-label="前进 15 秒">+15s</button>'
                      f'<button type="button" class="ap-rate" aria-label="播放速度">1×</button>'
                      f'<span class="ap-resume" hidden>已从上次位置继续 · <button type="button" class="ap-restart">从头听</button></span>'
                      f'</div></div></div>')

    video_html = ''
    if y:
        img = f'https://i.ytimg.com/vi/{y}/mqdefault.jpg'
        video_html = (f'<button type="button" class="video-facade" data-yt="{H.escape(y)}" aria-label="播放 YouTube 视频">'
                      f'<img src="{img}" alt="" width="320" height="180">'
                      f'<span class="vf-play">{ICON_PLAY}<span>看视频</span></span></button>')
    if audio or y:
        media_html = f'<div class="media{" has-audio" if audio else ""}">{video_html}{audio_html}</div>'
    else:
        media_html = '<div class="ep-notice">该期暂无音视频存档，可通过下方资源访问原始页面。</div>'
    media_html += chapters_html(e, bool(audio))

    tab_buttons = ['<div class="tabs" role="tablist">']
    if has_zh:
        tab_buttons.append('<button class="tab-btn" type="button" data-tab="zh" role="tab" aria-controls="panel-zh">中文精读</button>')
    if tr_tag:
        tab_buttons.append('<button class="tab-btn" type="button" data-tab="tr" role="tab" aria-controls="panel-tr">英文文稿</button>')
    tab_buttons.append('</div>')
    tab_buttons = ''.join(tab_buttons) if (has_zh or tr_tag) else ''

    panel_zh = ''
    if has_zh:
        panel_zh = (f'<div class="tab-panel" id="panel-zh" data-panel="zh">'
                    f'<div class="zh-note">中文精读由 AI 基于原始节目改写，非官方翻译，可能存在偏差或错误；观点归属与表述以原音频与官方文稿为准。<a href="/disclaimer/">说明</a></div>'
                    f'<div class="zh-article">{zh_raw}</div>{COLOPHON}</div>')

    panel_tr = ''
    if tr_tag:
        body = tr_text.split('\n\n', 1)[1] if tr_text.count('\n') > 2 else tr_text   # 去掉文件头两行来源说明
        panel_tr = (f'<div class="tab-panel" id="panel-tr" data-panel="tr">'
                    f'<div class="tr-note">{TR_SOURCE_NOTE[tr_tag]}</div>'
                    f'<div class="transcript-text">{H.escape(body.strip())}</div></div>')

    no_tr_note = ''
    if not has_zh and not tr_tag:
        parts = ['该期文稿正在整理中。']
        if audio or y:
            parts.append('可先收听上方音频或观看视频。')
        no_tr_note = f'<div class="ep-notice">{"".join(parts)}</div>'

    res = ['<section class="ep-resources"><h2>本集资源与出处</h2><ul>']
    if y:
        res.append(f'<li><span class="res-k">视频</span><a href="https://www.youtube.com/watch?v={H.escape(y)}" target="_blank" rel="noopener">在 YouTube 收看</a></li>')
    if audio:
        res.append(f'<li><span class="res-k">音频</span><a href="{H.escape(audio)}" target="_blank" rel="noopener">官方 MP3 直链</a><span class="res-sub">来自 All-In 官方播客 RSS（libsyn）</span></li>')
    plats = []
    if e.get('apple'):
        plats.append(f'<a href="https://podcasts.apple.com/podcast/id{H.escape(e["apple"])}" target="_blank" rel="noopener">Apple 播客</a>')
    if e.get('spotify'):
        plats.append(f'<a href="https://open.spotify.com/episode/{H.escape(e["spotify"])}" target="_blank" rel="noopener">Spotify</a>')
    if e.get('libsyn'):
        plats.append(f'<a href="{H.escape(e["libsyn"])}" target="_blank" rel="noopener">节目原始页面</a>')
    if plats:
        res.append(f'<li><span class="res-k">收听</span>{" · ".join(plats)}</li>')
    res.append('<li><span class="res-k">官网</span><a href="https://allin.com/episodes" target="_blank" rel="noopener">All-In 官网节目列表</a></li>')
    if tr_tag == 'ot' and e.get('transcriptURL'):
        res.append('<li><span class="res-k">文稿</span><span>官方 Transcript 来自 allin.com 单集页</span></li>')
    elif tr_tag:
        res.append(f'<li><span class="res-k">文稿</span><a href="/transcripts/{H.escape(slug)}.txt" target="_blank" rel="noopener">本站纯文本版</a></li>')
    res.append('</ul></section>')
    resources = ''.join(res)

    prev_e, next_e = (eps[i - 1] if i > 0 else None), (eps[i + 1] if i < len(eps) - 1 else None)

    def pn_link(ne, label):
        if not ne:
            return '<span></span>'
        t = norm_zh(ne['zh']) or strip_no(ne['t'])
        return (f'<a href="/ep/{ne["s"]}/" rel="{"prev" if label.startswith("←") else "next"}">'
                f'<span class="pn-k">{label}</span><span class="pn-t">{H.escape(t)}</span></a>')

    prevnext = f'{pn_link(next_e, "← 上一期")}{pn_link(prev_e, "下一期 →")}'

    page = EP_SHELL.format(
        title_tag=H.escape(title_tag), desc=H.escape(desc), url=url,
        og_title=H.escape(og_title), og_img=f'{DOMAIN}/og/{slug}.jpg',
        ldjson=ldjson, css_v=CSS_V, ep_v=EP_V,
        theme_script=THEME_SCRIPT,
        feed_link=FEED_LINK, site_footer=SITE_FOOTER,
        h1=h1, orig_line=orig_line, kicker=kicker, dek=dek, byline=byline,
        main_cls=' has-toc' if has_zh else '',
        article_meta=(f'<meta property="article:published_time" content="{d}">' if d else '') +
                     (f'<meta property="article:modified_time" content="{zh_mtime}">' if zh_mtime else ''),
        md_link=(f'<link rel="alternate" type="text/markdown" title="中文精读 Markdown" href="/ep/{slug}/index.md">' if has_zh else ''),
        head_extra=HEAD_EXTRA, site_header=SITE_HEADER,
        media_html=media_html, tab_buttons=tab_buttons,
        panel_zh=panel_zh, panel_tr=panel_tr, no_tr_note=no_tr_note,
        resources=resources, prevnext=prevnext, umami=UMAMI,
    )
    dest = SITE / 'ep' / slug / 'index.html'
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(page)
    md = dest.parent / 'index.md'
    if has_zh:
        who = '、'.join(hosts) + (('；嘉宾：' + '、'.join(guests)) if guests else '')
        head = [f'# {zh_title or title}', '',
                f'> {tr_info.get("desc") or ""}', '',
                f'- 原标题：{title}', f'- 类型：{kind}',
                f'- 发布日期：{d}' if d else None, f'- 话题：{topic}' if topic else None,
                f'- 出场：{who}' if who else None,
                f'- 网页版：{url}/', f'- 视频：https://www.youtube.com/watch?v={y}' if y else None,
                f'- 精读字数：约 {zh_chars} 字（阅读约 {read_min} 分钟）', '',
                '> 本文为 allin.qiaomu.ai 基于原始节目的 AI 改写中文精读，非官方翻译；引用请注明出处并链接网页版。', '']
        md.write_text('\n'.join(x for x in head if x is not None) + '\n' + html_to_md(zh_raw))
    elif md.exists():
        md.unlink()


def build_sitemap(eps):
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
             f'<url><loc>{DOMAIN}/</loc><lastmod>{TODAY}</lastmod><priority>1.0</priority></url>',
             f'<url><loc>{DOMAIN}/archive/</loc><lastmod>{TODAY}</lastmod><priority>0.7</priority></url>',
             f'<url><loc>{DOMAIN}/disclaimer/</loc><lastmod>{TODAY}</lastmod><priority>0.3</priority></url>']
    for x in eps:
        zf = SITE / 'zh' / f"{x['s']}.html"
        lm = date.fromtimestamp(zf.stat().st_mtime).isoformat() if x.get('r') and zf.exists() else (x['d'] or TODAY)
        lines.append(f'<url><loc>{DOMAIN}/ep/{x["s"]}/</loc><lastmod>{lm}</lastmod><priority>0.8</priority></url>')
    lines.append('</urlset>')
    (SITE / 'sitemap.xml').write_text('\n'.join(lines))
    print(f'sitemap.xml: {len(eps) + 3} urls')


DISCLAIMER_BODY = """
    <article class="ep">
      <header class="ep-head"><div class="ep-kicker">关于本站</div><h1 class="ep-title">免责声明与内容说明</h1></header>
      <div class="zh-article disclaimer-body">
        <p>本站（allin.qiaomu.ai）是一个非官方的中文导读与检索项目，帮助中文读者发现并理解 All-In Podcast。本站与 All-In 节目、四位主持人（Chamath Palihapitiya、Jason Calacanis、David Sacks、David Friedberg）、制作方及任何嘉宾均无隶属或合作关系，站点内容不代表其观点或立场。</p>

        <h2>内容版权</h2>
        <p>本站所涉全部原始内容，包括音频、视频、原始文字稿、标题与节目名称，版权归 All-In 节目及相应嘉宾、制作方所有。本站不托管、不存储、不分发任何媒体文件：音频通过官方播客 RSS 发布渠道（libsyn）在线播放，视频嵌入 YouTube 官方频道，原始文稿注明出处。对原始内容本身，本站仅作必要限度的引用与出处标注。</p>

        <h2>中文精读</h2>
        <p>「中文精读」是 AI 基于原始节目内容辅助生成的中文解读，属于二次创作，并非官方翻译，也未经主持人或嘉宾审阅。多人对谈的观点归属依赖文稿的说话人标注，受语言理解与信息压缩所限，可能存在理解偏差、遗漏、张冠李戴或错误，不构成对原意的权威解释，更不构成任何投资建议。请以官方发布的原始音频与文字稿为准。</p>

        <h2>英文文稿来源</h2>
        <p>单集页的英文文稿按以下优先级获取，并在每页文稿区内标注来源：</p>
        <ul>
          <li>官方 Transcript：来自 All-In 官网 allin.com 公开发布，含说话人分轨；</li>
          <li>YouTube 字幕：来自 All-In 官方 YouTube 频道的字幕或自动字幕；</li>
          <li>机器转写：本站基于官方发布音频用语音识别模型（Whisper）生成，可能有识别错误，人名与数字请以音频为准。</li>
        </ul>

        <h2>使用目的</h2>
        <p>本站为非商业性质，无广告、无收费，仅用于学习、检索与导读。如权利方认为本站任何内容（包括精读文字、文稿片段或页面呈现方式）不适合展示，请联系站点维护者，经确认后将移除或更正。</p>

        <h2>反馈与联系</h2>
        <p>纠错、建议或版权事宜，可通过 <a href="https://x.com/vista8" target="_blank" rel="noopener">X（@vista8）</a> 或 <a href="https://github.com/joeseesun/" target="_blank" rel="noopener">GitHub</a> 联系站点维护者。</p>
      </div>
    </article>
"""


def build_disclaimer():
    body = DISCLAIMER_BODY
    title = f'免责声明与内容说明 · {SITE_NAME}'
    desc = '本站性质、内容版权归属、中文精读与文稿来源说明，以及反馈与移除流程。'
    url = f'{DOMAIN}/disclaimer'
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="color-scheme" content="dark light">
<meta name="theme-color" content="#ffffff">
<title>{H.escape(title)}</title>
<meta name="description" content="{H.escape(desc)}">
<link rel="canonical" href="{url}/">
<meta property="og:title" content="{H.escape(title)}">
<meta property="og:description" content="{H.escape(desc)}">
<meta property="og:type" content="article">
<meta property="og:url" content="{url}/">
<meta property="og:image" content="{DOMAIN}/og/default.jpg">
<meta property="og:site_name" content="All-In 播客中文版">
<meta name="twitter:card" content="summary_large_image">
{THEME_SCRIPT}
{HEAD_EXTRA}
{FEED_LINK}
<link rel="stylesheet" href="/style.css?v={CSS_V}">
</head>
<body>
<a class="skip-link" href="#main">跳到主要内容</a>
{SITE_HEADER}

<main class="wrap ep-main" id="main">{body}
  <nav class="ep-prevnext">
    <span></span><a href="/">← 返回全部单集</a>
  </nav>
</main>

{SITE_FOOTER}

{UMAMI}
<script src="/app.js?v={APP_V}"></script>
</body>
</html>
"""
    out = SITE / 'disclaimer' / 'index.html'
    out.parent.mkdir(exist_ok=True)
    out.write_text(html)
    print('disclaimer page: ok')


def build_llms(eps):
    n_zh = sum(1 for x in eps if x.get('r'))
    lines = [f'# All-In 播客中文版（allin.qiaomu.ai）', '',
             f'> All-In Podcast 全部 {len(eps)} 期中文导航站（非官方粉丝站点）：每期一个独立页面，含官方音频直链、'
             f'YouTube 视频、英文文稿（官网官方 Transcript / YouTube 字幕 / 机器转写，均标明来源）与 AI 改写的中文精读，'
             f'目前 {n_zh} 篇。主持人：Chamath Palihapitiya、Jason Calacanis、David Sacks、David Friedberg。'
             f'内容版权归 All-In 节目及嘉宾所有。', '',
             '## 使用说明', '',
             '- 每篇中文精读都有纯 Markdown 版本：在单集网址后加 `index.md`，例如 '
             f'{DOMAIN}/ep/{next((x["s"] for x in eps if x.get("r")), "e293")}/index.md',
             f'- 全部单集按年份的目录：{DOMAIN}/archive/',
             f'- RSS 全文订阅（最新 20 篇精读）：{DOMAIN}/feed.xml',
             '- 周播网址为 /ep/e{期号}/，例如 /ep/e293/；嘉宾访谈与峰会对谈用标题关键词作网址。',
             '- 引用时请注明「allin.qiaomu.ai 中文精读（AI 改写，非官方翻译）」并链接对应网页。', '',
             '## 中文精读（AI 深度改写）', '']
    for x in eps:
        if x.get('r'):
            t = norm_zh(x['zh']) or x['t']
            lines.append(f'- [{x["k"]} · {t}]({DOMAIN}/ep/{x["s"]}/index.md)：{(trans.get(x["s"], {}) or {}).get("desc", "")}')
    lines += ['', '## 全部单集索引', '']
    for x in eps:
        t = norm_zh(x['zh']) or x['t']
        orig = '' if not x['zh'] else f'（原标题：{x["t"]}）'
        d = trans.get(x['s'], {}) or {}
        desc = f'：{d["desc"]}' if d.get('desc') else ''
        lines.append(f'- [{x["k"]} · {t}]({DOMAIN}/ep/{x["s"]}/){desc}{orig}')
    (SITE / 'llms.txt').write_text('\n'.join(lines))
    print(f'llms.txt: {len(lines)} lines')


def cdata(s):
    return '<![CDATA[' + s.replace(']]>', ']]]]><![CDATA[>') + ']]>'


def rfc822(d):
    import datetime
    import email.utils
    try:
        dt = datetime.datetime.fromisoformat(d).replace(tzinfo=datetime.timezone.utc)
    except (ValueError, TypeError):
        dt = datetime.datetime.now(datetime.timezone.utc)
    return email.utils.format_datetime(dt)


def audio_len(url):
    """enclosure 的 Content-Length（HEAD，带 UA；Range 兜底），结果缓存 tools/audio_sizes.json。拿不到返回 0（RSS 规范允许）。"""
    import urllib.request
    cache = ROOT / 'audio_sizes.json'
    try:
        sizes = json.load(open(cache)) if cache.exists() else {}
    except Exception:
        sizes = {}
    if url not in sizes:
        sizes[url] = 0
        try:
            req = urllib.request.Request(url, method='HEAD',
                                         headers={'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'})
            with urllib.request.urlopen(req, timeout=8) as r:
                sizes[url] = int(r.headers.get('Content-Length') or 0)
            if not sizes[url]:
                req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Range': 'bytes=0-0'})
                with urllib.request.urlopen(req, timeout=8) as r:
                    cr = r.headers.get('Content-Range') or ''
                    if '/' in cr:
                        sizes[url] = int(cr.rsplit('/', 1)[1] or 0)
        except Exception:
            pass
        try:
            json.dump(sizes, open(cache, 'w'))
        except Exception:
            pass
    return sizes.get(url) or 0


def build_feed(eps, limit=20):
    """RSS 2.0 全文订阅：最新 limit 期；有精读给全文，无精读只放简介与原文链接（不塞文稿节选，控制体积）。"""
    import html as HH
    import datetime
    import email.utils
    au_map = {}
    for e in master:
        au_map[e['slug']] = audio_for(e)
    items = []
    feed_eps = [x for x in eps if x.get('r')] or eps
    for x in feed_eps[:limit]:
        slug, num = x['s'], x['n']
        title = norm_zh(x['zh']) or x['t']
        title = f'{x["k"]} · {title}'
        url = f'{DOMAIN}/ep/{slug}/'
        d = trans.get(slug, {}) or {}
        desc = d.get('desc') or f'All-In 播客 {x["k"]}中文精读。'
        zh_file = SITE / 'zh' / f'{slug}.html'
        if x.get('r') and zh_file.exists():
            body = zh_file.read_text() + (
                f'<hr><p>本文为中文精读（AI 基于原始节目改写，仅供参考，不构成投资建议）。'
                f'收听原始音频、观看视频与阅读英文文稿请访问 <a href="{url}">原文页面</a>。</p>')
        else:
            body = ''
        au = au_map.get(slug)
        enc = (f'<enclosure url="{HH.escape(au)}" type="audio/mpeg" length="{audio_len(au)}"/>'
               if au else '')
        content = f'<content:encoded>{cdata(body)}</content:encoded>' if body else ''
        items.append(
            f'<item><title>{HH.escape(title)}</title>'
            f'<link>{url}</link>'
            f'<guid isPermaLink="true">{url}</guid>'
            f'<pubDate>{rfc822(x["d"])}</pubDate>'
            f'<description>{cdata(HH.escape(desc))}</description>'
            f'{content}'
            f'{enc}</item>')
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/" '
        'xmlns:atom="http://www.w3.org/2005/Atom">\n<channel>\n'
        f'<title>{SITE_NAME} · 中文精读</title>\n'
        f'<link>{DOMAIN}/</link>\n'
        f'<atom:link href="{DOMAIN}/feed.xml" rel="self" type="application/rss+xml"/>\n'
        f'<description>All-In 播客 {len(eps)} 期的中文精读全文、官方音频与文稿导航（非官方粉丝站点）</description>\n'
        '<language>zh-CN</language>\n'
        f'<lastBuildDate>{email.utils.format_datetime(datetime.datetime.now(datetime.timezone.utc))}</lastBuildDate>\n'
        '<generator>allin.qiaomu.ai static build</generator>\n'
        + '\n'.join(items) + '\n</channel>\n</rss>\n')
    (SITE / 'feed.xml').write_text(xml)
    print(f'feed.xml: {len(items)} items, {len(xml) // 1024} KB')


def clean_stale(eps):
    live = {x['s'] for x in eps}
    epdir = SITE / 'ep'
    if epdir.exists():
        for d in epdir.iterdir():
            if d.is_dir() and d.name not in live:
                shutil.rmtree(d)
                print('removed stale', d.name)


def _strip_no(t):
    return strip_no(t)


def ssr_card(x):
    """与 app.js 的 cardHTML 同构：首屏直接输出，爬虫与无 JS 环境也能读到。"""
    zh, en = norm_zh(x['zh']) or _strip_no(x['t']), _strip_no(x['t'])
    num = x['k']
    dt = (x['d'] or '')[:10].replace('-', '.')
    thumb = (f'<img loading="lazy" decoding="async" src="https://i.ytimg.com/vi/{x["y"]}/mqdefault.jpg" alt="" width="320" height="180">'
             if x['y'] else '<div class="no-img">ALL-IN</div>')
    tag = '<span class="zh-tag">中文精读</span>' if x.get('r') else ''
    en_html = f'<div class="card-title-en" lang="en">{H.escape(en)}</div>' if x['zh'] else ''
    return (f'<a class="card" href="/ep/{x["s"]}/"><div class="thumb">{thumb}</div><div class="card-body">'
            f'<div class="card-kicker"><span class="ep-n">{num}</span><span>{dt}</span>{tag}</div>'
            f'<div class="card-title">{H.escape(zh)}</div>{en_html}</div></a>')


def ssr_hero(x):
    zh, en = norm_zh(x['zh']) or _strip_no(x['t']), _strip_no(x['t'])
    d = x['d'] or ''
    when = f'{d[:4]} 年 {int(d[5:7])} 月 {int(d[8:10])} 日' if d else ''
    by = ' · '.join(H.escape(v) for v in [x['k'], when] if v)
    img = (f'<img src="https://i.ytimg.com/vi/{x["y"]}/maxresdefault.jpg" alt="" '
           f'onerror="this.onerror=null;this.src=\'https://i.ytimg.com/vi/{x["y"]}/hqdefault.jpg\'">' if x['y'] else '')
    desc = f'<p class="hero-desc">{H.escape(x["ds"])}</p>' if x.get('ds') else ''
    return (f'<a class="hero-media" href="/ep/{x["s"]}/" tabindex="-1" aria-hidden="true">{img}</a>'
            f'<div class="hero-text"><div class="hero-kicker">最新精读</div>'
            f'<h2 class="hero-title"><a href="/ep/{x["s"]}/">{H.escape(zh)}</a></h2>'
            f'<p class="hero-en" lang="en">{H.escape(en)}</p>{desc}<p class="hero-by">{by}</p>'
            f'<a class="hero-cta" href="/ep/{x["s"]}/">阅读全文<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h14M13 6l6 6-6 6"/></svg></a></div>')


def sync_index(eps):
    """首页 index.html 手写维护外壳；共用 <head> 片段、结构化数据、首屏内容（头条 + 前 30 期）与页脚由构建注入。"""
    f = SITE / 'index.html'
    html = f.read_text()
    n_zh = sum(1 for x in eps if x.get('r'))
    since = min((x['d'] for x in eps if x['d']), default='')[:4]
    latest = next((x for x in eps if x.get('r')), None) or (eps[0] if eps else None)
    n_week = sum(1 for x in eps if x['n'] is not None)
    ld = {"@context": "https://schema.org", "@graph": [
        {"@type": "WebSite", "@id": f'{DOMAIN}/#website', "name": SITE_NAME, "url": f'{DOMAIN}/',
         "description": f'All-In 播客 {len(eps)} 期中文导航：官方音频、视频、英文文稿与 {n_zh} 篇中文精读',
         "inLanguage": "zh-CN", "publisher": {"@id": f'{DOMAIN}/#org'},
         "potentialAction": {"@type": "SearchAction", "target": f'{DOMAIN}/?q={{search_term_string}}',
                             "query-input": "required name=search_term_string"}},
        {"@type": "Organization", "@id": f'{DOMAIN}/#org', "name": SITE_NAME, "url": f'{DOMAIN}/',
         "logo": f'{DOMAIN}/icon-512.png', "founder": {"@type": "Person", "name": "向阳乔木", "url": "https://x.com/vista8"},
         "sameAs": ["https://github.com/joeseesun/allin-podcast-cn", "https://x.com/vista8"]},
        {"@type": "ItemList", "name": "最新单集", "itemListOrder": "https://schema.org/ItemListOrderDescending",
         "numberOfItems": len(eps),
         "itemListElement": [{"@type": "ListItem", "position": k + 1, "url": f'{DOMAIN}/ep/{x["s"]}/',
                              "name": norm_zh(x['zh']) or x['t']} for k, x in enumerate(eps[:30])]},
    ]}
    blocks = {
        'head': f'{THEME_SCRIPT}\n{HEAD_EXTRA}',
        'ld': '<script type="application/ld+json">' + json.dumps(ld, ensure_ascii=False).replace('<', '\\u003c') + '</script>',
        'sub': f'{n_week} 期周播 · {len(eps) - n_week} 场访谈与峰会 · {n_zh} 篇中文精读 · {since} 年至今',
        'hero': ssr_hero(latest) if latest else '',
        'grid': ''.join(ssr_card(x) for x in eps[:30]),
        'footer': SITE_FOOTER,
    }
    for k, v in blocks.items():
        html = re.sub(rf'<!--site:{k}-->.*?<!--/site:{k}-->', lambda m, k=k, v=v: f'<!--site:{k}-->{v}<!--/site:{k}-->', html, flags=re.S)
    if latest:
        html = re.sub(r'<section class="hero" id="hero"[^>]*>', f'<section class="hero" id="hero" data-s="{latest["s"]}" aria-label="最新精读">', html)
    html = re.sub(r'<script defer src="https://umami\.qiaomu\.ai/script\.js"[^>]*></script>', UMAMI, html)
    html = re.sub(r'/style\.css\?v=\d+', f'/style.css?v={CSS_V}', html)
    html = re.sub(r'/app\.js\?v=\d+', f'/app.js?v={APP_V}', html)
    f.write_text(html)


def build_archive(eps):
    """单集目录：按年份列出全部单集，纯静态，便于爬虫与读者一览全貌。"""
    years = {}
    for x in eps:
        years.setdefault((x['d'] or '0000')[:4], []).append(x)
    n_zh = sum(1 for x in eps if x.get('r'))
    nav = ''.join(f'<a href="#y{y}">{y}</a>' for y in sorted(years, reverse=True) if y != '0000')
    sec = []
    for y in sorted(years, reverse=True):
        rows = []
        for x in years[y]:
            zh = norm_zh(x['zh']) or _strip_no(x['t'])
            md = (x['d'] or '')[5:10].replace('-', '.')
            tag = '<span class="ar-zh">精读</span>' if x.get('r') else ''
            rows.append(f'<li><a href="/ep/{x["s"]}/"><span class="ar-n">{("E" + str(x["n"])) if x["n"] is not None else {"峰会对谈": "峰会", "特别节目": "特别"}.get(x["k"], "访谈")}</span>'
                        f'<span class="ar-t"><b>{H.escape(zh)}</b><i lang="en">{H.escape(_strip_no(x["t"]))}</i></span>'
                        f'<span class="ar-d">{md}{tag}</span></a></li>')
        label = '年份未知' if y == '0000' else f'{y}'
        sec.append(f'<section class="ar-year" id="y{y}"><h2>{label}<small>{len(years[y])} 期</small></h2><ol>{"".join(rows)}</ol></section>')
    title = f'单集目录 · 全部 {len(eps)} 期 · {SITE_NAME}'
    desc = f'All-In 播客全部 {len(eps)} 期单集（周播、嘉宾访谈与峰会对谈）按年份排列的目录，含 {n_zh} 篇中文精读、官方音频、视频与英文文稿。'
    url = f'{DOMAIN}/archive'
    ld = {"@context": "https://schema.org", "@type": "CollectionPage", "name": title, "url": f'{url}/', "inLanguage": "zh-CN",
          "description": desc, "isPartOf": {"@id": f'{DOMAIN}/#website'}}
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="color-scheme" content="light dark">
<meta name="theme-color" content="#ffffff">
<title>{H.escape(title)}</title>
<meta name="description" content="{H.escape(desc)}">
<link rel="canonical" href="{url}/">
<meta property="og:title" content="{H.escape(title)}">
<meta property="og:description" content="{H.escape(desc)}">
<meta property="og:type" content="website">
<meta property="og:url" content="{url}/">
<meta property="og:locale" content="zh_CN">
<meta property="og:image" content="{DOMAIN}/og/default.jpg">
<meta property="og:site_name" content="All-In 播客中文版">
<meta name="twitter:card" content="summary_large_image">
<script type="application/ld+json">{json.dumps(ld, ensure_ascii=False)}</script>
{THEME_SCRIPT}
{HEAD_EXTRA}
{FEED_LINK}
<link rel="stylesheet" href="/style.css?v={CSS_V}">
</head>
<body>
<a class="skip-link" href="#main">跳到主要内容</a>
{SITE_HEADER}
<main class="wrap ar-main" id="main">
  <header class="ep-head"><div class="ep-kicker">单集目录</div><h1 class="ep-title">全部 {len(eps)} 期</h1>
    <p class="ep-dek">从 2020 年疫情初起的第 0 期到最新一期，周播与嘉宾访谈、峰会对谈按年份排列；标有「精读」的单集附中文长文。</p></header>
  <nav class="ar-nav" aria-label="按年份跳转">{nav}</nav>
  {''.join(sec)}
</main>
{SITE_FOOTER}
{UMAMI}
</body>
</html>
"""
    out = SITE / 'archive' / 'index.html'
    out.parent.mkdir(exist_ok=True)
    out.write_text(html)
    print(f'archive page: {len(eps)} eps')


def build_special_pages(eps):
    """404 与离线页：同一套报头 / 页脚，给出下一步可去的地方。"""
    picks = [x for x in eps if x.get('r')][:4]
    links = ''.join(f'<li><a href="/ep/{x["s"]}/"><b>{H.escape(norm_zh(x["zh"]) or x["t"])}</b>'
                    f'<i>{x["k"]}</i></a></li>' for x in picks)
    pages = {
        '404.html': ('页面不存在', '这一页没有找到', '链接可能写错了，或者那一期换了地址。试试搜索，或者从最新的几篇精读读起。', True),
        'offline.html': ('离线', '现在没有网络', '读过的文章已为你离线保存，可以返回上一页继续阅读；联网后会自动恢复。', False),
    }
    for fn, (t, h1, dek, show) in pages.items():
        body = f"""<main class="wrap ep-main special" id="main">
  <header class="ep-head"><div class="ep-kicker">{t}</div><h1 class="ep-title">{h1}</h1><p class="ep-dek">{dek}</p>
  <p class="special-actions"><a class="more-btn" href="/">回到首页</a><a class="more-btn ghost" href="/#search">搜索单集</a></p></header>
  {f'<section class="special-list"><h2>最新精读</h2><ol>{links}</ol></section>' if show else ''}
</main>"""
        html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="color-scheme" content="light dark">
<meta name="theme-color" content="#ffffff">
<meta name="robots" content="noindex">
<title>{t} · {SITE_NAME}</title>
{THEME_SCRIPT}
{HEAD_EXTRA}
<link rel="stylesheet" href="/style.css?v={CSS_V}">
</head>
<body>
{SITE_HEADER}
{body}
{SITE_FOOTER}
</body>
</html>
"""
        (SITE / fn).write_text(html)
    print('404 / offline pages: ok')


def main():
    eps = build_data()
    sync_index(eps)
    build_archive(eps)
    for i, x in enumerate(eps):
        build_page(i, eps)
    print(f'ep pages: {len(list((SITE / "ep").glob("*/index.html")))}')
    build_sitemap(eps)
    build_disclaimer()
    build_llms(eps)
    build_feed(eps)
    build_special_pages(eps)
    try:
        import brand
        brand.build_og(eps, _strip_no, norm_zh)
    except ImportError as e:  # 没装 Pillow 时跳过分享卡片，不影响站点构建
        print('og cards skipped:', e)
    clean_stale(eps)


if __name__ == '__main__':
    main()
