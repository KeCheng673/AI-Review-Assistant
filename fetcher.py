"""文献摘要抓取适配器。

引擎优先级（可用 --engine 强制指定）
------------------------------------
auto     : CrossRef → OpenAlex → browser-act
ba       : 只走 browser-act（用户要求的默认主引擎）
api      : 只走 CrossRef / OpenAlex（最快，适合批量预筛）

设计要点
--------
1. 任何一条都必须做「标题相似度校验」——检索源经常返回同主题的另一篇，
   抓错摘要比抓不到危害大得多。
2. 抓不到就如实返回 status=未获取 + 原因，绝不编造摘要。
3. 抓取失败不中断整批：逐篇记录，最后汇总。
"""
from __future__ import annotations

import difflib
import json
import os
import re
import shutil
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
TITLE_MIN_RATIO = 0.82          # 标题相似度门槛，低于此判为「不是同一篇」
# CrossRef / OpenAlex 的 polite pool：带上邮箱能显著降低 429
MAILTO = os.environ.get("REVIEWER_MAILTO") or "academic-reviewer@example.org"


# ---------------------------------------------------------------- 工具

def norm_title(s):
    return re.sub(r"[\s\u3000·，,。.、：:；;（）()\-—_《》\"'“”‘’/]", "", s or "").lower()


def title_match(a, b):
    """返回 (是否同一篇, 相似度)。"""
    na, nb = norm_title(a), norm_title(b)
    if not na or not nb:
        return False, 0.0
    if na == nb:
        return True, 1.0
    if na in nb or nb in na:
        shorter, longer = sorted((na, nb), key=len)
        if len(shorter) / len(longer) > 0.72:
            return True, 0.95
    r = difflib.SequenceMatcher(None, na, nb).ratio()
    return r >= TITLE_MIN_RATIO, r


