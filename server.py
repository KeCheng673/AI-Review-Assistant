#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
学术审稿专家 · 工作台本地服务（零依赖，仅标准库）

作用：把 workbench 前端（index.html）与技能里的真实脚本打通。
  - 静态托管 index.html 等文件
  - 提供解析 / 检索 / DOI 核验 / 意见草稿 / 导出 的 HTTP 接口

用法：
  python server.py                 # 默认 http://127.0.0.1:8770
  python server.py --port 8771     # 指定端口
  python server.py --open          # 启动后自动打开浏览器

前端两种模式：
  - 直接双击 index.html          → 演示模式（mock 数据，不发请求）
  - 经本服务打开 http://127.0.0.1:8770 → 真实模式（调用技能脚本）
"""
import argparse
import concurrent.futures
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
WORKSPACE_ROOT = BASE_DIR / "review-workspace"
MAX_BODY = 64 * 1024 * 1024          # 上传上限 64MB
SUBPROC_TIMEOUT = 180                # 单个脚本最长执行时间（秒）

sys.path.insert(0, str(BASE_DIR))
import llm                                                       # noqa: E402
import refs as refs_mod                                          # noqa: E402
import kb as kb_mod                                              # noqa: E402
import fetcher                                                   # noqa: E402

# 元数据补全用的礼貌标识（Crossref 要求可联系的 UA / mailto）
MAILTO = os.environ.get("REVIEWER_MAILTO") or "academic-reviewer@example.org"

# ---------------------------------------------------------------- 抓取任务状态
_FETCH_LOCK = __import__("threading").Lock()
_FETCH_JOB = {"running": False, "done": 0, "total": 0, "succeeded": 0, "failed": 0,
              "rows": [], "engine": "", "started": "", "finished": "", "error": ""}


def _fetch_snapshot():
    with _FETCH_LOCK:
        return {k: (_FETCH_JOB[k] if k != "rows" else list(_FETCH_JOB["rows"]))
                for k in _FETCH_JOB}
UA = "AcademicReviewWorkbench/0.2 (mailto:%s)" % MAILTO

SKILL_CANDIDATES = [
    os.environ.get("REVIEW_SKILL_DIR"),
    str(Path.home() / ".wpscomate" / "agent" / "skills" / "custom" / "academic-reviewer"),
    str(Path.home() / ".wpscomate" / "agent" / "skills" / "official" / "academic-reviewer"),
    str(BASE_DIR / "skills" / "academic-reviewer"),
]


def find_skill_dir():
    for c in SKILL_CANDIDATES:
        if not c:
            continue
        p = Path(c)
        if (p / "scripts" / "manuscript_parse.py").is_file():
            return p
    return None


SKILL_DIR = find_skill_dir()
PY = sys.executable


# ---------------------------------------------------------------- 基础工具
def slugify(title: str) -> str:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    ascii_part = re.sub(r"[^0-9A-Za-z]+", "-", title or "").strip("-").lower()[:24]
    return "paper-" + (ascii_part + "-" if ascii_part else "") + stamp[-6:] + "-" + stamp[:8]


def safe_path(rel: str) -> Path:
    """把相对路径限制在工作区内，防目录穿越。"""
    rel = (rel or "").replace("\\", "/").lstrip("/")
    p = (WORKSPACE_ROOT / rel).resolve()
    root = WORKSPACE_ROOT.resolve()
    if not str(p).startswith(str(root)):
        raise ValueError("路径越界: " + rel)
    return p


def safe_ws_path(rel):
    """把相对路径解析到「当前工作区」内（仍限制在工作区根之下）。"""
    rel = (rel or "").replace("\\", "/").lstrip("/")
    root = current_ws() or WORKSPACE_ROOT
    p = (root / rel).resolve()
    if not str(p).startswith(str(WORKSPACE_ROOT.resolve())):
        raise ValueError("路径越界: " + rel)
    return p
DOI_RE = re.compile(r"10\.[0-9]{4,9}/[A-Za-z0-9._()/:;-]+")


_PDF_TEXT_CACHE = {}                       # {(绝对路径): (mtime, 纯文本)}


_PLACEHOLDER_KEYS = {"", "未获取", "unknown", "none", "null", "n/a", "—", "-", "无"}


def safe_citekey(it, used):
    """citekey 缺失或为占位符时自动生成，并保证同一批内不重名。

    模型在题录缺字段时常把 citekey 填成「未获取」，直接当文件名会让整批卡片
    互相覆盖成同一个文件（实测 8 张卡只剩 1 个）。
    """
    raw = str(it.get("citekey") or "").strip()
    if raw.lower() in _PLACEHOLDER_KEYS:
        raw = ""
    if not raw:
        doi = norm_doi(it.get("doi") or "")
        title = str(it.get("title") or "").strip()
        year = str(it.get("year") or "").strip()
        if doi:
            raw = doi.split("/")[-1]
        elif title:
            raw = re.sub(r"[^\w\u4e00-\u9fa5]+", "", title)[:24]
        else:
            raw = "card"
        if year and year not in raw:
            raw = "%s_%s" % (raw, year)
    raw = re.sub(r'[\\/:*?"<>|\s]+', "-", raw).strip("-.")[:60] or "card"
    base, n = raw, 2
    while raw in used:
        raw = "%s_v%d" % (base, n)
        n += 1
    used.add(raw)
    return raw


def manuscript_text(path):
    """读取稿件纯文本，用于抽取稿件自身引用的 DOI。
    .docx 走 OOXML 解包（仅标准库）；.md/.txt/.html 直接读；.pdf 交由技能脚本处理。"""
    p = Path(path)
    if p.suffix.lower() == ".docx":
        try:
            import zipfile
            from html import unescape
            with zipfile.ZipFile(p) as z:
                parts = []
                for name in z.namelist():
                    if re.match(r"word/(document|header\d*|footer\d*|footnotes|endnotes)\.xml$", name):
                        xml = z.read(name).decode("utf-8", "ignore")
                        for chunk in re.split(r"</w:p>", xml):
                            ts = re.findall(r"<w:t[^>]*>(.*?)</w:t>", chunk, re.S)
                            if ts:
                                parts.append(unescape("".join(ts)))
                return "\n".join(parts)
        except Exception:
            return ""
    if p.suffix.lower() == ".pdf":
        # PyMuPDF 优先：中文 PDF 常缺 ToUnicode，pypdf 会整页提成空白。
        # 解析结果按 (路径, mtime) 缓存，避免每次起草都重解大文件。
        try:
            mt = p.stat().st_mtime
        except OSError:
            return ""
        key = str(p.resolve())
        hit = _PDF_TEXT_CACHE.get(key)
        if hit and hit[0] == mt:
            return hit[1]
        text = ""
        try:
            import fitz                     # PyMuPDF
            with fitz.open(str(p)) as doc:
                text = "\n".join(pg.get_text("text") for pg in doc)
        except Exception:
            text = ""
        if not text.strip():                # 回退：pypdf（未安装则跳过）
            try:
                from pypdf import PdfReader
                text = "\n".join((pg.extract_text() or "") for pg in PdfReader(str(p)).pages)
            except Exception:
                text = ""
        _PDF_TEXT_CACHE[key] = (mt, text)
        return text
    try:
        return p.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""


def extract_dois(text):
    """从稿件文本抽取去重后的 DOI 列表（含稿件自引）。"""
    out = []
    for m in DOI_RE.finditer(text or ""):
        d = norm_doi(m.group()).rstrip(".,;:)]}>")
        if d and d not in out:
            out.append(d)
    return out


def ws_manuscript(ws):
    d = ws / "00_manuscript"
    if not d.is_dir():
        return None
    cands = sorted([x for x in d.iterdir()
                    if x.is_file() and x.suffix.lower() in {".docx", ".md", ".txt", ".html", ".htm", ".pdf"}])
    return cands[0] if cands else None


def current_ws():
    idx = WORKSPACE_ROOT / ".current"
    if idx.is_file():
        name = idx.read_text(encoding="utf-8").strip()
        p = WORKSPACE_ROOT / name
        if p.is_dir():
            return p
    return None


def set_current_ws(name: str):
    WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)
    (WORKSPACE_ROOT / ".current").write_text(name, encoding="utf-8")


def ws_meta(ws: Path) -> dict:
    f = ws / "meta.json"
    if f.is_file():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"title": ws.name, "domain": "", "language": "zh"}


def save_meta(ws: Path, meta: dict):
    (ws / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def run_script(script: str, args, cwd=None):
    """执行技能脚本，返回 (ok, stdout, stderr)。"""
    if not SKILL_DIR:
        return False, "", "未找到技能目录（可用环境变量 REVIEW_SKILL_DIR 指定）"
    cmd = [PY, str(SKILL_DIR / "scripts" / script)] + [str(a) for a in args]
    try:
        r = subprocess.run(cmd, cwd=str(cwd or WORKSPACE_ROOT),
                           capture_output=True, timeout=SUBPROC_TIMEOUT)
        out = r.stdout.decode("utf-8", "replace")
        err = r.stderr.decode("utf-8", "replace")
        return r.returncode == 0, out, err
    except subprocess.TimeoutExpired:
        return False, "", f"脚本超时（>{SUBPROC_TIMEOUT}s）"
    except Exception as e:                                    # noqa: BLE001
        return False, "", f"脚本执行失败: {e}"




def run_json(script: str, args):
    """执行并解析 JSON 输出。"""
    ok, out, err = run_script(script, args)
    if not ok:
        return None, (err or out or "未知错误")
    txt = out.strip()
    start = min([i for i in (txt.find("{"), txt.find("[")) if i >= 0], default=-1)
    if start < 0:
        return None, "脚本未返回 JSON：" + txt[:400]
    try:
        return json.loads(txt[start:]), None
    except Exception as e:                                    # noqa: BLE001
        return None, f"JSON 解析失败: {e} | {txt[:300]}"


def read_text(p: Path, limit=400000):
    try:
        return p.read_text(encoding="utf-8", errors="replace")[:limit]
    except Exception:
        return ""


def norm_doi(d):
    return (d or "").strip().lower().replace("https://doi.org/", "")


def to_int(v, default=0):
    """year / cited_by 可能是字符串（Crossref 会返回带日期前缀的值），统一转整数。"""
    if v is None or v == "":
        return default
    m = re.search(r"[0-9]+", str(v))
    return int(m.group()) if m else default


def norm_title(t):
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", (t or "").lower())


def tree_of(ws: Path):
    items = []
    if not ws:
        return items
    for p in sorted(ws.rglob("*")):
        if p.is_dir() or p.name == "meta.json":
            continue
        if p.name.startswith("."):
            continue
        rel = p.relative_to(ws)
        items.append({
            "path": str(rel).replace("\\", "/"),
            "name": p.name,
            "dir": str(rel.parent).replace("\\", "/") or ".",
            "size": p.stat().st_size,
            "mtime": time.strftime("%Y-%m-%d %H:%M", time.localtime(p.stat().st_mtime)),
        })
    items.sort(key=lambda x: (x["path"].count("/"), x["path"]))
    return items


# ---------------------------------------------------------------- 正文规范化与模型上下文
HEAD_HINT = re.compile(r"heading|标题|^Title$", re.I)
SECTION_NAMES = ("摘要", "abstract", "关键词", "keywords", "引言", "introduction", "方法", "method",
                 "数据", "结果", "result", "讨论", "discussion", "结论", "conclusion",
                 "参考文献", "references", "致谢", "附录")


def manuscript_paragraphs(path):
    """按顺序返回 [(text, style)]，用于正文规范化与段落引用。
    .docx 解析 w:pStyle 以识别标题；其余格式按行为一段。"""
    p = Path(path)
    out = []
    if p.suffix.lower() == ".docx":
        try:
            import zipfile
            from html import unescape
            with zipfile.ZipFile(p) as z:
                xml = z.read("word/document.xml").decode("utf-8", "ignore")
            for chunk in re.split(r"</w:p>", xml):
                m = re.search(r'<w:pStyle w:val="([^"]+)"', chunk)
                style = m.group(1) if m else ""
                ts = re.findall(r"<w:t[^>]*>(.*?)</w:t>", chunk, re.S)
                text = re.sub(r"\s+", " ", unescape("".join(ts))).strip()
                if text:
                    out.append((text, style))
        except Exception:
            return []
        return out
    txt = manuscript_text(p)
    for line in txt.splitlines():
        s = line.strip()
        if not s:
            continue
        head = bool(re.match(r"^#{1,6}\s+", s)) or s in SECTION_NAMES or s.rstrip("：:").lower() in SECTION_NAMES
        out.append((re.sub(r"^#{1,6}\s+", "", s), "Heading" if head else ""))
    return out


def _is_heading(text, style):
    if style and HEAD_HINT.search(style):
        return True
    key = (text or "").strip().rstrip("：:").lower()
    return key in {s.lower() for s in SECTION_NAMES} and len(text or "") <= 30















# ---------------------------------------------------------------- 静态模板：检索计划
# ---------------------------------------------------------------- 静态模板：检索计划








# ---------------------------------------------------------------- 模板：审稿意见草稿


# ---------------------------------------------------------------- HTTP 服务
STATIC_EXT = {".html", ".css", ".js", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp", ".md"}


class Handler(BaseHTTPRequestHandler):
    server_version = "ReviewWorkbench/1.0"

    def log_message(self, fmt, *args):                        # noqa: A003
        if "/api/" in self.path:
            sys.stderr.write("[api] %s %s\n" % (self.command, self.path))

    # ---------- 响应工具 ----------
    def _send(self, code, body: bytes, ctype="application/json; charset=utf-8", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError):
            pass

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def _err(self, msg, code=400):
        self._json({"ok": False, "error": msg}, code)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            raise ValueError("请求体过大")
        return self.rfile.read(n) if n else b""

    def _json_body(self):
        raw = self._body()
        return json.loads(raw.decode("utf-8")) if raw else {}

    def _query(self):
        return urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)

    # ---------- GET ----------
    def do_GET(self):                                         # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        if path.startswith("/api/"):
            return self.api_get(path)
        return self.serve_static(path)

    def serve_static(self, path):
        rel = urllib.parse.unquote(path.lstrip("/")) or "index.html"
        p = (BASE_DIR / rel).resolve()
        if not str(p).startswith(str(BASE_DIR)) or p.suffix.lower() not in STATIC_EXT or not p.is_file():
            return self._err("找不到资源: " + rel, 404)
        ctype = {
            ".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8", ".md": "text/markdown; charset=utf-8",
            ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".svg": "image/svg+xml", ".gif": "image/gif", ".webp": "image/webp",
            ".ico": "image/x-icon",
        }.get(p.suffix.lower(), "application/octet-stream")
        body = p.read_bytes()
        if p.name == "index.html" and b"</body>" in body:
            inject = (b'<script src="/real-mode.js"></script>' + bytes([10]) + b"</body>")

            body = body.replace(b"</body>", inject, 1)
        self._send(200, body, ctype)

    def api_get(self, path):
        ws = current_ws()
        if path == "/api/state":
            return self._json({
                "ok": True,
                "mode": "real",
                "skill_dir": str(SKILL_DIR) if SKILL_DIR else None,
                "python": PY,
                "workspace_root": str(WORKSPACE_ROOT),
                "current": ({**ws_meta(ws), "slug": ws.name} if ws else None),
                # 下划线开头的是共享资源（_library 跨稿件知识库），不算「工作区」
                "workspaces": sorted([p.name for p in WORKSPACE_ROOT.iterdir()
                                      if p.is_dir() and not p.name.startswith("_")])
                if WORKSPACE_ROOT.is_dir() else [],
                "features": {
                    "parse": bool(SKILL_DIR),
                    "search_sources": ["openalex", "crossref", "europepmc"],
                    "unsupported_sources": {
                        "PubMed": "未接入 PubMed E-utilities（可用 Europe PMC 覆盖大部分生物医学文献）",
                        "智慧芽 MCP": "需在 Comate 连接器中启用智慧芽 MCP 后方可调用",
                    },
                },
            })
        if path == "/api/llm/status":
            st = llm.status(BASE_DIR)
            return self._json({"ok": True, **st})
        if path == "/api/refs":
            return self._json(self._refs_data())
        if path == "/api/kb":
            return self._json(self._kb_data())
        if path == "/api/abstracts/progress":
            return self._json(_fetch_snapshot())
        if path == "/api/llm2/status":
            return self.api_llm2_status()
        if path == "/api/cnki/status":
            # 浏览器可能停在别的站（cookie 按域隔离），先切回知网再读
            return self._json({"ok": True, **fetcher.cnki_status(navigate_home=True)})
        if path == "/api/tree":
            return self._json({"ok": True, "slug": ws.name if ws else None, "files": tree_of(ws)})
        if path == "/api/file":
            q = self._query()
            rel = (q.get("path") or [""])[0]
            try:
                p = safe_ws_path(rel)
            except ValueError as e:
                return self._err(str(e), 403)
            if not p.is_file():
                return self._err("文件不存在: " + rel, 404)
            if p.suffix.lower() in {".json", ".md", ".txt", ".csv"}:
                return self._json({"ok": True, "path": rel, "content": read_text(p)})
            return self._err("仅支持预览文本类文件", 415)
        if path == "/api/download":
            q = self._query()
            rel = (q.get("path") or [""])[0]
            try:
                p = safe_ws_path(rel)
            except ValueError as e:
                return self._err(str(e), 403)
            if not p.is_file():
                return self._err("文件不存在: " + rel, 404)
            name = urllib.parse.quote(p.name)
            return self._send(200, p.read_bytes(), "application/octet-stream",
                              {"Content-Disposition": f"attachment; filename*=UTF-8''{name}"})
        return self._err("未知接口: " + path, 404)

    # ---------- POST ----------
    def do_POST(self):                                        # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        try:
            if path == "/api/task":
                return self.api_task()
            if path == "/api/upload":
                return self.api_upload()
            if path == "/api/llm/config":
                return self.api_llm_config()
            if path == "/api/refs/extract":
                return self.api_refs_extract()
            if path == "/api/abstracts/fetch":
                return self.api_abstracts_fetch()
            if path == "/api/kb/build":
                return self.api_kb_build()
            if path == "/api/archive":
                return self.api_archive()
            if path == "/api/meta":
                return self.api_meta()
            if path == "/api/review/draft":
                return self.api_review_draft()
            if path == "/api/review/second":
                return self.api_review_second()
            if path == "/api/llm2/config":
                return self.api_llm2_config()
            if path == "/api/cnki/login":
                return self.api_cnki_login()
        except ValueError as e:
            return self._err(str(e))
        except Exception as e:                                # noqa: BLE001
            import traceback
            traceback.print_exc()
            return self._err("服务端异常: %s" % e, 500)
        return self._err("未知接口: " + path, 404)

    # ---------- 业务接口 ----------
    def api_task(self):
        d = self._json_body()
        title = (d.get("title") or "").strip() or "未命名稿件"
        meta = {
            "title": title,
            "domain": d.get("domain") or "",
            "scenario": d.get("scenario") or "",
            "language": d.get("language") or "zh",
            "focus": d.get("focus") or [],
            "decisions": d.get("decisions") or [],
            "journal": d.get("journal") or "",
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        slug = slugify(title)
        ws = WORKSPACE_ROOT / slug
        args = [str(ws), "--title", title, "--domain", meta["domain"] or "待填写",
                "--language", "en" if meta["language"].lower().startswith("en") else "zh"]
        ok, out, err = run_script("init_review_workspace.py", args, cwd=BASE_DIR)
        if not ok:
            return self._err("工作区初始化失败：" + (err or out)[:500], 500)
        meta["slug"] = slug
        save_meta(ws, meta)
        set_current_ws(slug)
        # 新流程只用 00_manuscript / 01_refs / 02_abstracts / 03_review 四层，
        # 把 init 脚本建的旧目录与模板 README 清掉（新工作区，删了不会丢产物）
        import shutil as _shutil
        for legacy in ("01_parse", "02_search", "03_cards", "04_knowledge_base",
                       "04_experience", "05_review", "06_benchmark", "07_drafts"):
            _shutil.rmtree(ws / legacy, ignore_errors=True)
        try:
            (ws / "README.md").unlink()
        except OSError:
            pass
        kb_mod.ws_dirs(ws)
        return self._json({"ok": True, "slug": slug, "dir": str(ws), "meta": meta,
                           "log": (out or "").strip()[-400:]})

    def api_upload(self):
        ws = current_ws()
        if not ws:
            return self._err("尚未创建审稿任务（请先完成 Step 0）", 409)
        name = urllib.parse.unquote(self.headers.get("X-Filename") or "")
        name = re.sub(r"[\\/:*?\"<>|]+", "_", name).strip() or "manuscript.bin"
        raw = self._body()
        if not raw:
            return self._err("文件内容为空")
        dst = ws / "00_manuscript" / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(raw)
        # 必须把 meta.manuscript 指到新文件，否则后续抽取还读旧稿件
        meta = ws_meta(ws)
        meta["manuscript"] = name
        meta["uploaded_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        save_meta(ws, meta)
        return self._json({"ok": True, "name": name, "size": len(raw),
                           "path": f"00_manuscript/{name}", "saved_to": str(dst),
                           "meta": meta})







    # ---------- 模型层（受约束生成 / Agent 在环） ----------


    def api_llm_config(self):
        """保存模型接入配置，并就地做一次最小连通性测试（不发送稿件内容）。"""
        d = self._json_body()
        cfg = llm.save_config(BASE_DIR, {
            "base_url": (d.get("base_url") or "").strip(),
            "api_key": d.get("api_key") or "",
            "model": (d.get("model") or "").strip(),
            "privacy_mode": d.get("privacy_mode") or "abstract_only",
        })
        st = llm.status(BASE_DIR)
        err = ""
        if cfg.get("provider") != "stub":
            ok, err = llm.test_connection(cfg)
            llm.save_test_result(BASE_DIR, ok, err)
            st = llm.status(BASE_DIR)
            if ok:
                err = ""
        return self._json({"ok": True, "path": "config.json", "connected": st.get("connected"),
                           "provider": cfg.get("provider"), "model": cfg.get("model"),
                           "error": err, "status": st})





    # ============================ 四步框架（新） ============================
    def _refs_data(self):
        ws = current_ws()
        if not ws:
            return {"ok": False, "error": "尚未创建审稿任务"}
        meta = ws_meta(ws)
        d = kb_mod.ws_dirs(ws)
        refs_p = d["refs"] / "refs.json"
        base = {"ok": True, "manuscript": meta.get("manuscript"),
                "domain": meta.get("domain"), "slug": ws.name}
        if not refs_p.is_file():
            return dict(base, extracted=False, stats=None, picked=[])
        blob = json.loads(refs_p.read_text(encoding="utf-8"))
        return dict(base, extracted=True, stats=blob.get("stats"),
                    total=len(blob.get("refs") or []),
                    n=blob.get("n"), seed=blob.get("seed"),
                    picked=blob.get("picked") or [])

    def _kb_data(self):
        ws = current_ws()
        if not ws:
            return {"ok": False, "error": "尚未创建审稿任务"}
        d = kb_mod.ws_dirs(ws)
        kb_md = d["abstracts"] / "kb.md"
        text = kb_md.read_text(encoding="utf-8") if kb_md.is_file() else ""
        count = with_abs = 0
        m = re.search(r"抽中 (\d+) 篇\s*｜\s*已获摘要 \*\*(\d+)\*\*\s*篇", text)
        if m:
            count, with_abs = int(m.group(1)), int(m.group(2))
        return {"ok": True, "count": count,
                "with_abstract": with_abs,
                "cards": [],
                "kb_md": text,
                "fetch": _fetch_snapshot()}

    def api_refs_extract(self):
        """从稿件抽取参考文献并随机抽 N 篇。"""
        ws = current_ws()
        if not ws:
            return self._err("尚未创建审稿任务", 409)
        d = self._json_body()
        n = int(d.get("n") or 10)
        seed = d.get("seed")
        meta = ws_meta(ws)
        name = (meta.get("manuscript") or "").strip()
        pdf = (ws / "00_manuscript" / name) if name else None
        if not pdf or not pdf.is_file():
            # 不静默猜：只有一个文件时才认，多个文件必须让用户重新上传
            cands = [p for p in (ws / "00_manuscript").glob("*") if p.is_file()]
            if len(cands) == 1:
                pdf = cands[0]
                meta["manuscript"] = pdf.name
                save_meta(ws, meta)
            elif not cands:
                return self._err("00_manuscript 里没有稿件，请先在 Step 1 上传", 409)
            else:
                return self._err(
                    "00_manuscript 里有 %d 个文件，无法确定用哪份（%s），请重新上传一次"
                    % (len(cands), "、".join(p.name for p in cands[:3])), 409)
        res = refs_mod.extract_refs(pdf)
        if not res.get("ok"):
            return self._json({"ok": False, "error": res.get("error"),
                               "manuscript": pdf.name,
                               "stats": res.get("stats") or {}})
        # 抽取前清掉上一篇留下的卡片，避免新旧混淆
        d = kb_mod.ws_dirs(ws)
        for old in list(d["abstracts"].glob("*.json")) + list(d["abstracts"].glob("*.md")):
            old.unlink(missing_ok=True)
        saved = kb_mod.save_refs(ws, res, n=n, seed=seed)
        return self._json({"ok": True, "stats": res["stats"],
                           "manuscript": pdf.name,
                           "picked": saved["picked"],
                           "files": saved["files"], "n": n, "seed": seed})

    def api_abstracts_fetch(self):
        """启动后台抓取任务（10 篇 × 多源，可能数分钟）。"""
        global _FETCH_JOB
        ws = current_ws()
        if not ws:
            return self._err("尚未创建审稿任务", 409)
        if _fetch_snapshot()["running"]:
            return self._json({"ok": False, "msg": "已有抓取任务在跑", **_fetch_snapshot()})
        d = self._json_body()
        engine = d.get("engine") or "ba"
        picked = kb_mod.load_sample(ws)
        if not picked:
            return self._err("还没有抽样，请先完成参考文献抽取", 409)
        with _FETCH_LOCK:
            _FETCH_JOB.update({"running": True, "done": 0, "total": len(picked),
                               "succeeded": 0, "failed": 0, "rows": [], "engine": engine,
                               "started": time.strftime("%H:%M:%S"), "finished": "",
                               "error": ""})
        import threading
        threading.Thread(target=self._run_fetch_job, args=(ws, picked, engine),
                         daemon=True).start()
        return self._json({"ok": True, "started": True, "total": len(picked),
                           "engine": engine})

    def _run_fetch_job(self, ws, picked, engine):
        """后台抓取：并行跑，逐篇落卡片与进度。"""
        global _FETCH_JOB
        try:
            # browser-act 每次要起浏览器，并发过高会互相抢占；API 源可放心并发
            workers = 2 if engine in ("ba", "auto") else 3
            cards = []                     # 摘要只在内存累积，最后统一写 kb.md
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
                futs = {ex.submit(fetcher.fetch_one, r, engine): r for r in picked}
                for fut in concurrent.futures.as_completed(futs):
                    r = futs[fut]
                    try:
                        res = fut.result()
                    except Exception as e:                       # noqa: BLE001
                        res = {"abstract": "", "source": "", "status": "未获取",
                               "note": "抓取异常 %s" % str(e)[:100]}
                    card = kb_mod.save_card(ws, r, abstract=res["abstract"],
                                            source=res["source"], status=res["status"],
                                            note=res["note"])
                    cards.append(card)
                    with _FETCH_LOCK:
                        _FETCH_JOB["done"] += 1
                        if res["status"] == "已获取":
                            _FETCH_JOB["succeeded"] += 1
                        else:
                            _FETCH_JOB["failed"] += 1
                        _FETCH_JOB["rows"].append({
                            "no": r.get("no"), "title": r.get("title"),
                            "citekey": card["citekey"], "status": res["status"],
                            "source": res["source"], "note": res["note"],
                            "chars": len(res["abstract"] or "")})
            kb_mod.build_kb_from_cards(ws, cards, ws_meta(ws))
        except Exception as e:                                   # noqa: BLE001
            with _FETCH_LOCK:
                _FETCH_JOB["error"] = str(e)[:200]
        finally:
            with _FETCH_LOCK:
                _FETCH_JOB["running"] = False
                _FETCH_JOB["finished"] = time.strftime("%H:%M:%S")

    def api_kb_build(self):
        ws = current_ws()
        if not ws:
            return self._err("尚未创建审稿任务", 409)
        b = kb_mod.build_kb(ws, ws_meta(ws))
        return self._json({"ok": True, **b,
                           "kb_md": kb_mod.kb_context(ws, 60000)})

    def api_review_draft(self):
        """Step 3：模型先读本篇知识库，再出审稿意见。"""
        ws = current_ws()
        if not ws:
            return self._err("尚未创建审稿任务", 409)
        meta = ws_meta(ws)
        kb_text = kb_mod.kb_context(ws, 40000)
        if not kb_text.strip():
            return self._err("本篇知识库为空，请先完成 Step 2（抽文献 + 抓摘要）", 409)
        cfg = llm.load_config(BASE_DIR)
        name = meta.get("manuscript")
        pdf = (ws / "00_manuscript" / name) if name else None
        if not pdf or not Path(pdf).is_file():
            cands = [p for p in (ws / "00_manuscript").glob("*") if p.is_file()]
            pdf = cands[0] if cands else None
        paras = manuscript_paragraphs(pdf) if pdf else []
        segs, sent = llm.select_context(paras,
                                        max_chars=int(cfg.get("max_chars") or 60000),
                                        mode=cfg.get("privacy_mode") or "abstract_only")
        body = "\n\n".join((s.get("text") or "") for s in segs)
        system = ("你是严谨的学术审稿专家。所有判断必须基于稿件原文与给定文献知识库；"
                  "知识库里没有的内容不得编造，也不得虚构文献。")
        user = (
            "【稿件信息】\n标题：%s\n类别：%s\n场景：%s\n重点：%s\n\n"
            "【稿件正文节选】\n%s\n\n"
            "【文献知识库：作者自己参考文献的摘要】\n%s\n\n"
            "请仅以 json 对象回答（不要 markdown 代码块），字段如下：\n"
            '{"summary":"一句话结论","strengths":["优点"],'
            '"problems":[{"title":"问题","location":"位置",'
            '"problem":"问题描述","suggestion":"修改建议","confidence":"高/中/低"}],'
            '"decision":"接受/小修/大修/拒稿/需补充信息",'
            '"review_md":"完整审稿意见 markdown 正文"}\n'
            "要求：problems 至少 3 条；review_md 中引用的文献必须来自上面的知识库。"
            % (meta.get("title") or "", meta.get("domain") or "",
               meta.get("scenario") or "", "、".join(meta.get("focus") or []),
               body, kb_text)
        )
        try:
            raw = llm.call_openai_compatible(cfg, system, user)
        except Exception as e:                                   # noqa: BLE001
            return self._err("模型调用失败：%s" % str(e)[:200], 502)
        try:
            res = json.loads(raw)
        except Exception:
            res = {"review_md": raw, "problems": [], "summary": "", "decision": ""}
        md = (res.get("review_md") or "").strip()
        if not md:
            md = "# 审稿意见\n\n" + str(raw)[:4000]
        out = ws / "03_review" / "review.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(md, encoding="utf-8")
        return self._json({"ok": True, "review": res, "md_path": "03_review/review.md",
                           "sent": sent, "kb_chars": len(kb_text)})

    def api_cnki_login(self):
        """打开有头浏览器到知网，交给用户手动登录（断线后自助重连）。"""
        return self._json(fetcher.open_cnki_login())

    # ============================ 复审模型（第二意见） ============================
    def api_llm2_status(self):
        cfg = llm.load_config2(BASE_DIR)
        return self._json({
            "ok": True,
            "provider": cfg.get("provider"),
            "model": cfg.get("model"),
            "base_url": cfg.get("base_url"),
            "configured": cfg.get("provider") != "stub",
            "has_key": bool(cfg.get("api_key")),
        })

    def api_llm2_config(self):
        d = self._json_body()
        patch = {
            "base_url": (d.get("base_url") or "").strip(),
            "api_key": d.get("api_key") or "",
            "model": (d.get("model") or "").strip(),
        }
        if patch["base_url"] or patch["api_key"]:
            patch["provider"] = "openai"
        llm.save_config2(BASE_DIR, patch)
        cfg = llm.load_config2(BASE_DIR)
        ok, err = None, ""
        if cfg.get("provider") != "stub":
            ok, err = llm.test_connection(cfg)
        return self._json({"ok": True, "connected": ok,
                           "provider": cfg.get("provider"), "model": cfg.get("model"),
                           "error": err or ""})

    def api_review_second(self):
        """第二模型复审：读初审意见 + 稿件 + 知识库，产出复审意见与沉淀总结。"""
        ws = current_ws()
        if not ws:
            return self._err("尚未创建审稿任务", 409)
        meta = ws_meta(ws)
        review_p = ws / "03_review" / "review.md"
        if not review_p.is_file():
            return self._err("还没有初审意见，请先在 Step 3 生成", 409)
        first_md = review_p.read_text(encoding="utf-8")[:12000]

        cfg2 = llm.load_config2(BASE_DIR)
        if cfg2.get("provider") == "stub":
            return self._err("复审模型未接入：请在左栏「复审模型」填地址/密钥/模型名", 409)

        kb_text = kb_mod.kb_context(ws, 40000)
        name = meta.get("manuscript")
        pdf = (ws / "00_manuscript" / name) if name else None
        if not pdf or not pdf.is_file():
            cands = [p for p in (ws / "00_manuscript").glob("*") if p.is_file()]
            pdf = cands[0] if cands else None
        paras = manuscript_paragraphs(pdf) if pdf else []
        cfg1 = llm.load_config(BASE_DIR)
        segs, sent = llm.select_context(
            paras, max_chars=int(cfg1.get("max_chars") or 60000), mode=cfg1.get("privacy_mode") or "abstract_only")
        body = "\n\n".join((s.get("text") or "") for s in segs)

        system = ("你是资深学术审稿专家，负责复审另一位审稿人的意见。"
                  "要判断初审是否成立、有没有漏判或误判，不得回避分歧，也不得编造文献。")
        user = (
            "【稿件】\n标题：%s\n类别：%s\n\n"
            "【正文节选】\n%s\n\n"
            "【文献知识库（作者参考文献摘要）】\n%s\n\n"
            "【初审意见（另一位审稿人）】\n%s\n\n"
            "请仅以 json 对象回答，字段：\n"
            '{"verdict":"认可/部分认可/不认可",'
            '"agreements":["你同意的判断"],'
            '"disagreements":[{"point":"初审哪条","reason":"为什么不同意","suggestion":"应该怎么改"}],'
            '"missed":["初审漏掉的重要问题"],'
            '"second_review_md":"完整复审意见 markdown",'
            '"summary":"沉淀用总结：这篇的核心问题 + 可复用的判断经验（200 字内）",'
            '"confidence":"高/中/低"}'
            % (meta.get("title") or "", meta.get("domain") or "",
               body, kb_text, first_md)
        )
        try:
            raw = llm.call_openai_compatible(cfg2, system, user)
        except Exception as e:                                   # noqa: BLE001
            return self._err("复审模型调用失败：%s" % str(e)[:200], 502)
        try:
            res = json.loads(raw)
        except Exception:
            res = {"second_review_md": raw, "summary": "", "verdict": ""}

        parts = ["# 复审意见（第二模型 · %s）" % (cfg2.get("model") or ""), "",
                 "- 复审结论：%s" % (res.get("verdict") or "—"),
                 "- 置信度：%s" % (res.get("confidence") or "—"), ""]
        if res.get("agreements"):
            parts += ["## 同意的判断", ""] + ["- %s" % x for x in res["agreements"]] + [""]
        if res.get("disagreements"):
            parts += ["## 不同意的判断", ""]
            for dd in res["disagreements"]:
                if isinstance(dd, dict):
                    parts.append("- **%s**：%s → %s" % (
                        dd.get("point") or "", dd.get("reason") or "",
                        dd.get("suggestion") or ""))
            parts.append("")
        if res.get("missed"):
            parts += ["## 初审漏掉的问题", ""] + ["- %s" % x for x in res["missed"]] + [""]
        if res.get("second_review_md"):
            parts += ["## 复审意见正文", "", str(res["second_review_md"]), ""]
        if res.get("summary"):
            parts += ["## 沉淀用总结", "", str(res["summary"]), ""]
        md_p = ws / "03_review" / "second-review.md"
        md_p.parent.mkdir(parents=True, exist_ok=True)
        md_p.write_text("\n".join(parts), encoding="utf-8")

        return self._json({"ok": True, "review": res,
                           "md_path": "03_review/second-review.md",
                           "model": cfg2.get("model"), "sent": sent,
                           "kb_chars": len(kb_text)})

    def api_meta(self):
        """更新稿件元信息（类别决定总知识库归档目录）。"""
        ws = current_ws()
        if not ws:
            return self._err("尚未创建审稿任务", 409)
        d = self._json_body()
        meta = ws_meta(ws)
        for k in ("title", "domain", "scenario", "language", "journal"):
            v = d.get(k)
            if v not in (None, ""):
                meta[k] = v
        if isinstance(d.get("focus"), list):
            meta["focus"] = d["focus"]
        meta["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        (ws / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        return self._json({"ok": True, "meta": meta,
                           "domain_slug": kb_mod.domain_slug(meta)})

    def api_archive(self):
        """把本篇摘要卡片与审稿经验沉淀进总库（按类别分目录）。"""
        ws = current_ws()
        if not ws:
            return self._err("尚未创建审稿任务", 409)
        d = self._json_body()
        rep = kb_mod.archive(ws, ws_meta(ws), WORKSPACE_ROOT, d.get("experience") or None)
        return self._json({"ok": True, "report": rep,
                           "msg": "已沉淀到「%s」，累计 %d 条经验"
                                  % (kb_mod.domain_slug(ws_meta(ws)), rep["entries"])})

# ---------------------------------------------------------------- 启动
def main():
    ap = argparse.ArgumentParser(description="学术审稿专家 · 工作台本地服务")
    ap.add_argument("--port", type=int, default=8770)
    ap.add_argument("--host", default="127.0.0.1", help="默认仅本机可访问")
    ap.add_argument("--open", action="store_true", help="启动后打开浏览器")
    a = ap.parse_args()

    WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)
    url = f"http://{a.host}:{a.port}/"
    print("=" * 62)
    print("  学术审稿专家 · 工作台本地服务")
    print("=" * 62)
    print(f"  地址      : {url}")
    print(f"  技能目录  : {SKILL_DIR or '❌ 未找到（设置环境变量 REVIEW_SKILL_DIR）'}")
    print(f"  Python    : {PY}")
    print(f"  工作区根  : {WORKSPACE_ROOT}")
    print(f"  接入脚本  : manuscript_parse / academic_api_client / review_outline / "
          f"citation_check / init_review_workspace")
    print("  未接入    : PubMed E-utilities、智慧芽 MCP（需在 Comate 连接器中启用）")
    print("-" * 62)
    print("  按 Ctrl+C 停止服务")
    print("=" * 62)

    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    if a.open:
        import threading
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
