"""参考文献抽取与题录解析（AI审稿助手 · 四步框架的地基）。

职责：
  1. 从 PDF / docx / md 里定位「参考文献」章节
  2. 按 [n] 边界切分条目（跨页、去页眉页码水印）
  3. 把每条解析成结构化题录：类型 / 作者 / 标题 / 期刊 / 年 / 卷期 / 页 / DOI / 语种
  4. 按参数随机抽样，默认优先期刊论文（只有期刊论文有摘要可抓）

只依赖标准库；PDF 走 PyMuPDF，缺失时回退 pypdf。
"""
from __future__ import annotations

import json
import random
import re
from pathlib import Path

# ---------------------------------------------------------------- 常量

REF_HEAD_RE = re.compile(
    r"^\s*(参\s*考\s*文\s*献|引\s*用\s*文\s*献|references?|bibliography|works\s+cited)\s*[:：]?\s*$",
    re.I,
)
# 参考文献章节的结束标志
REF_STOP_RE = re.compile(
    r"^\s*(致\s*谢|附\s*录|攻读学位期间|作者简介|个人简历|acknowledg|appendix|"
    r"作者贡献|声明|学位论文原创性声明)",
    re.I,
)
# 噪声行：纯页码、学校水印、监测平台
NOISE_RES = [
    re.compile(r"^\s*\d{1,3}\s*$"),
    re.compile(r"学位中心学位论文质量监测平台.*$"),
    re.compile(r"^\s*[-—_]{3,}\s*$"),
]
# 行内水印碎片：PDF 会把「学位中心学位论文质量监测平台…」拆成片段插进正文
INLINE_NOISE_RE = re.compile(r"学位中心[^\s]*")
ENTRY_RE = re.compile(r"^\s*\[(\d{1,3})\]\s*(.*)$")
# 作者-年份格式的条目起点：`Ang, A.` / `O'Connor, M.` / `van der Heijden, J.`
EN_AUTHOR_START = re.compile(
    r"^[A-Z][A-Za-z\u00c0-\u024f'\-]+,\s+[A-Z]\.(?:\s|,|$)")
# 中文作者-年份条目起点：`卢珊 & 王惠文. (2021). …` / `王惠文，等．(2019)．…`
ZH_AY_START = re.compile(
    r"^[一-龥][^（(]{2,50}[.．]\s*[（(]\s*(?:19|20)\d{2}")
# 解析：`Authors (2018). Title. …`，兼容全角括号与全角句点
AY_RE = re.compile(
    r"^(?P<authors>.+?)[.．,]?\s*[（(](?P<year>(?:19|20)\d{2})[）)][.．,]?\s*(?P<rest>.+)$")
# 作者-年份的卷期页：`Journal of Finance 61: 259–299.`
AY_TAIL_RE = re.compile(
    r"(?P<venue>[A-Za-z\u00c0-\u024f][^0-9]{3,}?)\s*"
    r"(?:(?P<vol>\d+)\s*(?:\(\s*(?P<issue>\d+)\s*\))?\s*)?"
    r"[:：,]\s*(?P<pages>\d+\s*[-–—~]\s*\d+)")
TYPE_RE = re.compile(r"\[\s*([A-Z]{1,2}(?:\s*/\s*OL)?)\s*\]")
DOI_RE = re.compile(r"10\.\d{4,9}/[^\s，。,;；\]\)]+")

# 题录尾部：期刊名, 年, 卷(期): 起页-止页
TAIL_RE = re.compile(
    r"(?P<venue>[^,，]+?)\s*[,，]\s*(?P<year>(?:19|20)\d{2})"
    r"(?:\s*[,，]\s*(?P<vol>\d+)\s*(?:\(\s*(?P<issue>\d+)\s*\))?)?"
    r"(?:\s*[:：]\s*(?P<pages>\d+(?:\s*[-–—~]\s*\d+)?))?"
)
ZH_RE = re.compile(r"[\u4e00-\u9fa5]")
CJK_TAIL_RE = re.compile(r"[\u4e00-\u9fa5]")
# 英文作者段结尾：`Goetz S.` / `Yilmaz K.` / `et al.`
# 用这个模式定位作者↔标题边界，避免把标题里的 "U.S." 之类缩写当成句末
EN_AUTHOR_END = re.compile(
    r"(?:et\s+al\.|[A-Z][a-z\u00c0-\u024f]+(?:\s+[A-Z][a-z\u00c0-\u024f]+)*\s+[A-Z]\.)\s+")