def _get(url, timeout=25, as_json=True, retries=2):
    """带 429 退避重试的 GET。"""
    last = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, headers={
            "User-Agent": UA + " (mailto:%s)" % MAILTO,
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
            if as_json:
                return json.loads(raw.decode("utf-8", "replace"))
            return raw.decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (429, 503) and attempt < retries:
                time.sleep(2 + attempt * 2)          # 2s → 4s
                continue
            raise
        except Exception as e:                        # noqa: BLE001
            last = e
            if attempt < retries:
                time.sleep(1.5)
                continue
            raise
    if last:
        raise last
    return None


def _with_mailto(url):
    return url + ("&" if "?" in url else "?") + "mailto=" + urllib.parse.quote(MAILTO)


def _strip_html(s):
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = re.sub(r"&nbsp;?", " ", s)
    s = re.sub(r"&amp;", "&", s)
    s = re.sub(r"&lt;", "<", s)
    s = re.sub(r"&gt;", ">", s)
    s = re.sub(r"&#\d+;", " ", s)
    return re.sub(r"\s{2,}", " ", s).strip()


# ---------------------------------------------------------------- 摘要抽取（HTML）

def extract_abstract(html):
    """从页面 HTML 抽摘要，按可靠性从高到低尝试。返回 (摘要, 抽取方式)。"""
    if not html:
        return "", ""
    # 1) 标准 meta（玛格泰克、Springer、Wiley、OUP 等都用）
    for tag in ("citation_abstract", "dc.description", "DC.Description",
                "description", "twitter:description", "og:description"):
        m = re.search(r'<meta[^>]+name=["\']%s["\'][^>]+content=["\']([^"\']{80,})' % re.escape(tag),
                      html, re.I)
        if not m:
            m = re.search(r'<meta[^>]+content=["\']([^"\']{80,})["\'][^>]+name=["\']%s["\']'
                          % re.escape(tag), html, re.I)
        if m:
            txt = _strip_html(m.group(1))
            if len(txt) >= 80:
                return txt, "meta:" + tag
    # 2) JSON-LD
    for m in re.finditer(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', html, re.S | re.I):
        try:
            j = json.loads(m.group(1).strip())
        except Exception:
            continue
        for obj in (j if isinstance(j, list) else [j]):
            if isinstance(obj, dict) and obj.get("abstract"):
                txt = _strip_html(str(obj["abstract"]))
                if len(txt) >= 80:
                    return txt, "json-ld"
    # 3) 常见容器
    for pat in (r'<div[^>]+(?:id|class)=["\'][^"\']*abstract[^"\']*["\'][^>]*>(.*?)</div>',
                r'<section[^>]+abstract[^>]*>(.*?)</section>',
                r'<p[^>]+class=["\'][^"\']*abstract[^"\']*["\'][^>]*>(.*?)</p>'):
        m = re.search(pat, html, re.S | re.I)
        if m:
            txt = _strip_html(m.group(1))
            if len(txt) >= 80:
                return txt, "container"
    # 4) 中文「摘要」段落
    m = re.search(r"(?:摘\s*要|Abstract)\s*[:：]?\s*</?[^>]*>?(.{80,1200}?)(?:关\s*键\s*词|Keywords|</p>)",
                  html, re.S | re.I)
    if m:
        txt = _strip_html(m.group(1))
        if len(txt) >= 80:
            return txt, "zh-abstract"
    return "", ""


# ---------------------------------------------------------------- CrossRef

def crossref_abstract(ref):
    """按 DOI 或题录查 CrossRef。返回 (摘要, 来源说明)。"""
    try:
        if ref.get("doi"):
            j = _get(_with_mailto("https://api.crossref.org/works/" + urllib.parse.quote(ref["doi"])))
            m = j.get("message", {})
            ab = _strip_html(m.get("abstract") or "")
            if ab:
                t = (m.get("title") or [""])[0]
                ok, r = title_match(ref.get("title"), t) if t else (True, 1.0)
                if ok:
                    return ab, "crossref:doi"
            return "", "crossref:doi-无摘要"
        q = " ".join(x for x in (ref.get("title"), ref.get("venue"), ref.get("year")) if x)
        j = _get(_with_mailto("https://api.crossref.org/works?query.bibliographic=%s&rows=5"
                              % urllib.parse.quote(q)))
        for it in j.get("message", {}).get("items", []):
            t = (it.get("title") or [""])[0]
            ok, r = title_match(ref.get("title"), t)
            ab = _strip_html(it.get("abstract") or "")
            if ok and ab:
                return ab, "crossref:bibliographic(%.2f)" % r
        return "", "crossref:未命中"
    except Exception as e:
        return "", "crossref:异常 %s" % str(e)[:60]


# ---------------------------------------------------------------- OpenAlex

def _inv_to_text(inv):
    """OpenAlex 的摘要倒排索引还原。英文必须用空格连接，否则变成一长串。"""
    if not inv:
        return ""
    pos = {}
    for w, idxs in inv.items():
        for i in idxs:
            pos[i] = w
    txt = " ".join(pos[i] for i in sorted(pos))
    # 收尾清理：标点前多余空格
    txt = re.sub(r"\s+([,.;:!?)\]])", r"\1", txt)
    txt = re.sub(r"\(\s+", "(", txt)
    return txt.strip()


def openalex_abstract(ref):
    """按标题查 OpenAlex，必须标题高相似才采用。"""
    try:
        q = ref.get("title") or ""
        if not q:
            return "", "openalex:无标题"
        j = _get(_with_mailto("https://api.openalex.org/works?search=%s&per_page=25"
                              % urllib.parse.quote(q)))
        best = None
        for r in j.get("results", []):
            ok, ratio = title_match(q, r.get("title"))
            if not ok:
                continue
            ab = _inv_to_text(r.get("abstract_inverted_index"))
            if ab and len(ab) >= 80:
                if not best or ratio > best[0]:
                    best = (ratio, ab)
        if best:
            return best[1], "openalex(%.2f)" % best[0]
        return "", "openalex:未命中"
    except Exception as e:
        return "", "openalex:异常 %s" % str(e)[:60]


# ---------------------------------------------------------------- browser-act

def find_browser_act():
    p = shutil.which("browser-act")
    if p:
        return p
    cand = Path.home() / ".local" / "bin" / "browser-act.exe"
    if cand.is_file():
        return str(cand)
    cand2 = Path.home() / ".local" / "bin" / "browser-act"
    return str(cand2) if cand2.is_file() else ""


def ba_extract(url, timeout=90, content_type="html"):
    """调 browser-act stealth-extract 抓页面。返回 (正文, 错误)。

    注意两个实测坑：
      * 必须去掉 PYTHONPATH，否则 Hermes venv 会污染它；
      * 它会把 HTTP 500 当失败，但内容其实是完整的（玛格泰克系统），
        因此认 stdout 有内容就算成功。
    """
    exe = find_browser_act()
    if not exe:
        return "", "browser-act 未安装"
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["PATH"] = str(Path.home() / ".local" / "bin") + os.pathsep + env.get("PATH", "")
    cmd = [exe, "stealth-extract", url, "--content-type", content_type]
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return "", "browser-act 超时 %ds" % timeout
    except Exception as e:
        return "", "browser-act 异常 %s" % str(e)[:80]
    out = (p.stdout or b"").decode("utf-8", "replace")
    err = (p.stderr or b"").decode("utf-8", "replace")
    if out.strip():
        return out, ""
    return "", (err.strip().splitlines() or ["空响应"])[0][:160]


def ba_abstract(ref):
    """走 browser-act：有 DOI 就打 doi.org，否则不做（无 DOI 需要期刊站内检索）。"""
    if not ref.get("doi"):
        return "", "browser-act:无 DOI 暂不支持（需期刊站内检索）"
    url = "https://doi.org/" + ref["doi"]
    html, err = ba_extract(url)
    if not html:
        return "", "browser-act:" + err
    ab, how = extract_abstract(html)
    if ab:
        return ab, "browser-act:doi.org(%s)" % how
    return "", "browser-act:页面无可用摘要"


# ---------------------------------------------------------------- CNKI（登录态）

BA_SESSION = os.environ.get("REVIEWER_BA_SESSION") or "cnki"
ADVSEARCH_URL = "https://kns.cnki.net/kns8s/AdvSearch"
# 专业检索：TI='标题' 精确限定篇名。主题检索会把「前半段相同」的别的文章排在前面
EXPERT_URL = "https://kns.cnki.net/kns8s/AdvSearch?type=expert"
# CNKI 只有一个浏览器会话，必须串行；并发会互相覆盖页面
_CNKI_LOCK = __import__("threading").Lock()
# 关掉「手机号认证」浮层，它会把正文挡住
HIDE_POPUP_JS = (
    "var n=0;document.querySelectorAll('div,section').forEach(function(e){"
    "if(e.innerText&&e.innerText.indexOf('手机号认证')>=0&&e.innerText.length<400"
    "&&e.tagName!=='BODY'){e.style.display='none';n++}});'hidden '+n"
)
READ_ABSTRACT_JS = (
    "var e=document.querySelector('#ChDivSummary');e?e.innerText:''"
)


def ba_session(args, timeout=90):
    """在 browser-act 的 CNKI 会话里执行一条命令，返回 (stdout, stderr)。"""
    exe = find_browser_act()
    if not exe:
        return "", "browser-act 未安装"
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["PATH"] = str(Path.home() / ".local" / "bin") + os.pathsep + env.get("PATH", "")
    cmd = [exe, "--session", BA_SESSION] + list(args)
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return "", "timeout %ds" % timeout
    except Exception as e:                                    # noqa: BLE001
        return "", "异常 %s" % str(e)[:80]
    return ((p.stdout or b"").decode("utf-8", "replace"),
            (p.stderr or b"").decode("utf-8", "replace"))


CNKI_STATUS_JS = (
    "(function(){"
    "var m=document.cookie.match(/Ecp_LoginStuts=([^;]*)/);"
    "var u='',n='';"
    "if(m){try{var j=JSON.parse(decodeURIComponent(m[1]));u=j.UserName||'';n=decodeURIComponent(j.ShowName||'')}catch(e){}}"
    "return JSON.stringify({user:u,org:n,title:document.title.slice(0,40),url:location.href.slice(0,60)});"
    "})()"
)


def cnki_status(navigate_home=False):
    """检查 CNKI 会话是否在线、是否已登录（读 Ecp_LoginStuts cookie）。

    注意：cookie 是按域隔离的，浏览器若停在别的网站（如 ScienceDirect），
    document.cookie 里没有 CNKI 的键，所以要先把页面切回 CNKI 再读。
    """
    if navigate_home:
        ba_session(["navigate", "https://www.cnki.net/"], 45)
        ba_session(["wait", "stable"], 25)
    out, err = ba_session(["eval", CNKI_STATUS_JS], timeout=25)
    if not out.strip():
        return {"session": False, "logged_in": False,
                "error": (err or "会话不存在（浏览器没开或会话已断）").strip()[:160]}
    line = ""
    for ln in reversed(out.strip().splitlines()):
        if ln.strip().startswith("{"):
            line = ln.strip()
            break
    if not line:
        return {"session": True, "logged_in": False, "error": "返回无法解析"}
    try:
        j = json.loads(line)
    except Exception:
        return {"session": True, "logged_in": False, "error": "JSON 解析失败"}
    return {"session": True,
            "logged_in": bool(j.get("user")),
            "account": j.get("user") or "",
            "org": j.get("org") or "",
            "title": (j.get("title") or "")[:40],
            "url": j.get("url") or ""}


def _cnki_abstract_inner(ref, timeout=150):
    """走登录态：高级检索 → 打开第一条 → 读 #ChDivSummary。返回 (摘要, 说明)。"""
    title = (ref.get("title") or "").strip()
    if not title:
        return "", "cnki:无标题"

    # ① 先切回 CNKI 域（顺带把登录态检查做在正确域名下）
    ba_session(["navigate", EXPERT_URL], 45)
    ba_session(["wait", "stable"], 30)
    st = cnki_status()
    if not st.get("session"):
        return "", "cnki:会话不存在（请先打开知网浏览器并登录）"
    if not st.get("logged_in"):
        return "", "cnki:未登录（%s）" % (st.get("error") or "Ecp_LoginStuts 缺失")

    ba_session(["eval", HIDE_POPUP_JS], 20)

    # ② 填表达式 + 检索（单引号要转义，否则语法失效）
    expr = "TI='%s'" % title.replace("'", "''")
    ba_session(["input", "--selector", "textarea[maxlength='18000']", "--text", expr], 60)
    ba_session(["click", "--selector", "input[value='检索']"], 30)
    time.sleep(2.5)
    ba_session(["wait", "stable"], 30)
    ba_session(["eval", HIDE_POPUP_JS], 20)

    # ③ 结果页里找文章链接（markdown 是 [标题](链接) 形式）
    md, _ = ba_session(["get", "markdown"], 60)
    pairs = re.findall(
        r"\[([^\]\n]{8,150})\]\((https://kns\.cnki\.net/kcms2/article/abstract\?v=[^)\s]+)\)",
        md or "")
    if not pairs:
        return "", "cnki:检索无结果"
    best = None
    for t, lk in pairs:
        ok, ratio = title_match(title, t.strip())
        if best is None or ratio > best[0]:
            best = (ratio, t.strip(), lk)
    ratio, hit_title, link = best
    # 中文期刊里「前半段相同、后半段不同」的标题很多，门槛必须高，
    # 否则会把别人的摘要安到这篇头上（比抓不到更糟）
    if ratio < 0.80:
        return "", "cnki:未精确命中（最像的是 %.2f：%s）" % (ratio, hit_title[:28])

    # ④ 打开文章页并读摘要
    ba_session(["navigate", link], 45)
    ba_session(["wait", "stable"], 30)
    time.sleep(1.5)
    ba_session(["eval", HIDE_POPUP_JS], 20)
    ab, err = ba_session(["eval", READ_ABSTRACT_JS], 40)
    ab = (ab or "").strip()
    # stdout 可能带一行杂讯，取最长的含中文的那段
    if ab and ("'" in ab or ab.startswith("{")):
        cands = [x for x in ab.splitlines() if len(x) > 60]
        ab = max(cands, key=len) if cands else ""
    if len(ab) >= 80:
        return ab, "cnki:#ChDivSummary"
    return "", "cnki:页面无摘要（%s）" % (err or "空")[:60]


def cnki_abstract(ref, timeout=150):
    """串行包装：同一浏览器会话不能被并发操作。"""
    with _CNKI_LOCK:
        return _cnki_abstract_inner(ref, timeout)


def crossref_lookup_doi(ref):
    """按题录查 DOI（给需要 DOI 才能打开的站点用）。"""
    title = ref.get("title") or ""
    if not title:
        return "", "无标题"
    q = " ".join(x for x in (title, ref.get("venue"), ref.get("year")) if x)
    try:
        j = _get(_with_mailto(
            "https://api.crossref.org/works?query.bibliographic=%s&rows=5&select=DOI,title"
            % urllib.parse.quote(q)), timeout=25)
    except Exception as e:                                    # noqa: BLE001
        return "", "crossref 异常 %s" % str(e)[:40]
    for it in j.get("message", {}).get("items", []):
        t = (it.get("title") or [""])[0]
        ok, r = title_match(title, t)
        if ok:
            return it["DOI"], "crossref(%.2f)" % r
    return "", "crossref 未命中"


# 这些前缀的出版商用 Cloudflare，stealth-extract 会被挡；真浏览器能过
CF_DOI_PREFIXES = ("10.1016/", "10.1002/", "10.1111/", "10.1080/", "10.1177/")


def cloudflare_abstract(ref):
    """Elsevier/Wiley/T&F 等：查 DOI → 真浏览器打开 doi.org → 读 #abstracts。"""
    doi = (ref.get("doi") or "").strip()
    src = "自带 DOI"
    if not doi:
        doi, src = crossref_lookup_doi(ref)
    if not doi:
        return "", "cf:%s" % src
    if not doi.startswith(CF_DOI_PREFIXES):
        return "", "cf:非 Cloudflare 站（%s）" % doi[:24]

    with _CNKI_LOCK:                    # 与 CNKI 共用同一浏览器会话，必须串行
        ba_session(["navigate", "https://doi.org/" + doi], 60)
        ba_session(["wait", "stable"], 30)
        time.sleep(2.5)
        out, err = ba_session(
            ["eval", "var el=document.querySelector('#abstracts');el?el.innerText:''"], 40)
    txt = (out or "").strip()
    i = txt.find("Abstract")            # 去掉前面的 Highlights
    if i > 0:
        txt = txt[i + len("Abstract"):].strip()
    txt = re.sub(r"\s{2,}", " ", txt)
    if len(txt) >= 80:
        return txt, "sciencedirect(%s)" % src
    return "", "cf:页面无摘要（%s）" % (err or "空")[:50]


def pick_browser_id():
    """挑一个 chrome 类型的浏览器用于登录（优先名字里带 cnki / deep-probe 的）。"""
    exe = find_browser_act()
    if not exe:
        return ""
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["PATH"] = str(Path.home() / ".local" / "bin") + os.pathsep + env.get("PATH", "")
    try:
        p = subprocess.run([exe, "browser", "list"], capture_output=True,
                           timeout=25, env=env)
    except Exception:                                        # noqa: BLE001
        return ""
    out = (p.stdout or b"").decode("utf-8", "replace")
    cands = re.findall(r'id=(\S+)\s+name="([^"]*)"\s+type=(\S+)', out)
    chromes = [(bid, nm) for bid, nm, tp in cands if tp == "chrome"]
    if not chromes:
        return ""
    for bid, nm in chromes:
        if "cnki" in nm.lower() or "知网" in nm:
            return bid
    for bid, nm in chromes:
        if "deep-probe" in nm.lower():
            return bid
    return chromes[0][0]


def open_cnki_login():
    """关掉现有会话，用【有头】模式打开知网登录页，让用户手动登录。

    cookie 存在浏览器 profile 里，重开会话不会丢已登录状态。
    """
    exe = find_browser_act()
    if not exe:
        return {"ok": False, "error": "browser-act 未安装"}
    bid = pick_browser_id()
    if not bid:
        return {"ok": False,
                "error": "没有可用的 chrome 类型浏览器，请先在 browser-act 里创建一个"}

    with _CNKI_LOCK:                      # 与抓取共用会话，必须串行
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        env["PATH"] = str(Path.home() / ".local" / "bin") + os.pathsep + env.get("PATH", "")
        # 先关旧会话（不带 --session 前缀）
        try:
            subprocess.run([exe, "session", "close", BA_SESSION],
                           capture_output=True, timeout=25, env=env)
        except Exception:                                    # noqa: BLE001
            pass
        cmd = [exe, "--session", BA_SESSION, "browser", "open", bid,
               "https://www.cnki.net/", "--headed"]
        try:
            p = subprocess.run(cmd, capture_output=True, timeout=90, env=env)
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "打开浏览器超时"}
        out = (p.stdout or b"").decode("utf-8", "replace")
        err = (p.stderr or b"").decode("utf-8", "replace")
        if "session_name" not in out and not out.strip():
            return {"ok": False, "error": (err or "打开失败").strip()[:200]}

        ba_session(["wait", "stable"], 25)
        # 尽量把窗口切到前台（Windows）
        try:
            ps = ("Add-Type -AssemblyName Microsoft.VisualBasic;"
                  "$p=Get-Process chrome -ErrorAction SilentlyContinue | "
                  "Where-Object {$_.MainWindowHandle -ne 0} | Select-Object -First 1;"
                  "if($p){[Microsoft.VisualBasic.Interaction]::AppActivate($p.Id)}")
            subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, timeout=20)
        except Exception:                                    # noqa: BLE001
            pass

        st = cnki_status()
    return {"ok": True, "browser_id": bid, "headed": True,
            "logged_in": st.get("logged_in", False),
            "account": st.get("account", ""),
            "org": st.get("org", ""),
            "msg": "已打开知网窗口，请在其中完成登录（个人扫码或机构登录）"}


