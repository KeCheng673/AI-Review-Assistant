# AI Review Assistant · Local Academic Review Workbench

> **English** | [简体中文](README.zh-CN.md)

A local-first assistant for academic peer review:

**take N references from the author's own bibliography → fetch their abstracts → build a paper-specific knowledge base → let the model read that base and draft a review → archive the distilled experience into a category-indexed master library.**

Four steps. No extra clicks.

---

## 1. The Four-Step Workflow

| Step | What happens | Output |
|---|---|---|
| **1 Prepare** | Upload the manuscript; set category, scenario, language, review focus | `meta.json` |
| **2 Build refs** | Parse the bibliography → randomly sample N papers (configurable) → fetch abstracts → build the paper-specific KB | `01_refs/` · `02_abstracts/` |
| **3 Draft review** | The model reads the whole KB first, then reviews the manuscript | `03_review/review.md` |
| **4 Distill** | Abstract cards + review experience are filed into the master library by category | `_library/<category>/` |

**Deliberately left out**: scoring rubrics (decision items), submission deadlines, external literature search.

**One single source of literature**: the author's own reference list.

---

## 2. Directory Layout

```
AI Review Assistant/
  server.py          HTTP service (886 lines, 17 endpoints)
  refs.py            Reference extraction and bibliographic parsing
  kb.py              Knowledge base (per-paper + category-indexed master)
  fetcher.py         Abstract fetchers (browser-act primary, APIs as fallback)
  llm.py             Model invocation and config I/O
  index.html         Frontend (four steps)
  real-mode.js       Frontend logic (real mode, injected by the server)
  config.example.json  Config template — copy to config.json

  review-workspace/
    .current                     Points to the active workspace
    <paper-slug>/
      00_manuscript/             Original manuscript
      01_refs/                   refs.json (all records + this run's sample) · refs.md (human-readable)
      02_abstracts/              One <citekey>.json per paper · kb.md (aggregate, read by the model)
      03_review/                 review.md
    _library/
      index.json                 Top-level category index
      <category>/                e.g. "Statistics-Functional Data"
        experience.md            Accumulated review experience (experience only, no paper cards)
        index.json               Category index (number of entries)
```

**The knowledge base is just folders.** Every `save_*` in `kb.py` returns a dict, so swapping in a database only touches that layer — nothing above it changes.

---

## 3. Quick Start

### 3.1 Requirements

| Item | Requirement | Notes |
|---|---|---|
| **Python** | 3.10+ | Standard library only, plus PyMuPDF |
| **PyMuPDF** | >= 1.23 | Parsing manuscript and reference PDFs (`import fitz`) |
| Browser | Chrome or Edge | Needed for Chinese abstract fetching (optional) |
| Disk | ~500 MB | Workspace stores manuscripts, abstracts, review output |
| Network | Reachable `api.deepseek.com` | Model calls |

### 3.2 Install

```bash
git clone <your-repo-url>
cd "AI Review Assistant"

# Recommended: use a virtual environment
python -m venv .venv

# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

# Install dependencies (only one package)
pip install -r requirements.txt
```

### 3.3 Configure the API key

```bash
# Windows
copy config.example.json config.json
# macOS / Linux
cp config.example.json config.json
```

Then edit `config.json` and replace `api_key` with your own:

```json
{
  "llm": {
    "provider": "openai",
    "base_url": "https://api.deepseek.com/v1",
    "model": "deepseek-flash",
    "api_key": "sk-your-key-here",
    "privacy_mode": "fulltext"
  }
}
```

**Get a key**: <https://platform.deepseek.com/api_keys>

> `config.json` is listed in `.gitignore` and is **never** committed.
> The repo only ships `config.example.json`, which contains no real credentials.

### 3.4 Run

```bash
python server.py --port 8770
```

Open <http://127.0.0.1:8770/>

**Frontend changes need no restart** (`server.py` re-reads the files on every request). **Python changes do require a restart.**

### 3.5 Optional: browser-act for abstract fetching

**Works without it** — English abstract coverage drops from roughly 80% to roughly 40% (only the CrossRef / OpenAlex open APIs remain).

```bash
# Install per your platform, then make sure the command is on PATH
which browser-act        # Windows: where browser-act
```

`fetcher.py` probes, in order: `PATH` → `~/.local/bin/browser-act.exe` → `~/.local/bin/browser-act`.

**Chinese abstracts require an authenticated CNKI session** — see section 6.

---

## 4. Which Files Are Required, Which Are Generated

### Required (the app won't start without these)

| File | Role |
|---|---|
| `server.py` | HTTP service, 17 endpoints |
| `refs.py` | Reference extraction and bibliographic parsing (uses PyMuPDF) |
| `kb.py` | Knowledge base read/write (per-paper + category master) |
| `fetcher.py` | Abstract fetchers (CrossRef / OpenAlex / browser-act / CNKI) |
| `llm.py` | Model invocation and config handling |
| `index.html` | Frontend page (the four-step UI) |
| `real-mode.js` | Frontend logic (real mode, injected by the server) |
| `config.example.json` | Config template — **must be copied to `config.json` to run** |
| `requirements.txt` | Dependency list |

### Generated locally (never commit; already gitignored)

| Item | Created when | Notes |
|---|---|---|
| `config.json` | You copy the template | **Contains a real API key — never commit** |
| `review-workspace/` | First manuscript upload | Contains unpublished manuscripts — **gitignored by default** |
| `server.log` | On startup | Runtime log |
| `__pycache__/` | First run | Python bytecode cache |
| `llm-last-test.json` | After clicking "Test connection" | Last connectivity test result |
| `.wpscomate/` | When WPS Comate opens the folder | Unrelated tool cache |

### Optional docs (safe to commit, not needed to run)

