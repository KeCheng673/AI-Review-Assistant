# 学术审稿专家 · 智能工作台

> [English](README.md) | **简体中文**

一个本地跑的学术审稿助手：**从作者自己的参考文献里抽 N 篇 → 抓摘要建本篇知识库 → 模型读库出审稿意见 → 经验沉淀进按类别归档的总知识库**。

四步，没有多余的点击。

---

## 一、四步流程

| 步 | 做什么 | 产物 |
|---|---|---|
| **1 准备稿件** | 上传稿件，定文章类别、场景、语言、审稿重点 | `meta.json` |
| **2 抽参考文献建库** | 解析参考文献 → 随机抽 N 篇（篇数可设）→ 抓摘要 → 建本篇知识库 | `01_refs/` · `02_abstracts/` |
| **3 读库出意见** | 模型先读完知识库的文献摘要，再结合稿件给意见 | `03_review/review.md` |
| **4 沉淀经验** | 摘要卡片 + 审稿经验按类别写入总知识库 | `_library/<类别>/` |

**刻意去掉的**：审稿量表（决定项）、截稿日期、外部文献检索。
**文献来源只有一处**：作者自己的参考文献。

---

## 二、目录结构

```
AI Review Assistant/
  server.py          HTTP 服务（886 行，17 个接口）
  refs.py            参考文献抽取与题录解析
  kb.py              知识库（本篇 + 总库分类归档）
  fetcher.py         摘要抓取适配器（browser-act 主 + API 兜底）
  llm.py             模型调用与配置
  index.html         前端（四步）
  real-mode.js       前端逻辑
  config.json        模型配置

  review-workspace/
    .current                     指向当前工作区
    <稿件 slug>/
      00_manuscript/             原稿
      01_refs/                   refs.json（全量题录 + 本次抽中记录）· refs.md（人读清单）
      02_abstracts/              每篇 <citekey>.json · kb.md（汇总，给模型读）
      03_review/                 review.md
    _library/
      index.json                 顶层类别索引
      <类别>/                    如「统计学-函数型数据」
        experience.md            审稿经验累积（只存经验，不存文献卡片）
        index.json               该类别索引（经验条数）
```

**知识库现在用文件夹。** `kb.py` 的每个 `save_*` 都返回 dict，接数据库时只换这一层，上层不动。

---

## 三、快速开始

### 3.1 环境要求

| 项 | 要求 | 说明 |
|---|---|---|
| **Python** | 3.10 及以上 | 除 PyMuPDF 外全用标准库 |
| **PyMuPDF** | >= 1.23 | 解析 PDF 稿件与参考文献（`import fitz`） |
| 浏览器 | Chrome / Edge 任一 | 中文摘要抓取需要（可选） |
| 磁盘 | 约 500 MB | 工作区要存原稿、摘要和审稿产物 |
| 网络 | 能访问 `api.deepseek.com` | 模型调用 |

### 3.2 安装

```bash
git clone <你的仓库地址>
cd "AI Review Assistant"

# 建虚拟环境（推荐，避免污染系统 Python）
python -m venv .venv

# Windows 激活
.venv\Scripts\activate
# macOS / Linux 激活
source .venv/bin/activate

# 装依赖（只有一个包）
pip install -r requirements.txt
```

### 3.3 配置密钥

```bash
# Windows
copy config.example.json config.json
# macOS / Linux
cp config.example.json config.json
```

然后编辑 `config.json`，把 `api_key` 换成你自己的：

```json
{
  "llm": {
    "provider": "openai",
    "base_url": "https://api.deepseek.com/v1",
    "model": "deepseek-flash",
    "api_key": "sk-你的key",
    "privacy_mode": "fulltext"
  }
}
```

**Key 获取**：<https://platform.deepseek.com/api_keys>

> `config.json` 已在 `.gitignore` 中，**不会**上传到 GitHub。
> 仓库里只有 `config.example.json` 这个不含真实密钥的模板。

### 3.4 启动

```bash
python server.py --port 8770
```

浏览器打开 <http://127.0.0.1:8770/>

**改完前端不用重启**（`server.py` 每次请求都重读文件）；**改完 `.py` 必须重启**。

### 3.5 可选：抓摘要引擎 browser-act