# ---------------------------------------------------------------- 编排

def fetch_one(ref, engine="auto"):
    """抓一篇。返回 dict：abstract / source / status / note。

    分流规则：中文文献在 auto/cnki 模式下优先走 CNKI 登录态（覆盖中文期刊），
    英文文献走 CrossRef / OpenAlex / DOI 直连。
    """
    title = ref.get("title") or ""
    if not title:
        return {"abstract": "", "source": "", "status": "未获取", "note": "标题未解析出"}

    lang = ref.get("lang") or "en"
    tried = []

    if engine in ("cnki", "auto") and lang == "zh":
        ab, why = cnki_abstract(ref)
        tried.append(why)
        if ab and len(ab) >= 80:
            return {"abstract": re.sub(r"\s{2,}", " ", ab).strip(),
                    "source": why, "status": "已获取", "note": ""}

    if engine == "ba":
        chain = [ba_abstract]
    elif engine == "cnki":
        chain = []
    elif engine == "api":
        chain = [crossref_abstract, openalex_abstract]
    else:
        # auto：API 先试（快），失败再上真浏览器过 Cloudflare（慢但能拿到 Elsevier 摘要）
        chain = [crossref_abstract, openalex_abstract, cloudflare_abstract, ba_abstract]

    for fn in chain:
        ab, why = fn(ref)
        tried.append(why)
        if ab and len(ab) >= 80:
            return {"abstract": re.sub(r"\s{2,}", " ", ab).strip(),
                    "source": why, "status": "已获取", "note": ""}
    return {"abstract": "", "source": "", "status": "未获取",
            "note": "；".join(tried)[:200]}


def fetch_batch(refs, engine="auto", limit=None):
    """逐篇抓，返回 [{ref, abstract, source, status, note}]。"""
    out = []
    for i, r in enumerate(refs, 1):
        if limit and i > limit:
            break
        res = fetch_one(r, engine=engine)
        res["ref"] = r
        out.append(res)
    return out
