#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""学术审稿专家 · 模型层（受约束生成）

这个文件存在的理由：平台必须真的「有 AI」，但 AI 不能有自由发挥权。
否则它就退化成市面上那种提示词型审稿技能——靠模型的记忆回答事实，
正是我们用「记忆型 DOI 错误」批判过的失效模式。

因此这里的每一条约束都是硬编码的，不靠提示词自律：

  1. 事实层永远由脚本产出。模型只在脚本给出的事实上做归纳与措辞。
  2. 每条产出必须带证据引用：原文段落号 `P<n>` 或 `doi:<DOI>`。
     校验层逐条核对引用能否落地；不能归因的条目自动降级为「未验证/需人工确认」。
  3. 模型输出中出现的任何 DOI，必须已在 DOI 双源核验的通过清单内；
     否则视为幻觉，拦截并报错（不是"标注一下"就放过）。
  4. 保密：默认只发送「摘要 + 脚本指定的关键段落」，发送前记录字符数与模式，可审计。
  5. provider 可替换：stub（无 key，用于链路自检）/ openai（OpenAI 兼容协议，
     通过 config.json 或环境变量配置，改配置即插即用）。
"""

import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

DOI_RE = re.compile(r"10\.[0-9]{4,9}/[A-Za-z0-9._()/:;-]+")
PARA_REF_RE = re.compile(r"^P(\d{1,4})$", re.I)

# ---------------------------------------------------------------- 配置
DEFAULT_MODEL = "deepseek-flash"
DEFAULT_BASE = "https://api.deepseek.com/v1"


def load_config(base_dir):
    """优先级：config.json > 环境变量 > stub。

    不含 key 时一律回落到 stub —— 不假装已接入模型。
    """
    cfg = {
        "provider": "stub",
        "base_url": "",
        "api_key": "",
        "model": "",
        "timeout": 90,
        "max_chars": 60000,
        "temperature": 0.2,
        "privacy_mode": "abstract_only",
    }
    cf = Path(base_dir) / "config.json"
    if cf.is_file():
        try:
            raw = json.loads(cf.read_text(encoding="utf-8"))
            llm = raw.get("llm") or raw
            for k in ("provider", "base_url", "api_key", "model", "timeout", "max_chars",
                      "temperature", "privacy_mode"):
                if llm.get(k) not in (None, ""):
                    cfg[k] = llm[k]
        except Exception:
            pass

    env = {
        "provider": os.environ.get("REVIEWER_LLM_PROVIDER"),
        "base_url": os.environ.get("REVIEWER_LLM_BASE_URL"),
        "api_key": os.environ.get("REVIEWER_LLM_API_KEY"),
        "model": os.environ.get("REVIEWER_LLM_MODEL"),
    }
    for k, v in env.items():
        if v:
            cfg[k] = v

    # 环境里已有 OPENAI_API_KEY 时，不自动启用（稿件外发必须先由人明确选择），
    # 只把它作为「可一键启用」的信息暴露给界面。
    cfg["env_key_available"] = bool(os.environ.get("OPENAI_API_KEY")
                                    or os.environ.get("REVIEWER_LLM_API_KEY"))
    cfg["env_key_name"] = ("OPENAI_API_KEY" if os.environ.get("OPENAI_API_KEY") else
                           ("REVIEWER_LLM_API_KEY" if os.environ.get("REVIEWER_LLM_API_KEY") else ""))

    # 只有当 base_url 与 api_key 同时可用时，才认为模型真的接上了
    if cfg["provider"] != "stub" and cfg.get("api_key") and cfg.get("base_url"):
        # 占位文字（如「stub（未接入模型）」）不是模型名，必须换成默认值，
        # 否则会原样发给 API 并得到 400 invalid model。
        _m = (cfg.get("model") or "").strip()
        cfg["model"] = DEFAULT_MODEL if (not _m or _m.lower().startswith("stub")) else _m
    else:
        cfg["provider"] = "stub"
        cfg["model"] = "stub（未接入模型）"
    return cfg


def load_config2(base_dir):
    """复审模型（第二模型）配置。

    刻意不回落：没配就是 stub，绝不偷偷用第一个模型冒充「第二意见」。
    """
    cfg = {
        "provider": "stub", "base_url": "", "api_key": "", "model": "",
        "timeout": 150, "temperature": 0.2,
    }
    cf = Path(base_dir) / "config.json"
    if cf.is_file():
        try:
            raw = json.loads(cf.read_text(encoding="utf-8"))
            llm2 = raw.get("llm2") or {}
            for k in ("provider", "base_url", "api_key", "model", "timeout", "temperature"):
                if llm2.get(k) not in (None, ""):
                    cfg[k] = llm2[k]
        except Exception:
            pass
    if cfg.get("api_key") and cfg.get("base_url") and cfg.get("provider") != "stub":
        m = (cfg.get("model") or "").strip()
        cfg["model"] = DEFAULT_MODEL if (not m or m.lower().startswith("stub")) else m
    else:
        cfg["provider"] = "stub"
        cfg["model"] = "stub（未接入复审模型）"
    return cfg


def save_config2(base_dir, patch):
    """写入 config.json 的 llm2 段（与第一个模型互不影响）。"""
    cf = Path(base_dir) / "config.json"
    raw = {}
    if cf.is_file():
        try:
            raw = json.loads(cf.read_text(encoding="utf-8"))
        except Exception:
            raw = {}
    if not isinstance(raw, dict):
        raw = {}
    llm2 = dict(raw.get("llm2") or {})
    for k in ("provider", "base_url", "api_key", "model", "timeout", "temperature"):
        v = patch.get(k)
        if v is not None:
            llm2[k] = v
    raw["llm2"] = llm2
    cf.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    return llm2


def save_config(base_dir, patch):
    """把接入配置写入 config.json（仅本机，已加入 .gitignore）。

    api_key 传空字符串表示「保持原值不变」，避免界面回显密钥。
    """
    cf = Path(base_dir) / "config.json"
    raw = {}
    if cf.is_file():
        try:
            raw = json.loads(cf.read_text(encoding="utf-8"))
        except Exception:
            raw = {}
    if not isinstance(raw, dict):
        raw = {}
    if not isinstance(raw.get("llm"), dict):
        raw = {"llm": {k: v for k, v in raw.items() if k != "llm"}}
    llm = raw["llm"]
    for k in ("base_url", "model", "privacy_mode", "provider", "timeout", "max_chars", "temperature"):
        if k in patch and patch[k] not in (None, ""):
            # 界面回显的占位文字绝不能写进配置文件（否则会永久污染 model 字段）
            if k == "model" and str(patch[k]).strip().lower().startswith("stub"):
                continue
            llm[k] = patch[k]
    if patch.get("api_key"):                      # 空 = 保持原值，避免界面回显后误清空
        llm["api_key"] = patch["api_key"]
    if llm.get("base_url") and llm.get("api_key") and not llm.get("provider"):
        llm["provider"] = "openai"
    if llm.get("base_url") and not llm.get("model"):
        llm["model"] = DEFAULT_MODEL if "deepseek" in llm["base_url"] else ""
    cf.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(cf, 0o600)
    except Exception:
        pass
    return load_config(base_dir)


def save_test_result(base_dir, ok, err):
    """记录最近一次连通性测试结果（不含密钥），供界面如实展示。"""
    f = Path(base_dir) / "llm-last-test.json"
    f.write_text(json.dumps({"ok": bool(ok), "error": (err or "")[:300],
                             "at": time.strftime("%Y-%m-%d %H:%M:%S")},
                            ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(f, 0o600)
    except Exception:
        pass
    return f


def load_test_result(base_dir):
    f = Path(base_dir) / "llm-last-test.json"
    if not f.is_file():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return None


def test_connection(cfg, timeout=20):
    """最小代价连通性测试：只发一句「回复 OK」，不发送任何稿件内容。"""
    if cfg.get("provider") == "stub":
        return False, "未配置 base_url / api_key（当前为 stub 模式）"
    try:
        # ⚠ DeepSeek 的 json_object 模式要求提示词里必须出现 json 字样，
        #    否则返回 400「Prompt must contain the word 'json'」。
        out = call_openai_compatible(dict(cfg, timeout=timeout),
                                     "你要以 JSON 格式回复。",
                                     '返回 JSON：{"ok": true}')
        return True, ""
    except Exception as e:                                # noqa: BLE001
        return False, str(e)[:300]


def status(base_dir):
    cfg = load_config(base_dir)
    host = ""
    if cfg.get("base_url"):
        m = re.match(r"https?://([^/]+)", cfg["base_url"])
        host = m.group(1) if m else cfg["base_url"]
    privacy = cfg.get("privacy_mode") or "abstract_only"
    last = load_test_result(base_dir)
    hints = {
        "abstract_only": "仅发送摘要与关键章节首段，正文其余部分不出本机",
        "abstract_only_trim": "仅发送摘要，正文不出本机",
        "fulltext": "全文发送（含正文全部内容）",
    }
    return {
        "provider": cfg["provider"],
        "mode": cfg["provider"],
        "configured": cfg["provider"] != "stub",
        "connected": cfg["provider"] != "stub" and bool((last or {}).get("ok")),
        "last_test": last,
        "has_key": bool(cfg.get("api_key")),
        "env_key_available": cfg.get("env_key_available"),
        "env_key_name": cfg.get("env_key_name"),
        "base_url": cfg.get("base_url") or "",
        "model": cfg["model"],
        "base_host": host,
        "privacy_mode": privacy,
        "privacy_hint": hints.get(privacy, ""),
        "config": {"base_url": cfg.get("base_url") or "", "model": cfg.get("model") or "",
                   "privacy_mode": privacy},
        "note": ("未配置模型：当前不调用任何外部服务，各步骤只用本机脚本事实工作。"
                 "在左栏「模型接口」填 base_url 与 api_key 并测试连通即可接入；"
                 "接入后 Step 4 / 6 / 7 / 9 自动调用，步骤内无需再配置。"),
    }


# ---------------------------------------------------------------- 上下文（保密闸门）
ABSTRACT_HEADS = ("摘要", "abstract")
KEY_HEADS = ("引言", "introduction", "方法", "method", "方法学", "数据", "结论", "conclusion", "讨论", "discussion")
_NUM_PREFIX = re.compile(r"^[\s\d\.．、（）()一二三四五六七八九十]+[\.、\s]*")


def _norm_head(text):
    """去掉章节号与标点，便于匹配「1 引言」「3. 数据与实证设计」这类标题。"""
    t = (text or "").strip()
    t = t.rstrip("：:　").strip()
    t = _NUM_PREFIX.sub("", t)
    return t.lower()


def _is_head(text, style):
    if style and ("head" in style.lower() or "标题" in style):
        return True
    n = _norm_head(text)
    return any(n.startswith(h) for h in KEY_HEADS + ABSTRACT_HEADS) and len(text or "") <= 30


def select_context(paras, max_chars=60000, mode="abstract_only"):
    """按保密模式选择要发给模型的稿件内容。

    abstract_only（默认）：摘要全文 + 各关键章节的首段（用于写卡片与对标，
    不发送正文其余部分）。返回的 segments 带段落号，模型引用时必须回指。
    """
    segs = []
    in_abs = False
    abstract_taken = 0
    for i, (text, style) in enumerate(paras):
        is_head = _is_head(text, style)
        n = _norm_head(text)
        if is_head and any(n.startswith(h) for h in ABSTRACT_HEADS):
            in_abs = True
            continue
        if is_head:
            in_abs = False
        if in_abs:
            if (text or "").strip() and abstract_taken < 6:
                segs.append({"p": i + 1, "role": "摘要", "text": text})
                abstract_taken += 1
            continue

    # 关键章节首段：对每个关键章节标题，取其后第一个足够长的非空段落
    for i, (text, style) in enumerate(paras):
        if not _is_head(text, style):
            continue
        n = _norm_head(text)
        for h in KEY_HEADS:
            if n.startswith(h):
                for j in range(i + 1, min(i + 4, len(paras))):
                    t = (paras[j][0] or "").strip()
                    if len(t) >= 20:
                        segs.append({"p": j + 1, "role": h, "text": t})
                        break
                break

    if mode == "abstract_only_trim":     # 更保守：只发摘要
        segs = [s for s in segs if s["role"] == "摘要"]
    elif mode == "fulltext":             # 用户显式选择：全文（仍在字符预算内截断）
        segs = [{"p": i + 1, "role": "正文", "text": (t or "").strip()}
                for i, (t, s) in enumerate(paras) if (t or "").strip()]

    # 去重 + 截断（保证总量可控）
    out, used, budget = [], set(), max_chars
    for s in segs:
        if s["p"] in used:
            continue
        used.add(s["p"])
        t = s["text"][:4000]
        if len(t) > budget:
            t = t[:budget]
        if not t:
            continue
        budget -= len(t)
        out.append({"p": s["p"], "role": s["role"], "text": t})
        if budget <= 200:
            break
    meta = {
        "mode": mode,
        "segments": len(out),
        "chars": sum(len(s["text"]) for s in out),
        "roles": sorted(set(s["role"] for s in out)),
        "policy": {"abstract_only": "仅发送摘要与关键章节首段，不发送正文其余部分",
                   "abstract_only_trim": "仅发送摘要，正文不出本机",
                   "fulltext": "全文发送（由用户在左侧「模型接口」中显式选择）"}.get(
                       mode, "仅发送摘要与关键章节首段"),
    }
    return out, meta


# ---------------------------------------------------------------- 任务规格
DIMENSIONS = ["研究问题与创新性", "理论贡献", "方法严谨性", "数据与样本", "统计与报告规范",
              "结果解释与结论", "局限与泛化", "伦理与合规", "写作与可复现性"]

SPECS = {
    4: {
        "name": "文献知识卡（Step 4）",
        "out": "03_cards",
        "instruction": (
            "为「已通过 DOI 双源核验」的文献逐篇生成知识卡。"
            "题录字段只能抄写脚本给出的核验结果，不得改写或补充。"
            "若只能看到摘要，请把「全文状态」写为「仅摘要」，"
            "无法判断的字段写「未获取」，不得推测。"),
        "item_hint": ('{"citekey":"smith2024","doi":"10.x/y","title":"","venue":"","year":"",'
                      '"question":"","theory":"","method":"","sample":"","stats":"","results":"",'
                      '"limits":"","relation":"","cite_point":"","evidence":["doi:10.x/y"]}'),
    },
    6: {
        "name": "稿件对标（Step 6）",
        "out": "06_benchmark",
        "instruction": (
            "按九个维度，把稿件主张与已核验文献逐项对标。"
            "manuscript_claim 必须回指原文段落号；literature_evidence 只能引用已核验 DOI。"
            "没有文献支持时 verdict 写「无文献支持」，不要编造对比对象。"),
        "item_hint": ('{"dimension":"","manuscript_claim":"","manuscript_refs":["P12"],'
                      '"literature_evidence":[{"doi":"10.x/y","point":""}],"verdict":"",'
                      '"gap":"","suggestion":"","confidence":"中","evidence":["P12","doi:10.x/y"]}'),
    },
    9: {
        "name": "更新领域知识库（Step 9）",
        "out": "04_knowledge_base",
        "instruction": (
            "阅读「本机领域知识库」中已有的卡片清单与实体条目，归纳或修订五类条目："
            "共识 / 争议 / 空白 / 规范 / 伦理。"
            "每条 claim 必须由库内卡片支撑，evidence 只能填库内已有的 citekey，"
            "禁止引用库外文献、禁止新造 citekey。"
            "已被库中条目覆盖的内容不要重复提出；确无支撑就少写，不要为凑数编造。"),
        "item_hint": '{"section":"共识","claim":"","evidence":["smith2024"],"confidence":"中"}',
    },
    7: {
        "name": "审稿问题清单（Step 7）",
        "out": "05_review",
        "instruction": (
            "基于脚本给出的事实与对标结果，起草主要问题清单。"
            "location 必须回指原文段落号；问题描述区分「稿件事实」与「审稿推断」。"
            "不得提出稿件中并不存在的问题，不得编造文献。"),
        "item_hint": ('{"title":"","location":"P12","problem":"","suggestion":"",'
                      '"kind":"审稿意见","confidence":"中","evidence":["P12"]}'),
    },
}


# ---------------------------------------------------------------- 校验闸门
def verify_refs(refs, ctx):
    """逐条核对证据引用能否落地。返回 (ok_refs, bad_refs)。"""
    ok, bad = [], []
    seen_p = ctx["seen_p"]
    seen_doi = ctx["seen_doi"]
    for r in refs or []:
        r = str(r).strip()
        m = PARA_REF_RE.match(r)
        if m:
            (ok if int(m.group(1)) in seen_p else bad).append(r)
            continue
        if r.lower().startswith("doi:"):
            d = r[4:].strip().lower().replace("https://doi.org/", "")
            (ok if d in seen_doi else bad).append(r)
            continue
        bad.append(r)
    return ok, bad


def validate(step, items, ctx):
    """对模型产出做硬校验。返回 (items, issues[])。"""
    issues = []
    libkeys = (ctx.get("library") or dict()).get("citekeys") or set()
    for it in items:
        if step == 9:
            # Step 9 的证据是「库内 citekey」，与 doi:/P## 不同命名空间，单独校验
            ev9 = [str(e) for e in (it.get("evidence") or [])]
            bad9 = [e for e in ev9 if e not in libkeys]
            it["_refs_ok"] = bool(ev9) and not bad9
            it["_refs_bad"] = bad9
            if not ev9 or bad9:
                it["unverified"] = True
                it["confidence"] = "需人工确认"
                issues.append({"level": "error", "item": it.get("section"),
                               "msg": "知识库条目必须引用库内 citekey（无效或缺失：%s），"
                                      "已降级为「需人工确认」，入库时将被拒绝" % ("、".join(bad9) or "无")})
        else:
            refs = list(it.get("evidence") or []) + list(it.get("manuscript_refs") or [])
            for le in (it.get("literature_evidence") or []):
                if isinstance(le, dict) and le.get("doi"):
                    refs.append("doi:" + str(le["doi"]))
            ok, bad = verify_refs(refs, ctx)
            it["_refs_ok"] = ok
            it["_refs_bad"] = bad

            # 1) 引用无法落地 → 降级，不允许静默保留
            if bad:
                it["unverified"] = True
                it["confidence"] = "需人工确认"
                issues.append({"level": "error", "item": it.get("title") or it.get("citekey") or it.get("dimension"),
                               "msg": "证据引用无法落地：" + "、".join(bad) + "，已降级为「未验证/需人工确认」"})
            if not ok:
                it["unverified"] = True
                it["confidence"] = "需人工确认"
                issues.append({"level": "warn", "item": it.get("title") or it.get("citekey") or it.get("dimension"),
                               "msg": "未附任何可核验的证据引用，已降级为「未验证/需人工确认」"})

        # 2) 输出中的 DOI 必须已在双源核验通过清单内，否则视为幻觉
        blob = json.dumps(it, ensure_ascii=False)
        for d in set(x.lower() for x in DOI_RE.findall(blob)):
            if d not in ctx["seen_doi"]:
                it["unverified"] = True
                it["confidence"] = "需人工确认"
                issues.append({"level": "error", "item": it.get("title") or it.get("citekey") or it.get("dimension"),
                               "msg": "输出含未通过双源核验的 DOI：%s（已拦截，不得作为文献事实）" % d})

        # 3) 结构完整性（必填字段不得留空）
        if step == 4:
            for f in ("citekey", "title", "relation"):
                if not str(it.get(f) or "").strip():
                    issues.append({"level": "warn", "item": it.get("citekey") or "?",
                                   "msg": "字段「%s」为空，落盘时写为「未获取」" % f})
        if step == 6:
            if it.get("verdict") in ("", None):
                issues.append({"level": "warn", "item": it.get("dimension"),
                               "msg": "verdict 为空，需人工判断"})
    return items, issues

def scan_text_dois(text, seen_doi):
    """扫描模型原始输出里的 DOI，拦截语料外的引用。"""
    bad = sorted(set(d.lower() for d in DOI_RE.findall(text or "") if d.lower() not in seen_doi))
    return bad


# ---------------------------------------------------------------- provider
def call_openai_compatible(cfg, system, user):
    payload = {
        "model": cfg["model"],
        "temperature": cfg.get("temperature", 0.2),
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "response_format": {"type": "json_object"},
    }
    req = urllib.request.Request(
        cfg["base_url"].rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + cfg["api_key"]},
        method="POST")
    with urllib.request.urlopen(req, timeout=cfg.get("timeout", 90)) as resp:
        data = json.loads(resp.read().decode("utf-8", "ignore"))
    return (data.get("choices") or [])[0].get("message", dict()).get("content", "")


def _stub_items(step, ctx):
    """stub：不产生任何模型判断，只用脚本事实拼出结构合法的产出。

    目的是验证链路（引用校验、闸门、写回）本身可跑通；
    所有需要模型归纳的字段都显式写成占位，绝不伪装成真实分析。
    """
    STUB = "（stub 占位：未接入模型，此处应为模型归纳）"
    if step == 9:
        keys = sorted((ctx.get("library") or {}).get("citekeys") or [])
        out = []
        for sec in ("共识", "争议", "空白", "规范", "伦理"):
            out.append({"section": sec,
                        "claim": "（stub 占位：未接入模型，此处应为基于库内 " + str(len(keys)) + " 张卡片的归纳）",
                        "evidence": keys[:2],   # stub 也不许凭空引用：证据取自库内真实 citekey
                        "confidence": "需人工确认"})
        return out

    rows = ctx["verified"]
    if step == 4:
        picked = ctx["top_records"] or rows[:3]
        items, used = [], {}
        today = time.strftime("%Y-%m-%d")
        for r in picked:
            au = r.get("authors") or []
            first = (au[0] if au else "") or "anon"
            year = str(r.get("year") or "")
            key = re.sub(r"[^a-z]", "", first.lower())[:20] + year
            if not key or key == year:
                key = "anon" + year
            used[key] = used.get(key, 0) + 1
            if used[key] > 1:                       # 同作者同年份：加后缀，避免卡片互相覆盖
                key = "%s-%d" % (key, used[key])
            cite = []
            src = r.get("authors_source") or ""
            if r.get("cited_by_crossref") or r.get("cited_by_openalex"):
                cite.append("被引 %s/%s" % (r.get("cited_by_crossref") or "—",
                                            r.get("cited_by_openalex") or "—"))
            items.append({
                "citekey": key or "unknown",
                "authors": "、".join(au) if au else "未获取",
                "doi": r.get("doi") or "",
                "title": r.get("title") or "",
                "venue": r.get("venue") or "",
                "year": r.get("year") or "",
                "source": "DOI 双源核验通过（Crossref%s）· %s%s" % (
                    "+OpenAlex" if r.get("openalex") else "", today,
                    ("　" + "　".join(cite)) if cite else ""),
                "fulltext_status": "仅题录摘要（未获取全文）" if not r.get("abstract") else "题录+摘要",
                "question": STUB, "theory": STUB, "method": STUB, "sample": "未获取",
                "stats": "未获取", "results": STUB, "limits": STUB,
                "relation": STUB, "cite_point": STUB,
                "evidence": ["doi:" + (r.get("doi") or "")],
            })
        return items
    if step == 6:
        seg = {s["role"]: s for s in ctx["segments"]}
        items = []
        for d in DIMENSIONS:
            items.append({
                "dimension": d, "manuscript_claim": STUB,
                "manuscript_refs": [], "literature_evidence": [],
                "verdict": "需人工判断（stub）", "gap": STUB, "suggestion": STUB,
                "confidence": "需人工确认", "evidence": [],
            })
        return items
    if step == 7:
        items = []
        # 先承接 Step 6 的对标发现（若已跑过），再补解析层面的声明缺失
        for f in (ctx.get("benchmark") or [])[:4]:
            if f.get("verdict") in ("证据一致", "一致"):
                continue
            items.append({
                "title": "对标发现：%s" % (f.get("dimension") or "未命名维度"),
                "location": "、".join(f.get("manuscript_refs") or []) or (ctx["first_body_ref"] or ""),
                "problem": "（来自 Step 6 对标）%s；差距：%s" % (f.get("manuscript_claim") or "—",
                                                                 f.get("gap") or "—"),
                "suggestion": STUB, "kind": "审稿意见", "confidence": "需人工确认",
                "evidence": list(f.get("manuscript_refs") or []) or ([ctx["first_body_ref"]]
                                                                     if ctx["first_body_ref"] else []),
            })
        for fact in (ctx["parse"].get("author_requests") or [])[:3]:
            items.append({
                "title": "稿件需补充：%s" % fact,
                "location": ctx["first_body_ref"] or "",
                "problem": "解析器未识别到：%s。此为待核对事实，不代表作者未做（解析器 note：%s）" % (
                    fact, (ctx["parse"].get("note") or "")[:40]),
                "suggestion": STUB, "kind": "审稿意见", "confidence": "需人工确认",
                "evidence": [ctx["first_body_ref"]] if ctx["first_body_ref"] else [],
            })
        return items
    return []


# ---------------------------------------------------------------- 主入口
def draft(step, ctx, cfg):
    """生成草案：构建受约束的上下文 → 调用 provider → 校验。"""
    spec = SPECS.get(step)
    if not spec:
        raise ValueError("不支持的步骤：%s" % step)

    facts = {
        "稿件题录": {k: ctx["meta"].get(k) for k in ("title", "domain", "scenario", "language", "journal")},
        "解析统计": ctx["parse"].get("stats") or {},
        "解析发现的待核对项": ctx["parse"].get("author_requests") or ctx["parse"].get("missing_statements") or [],
        "已核验文献数": len(ctx["verified"]),
        "可引用文献（题录为脚本核验结果）": [
            {"doi": r.get("doi"), "title": r.get("title"), "venue": r.get("venue"),
             "year": r.get("year"), "cited_by": r.get("cited_by_crossref") or r.get("cited_by_openalex")}
            for r in (ctx["top_records"] or ctx["verified"][:12])],
        "本机领域知识库（跨稿件累积，引用时标注 citekey）": (ctx.get("library") or {}).get("text") or "（库为空，本轮为首篇）",
        "发送的稿件段落（引用时写作 P<编号>）": [
            {"p": "P%d" % s["p"], "role": s["role"], "text": s["text"]} for s in ctx["segments"]],
    }
    system = (
        "你是学术审稿助手，只做归纳与措辞，不产生新事实。\n"
        "硬约束：\n"
        "1) 凡涉及文献题录、DOI、年份、期刊，只能抄写给定事实，不得凭记忆补充。\n"
        "2) 每条产出必须带 evidence 引用；引用稿件用 P<编号>（必须来自给定段落），"
        "引用文献用 doi:<DOI>（必须来自给定可引用文献清单）。\n"
        "3) 无法判断的字段写「未获取」或「需人工确认」，禁止推测。\n"
        "4) 领域知识库只作为已核验文献的索引使用：其中 citekey 可复用，但不得据此推断出新文献、新 DOI 或新结论。\n"
        "5) 只输出 JSON，形如 {\"items\": [ ... ]}。\n"
        "任务：%s\n%s\n单条结构示例：%s" % (spec["name"], spec["instruction"], spec["item_hint"]))
    user = json.dumps(facts, ensure_ascii=False)

    t0 = time.time()
    provider = cfg["provider"]
    if provider == "stub":
        items = _stub_items(step, ctx)
        raw = json.dumps({"items": items}, ensure_ascii=False)
        note = "stub 模式：结构由脚本生成，模型判断字段为占位，仅用于验证链路。"
    else:
        raw = call_openai_compatible(cfg, system, user)
        note = ""
        try:
            items = json.loads(raw).get("items") or []
        except Exception:
            items = []
            note = "模型返回非 JSON，已丢弃（不写入工作区）。"

    items, issues = validate(step, items, ctx)
    halluc = scan_text_dois(raw, ctx["seen_doi"])
    if halluc:
        issues.append({"level": "error", "item": "（全局）",
                       "msg": "模型输出含未通过双源核验的 DOI：" + "、".join(halluc) + "，已拦截。"})
    return {
        "step": step, "step_name": spec["name"], "provider": provider, "model": cfg["model"],
        "elapsed": round(time.time() - t0, 2), "items": items, "issues": issues,
        "sent": ctx["sent_meta"], "raw_chars": len(raw), "note": note,
        "context_preview": [{"p": "P%d" % s["p"], "role": s["role"], "chars": len(s["text"])}
                            for s in ctx["segments"]],
    }