| File | Content |
|---|---|
| `README.md` / `README.zh-CN.md` | This file (bilingual) |
| `学术审稿助手_产品方案.html` | Product design document |

---

## 5. Model Configuration

All three fields in `config.json` must be correct:

```json
{
  "base_url": "https://api.deepseek.com/v1",
  "model": "deepseek-flash",
  "api_key": "sk-..."
}
```

| Pitfall | Explanation |
|---|---|
| `base_url` missing `/v1` | Chat may work but behaves oddly — keep `/v1` |
| `model` set to `deepseek-chat` | That model is retired; only `deepseek-flash` / `deepseek-v4-pro` exist now |
| `model` left as a placeholder | `save_config` rejects values starting with `stub` |

**A failed connection test ≠ failed review**: `response_format: json_object` requires the word "json" to appear in the prompt. The connection-test prompt has been rewritten accordingly.

---

## 6. Fetch Engines

The UI exposes a single "Fetch abstracts" button. The backend routes by language automatically — no manual engine selection.

### Chinese papers → authenticated CNKI session

```
Expert search page /kns8s/AdvSearch?type=expert
   ↓ enter TI='full title' (title-exact)
   ↓ click Search
   ↓ extract kcms2/article/abstract?v=... links from the result page
   ↓ open the article page
   ↓ read #ChDivSummary.innerText
```

About 30 seconds per paper. Three preconditions:

| Precondition | Explanation |
|---|---|
| Session exists | Cookie `Ecp_LoginStuts` contains `UserName` |
| Browser window must stay open | Closing it breaks the session; the UI has a "Re-check" button |
| One paper at a time | The same session cannot run concurrently; a lock is enforced in code |

**Use `TI=` rather than a topic search**: Chinese journals are full of papers sharing the first half of a title; a topic search ranks other people's papers first (observed in practice: a search for "port green development efficiency" returned "tertiary hospital operating efficiency").

**Similarity threshold 0.80**: anything below is marked as not fetched. An abstract belonging to another paper is never pasted onto this one.

### English papers → four-level fallback

| Order | Method | Coverage |
|---|---|---|
| 1 | CrossRef (with `mailto` to enter the polite pool) | Open-access journals |
| 2 | OpenAlex (with title similarity check) | Open-access journals |
| 3 | **Real browser opens doi.org and reads `#abstracts`** | **Elsevier / Wiley / T&F** |
| 4 | browser-act `stealth-extract` | Last resort |

Step 3 is the only way to get Elsevier abstracts: ScienceDirect sits behind Cloudflare human verification, which `stealth-extract` cannot pass, but a **headful real browser can** (it receives `cf_clearance` automatically).

DOI-first: query CrossRef `bibliographic` for the DOI (high hit rate for English), then open `doi.org`.

### Measured results

| Manuscript | Result |
|---|---|
| Chinese paper (sample 10: 2 Chinese + 8 English) | **10/10**, 115 seconds |
| English paper (sample 10, including 5 Elsevier) | 8/10 (2 misses: one book, one rate-limited) |

---

## 7. Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/state` | Service status + current workspace |
| GET | `/api/refs` | Reference stats + sampled list |
| GET | `/api/kb` | Paper-specific KB (cards + kb.md) |
| GET | `/api/abstracts/progress` | Fetch progress |
| GET | `/api/tree` `/api/file` `/api/download` | File browsing |
| GET | `/api/llm/status` | Model connection status |
| POST | `/api/task` `/api/upload` | Create workspace · upload manuscript |
| POST | `/api/meta` | Update category / scenario / language / focus |
| POST | `/api/refs/extract` | Extract references + random sampling (`{n, seed}`) |
| POST | `/api/abstracts/fetch` | Start background fetching (`{engine}`) |
| POST | `/api/kb/build` | Rebuild the paper-specific KB |
| POST | `/api/review/draft` | Read the KB and draft the review |
| POST | `/api/archive` | File into the master library (`{experience}`) |
| POST | `/api/llm/config` | Save model configuration |

**Fetching is a background task**: `/api/abstracts/fetch` returns immediately and the frontend polls `/api/abstracts/progress`.

---

## 8. Reference Parsing

Measured on a 31 MB doctoral dissertation:

```
259 entries, serial numbers 1..259 with no gaps
233 fetchable abstracts
Chinese 30 / English 229
Types: J 228 · M 9 · R 7 · D 7 · J/OL 4 · EB/OL 3 · C 1
```

**Two pitfalls that must be handled**:

1. **PDF watermarks get shredded into the body text** — the dissertation quality-monitoring platform inserts its mark mid-title (`...in diverse 学位中心学位ship types...`). `INLINE_NOISE_RE` strips in-line watermarks.
2. **`U.S.` inside a title reads as a sentence end** — the author/title boundary must not use "last period"; it is located by name patterns (`Goetz S.` / `et al.`).

---

## 9. Known Limitations

- **Low Chinese abstract coverage**: only about 3/10 through open APIs; the publisher-site path is verified but not wired in
- **CNKI / Wanfang require an authenticated session**, and their search pages have sliders — anonymous fetching fails
- **The workspace keeps four layers only**: `00_manuscript` / `01_refs` / `02_abstracts` / `03_review`, plus `meta.json`
- Two reference formats supported: `[1] …[J]` (numbered) and `Ang, A. (2006). …` (author–year)

---

## 10. Troubleshooting

**Page still looks old** → `Ctrl + F5`. `real-mode.js` is injected server-side, so a normal refresh may serve a cached copy.

**Port already in use** → Windows defaults to `allow_reuse_address=True`, so two processes can coexist silently and the old code may still be answering.

```bash
netstat -ano | grep ":8770.*LISTENING"
powershell -NoProfile -Command "Stop-Process -Id <PID> -Force"
```