def split_author_title(head):
    """把「作者. 标题」切开。英文按姓名模式，中文按全角句点。"""
    head = head.strip().rstrip(".．,，")
    if not ZH_RE.search(head):
        last = None
        for m in EN_AUTHOR_END.finditer(head):
            if m.end() < len(head) - 8:      # 标题至少还要有 8 个字符
                last = m
        if last:
            return head[:last.end()].strip(), head[last.end():].strip()
    cut = max(head.rfind(". "), head.rfind("．"), head.rfind(".　"))
    if cut > 0:
        return head[:cut].strip().rstrip(".．,，"), head[cut + 1:].strip().lstrip(".． ")
    return "", head


# ---------------------------------------------------------------- 读取全文

def read_fulltext(path):
    """返回 (全文, 每页文本列表)。PDF 走 PyMuPDF 优先。"""
    p = Path(path)
    suf = p.suffix.lower()
    if suf == ".pdf":
        pages = _read_pdf_pages(p)
        return "\n".join(pages), pages
    if suf == ".docx":
        text = _read_docx(p)
        return text, [text]
    text = p.read_text(encoding="utf-8", errors="replace")
    return text, [text]


def _read_pdf_pages(p):
    try:
        import fitz                                    # PyMuPDF
        with fitz.open(str(p)) as doc:
            return [pg.get_text("text") for pg in doc]
    except Exception:
        pass
    try:
        from pypdf import PdfReader
        return [(pg.extract_text() or "") for pg in PdfReader(str(p)).pages]
    except Exception:
        return []


def _read_docx(p):
    try:
        import zipfile
        from html import unescape
        with zipfile.ZipFile(p) as z:
            xml = z.read("word/document.xml").decode("utf-8", "ignore")
        parts = []
        for chunk in re.split(r"</w:p>", xml):
            ts = re.findall(r"<w:t[^>]*>(.*?)</w:t>", chunk, re.S)
            if ts:
                parts.append(unescape("".join(ts)))
        return "\n".join(parts)
    except Exception:
        return ""


# ---------------------------------------------------------------- 定位章节

def locate_ref_pages(pages):
    """找参考文献章节起止页。取「后面紧跟条目」的那次出现，避开目录。

    返回 (起始页索引, 章节内文本)。
    """
    if not pages:
        return -1, ""
    # 每页找标题行
    cands = []
    for i, t in enumerate(pages):
        for ln in t.splitlines():
            if REF_HEAD_RE.match(ln.strip()):
                cands.append(i)
                break
    if not cands:
        return -1, ""

    # 目录页也会出现「参考文献」，判据：该页之后 2 页内是否出现 [1] 这样的条目
    def looks_like_start(i):
        probe = "\n".join(pages[i:i + 3])
        return bool(re.search(r"^\s*\[1\]", probe, re.M))

    starts = [i for i in cands if looks_like_start(i)]
    start = starts[0] if starts else cands[-1]

    # 往后收集，直到遇到致谢/附录
    buf = []
    for i in range(start, len(pages)):
        t = pages[i]
        if i == start:
            # 关键：只取「参考文献」标题之后的部分。
            # docx / md 整篇只有一"页"，整页收会把正文也吞进来。
            acc = 0
            for ln in t.splitlines(keepends=True):
                acc += len(ln)
                if REF_HEAD_RE.match(ln.strip()):
                    t = t[acc:]
                    break
        stop_at = None
        for ln in t.splitlines():
            if i > start and REF_STOP_RE.match(ln.strip()):
                stop_at = t.find(ln)
                break
        if stop_at is not None and stop_at >= 0:
            buf.append(t[:stop_at])
            break
        buf.append(t)
        # 安全上限：连续 3 页没有 [n] 就停
        if i > start + 2 and not re.search(r"^\s*\[\d{1,3}\]", t, re.M):
            break
    return start, "\n".join(buf)


