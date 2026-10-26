"""知识库：本篇知识库（文件夹）+ 总知识库（按类别分目录）。

设计原则
--------
1. 全部落成文件，但不写死文件格式 —— 每个 save_* 都返回 dict，
   将来接数据库时只替换这一层，上层不动。
2. 总库按「类别」分目录，类别来自 meta["domain"]（Step 1 里定）。
3. 每篇稿件的工作区自带一份本篇知识库，互不干扰。

目录结构
--------
review-workspace/
  <slug>/
    00_manuscript/        原稿
    01_refs/              refs.json（全量题录）· sample.json（抽中的 N 篇）· refs.md
    02_abstracts/         每篇一个 <citekey>.json + .md；汇总 kb.md
    03_review/            review.md / review-report.md
    04_experience/        experience.json
  _library/
    index.json            类别索引
    <类别slug>/            cards/ · kb.md · experience.md · index.json
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

import refs as refs_mod

# ---------------------------------------------------------------- 路径

def ws_dirs(ws):
    ws = Path(ws)
    d = {
        "root": ws,
        "manuscript": ws / "00_manuscript",
        "refs": ws / "01_refs",
        "abstracts": ws / "02_abstracts",
        "review": ws / "03_review",
    }
    for k in ("refs", "abstracts", "review"):
        d[k].mkdir(parents=True, exist_ok=True)
    return d


def _write_json(p, obj):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(p)


def _write_text(p, s):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(s, encoding="utf-8")
    return str(p)


def citekey(ref):
    """稳定可读的卡片名：优先 DOI 尾段，其次 序号+标题首词。"""
    if ref.get("doi"):
        k = ref["doi"].split("/")[-1]
        k = re.sub(r'[\\/:*?"<>|\s]+', "-", k)
        return k[:60]
    t = re.sub(r"[^\w\u4e00-\u9fa5]+", "", ref.get("title") or "")[:22]
    return "r%03d_%s" % (ref.get("no", 0), t or "untitled")


# ---------------------------------------------------------------- 本篇：参考文献

def save_refs(ws, result, n=10, seed=None):
    """把题录与本次抽样合并写进 refs.json（不再单独存 sample.json）。"""
    d = ws_dirs(ws)
    all_refs = result.get("refs") or []
    picked = refs_mod.sample_refs(all_refs, n=n, seed=seed)
    picked_nos = {r.get("no") for r in picked}
    _write_json(d["refs"] / "refs.json", {
        "stats": result.get("stats"),
        "n": n,
        "seed": seed,
        "picked": picked,
        "refs": all_refs,
    })

    L = ["# 参考文献清单", "",
         "> 共 %d 条 ｜ 可抓摘要 %d 条 ｜ 本次抽中 %d 篇（标 ●）" % (
             len(all_refs), (result.get("stats") or {}).get("abstractable", 0), len(picked)),
         "", "## 本次抽中（将抓摘要）", ""]
    for r in picked:
        L.append("- **[%03d][%s][%s]** %s ｜ %s %s(%s):%s" % (
            r["no"], r["type"], r["lang"], r["title"] or "（标题未解析出）",
            r["venue"], r["year"], r["volume"], r["pages"]))
    L += ["", "## 全部条目", ""]
    for r in all_refs:
        mark = "●" if r.get("no") in picked_nos else "　"
        L.append("%s %d. [%s] %s" % (mark, r["no"], r["type"], r["title"] or r["raw"][:60]))
    _write_text(d["refs"] / "refs.md", "\n".join(L))
    return {"all": len(all_refs), "picked": picked,
            "files": ["01_refs/refs.json", "01_refs/refs.md"]}


def load_sample(ws):
    p = ws_dirs(ws)["refs"] / "refs.json"
    if not p.is_file():
        return []
    try:
        blob = json.loads(p.read_text(encoding="utf-8")) or {}
    except Exception:
        return []
    return blob.get("picked") or []


# ---------------------------------------------------------------- 本篇：摘要知识库

def save_card(ws, ref, abstract="", source="", status=None, note=""):
    """构造一篇摘要卡片（只返回 dict，不落盘）。

    02_abstracts/ 目录仍然保留（这里确保它存在），但里面不写每篇 json，
    最终只由 build_kb_from_cards 写一个 kb.md。
    """
    ws_dirs(ws)                       # 只建 02_abstracts/ 目录，不写任何文件
    card = {
        "citekey": citekey(ref),
        "ref_no": ref.get("no"),
        "type": ref.get("type"),
        "lang": ref.get("lang"),
        "title": ref.get("title"),
        "authors": ref.get("authors"),
        "venue": ref.get("venue"),
        "year": ref.get("year"),
        "volume": ref.get("volume"),
        "issue": ref.get("issue"),
        "pages": ref.get("pages"),
        "doi": ref.get("doi"),
        "abstract": abstract or "",
        "abstract_source": source or "",
        "status": status or ("已获取" if abstract else "未获取"),
        "note": note,
        "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    return card


def build_kb_from_cards(ws, cards, meta=None):
    """由内存卡片列表直接生成 kb.md（唯一落盘产物）。"""
    d = ws_dirs(ws)
    ok = [c for c in cards if c.get("abstract")]
    fail = [c for c in cards if not c.get("abstract")]

    L = ["# 本篇文献知识库", ""]
    if meta:
        L += ["- 稿件：%s" % (meta.get("title") or ""),
              "- 类别：%s" % (meta.get("domain") or ""),
              "- 生成时间：%s" % time.strftime("%Y-%m-%d %H:%M"), ""]
    L += ["- 抽中 %d 篇 ｜ 已获摘要 **%d** 篇 ｜ 未获取 %d 篇" % (len(cards), len(ok), len(fail)), ""]

    if ok:
        L += ["## 一、已获摘要文献", ""]
        for i, c in enumerate(ok, 1):
            L += ["### %d. %s" % (i, c.get("title") or "（无标题）"), "",
                  "**出处**：%s %s(%s):%s　｜　**DOI**：%s" % (
                      c.get("venue") or "—", c.get("year") or "—",
                      c.get("volume") or "—", c.get("pages") or "—",
                      c.get("doi") or "—"), "",
                  "**摘要**：%s" % c["abstract"], ""]
    if fail:
        L += ["## 二、未获取摘要（已跳过，未补齐）", ""]
        for c in fail:
            L += ["- %s（%s %s）— %s" % (c.get("title") or "（无标题）",
                                        c.get("venue") or "—", c.get("year") or "—",
                                        c.get("note") or c.get("status") or "未获取")]
        L.append("")

    _write_text(d["abstracts"] / "kb.md", "\n".join(L))
    return {"count": len(cards), "with_abstract": len(ok), "without": len(fail),
            "kb_path": "02_abstracts/kb.md"}


def build_kb(ws, meta=None):
    """重建 kb.md。

    旧工作区（遗留每篇 json）按 json 重建；
    新流程只有 kb.md，此时只回报统计，不覆盖已有内容。
    """
    d = ws_dirs(ws)
    cards = []
    for f in sorted(d["abstracts"].glob("*.json")):
        if f.name == "kb.json":          # 旧的汇总文件，不是卡片
            continue
        try:
            cards.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            continue
    if cards:
        return build_kb_from_cards(ws, cards, meta)

    p = d["abstracts"] / "kb.md"
    if not p.is_file():
        return {"count": 0, "with_abstract": 0, "without": 0, "kb_path": ""}
    t = p.read_text(encoding="utf-8")
    m = re.search(r"抽中 (\d+) 篇\s*｜\s*已获摘要 \*\*(\d+)\*\*\s*篇\s*｜\s*未获取 (\d+) 篇", t)
    if m:
        return {"count": int(m.group(1)), "with_abstract": int(m.group(2)),
                "without": int(m.group(3)), "kb_path": "02_abstracts/kb.md"}
    n = sum(1 for ln in t.splitlines() if ln.startswith("### "))
    return {"count": n, "with_abstract": n, "without": 0, "kb_path": "02_abstracts/kb.md"}



def kb_context(ws, max_chars=40000):
    """给模型读的知识库正文（超长按篇截断）。"""
    p = ws_dirs(ws)["abstracts"] / "kb.md"
    if not p.is_file():
        return ""
    t = p.read_text(encoding="utf-8")
    return t[:max_chars]


# ---------------------------------------------------------------- 总知识库（按类别）

def domain_slug(meta):
    raw = (meta or {}).get("domain") or "未分类"
    s = re.sub(r'[\\/:*?"<>|]+', "-", raw).strip().replace(" ", "")
    s = s.replace("·", "-")
    return s or "未分类"


def library_dir(root, meta):
    p = Path(root) / "_library" / domain_slug(meta)
    p.mkdir(parents=True, exist_ok=True)
    return p


def archive(ws, meta, root, experience=None):
    """只把本篇的复审经验沉淀进总库。

    不再复制文献卡片：总库是「经验库」，不是「文献库」；
    文献摘要留在各稿件自己的 02_abstracts/ 里。
    """
    lib = library_dir(root, meta)
    exp_path = lib / "experience.md"

    if experience:
        head = "" if exp_path.is_file() else "# %s · 审稿经验\n\n" % domain_slug(meta)
        block = ["## %s" % (meta.get("title") or ws.name), "",
                 "- 沉淀时间：%s" % time.strftime("%Y-%m-%d %H:%M"),
                 "- 来源稿件：%s" % ws.name, ""]
        for k, v in (experience or {}).items():
            if v:
                block.append("**%s**：%s" % (k, v))
                block.append("")
        with exp_path.open("a", encoding="utf-8") as fh:
            fh.write(head + "\n".join(block) + "\n")

    # 经验条数 = experience.md 里的二级标题数
    entries = 0
    if exp_path.is_file():
        entries = sum(1 for ln in exp_path.read_text(encoding="utf-8").splitlines()
                      if ln.startswith("## "))

    _write_json(lib / "index.json", {
        "domain": (meta or {}).get("domain") or "未分类",
        "slug": domain_slug(meta),
        "experience_entries": entries,
        "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
    })

    # 顶层索引：所有类别
    cats = []
    for sub in sorted((Path(root) / "_library").iterdir()):
        if sub.is_dir() and (sub / "index.json").is_file():
            try:
                cats.append(json.loads((sub / "index.json").read_text(encoding="utf-8")))
            except Exception:
                pass
    _write_json(Path(root) / "_library" / "index.json",
                {"categories": cats,
                 "updated": time.strftime("%Y-%m-%d %H:%M:%S")})

    return {"library": str(lib), "entries": entries,
            "experience_path": str(exp_path)}