**不装也能用**，只是英文摘要抓取率从约 80% 降到约 40%（只剩 CrossRef / OpenAlex 两条开放 API）。

```bash
# 装法取决于你的环境，装完确认命令在 PATH 里
which browser-act        # Windows: where browser-act
```

`fetcher.py` 会依次尝试：`PATH` → `~/.local/bin/browser-act.exe` → `~/.local/bin/browser-act`。

**中文摘要必须依赖知网登录态**，见第六章。

---

## 四、哪些文件必须有、哪些会自动生成

### 必须随仓库提供的（缺了跑不起来）

| 文件 | 作用 |
|---|---|
| `server.py` | HTTP 服务本体，17 个接口 |
| `refs.py` | 参考文献抽取与题录解析（依赖 PyMuPDF） |
| `kb.py` | 知识库读写（本篇 + 总库分类归档） |
| `fetcher.py` | 摘要抓取（CrossRef / OpenAlex / browser-act / 知网） |
| `llm.py` | 模型调用与配置读写 |
| `index.html` | 前端页面（四步界面） |
| `real-mode.js` | 前端逻辑（真实模式，由服务端注入） |
| `config.example.json` | 配置模板，**必须复制成 `config.json` 才能跑** |
| `requirements.txt` | 依赖清单 |

### 自动生成 / 本地产物（不需要提交，已在 `.gitignore`）

| 项 | 何时产生 | 说明 |
|---|---|---|
| `config.json` | 你手动复制模板后 | **含真实 API Key，绝不入库** |
| `review-workspace/` | 第一次上传稿件时 | 含未发表原稿，**默认不入库** |
| `server.log` | 启动后 | 运行日志 |
| `__pycache__/` | 首次运行 | Python 字节码缓存 |
| `llm-last-test.json` | 点「测试连接」后 | 最近一次连通性测试结果 |
| `.wpscomate/` | WPS Comate 打开目录时 | 与本项目无关的工具缓存 |

### 可选文档（可以提交，不影响运行）

| 文件 | 内容 |
|---|---|
| `README.md` / `README.zh-CN.md` | 本文件（中英双版） |
| `学术审稿助手_产品方案.html` | 产品设计文档 |

---

## 五、模型配置

`config.json` 三个字段都必须对：

```json
{
  "base_url": "https://api.deepseek.com/v1",
  "model": "deepseek-flash",
  "api_key": "sk-..."
}
```

| 坑 | 说明 |
|---|---|
| `base_url` 少 `/v1` | 能通 chat 但容易出怪问题，建议带 |
| `model` 写成 `deepseek-chat` | 该模型已下线，现在只有 `deepseek-flash` / `deepseek-v4-pro` |
| `model` 留占位符 | `save_config` 会拒绝写入 `stub` 开头的值 |

**测试连接失败 ≠ 审稿失败**：`response_format: json_object` 要求提示词里出现 "json"。测试连接的提示词已按此改写。

---

## 六、抓取引擎

界面只有一个「开始抓取摘要」按钮，后端按文献语种自动分流，不需要手动选引擎。

### 中文文献 → 知网登录态

```
专业检索页 /kns8s/AdvSearch?type=expert
   ↓ 输入 TI='完整标题'（精确限定篇名）
   ↓ 点「检索」
   ↓ 从结果页取 kcms2/article/abstract?v=... 链接
   ↓ 打开文章页
   ↓ 读 #ChDivSummary.innerText
```

单篇约 30 秒。三个必须知道的前提：

| 前提 | 说明 |
|---|---|
| 登录态存在 | cookie `Ecp_LoginStuts` 里有 `UserName` |
| 浏览器窗口不能关 | 会话断了就抓不了，界面有「重新检查」按钮 |
| 一次只能跑一篇 | 同一会话不能并发，代码里有锁 |

**用 `TI=` 而不是主题检索**：中文期刊里「前半段同名」的文章极多，
主题检索会把别人的文章排在前面（实测「港口绿色发展效率」被搜成「三甲医院运行效率」）。

**相似度门槛 0.80**：低于此一律标记未获取，绝不把别人的摘要安到这篇上。

### 英文文献 → 三级链路