# ---------------------------------------------------------------- 切分条目

def _clean_lines(text):
    out = []
    for ln in text.splitlines():
        s = ln.strip()
        if not s:
            continue
        if any(p.search(s) for p in NOISE_RES):
            continue
        if REF_HEAD_RE.match(s):
            continue
        s = INLINE_NOISE_RE.sub(" ", s)          # 清行内水印碎片
        s = re.sub(r"\s{2,}", " ", s).strip()
        if s:
            out.append(s)
    return out


def split_entries(ref_text):
    """按 [n] 切分，孤行并入上一条。返回 [{'no':int,'raw':str}]。

    若全文没有编号（0 条），回退到「作者-年份」切分：
    大量英文期刊（如 Journal of Financial Econometrics）用 `Ang, A. (2006). …` 格式。
    """
    lines = _clean_lines(ref_text)

    entries, cur = [], None
    for ln in lines:
        m = ENTRY_RE.match(ln)
        if m:
            if cur:
                entries.append(cur)
            cur = {"no": int(m.group(1)), "raw": m.group(2)}
        elif cur is not None:
            s = cur["raw"].rstrip()
            sep = "" if ZH_RE.search(s[-1:]) else " "
            cur["raw"] = s + sep + ln
    if cur:
        entries.append(cur)

    if len(entries) < 3:                       # 编号格式不成立 → 试作者-年份
        entries = _split_author_year(lines)

    for e in entries:
        e["raw"] = re.sub(r"\s*学位中心学位论文质量监测平台.*$", "", e["raw"]).strip()
    return entries


def _split_author_year(lines):
    """无编号参考文献：以「姓, 名首字母.」开头作为条目边界。"""
    out, cur = [], None
    for ln in lines:
        if EN_AUTHOR_START.match(ln) or ZH_AY_START.match(ln) \
                or re.match(r"^[A-Z][A-Za-z\u00c0-\u024f'\-]+,\s+[A-Z]", ln):
            if cur:
                out.append(cur)
            cur = {"no": len(out) + 1, "raw": ln}
        elif cur is not None:
            # 续行：上一行以连字符结尾则直接拼，否则补空格
            s = cur["raw"].rstrip()
            sep = "" if s.endswith("-") else " "
            cur["raw"] = s + sep + ln
    if cur:
        out.append(cur)
    return out


# ---------------------------------------------------------------- 题录解析

def parse_entry(raw, no=0):
    """把一条参考文献解析成结构化题录。解析不出的字段留空，不猜。"""
    text = raw.strip()
    out = {
        "no": no, "raw": text, "type": "", "authors": "", "title": "",
        "venue": "", "year": "", "volume": "", "issue": "", "pages": "",
        "doi": "", "lang": "zh" if ZH_RE.search(text) else "en",
        "has_abstract": False, "abstract": "", "abstract_source": "",
        "status": "待抓取",
    }
    m = DOI_RE.search(text)
    if m:
        out["doi"] = m.group().rstrip(".")
    tm = TYPE_RE.search(text)
    if tm:
        out["type"] = tm.group(1).replace(" ", "")
        head, tail = text[:tm.start()], text[tm.end():]
    else:
        ay = AY_RE.match(text)
        if ay:
            # 作者-年份：`Ang, A., ... (2006). Title. Journal 61: 259-299.`
            out["type"] = "A"
            out["authors"] = ay.group("authors").strip().rstrip(".,")
            out["year"] = ay.group("year")
            rest = ay.group("rest").strip()
            cut = rest.find(". ")
            out["title"] = (rest[:cut] if cut > 0 else rest).strip().rstrip(".．")
            out["lang"] = "zh" if ZH_RE.search(out["title"]) else "en"
            tail = rest[cut + 2:] if cut > 0 else ""
            m2 = AY_TAIL_RE.search(tail)
            if m2:
                out["venue"] = m2.group("venue").strip().rstrip(".,;，。 ")
                out["volume"] = m2.group("vol") or ""
                out["issue"] = m2.group("issue") or ""
                out["pages"] = (m2.group("pages") or "").replace(" ", "")
            else:
                out["venue"] = tail.strip().rstrip(".．")
            return out
        # 都没有：按最后一个句点粗略切
        out["type"] = "?"
        cut = max(text.rfind(". "), text.rfind("．"))
        head, tail = (text[:cut], text[cut + 1:]) if cut > 0 else (text, "")

    # 作者 / 标题：按姓名模式切（避开标题里的 U.S. 等缩写）
    out["authors"], out["title"] = split_author_title(head)
    out["title"] = out["title"].strip().rstrip(".．")
    # 语种按标题判定（整条文本可能混入水印等中文，会误判）
    out["lang"] = "zh" if ZH_RE.search(out["title"]) else "en"

    # 尾部：期刊 / 年 / 卷(期) / 页
    tail = tail.strip().lstrip(".． 　")
    tm2 = TAIL_RE.search(tail)
    if tm2:
        out["venue"] = tm2.group("venue").strip().rstrip(".．,，")
        out["year"] = tm2.group("year") or ""
        out["volume"] = tm2.group("vol") or ""
        out["issue"] = tm2.group("issue") or ""
        out["pages"] = (tm2.group("pages") or "").replace(" ", "")
    else:
        # 学位论文 [D]：城市：学校，年
        dm = re.search(r"[,，]?\s*(\d{4})\s*[.．]?\s*$", tail)
        if dm:
            out["year"] = dm.group(1)
        out["venue"] = tail.strip().rstrip(".．")
    return out