| 顺序 | 方法 | 覆盖 |
|---|---|---|
| 1 | CrossRef（带 mailto 进 polite pool） | 开放获取期刊 |
| 2 | OpenAlex（标题相似度校验） | 开放获取期刊 |
| 3 | **真浏览器打开 doi.org 读 `#abstracts`** | **Elsevier / Wiley / T&F** |
| 4 | browser-act stealth-extract | 兜底 |

第 3 步是拿 Elsevier 摘要的唯一办法：ScienceDirect 用 Cloudflare 人机验证，
`stealth-extract` 会被挡，但**有头的真浏览器能过**（会自动下发 `cf_clearance`）。

DOI 优先：先 `CrossRef bibliographic` 查 DOI（英文命中率高），再打开 `doi.org`。

### 实测结果

| 稿件 | 结果 |
|---|---|
| 中文稿（抽 10：中文 2 + 英文 8） | **10/10**，耗时 115 秒 |
| 英文稿（抽 10，含 Elsevier 5 篇） | 8/10（2 篇是书和限流） |

## 七、接口

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/state` | 服务状态 + 当前工作区 |
| GET | `/api/refs` | 参考文献统计 + 抽中清单 |
| GET | `/api/kb` | 本篇知识库（卡片 + kb.md） |
| GET | `/api/abstracts/progress` | 抓取进度 |
| GET | `/api/tree` `/api/file` `/api/download` | 文件浏览 |
| GET | `/api/llm/status` | 模型连接状态 |
| POST | `/api/task` `/api/upload` | 创建工作区 · 上传稿件 |
| POST | `/api/meta` | 更新类别 / 场景 / 语言 / 重点 |
| POST | `/api/refs/extract` | 抽参考文献 + 随机抽样（`{n, seed}`） |
| POST | `/api/abstracts/fetch` | 启动后台抓取（`{engine}`） |
| POST | `/api/kb/build` | 重建本篇知识库 |
| POST | `/api/review/draft` | 读库出审稿意见 |
| POST | `/api/archive` | 沉淀进总库（`{experience}`） |
| POST | `/api/llm/config` | 保存模型配置 |

**抓取是后台任务**：`/api/abstracts/fetch` 立刻返回，前端轮询 `/api/abstracts/progress`。

---

## 八、参考文献解析

实测（31 MB 博士学位论文）：

```
259 条，序号 1..259 零断裂
可抓摘要 233 条
中文 30 / 英文 229
类型：J 228 · M 9 · R 7 · D 7 · J/OL 4 · EB/OL 3 · C 1
```

**两个必须处理的坑**：

1. **PDF 水印会被拆成碎片嵌进正文** —— 学位论文的「学位中心学位论文质量监测平台」会插进标题中间（`...in diverse 学位中心学位ship types...`）。`INLINE_NOISE_RE` 负责清行内水印。
2. **标题里的 `U.S.` 会被当成句末** —— 作者/标题边界不能用「最后一个句点」，要按姓名模式（`Goetz S.` / `et al.`）定位。

---

## 九、已知限制

- **中文期刊摘要抓取率低**：靠开放 API 只有约 3/10；期刊官网链路已验通但未接入
- **CNKI / 万方需要登录态**，且检索页有滑块，匿名抓取过不去
- **工作区只保留 4 层**：`00_manuscript` / `01_refs` / `02_abstracts` / `03_review`，外加 `meta.json`
- 参考文献格式支持两种：`[1] …[J]`（编号式）与 `Ang, A. (2006). …`（作者-年份式）

---

## 十、排查

**页面还是旧样子** → `Ctrl + F5` 强制刷新（`real-mode.js` 由服务端注入，普通刷新可能读缓存）。

**端口被占用** → Windows 默认 `allow_reuse_address=True`，两个进程会静默共存，旧代码可能还在响应。

```bash
netstat -ano | grep ":8770.*LISTENING"
powershell -NoProfile -Command "Stop-Process -Id <PID> -Force"
```

**PDF 读不出字** → 中文 PDF 常缺 ToUnicode，`pypdf` 会整页提成空白。必须用 PyMuPDF。

**抓取全失败** → 看 `/api/abstracts/progress` 里每篇的 `note`，会写明是「无 DOI 暂不支持」还是「未命中」。