ABSTRACT_TYPES = {"J", "J/OL", "C", "C/OL", "A"}      # A = 作者-年份格式（多为期刊论文）


def extract_refs(path):
    """端到端：文件 → 结构化题录列表 + 元信息。"""
    text, pages = read_fulltext(path)
    start, ref_text = locate_ref_pages(pages)
    if start < 0:
        return {"ok": False, "error": "未找到参考文献章节", "refs": [], "stats": {}}
    raw_entries = split_entries(ref_text)
    refs = [parse_entry(e["raw"], e["no"]) for e in raw_entries]

    # 序号连续性
    nos = [r["no"] for r in refs]
    gaps = sum(1 for a, b in zip(nos, nos[1:]) if b != a + 1)
    stats = {
        "total": len(refs),
        "start_page": start + 1,
        "numbered": bool(nos) and nos[0] == 1,
        "no_gaps": gaps == 0,
        "with_doi": sum(1 for r in refs if r["doi"]),
        "abstractable": sum(1 for r in refs if r["type"] in ABSTRACT_TYPES),
        "by_type": {},
        "by_lang": {
            "zh": sum(1 for r in refs if r["lang"] == "zh"),
            "en": sum(1 for r in refs if r["lang"] == "en"),
        },
    }
    for r in refs:
        stats["by_type"][r["type"]] = stats["by_type"].get(r["type"], 0) + 1
    return {"ok": True, "refs": refs, "stats": stats, "ref_text_chars": len(ref_text)}


def sample_refs(refs, n=10, seed=None, abstractable_only=True):
    """随机抽 n 篇。默认只从「可能有摘要」的类型里抽（期刊/会议）。"""
    pool = [r for r in refs if r["type"] in ABSTRACT_TYPES] if abstractable_only else list(refs)
    if not pool:
        return []
    rng = random.Random(seed)
    k = min(n, len(pool))
    picked = rng.sample(pool, k)
    return sorted(picked, key=lambda r: r["no"])


if __name__ == "__main__":
    import sys
    p = sys.argv[1] if len(sys.argv) > 1 else ""
    res = extract_refs(p)
    if not res["ok"]:
        print("❌", res["error"])
        sys.exit(1)
    print(json.dumps(res["stats"], ensure_ascii=False, indent=2))
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    print("\n随机抽 %d 篇:" % n)
    for r in sample_refs(res["refs"], n, seed=42):
        print("  [%03d][%s][%s] %s | %s %s(%s):%s" % (
            r["no"], r["type"], r["lang"], r["title"][:52],
            r["venue"][:26], r["year"], r["volume"], r["pages"]))
