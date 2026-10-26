/* =========================================================================
   学术审稿专家 · 真实模式适配层（由 server.py 注入，不修改 index.html）
   -------------------------------------------------------------------------
   双击 index.html            → 演示模式（原有 mock 逻辑，不发任何网络请求）
   python server.py 后访问    → 真实模式（调用技能脚本：解析/检索/核验/成文）

   本文件不改动原有演示逻辑，仅在检测到本地服务时接管若干按钮与面板。
   设计原则：所有展示的数据都来自脚本真实输出；未接入的能力显式标注为
   「未接入」，不伪造成功结果。
   ========================================================================= */
(function () {
  'use strict';

  var API = '/api';
  var M = {
    mode: 'demo', state: null, slug: null, meta: null, file: null,
    parse: null, plan: null, rows: [], verify: [], searchStats: null,
    draft: null, cite: '',
    llm: null, autoDone: {}, curStep: null       // 模型接口状态（唯一入口在左栏）
  };

  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };
  var P = function (n) { return $('.panel[data-panel="' + n + '"]'); };
  var esc = function (s) {
    return String(s === null || s === undefined ? '' : s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  };
  var fmtSize = function (n) {
    return n > 1048576 ? (n / 1048576).toFixed(1) + ' MB' : (n / 1024).toFixed(0) + ' KB';
  };
  var num = function (v) {
    var m = /\d+/.exec(String(v === null || v === undefined ? '' : v));
    return m ? parseInt(m[0], 10) : 0;
  };
  var val = function (sel, root) { var e = $(sel, root); return e ? e.value : ''; };
  var vtrim = function (sel, root) { var e = $(sel, root); return e ? e.value.trim() : ''; };
  function pick(id) {
    var box = document.getElementById(id);
    if (!box) return [];
    return $$('.chip.on', box).map(function (c) { return c.dataset.val; });
  }
  function pickIn(sel) {
    var box = $(sel);
    if (!box) return [];
    return $$('.chip.on', box).map(function (c) { return c.dataset.val; });
  }

  /* ------------------------------------------------------------ 请求 */
  function req(path, opt) {
    opt = opt || {};
    return fetch(API + path, {
      method: opt.method || 'GET',
      headers: opt.headers || {},
      body: opt.body
    }).then(function (r) {
      return r.text().then(function (t) {
        var j = null;
        try { j = JSON.parse(t); } catch (e) { /* 非 JSON */ }
        if (!r.ok || !j || j.ok === false) {
          throw new Error((j && j.error) || ('HTTP ' + r.status + ' ' + t.slice(0, 180)));
        }
        return j;
      });
    });
  }
  function jpost(path, obj) {
    return req(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(obj || {})
    });
  }

  /* ------------------------------------------------------------ 片段 */
  function head(crumb, title, desc) {
    return '<div class="page-head"><div class="crumb">' + crumb + '</div>' +
      '<h2 class="page-title">' + title + '</h2>' +
      '<p class="page-desc">' + desc + '</p></div>';
  }
  function errCard(msg) {
    return '<div class="card"><div class="card-body"><div class="note warn">' +
      '<span class="ni">⚠</span><div><b>执行失败</b>：' + esc(msg) + '</div></div></div></div>';
  }
  function card(title, hint, body) {
    return '<div class="card"><div class="card-head"><h3>' + esc(title) + '</h3>' +
      (hint ? '<span class="hint">' + esc(hint) + '</span>' : '') + '</div>' +
      '<div class="card-body">' + body + '</div></div>';
  }
  function kpi(k, v, small) {
    return '<div class="kpi"><div class="k">' + esc(k) + '</div><div class="v">' + esc(v) +
      (small ? '<small>' + esc(small) + '</small>' : '') + '</div></div>';
  }
  function row2(k, v) {
    return '<tr><td class="strong" style="width:150px">' + esc(k) + '</td><td>' + v + '</td></tr>';
  }
  function pre(text, maxH) {
    return '<pre style="white-space:pre-wrap;font-size:12.5px;line-height:1.75;' +
      'background:#fafafa;border:1px solid var(--line);border-radius:8px;padding:14px;' +
      'max-height:' + (maxH || 460) + 'px;overflow:auto;margin:0">' + esc(text) + '</pre>';
  }
  function setPanel(n, html) {
    var p = P(n);
    if (p) p.innerHTML = html;
  }
  function btn(label, fn) {
    return '<button class="btn" onclick="' + fn + '">' + esc(label) + '</button>';
  }
  function btnP(label, fn) {
    return '<button class="btn btn-primary" onclick="' + fn + '">' + esc(label) + '</button>';
  }

  /* ------------------------------------------------------------ 模式标识 */
  function badge() {
    var right = $('.topbar-right');
    if (!right || $('#modeBadge')) return;
    var b = document.createElement('span');
    b.id = 'modeBadge';
    b.className = 'tag ' + (M.mode === 'real' ? 'ok' : 'gray');
    b.textContent = M.mode === 'real' ? '真实模式 · 本地服务已连接' : '演示模式 · 未连接服务';
    b.title = M.mode === 'real'
      ? ('技能目录：' + (M.state.skill_dir || '—') + '\n工作区根：' + (M.state.workspace_root || '—') +
         '\n解释器：' + (M.state.python || '—'))
      : '本页为演示数据。真实模式：运行 python server.py，再从 http://127.0.0.1:8770 打开。';
    right.insertBefore(b, right.firstChild);
  }
  function markMock() {
    /* Step 4/6/9 由 renderModelStep 接管；Step 7 由 renderStep7Model 注入状态条，
       模型接入只在左栏「模型接口」维护，此处不再统一贴「演示数据」标签。 */
  }

  /* ------------------------------------------------------------ 右栏 */
  var ICO = { md: '📝', json: '🧾', txt: '📄', docx: '📄', pdf: '📄', csv: '📊' };
  function refreshTree() {
    req('/tree').then(function (j) {
      var box = $('#tree');
      if (!box) return;
      var files = j.files || [];
      var html = '<div class="tree-folder"><div class="tf-n" onclick="toggleFolder(this)">' +
        '<span class="arw">▼</span>' + esc(j.slug || 'workspace') + '</div><div class="tf-kids">';
      if (!files.length) {
        html += '<div class="tf-f"><span class="fc">·</span>（空）</div>';
      } else {
        var groups = {};
        files.forEach(function (f) {
          var top = f.dir === '.' ? '.' : f.dir.split('/')[0];
          if (!groups[top]) groups[top] = [];
          groups[top].push(f);
        });
        Object.keys(groups).forEach(function (top) {
          if (top !== '.') html += '<div class="tf-f"><span class="fc">📁</span>' + esc(top) + '</div>';
          groups[top].forEach(function (f) {
            var label = top === '.' ? f.name : f.path.slice(top.length + 1);
            var ext = (f.name.split('.').pop() || '').toLowerCase();
            html += '<div class="tf-f new" title="' + esc(f.mtime + ' · ' + fmtSize(f.size)) +
              '" onclick="openWsFile(\'' + esc(f.path).replace(/'/g, '') + '\')">' +
              '<span class="fc">' + (ICO[ext] || '📄') + '</span>' + esc(label) + '</div>';
          });
        });
      }
      box.innerHTML = html + '</div></div>';
    }).catch(function () { /* 静默 */ });
  }
  window.openWsFile = function (path) {
    var ext = (path.split('.').pop() || '').toLowerCase();
    var url = (ext === 'md' || ext === 'json' || ext === 'txt')
      ? API + '/file?path=' + encodeURIComponent(path)
      : API + '/download?path=' + encodeURIComponent(path);
    window.open(url, '_blank');
  };
  function setMeter(label, pct, txt) {
    $$('.meter').forEach(function (m) {
      var lb = $('.m-lb', m);
      if (lb && lb.textContent.trim() === label) {
        $('.m-tr i', m).style.width = pct + '%';
        $('.m-v', m).textContent = txt || (pct + '%');
      }
    });
  }
  function setTitle(t) {
    var e = $('#topPaper'); if (e && t) e.textContent = t;
    var s = $('#sumTitle'); if (s && t) s.textContent = t;   // Step 0「上下文摘要」表也要跟着走
  }
  function setFile(f) {
    var s = $('#sumFile'); if (s) s.textContent = f || '—';
  }
  function syncDecisions() {
    var txt = pick('fDecision').join(' / ');
    $$('.ctx-item').forEach(function (it) {
      var b = $('b', it);
      if (b && b.textContent.trim() === '决定项') {
        var s = $('span', it);
        if (s && txt) s.textContent = txt;
      }
    });
  }

  /* ------------------------------------------------------------ Step 0 */
  function ensureInput() {
    var inp = $('#fileInput');
    if (!inp) {
      inp = document.createElement('input');
      inp.type = 'file';
      inp.id = 'fileInput';
      inp.style.display = 'none';
      inp.accept = '.docx,.md,.txt,.html,.htm,.pdf';
      inp.addEventListener('change', function () {
        if (inp.files && inp.files[0]) handleFile(inp.files[0]);
      });
      document.body.appendChild(inp);
    }
    return inp;
  }
  function ensureTask(title) {
    if (M.slug) return Promise.resolve();
    return jpost('/task', {
      title: title || '未命名稿件',
      domain: val('#fDomain'),
      scenario: val('#fScene'),
      language: pick('fLang')[0] || '中文',
      focus: pick('fFocus'),
      decisions: pick('fDecision')
    }).then(function (j) {
      M.slug = j.slug;
      M.meta = j.meta;
      setTitle(j.meta.title);
    });
  }
  function handleFile(f) {
    M.file = { name: f.name, size: f.size };
    setFile(f.name);
    var base = f.name.replace(/\.[^.]+$/, '');
    // 按文件名猜稿件语言，避免新工作区继承上一篇的语言设置
    var looksZh = /[\u4e00-\u9fa5]/.test(f.name);
    var lbox = document.getElementById('fLang');
    if (lbox) {
      $$('.chip', lbox).forEach(function (c) {
        var isEn = /english/i.test(c.dataset.val || '');
        c.classList.toggle('on', looksZh ? !isEn : isEn);
      });
    }
    // 每上传一份新稿件都开新工作区：旧工作区的题录与卡片不能带到新稿件上
    var oldSlug = M.slug;
    M.slug = null;
    M.meta = null;
    M.parse = null;
    M.parseResp = null;
    toast('已接收 ' + f.name + '，正在创建审稿工作区…');
    ensureTask(base)
      .then(function () {
        return fetch(API + '/upload', {
          method: 'POST',
          headers: { 'X-Filename': encodeURIComponent(f.name) },
          body: f
        }).then(function (r) { return r.json(); });
      })
      .then(function (j) {
        if (!j || !j.ok) throw new Error((j && j.error) || '上传失败');
        M.file.path = j.path;
        if (j.meta) M.meta = j.meta;
        if (j.meta && j.meta.slug) M.slug = j.meta.slug;
        var row = $('.panel[data-panel="0"] .file-row');
        if (row) {
          $('.file-name', row).textContent = M.file.name;
          $('.file-meta', row).textContent = fmtSize(j.size) + ' · 已写入 00_manuscript/ · 未发表稿';
          var t = $('.right .tag', row);
          if (t) { t.className = 'tag ok'; t.textContent = '已上传'; }
        }
        // 新稿件：清掉旧的第二步显示，避免看着还是上一篇的题录
        ['#s2Total', '#s2Abstractable', '#s2Lang', '#s2InKb'].forEach(function (s) {
          var el = $(s); if (el) el.textContent = '—';
        });
        var pk = $('#s2Picked'); if (pk) pk.innerHTML = '';
        var kb = $('#s2KbMd'); if (kb) kb.textContent = '';
        var ks = $('#s2KbStat'); if (ks) ks.textContent = '新稿件尚未建立知识库';
        var bar = $('#s2FetchBar'); if (bar) bar.innerHTML = '';
        refreshTree();
        toast('已上传：' + j.path + '　' + (oldSlug ? '（已另开工作区）' : '') + ' 接着填类别再进 Step 2');
      })
      .catch(function (e) { toast('上传失败：' + e.message); });
  }

  /* ------------------------------------------------------------ Step 1 */
  function doParse() {
    setPanel(1, head('Step 1 / 10 · 稿件解析', '解析稿件 <span class="tag warn">运行中</span>',
      '调用技能脚本 manuscript_parse.py 抽取结构与统计；缺失字段不猜，统一记入待补充清单。') +
      '<div class="card"><div class="card-body"><div class="note info"><span class="ni">⏳</span>' +
      '<div>正在解析 <code>' + esc(M.file ? M.file.name : '稿件') +
      '</code> …（.docx 走 OOXML 解析，仅用 Python 标准库）</div></div></div></div>');
    jpost('/parse')
      .then(function (j) {
        M.parse = j.parse;
        M.parseResp = j;
        setTitle(j.parse.title);
        renderParse(j);
        refreshTree();
        var secs = (j.parse.sections_detected || []).length;
        var reqs = (j.parse.author_requests || []).length;
        setMeter('稿件覆盖', Math.min(100, Math.round(secs / 8 * 100)));
        toast('解析完成：识别 ' + secs + ' 个章节，' + reqs + ' 项待补充');
      })
      .catch(function (e) {
        setPanel(1, head('Step 1 / 10 · 稿件解析', '解析稿件 <span class="tag err">失败</span>', '') +
          errCard(e.message));
      });
  }
  function renderParse(j) {
    var p = j.parse;
    var st = p.stats || {};
    var rf = p.references || {};
    var secs = p.sections_detected || [];
    var reqs = p.author_requests || [];
    var html = head('Step 1 / 10 · 稿件解析', '解析稿件 <span class="tag ok">已完成</span>',
      '来源：技能脚本 manuscript_parse.py → 01_parse/manuscript_parse.json');
    html += '<div class="grid-4" style="margin-bottom:14px">' +
      kpi('正文字数', (st.cjk_chars || 0).toLocaleString(), ' 字') +
      kpi('段落 / 表格', st.paragraphs || 0, ' / ' + (st.tables || 0)) +
      kpi('图片', st.images || 0, ' 张') +
      kpi('参考文献', rf.count || 0, ' 条') + '</div>';
    html += card('结构化字段', '01_parse/manuscript_parse.json',
      '<table><tbody>' +
      row2('标题', '<b>' + esc(p.title) + '</b>') +
      row2('文件', '<code>' + esc(j.manuscript) + '</code>　' + esc(p.suffix || '')) +
      row2('识别到的章节', secs.length ? esc(secs.join(' · ')) : '<span class="muted">未识别（需人工核对）</span>') +
      row2('关键词', (p.keywords && p.keywords.length) ? esc(p.keywords.join(' · ')) : '<span class="muted">未识别</span>') +
      row2('拉丁词数', st.latin_words || 0) +
      row2('稿件自引 DOI', ((p.dois || []).length) + ' 条'
        + (p.dois && p.dois.length ? '　<code>' + esc(p.dois.join('</code> <code>')) + '</code>' : '')
        + '<div class="muted small" style="margin-top:4px">Step 3 将连同检索结果一并做双源核验</div>') +
      row2('页数', st.pages || '<span class="muted">未获取</span>') +
      row2('解析时间', esc(p.parsed_at || '')) +
      '</tbody></table>');
    html += card('参考文献概况', (rf.count || 0) + ' 条',
      '<div class="grid-4">' + kpi('条数', rf.count || 0, '') + kpi('最早', rf.year_min || '—', '') +
      kpi('最新', rf.year_max || '—', '') + kpi('近 5 年', rf.recent_5y || 0, ' 篇') + '</div>');
    html += card('待作者补充信息', reqs.length + ' 项',
      '<div class="note info"><span class="ni">ⓘ</span><div>' +
      esc(p.note || '字段为空表示未在文本中识别到，需人工核对稿件，不得据此臆断作者缺失。') + '</div></div>' +
      (reqs.length ? '<ol style="margin:10px 0 0 18px">' + reqs.map(function (x) {
        return '<li>' + esc(x) + '</li>';
      }).join('') + '</ol>' : ''));
    html += '<div class="row" style="margin-top:14px;gap:9px;flex-wrap:wrap">' +
      btn('查看 manuscript_parse.json', "openWsFile('" + esc(j.json_path) + "')") +
      btn('查看 manuscript-outline.md', "openWsFile('" + esc(j.md_path) + "')") +
      btnP('进入 Step 2 · 生成检索策略 →', 'go(2)') +
      '<span class="muted small" style="align-self:center">' + esc(j.log || '') + '</span></div>';
    setPanel(1, html);
  }

  /* ------------------------------------------------------------ Step 2 */
  var GLOSSARY = [
    ['区间', 'interval-valued'], ['函数型', 'functional'], ['回归', 'regression'],
    ['分布', 'distribution'], ['预测', 'forecasting'], ['金融', 'financial'],
    ['高频', 'high-frequency'], ['数据', 'data'], ['模型', 'model'],
    ['时间序列', 'time series'], ['统计', 'statistical'], ['估计', 'estimation'],
    ['神经网络', 'neural network'], ['诊断', 'diagnosis'], ['影像', 'imaging'],
    ['风险', 'risk'], ['面板', 'panel'], ['因果', 'causal'], ['稳健', 'robust'],
    ['算法', 'algorithm'], ['抽样', 'sampling'], ['检验', 'testing']
  ];
  function guessTerms(text, domain) {
    var t = String(text || '') + ' ' + String(domain || '');
    var out = [];
    GLOSSARY.forEach(function (g) {
      if (t.indexOf(g[0]) >= 0 && out.indexOf(g[1]) < 0) out.push(g[1]);
    });
    return out.slice(0, 5);
  }
  function renderPlan() {
    var p = M.parse || {};
    var guess = guessTerms(p.title || '', val('#fDomain'));
    if (!guess.length) guess = [''];
    var q1 = guess.filter(Boolean).join(' ');
    var q2 = guess.length > 2 ? (guess[0] + ' ' + guess.slice(2).join(' ')) : '';
    var html = head('Step 2 / 10 · 检索策略', '生成检索策略 <span class="tag warn">等待人工确认</span>',
      '本步是人工确认闸门：确认之前不会调用任何外部 API。保密约束：仅发送关键词，不上传稿件正文。');
    html += card('概念拆分（可编辑）', '自动初拟 · 需人工校正',
      '<div class="note info"><span class="ni">ⓘ</span><div>下列检索词由领域词表自动初拟，' +
      '<b>仅供参考</b>。请按稿件的实际术语校正后再执行检索。</div></div>' +
      '<div style="margin-top:12px">' + guess.map(function (g, i) {
        return '<div class="field"><label>概念 ' + (i + 1) + '</label>' +
          '<input class="input plan-term" value="' + esc(g) + '" placeholder="英文术语" /></div>';
      }).join('') + '</div>');
    html += card('检索式', '每行一条 · 最多 4 条',
      '<div class="field"><label>实际下发检索词（用于 OpenAlex / Crossref / Europe PMC 全文检索）</label>' +
      '<textarea class="input plan-q" rows="3" style="width:100%;font-family:inherit">' +
      esc([q1, q2].filter(Boolean).join('\n')) + '</textarea></div>' +
      '<div class="field"><label>标准布尔式（写入 search-strategy.md，供支持布尔语法的数据库使用）</label>' +
      '<input class="input plan-bool" value="' + esc(guess.filter(Boolean).join(' OR ')) + '" /></div>');
    html += card('来源与参数', '仅使用公开标识符',
      '<div class="field"><label>数据源</label><div class="chips plan-src">' +
      '<div class="chip on" data-val="openalex">OpenAlex</div>' +
      '<div class="chip on" data-val="crossref">Crossref</div>' +
      '<div class="chip on" data-val="europepmc">Europe PMC</div>' +
      '<div class="chip soft" title="本机未接入 PubMed E-utilities，生物医学文献可用 Europe PMC 覆盖">PubMed（未接入）</div>' +
      '<div class="chip soft" title="需在 Comate 连接器中启用智慧芽 MCP 后才可调用">智慧芽 MCP（未接入）</div>' +
      '</div></div>' +
      '<div class="grid-2" style="gap:11px">' +
      '<div class="field"><label>起始年份</label><input class="input plan-from" type="number" value="2015" /></div>' +
      '<div class="field"><label>每源每词取回条数</label><input class="input plan-limit" type="number" value="12" /></div>' +
      '</div>');
    html += '<div class="row" style="margin-top:6px;gap:9px;flex-wrap:wrap">' +
      btnP('确认检索计划并执行 Step 3 →', 'confirmSearch()') +
      '<span class="muted small" style="align-self:center">确认后依次调用 /api/plan → /api/search → /api/verify</span></div>';
    setPanel(2, html);
    var box = $('.plan-src');
    if (box) {
      box.addEventListener('click', function (e) {
        var c = e.target.closest('.chip');
        if (!c || c.classList.contains('soft')) return;
        c.classList.toggle('on');
      });
    }
  }
  function collectPlan() {
    var terms = $$('.plan-term').map(function (i) { return i.value.trim(); }).filter(Boolean);
    var queries = val('.plan-q').split('\n').map(function (s) { return s.trim(); }).filter(Boolean);
    return {
      concepts: terms.map(function (t, i) { return { label: 'C' + (i + 1), terms: [t] }; }),
      boolean: val('.plan-bool'),
      queries: queries,
      sources: pickIn('.plan-src'),
      from_year: num(val('.plan-from')) || 2015,
      limit: num(val('.plan-limit')) || 12,
      include: ['提出或改进该方向的核心方法', '报告样本外预测 / 实证性能'],
      exclude: ['仅综述且无方法或实证评估', '无法获取全文的会议摘要'],
      types: '期刊论文 / 会议论文',
      confirmed_at: null
    };
  }

  /* ------------------------------------------------------------ Step 3 */
  function runSearch(plan) {
    M.plan = plan;
    setPanel(3, head('Step 3 / 10 · 多源检索', '检索真实文献 <span class="tag warn">运行中</span>',
      '正在调用检索脚本与 DOI 双源核验；仅发送关键词，不发送稿件正文。') +
      '<div class="card"><div class="card-body"><div class="note info"><span class="ni">⏳</span>' +
      '<div>1/3 写入检索计划　→　2/3 调用 ' + esc(plan.sources.join(' / ')) +
      '　→　3/3 DOI 双源核验…（视命中量需 10–60 秒）</div></div></div></div>');
    go(3);
    jpost('/plan', plan)
      .then(function () {
        return jpost('/search', {
          sources: plan.sources, queries: plan.queries,
          from_year: plan.from_year, limit: plan.limit
        });
      })
      .then(function (res) {
        M.rows = res.rows || [];
        M.searchStats = res.stats || {};
        var dois = M.rows.map(function (r) { return r.doi; }).filter(Boolean);
        return jpost('/verify', { dois: dois });
      })
      .then(function (v) {
        M.verify = v.records || [];
        var vmap = {};
        M.verify.forEach(function (r) { vmap[(r.doi || '').toLowerCase()] = !!r.verified; });
        M.rows.forEach(function (r) { r.verify = vmap[(r.doi || '').toLowerCase()] ? '已核验' : '未验证'; });
        renderResults();
        refreshTree();
        setMeter('文献核验', v.total ? Math.round(v.passed / v.total * 100) : 0);
        toast('检索完成：去重 ' + M.rows.length + ' 条；DOI 双源核验 ' + v.passed + '/' + v.total + ' 通过');
      })
      .catch(function (e) {
        setPanel(3, head('Step 3 / 10 · 多源检索', '检索真实文献 <span class="tag err">失败</span>', '') +
          errCard(e.message));
      });
  }
  function renderResults() {
    var passed = M.verify.filter(function (r) { return r.verified; }).length;
    var failed = M.verify.length - passed;
    var st = M.searchStats || {};
    var html = head('Step 3 / 10 · 多源检索',
      '检索真实文献 <span class="tag ok">去重后 ' + M.rows.length + ' 条</span>',
      '数据源：' + esc((st.sources || []).join(' / ')) +
      '　|　脚本：academic_api_client.py　|　产物：02_search/results.md');
    html += '<div class="grid-4" style="margin-bottom:14px">' +
      kpi('OpenAlex 命中', (st.raw || 0).toLocaleString(), '') +
      kpi('去重后', M.rows.length, ' 条') +
      kpi('DOI 已核验', passed, ' / ' + M.verify.length) +
      kpi('未验证', failed, ' 条') + '</div>';
    if (failed > 0) {
      html += '<div class="note warn" style="margin-bottom:12px"><span class="ni">⚠</span><div>' +
        '<b>' + failed + ' 条文献未通过 DOI 双源核验</b>，已禁止作为「文献事实」写入审稿意见，' +
        '需人工确认（详见 02_search/doi-verification.md）。</div></div>';
    }
    var list = M.rows.slice(0, 40);    html += card('检索结果（按被引降序）', '02_search/results.md',
      '<div style="overflow:auto"><table><thead><tr><th style="width:38px">#</th><th>文献</th>' +
      '<th style="width:50px">年份</th><th style="width:82px">来源</th><th style="width:56px">被引</th>' +
      '<th style="width:206px">DOI</th><th style="width:74px">核验</th></tr></thead><tbody>' +
      list.map(function (r, i) {
        var ok = r.verify === '已核验';
        return '<tr><td class="muted">' + (i + 1) + '</td>' +
          '<td>' + esc((r.title || '').slice(0, 92)) +
          '<div class="muted small">' + esc((r.venue || '').slice(0, 68)) + '</div></td>' +
          '<td>' + esc(r.year || '') + '</td>' +
          '<td class="muted small">' + esc(r.source || '') + '</td>' +
          '<td>' + esc(r.cited_by || 0) + '</td>' +
          '<td class="small"><code>' + esc(r.doi || '（无 DOI）') + '</code></td>' +
          '<td><span class="tag ' + (ok ? 'ok' : 'err') + '">' + (ok ? '已核验' : '未验证') + '</span></td></tr>';
      }).join('') + '</tbody></table></div>' +
      (M.rows.length > 40 ? '<div class="muted small" style="margin-top:8px">仅显示前 40 条，完整清单见 results.md</div>' : ''));
    var ms = M.verify.filter(function (r) { return r.origin === 'manuscript'; });
    if (ms.length) {
      var msBad = ms.filter(function (r) { return !r.verified; });
      html += card('稿件自身引用的 DOI 核验', '稿件引用的文献由脚本双源核验',
        '<div class="grid-4">' + kpi('稿件自引 DOI', ms.length, ' 条') +
        kpi('已核验', ms.length - msBad.length, '') +
        kpi('未通过', msBad.length, '') +
        kpi('结论', msBad.length ? '需作者核对' : '全部通过', '') + '</div>' +
        '<div style="overflow:auto;margin-top:10px"><table><thead><tr>' +
        '<th style="width:215px">DOI</th><th style="width:78px">状态</th><th>标题（双源返回）</th>' +
        '<th style="width:110px">载体 / 年份</th></tr></thead><tbody>' +
        ms.map(function (r) {
          return '<tr><td class="small"><code>' + esc(r.doi) + '</code></td>' +
            '<td><span class="tag ' + (r.verified ? 'ok' : 'err') + '">' +
            (r.verified ? '已核验' : '未验证') + '</span></td>' +
            '<td>' + esc((r.title || '（Crossref 与 OpenAlex 均未命中）').slice(0, 84)) + '</td>' +
            '<td class="muted small">' + esc((r.venue || '—').slice(0, 40)) + ' ' + esc(r.year || '') + '</td></tr>';
        }).join('') + '</tbody></table></div>' +
        (msBad.length ? '<div class="note warn" style="margin-top:10px"><span class="ni">⚠</span><div>' +
          '<b>稿件引用的 ' + msBad.length + ' 条 DOI 未通过双源核验</b>：这是可核对的硬事实，' +
          '建议直接写入意见，请作者逐一核对引用。</div></div>'
          : '<div class="note ok" style="margin-top:10px"><span class="ni">✅</span><div>' +
            '稿件引用的 DOI 全部通过双源核验。</div></div>'));
    }
    html += card('检索日志（原始）', '可复现性记录',
      pre((st.queries || []).map(function (q) { return '检索词：' + q; }).join('\n') +
        '\n数据源：' + (st.sources || []).join(' / ') +
        '\n未接入：PubMed E-utilities、智慧芽 MCP（需在 Comate 连接器中启用）' +
        '\n失败源：' + ((st.errors && st.errors.length) ? st.errors.join(' | ') : '无'), 220) +
      '<div class="row" style="margin-top:12px;gap:9px;flex-wrap:wrap">' +
      btn('检索策略', "openWsFile('02_search/search-strategy.md')") +
      btn('检索结果', "openWsFile('02_search/results.md')") +
      btn('DOI 核验记录', "openWsFile('02_search/doi-verification.md')") +
      btnP('进入 Step 4 · 知识卡 →', 'go(4)') + '</div>');
    setPanel(3, html);
  }

  /* ------------------------------------------------------------ Step 7 */
  function issueRow() {
    return '<div class="card" style="margin:0 0 9px;box-shadow:none;border-color:var(--line)" data-issue>' +
      '<div class="card-body">' +
      '<div class="grid-2" style="gap:11px">' +
      '<div class="field"><label>问题标题</label><input class="input i-title" placeholder="如：多重比较未做校正" /></div>' +
      '<div class="field"><label>位置（可回指原文）</label><input class="input i-loc" placeholder="如：§4 结果，表 1" /></div>' +
      '</div><div class="grid-2" style="gap:11px">' +
      '<div class="field"><label>问题</label><input class="input i-prob" placeholder="问题陈述，注明依据" /></div>' +
      '<div class="field"><label>修改建议</label><input class="input i-sug" placeholder="可执行的修改动作" /></div>' +
      '</div><div class="grid-2" style="gap:11px">' +
      '<div class="field"><label>信息类型</label><select class="select i-kind">' +
      '<option>审稿意见</option><option>稿件事实</option><option>文献事实</option>' +
      '<option>稿件事实 + 文献事实</option><option>审稿推断（需人工确认）</option></select></div>' +
      '<div class="field"><label>置信度</label><select class="select i-conf">' +
      '<option>高</option><option selected>中</option><option>低</option></select></div>' +
      '</div></div></div>';
  }
  window.addIssue = function () {
    var box = $('#issues');
    if (box) box.insertAdjacentHTML('beforeend', issueRow());
  };
  function renderDraftForm() {
    var p = M.parse || {};
    var reqs = p.author_requests || [];
    var html = head('Step 7 / 10 · 生成意见', '输出审稿意见 <span class="tag warn">待复核</span>',
      '稿件事实与文献事实由脚本注入，问题定性与建议由审稿人填写。未经复核的草稿不得提交。');
    if (!M.verify.length) {
      html += '<div class="note warn" style="margin-bottom:12px"><span class="ni">⚠</span><div>' +
        '尚未执行 Step 3 检索与核验，「文献事实」栏将为空。建议先完成 Step 2 → Step 3。</div></div>';
    }
    html += card('概要', '审稿人填写',
      '<textarea class="input d-summary" rows="4" style="width:100%;font-family:inherit" ' +
      'placeholder="本文做了什么 / 主要优点 / 主要问题 / 建议决定"></textarea>');
    html += card('主要问题', '位置 → 问题 → 建议 → 置信度',
      (reqs.length ? '<div class="note info"><span class="ni">ⓘ</span><div>解析发现以下待补充项，可直接转成问题：' +
        esc(reqs.slice(0, 3).join('；')) + '</div></div>' : '') +
      '<div id="issues" style="margin-top:10px">' + issueRow() + issueRow() + '</div>' +
      '<button class="btn btn-sm" style="margin-top:2px" onclick="addIssue()">+ 添加问题</button>');
    html += card('次要问题', '每行一条',
      '<textarea class="input d-minor" rows="3" style="width:100%;font-family:inherit" ' +
      'placeholder="每行一条，如：图 2 缺坐标轴单位与基函数个数设定"></textarea>');
    html += card('推荐决定与置信度', '仅给建议，不替代编辑决定',
      '<div class="grid-2" style="gap:11px">' +
      '<div class="field"><label>推荐决定</label><select class="select d-decision">' +
      pick('fDecision').map(function (d) {
        return '<option' + (d === '大修' ? ' selected' : '') + '>' + esc(d) + '</option>';
      }).join('') + '</select></div>' +
      '<div class="field"><label>置信度</label><select class="select d-conf">' +
      '<option>高</option><option selected>中</option><option>低</option></select></div></div>');
    html += '<div class="row" style="margin-top:6px;gap:9px">' +
      btnP('生成意见草稿 + 引用自检', 'generateDraft()') +
      btn('查看 Step 8', 'go(8)') + '</div><div id="draftOut" style="margin-top:14px"></div>';
    setPanel(7, html);
  }
  window.generateDraft = function () {
    var issues = $$('[data-issue]').map(function (el) {
      return {
        title: vtrim('.i-title', el), location: vtrim('.i-loc', el), problem: vtrim('.i-prob', el),
        suggestion: vtrim('.i-sug', el), kind: vtrim('.i-kind', el), confidence: vtrim('.i-conf', el)
      };
    }).filter(function (x) { return x.title || x.problem; });
    var minor = val('.d-minor').split('\n').map(function (s) { return s.trim(); })
      .filter(Boolean).map(function (s) { return { title: s }; });
    var payload = {
      summary: val('.d-summary'),
      decision: val('.d-decision'),
      confidence: val('.d-conf'),
      major: issues,
      minor: minor,
      pending: ['「首次提出」类声明受检索覆盖度限制，属审稿推断，需人工确认。']
    };
    var out = $('#draftOut');
    if (out) out.innerHTML = '<div class="note info"><span class="ni">⏳</span>' +
      '<div>正在生成意见草稿并运行 citation_check.py …</div></div>';
    jpost('/draft', payload).then(function (j) {
      M.draft = j;
      M.cite = j.citation_content || '';
      if (out) {
        out.innerHTML =
          '<div class="note ok"><span class="ni">✅</span><div><b>已写入</b> <code>' + esc(j.path) + '</code>' +
          (j.citation_path ? '　引用自检：<code>' + esc(j.citation_path) + '</code>' : '　引用自检未产出') +
          '</div></div><div class="row" style="margin:12px 0;gap:9px;flex-wrap:wrap">' +
          btnP('下载 Markdown', 'downloadDraft()') +
          btn('新窗口预览', "openWsFile('" + esc(j.path) + "')") +
          btn('进入 Step 8 · 引用自检 →', 'go(8)') +
          '</div>' + pre(j.content, 480);
      }
      refreshTree();
      toast('意见草稿已生成：' + j.path);
    }).catch(function (e) {
      if (out) out.innerHTML = errCard(e.message);
    });
  };
  window.downloadDraft = function () {
    if (!M.draft) return;
    window.open(API + '/download?path=' + encodeURIComponent(M.draft.path), '_blank');
  };

  /* ------------------------------------------------------------ Step 8 */
  function renderCitation() {
    if (!M.cite) {
      setPanel(8, head('Step 8 / 10 · 引用自检', '引用与证据自检',
        '尚未生成审稿意见草稿。请先在 Step 7 生成草稿，本步会自动运行 citation_check.py。') +
        '<div class="card"><div class="card-body">' + btnP('前往 Step 7 →', 'go(7)') + '</div></div>');
      return;
    }
    var bad = M.verify.filter(function (r) { return !r.verified; });
    var html = head('Step 8 / 10 · 引用自检', '引用与证据自检',
      '来源：技能脚本 citation_check.py　|　产物：05_review/citation-check.md');
    if (bad.length) {
      html += '<div class="note warn" style="margin-bottom:12px"><span class="ni">⚠</span><div>' +
        '<b>' + bad.length + ' 条 DOI 未通过双源核验</b>，已在意见中标注为未验证，须人工确认后才可使用：' +
        bad.map(function (r) { return '<code>' + esc(r.doi) + '</code>'; }).join('、') + '</div></div>';
    } else {
      html += '<div class="note ok" style="margin-bottom:12px"><span class="ni">✅</span><div>' +
        '所有引用均已在语料中找到；仍需人工确认文献是否真实存在。</div></div>';
    }
    html += card('自检结果（原样输出）', 'citation-check.md',
      pre(M.cite, 520) +
      '<div class="row" style="margin-top:12px;gap:9px;flex-wrap:wrap">' +
      btn('新窗口预览', "openWsFile('05_review/citation-check.md')") +
      btnP('完成并更新知识库 →', 'finish()') + '</div>');
    setPanel(8, html);
  }

  /* ------------------------------------------------------------ 模型接口
     设计要点：
     ① 模型接入只有一个入口：左栏「模型接口」（保存 / 测试 / 清除），步骤内不再重复配置；
     ② 接入成功后，进入需要模型的步骤（4 / 6 / 7 / 9）自动调用，只发送摘要与关键章节首段
        （范围由服务端 select_context 决定，可在左栏切换为「仅摘要」或「全文」）；
     ③ 产出不直接落盘，先过 validate：引用无法落地 / 段落号不存在 → 阻止写入；
     ④ 未验证项一律标注，须显式勾选「带风险写入」才能写入，且标注不会消失；
     ⑤ 不提供「交外部 Agent」的外发通道：证据与产出都在本工作台内闭环。
  */
  var LLM_STEPS = { 4: '精读建知识卡', 6: '稿件对标（九维度）', 7: '审稿意见', 9: '知识库更新' };
  var _lastIssues = {};

  function llmStatusTag() {
    var st = M.llm;
    if (!st) return { c: 'gray', t: '未探测' };
    if (st.provider === 'stub' || !st.configured) return { c: 'warn', t: '未接入' };
    if (st.connected) return { c: 'ok', t: '已连通 · ' + (st.model || st.provider || '') };
    if (st.last_test && st.last_test.ok === false) {
      return { c: 'danger', t: '连通失败', title: st.last_test.error || '' };
    }
    return { c: 'info', t: '已配置 · 未测试' };
  }

  function loadLlm() {
    return req('/llm/status').then(function (j) {
      M.llm = j;
      renderSideLlm();
      // 状态比面板晚到：当前步已打开时补一次自动调用，避免「打开太快就不调用」
      if (j.connected && M.curStep !== null && M.curStep !== undefined) maybeAutoDraft(M.curStep);
    }).catch(function () { /* 忽略 */ });
  }

  /* ---- 左栏「模型接口」：本工作台唯一的模型接入入口 ---- */
  function renderSideLlm() {
    var s = llmStatusTag();
    var tag = $('#lmTag');
    if (tag) {
      tag.className = 'tag ' + s.c;
      tag.textContent = s.t;
      if (s.title) tag.title = s.title; else tag.removeAttribute('title');
    }
    var st = M.llm || {};
    var cfg = st.config || {};
    var fill = function (id, v) {
      var el = document.getElementById(id);
      if (el && v !== undefined && v !== null && document.activeElement !== el) el.value = v;
    };
    fill('lmBase', cfg.base_url);
    fill('lmModel', cfg.model);
    fill('lmPriv', cfg.privacy_mode);
    var ks = $('#lmKeyState');
    if (ks) ks.textContent = st.has_key ? '（已保存，留空不修改）' : '（未设置）';
    var hint = $('#lmHint');
    if (hint) {
      hint.innerHTML = st.connected
        ? '当前发送范围：' + esc(st.privacy_hint || '') + '。Step 4 / 6 / 7 / 9 会自动调用该模型，步骤内无需再配置。'
        : (st.configured
          ? '配置已保存但尚未连通：请检查服务地址、密钥与模型名后重试。'
          : '未接入时，各步骤只用本机脚本事实工作，不向任何外部服务发送稿件内容。');
    }
    refreshStepNotice();
  }

  window.llmSideToggle = function () {
    var el = $('#sideModel');
    if (el) el.classList.toggle('open');
  };

  window.llmSideSave = function () {
    var out = $('#lmOut');
    if (out) out.innerHTML = '<div class="note info" style="margin-top:9px"><span class="ni">⏳</span>' +
      '<div>正在保存并测试连接（只发一句连通性测试，不发送稿件内容）…</div></div>';
    jpost('/llm/config', {
      base_url: vtrim('#lmBase'), api_key: val('#lmKey'), model: vtrim('#lmModel'),
      privacy_mode: val('#lmPriv')
    }).then(function (j) {
      M.llm = j.status || M.llm;
      var k = $('#lmKey'); if (k) k.value = '';          // 密钥不回显
      renderSideLlm();
      if (out) {
        out.innerHTML = j.connected
          ? '<div class="note ok" style="margin-top:9px"><span class="ni">✅</span><div>连接成功：' +
            esc(j.model || '') + '（provider=' + esc(j.provider || '') + '）</div></div>'
          : '<div class="note warn" style="margin-top:9px"><span class="ni">⚠</span><div>已保存，但未连通：' +
            esc(j.error || '需要服务地址与密钥') + '</div></div>';
      }
      // 接入成功后，若正停在需要模型的步骤，立即按需自动调用（步骤内不提供任何按钮）
      if (j.connected && LLM_STEPS.hasOwnProperty(M.curStep)) maybeAutoDraft(M.curStep);
    }).catch(function (e) { if (out) out.innerHTML = errCard(e.message); });
  };

  window.llmSideReset = function () {
    if (!confirm('确定清除本机模型配置（含密钥）并停止调用外部模型？')) return;
    var out = $('#lmOut');
    if (out) out.innerHTML = '<div class="note info" style="margin-top:9px"><span class="ni">⏳</span><div>正在清除…</div></div>';
    jpost('/llm/reset', {}).then(function (j) {
      M.llm = j.status || M.llm;
      renderSideLlm();
      if (out) out.innerHTML = '<div class="note ok" style="margin-top:9px"><span class="ni">✅</span><div>已清除' +
        (j.removed && j.removed.length ? '：' + esc(j.removed.join('、')) : '') +
        '，当前不会向任何外部服务发送稿件内容。</div></div>';
    }).catch(function (e) { if (out) out.innerHTML = errCard(e.message); });
  };

  /* ---- 步骤内的模型状态提示：只提示状态，不提供接入配置与外发通道 ---- */
  function modelNotice(step) {
    if (M.mode !== 'real' || !LLM_STEPS[step]) return '';
    var st = M.llm || {};
    var idAttr = ' id="llmNote' + step + '"';
    if (st.connected) {
      return '<div class="note info"' + idAttr + ' style="margin-bottom:11px"><span class="ni">ⓘ</span><div>' +
        '模型已接入：' + esc((st.provider || '') + (st.model ? ' / ' + st.model : '')) +
        ' · 本步自动调用 · 发送范围：' + esc(st.privacy_hint || '') +
        '（在左栏「模型接口」调整）。</div></div>';
    }
    if (st.configured) {
      return '<div class="note warn"' + idAttr + ' style="margin-bottom:11px"><span class="ni">⚠</span><div>' +
        '模型已配置但<b>尚未连通</b>：请看左栏「模型接口」的测试结果，修正后本步会自动调用。</div></div>';
    }
    return '<div class="note warn"' + idAttr + ' style="margin-bottom:11px"><span class="ni">⚠</span><div>' +
      '本步需要模型 · <b>尚未接入</b>。请在左栏「模型接口」填入你自己的服务地址与密钥；' +
      '接入后本步自动调用，此处无需任何配置。</div></div>';
  }

  function runBox(step) { return '<div id="llmRun' + step + '"></div>'; }
  function outBox(step) { return '<div id="llmOut' + step + '" style="margin-top:12px"></div>'; }

  /* 左栏保存 / 清除配置后，把当前步骤的状态提示就地刷新（不重绘面板，避免丢失已填内容） */
  function refreshStepNotice() {
    var n = M.curStep;
    if (n === null || n === undefined) return;
    var el = document.getElementById('llmNote' + n);
    if (el) el.outerHTML = modelNotice(n);
  }

  /* ---- 产物检查：本步已有产物就不再调用模型，避免覆盖人工修改 ---- */
  function existingArtifact(step) {
    if (step === 4) {
      return req('/tree').then(function (j) {
        return (j.files || []).some(function (f) { return f.indexOf('03_cards/') === 0; });
      }).catch(function () { return true; });            // 查不到就保守跳过
    }
    if (step === 6) {
      return readWs('06_benchmark/benchmark.md').then(function (t) { return !!t; })
        .catch(function () { return true; });
    }
    if (step === 9) {
      return req('/library').then(function (j) {
        var all = (j.cards_all || []).length;
        var inLib = ((j.stats || {}).cards) || 0;
        return (all - inLib) <= 0;                       // 没有「本篇未入库」的卡片 → 无事可做
      }).catch(function () { return true; });
    }
    if (step === 7) {
      return Promise.resolve($$('[data-issue]').some(function (el) {
        return !!(vtrim('.i-title', el) || vtrim('.i-prob', el));
      }));
    }
    return Promise.resolve(true);
  }

  /* ---- 已有产物 / 本轮已执行过：说明清楚，并留一个「重新生成」出口 ---- */
  function skipHint(step, why) {
    var box = document.getElementById('llmRun' + step);
    if (!box || box.innerHTML) return;
    var msg = why === 'exists'
      ? '本步<b>已有产物</b>，没有自动调用模型（避免覆盖你已复核的内容）。'
      : (step === 9
        ? '本轮已自动起草过本步；<b>未点「并入」前不会改写知识库</b>。'
        : '本轮已自动执行过本步，成果已写入上方产物与右栏文件。');
    box.innerHTML = '<div class="note info"><span class="ni">ⓘ</span><div>' + msg +
      (step === 7
        ? '如需更多条目，请在下方表单继续添加。'
        : '确需重新生成时：<span style="margin-left:7px">' +
          btn('重新生成（覆盖现有产物）', 'llmRedraft(' + step + ')') + '</span>') +
      '</div></div>';
  }

  /* ---- 自动调用：满足条件才调用，未接入模型时一步都不调 ---- */
  function maybeAutoDraft(step) {
    if (M.mode !== 'real') return;
    if (!LLM_STEPS[step]) return;
    if (!M.llm || !M.llm.connected) return;              // 未接入 → 不发任何外部请求
    if (M.autoDone[step]) { skipHint(step, 'done'); return; }
    existingArtifact(step).then(function (has) {
      if (M.autoDone[step]) { skipHint(step, 'done'); return; }
      if (has) { skipHint(step, 'exists'); return; }
      M.autoDone[step] = true;
      runModelDraft(step);
    }).catch(function () { /* 检查失败则不自动调用 */ });
  }

  function issuesHtml(issues) {
    if (!issues || !issues.length) return '';
    var map = { error: ['warn', '⛔'], warn: ['warn', '⚠'], info: ['info', 'ⓘ'] };
    var rows = issues.map(function (i) {
      var m = map[i.level] || ['info', 'ⓘ'];
      return '<div class="note ' + m[0] + '" style="margin:0 0 7px"><span class="ni">' + m[1] +
        '</span><div>' + esc(i.msg) + ' <span style="color:var(--muted)">（' + esc(i.code) + '）</span></div></div>';
    }).join('');
    var errs = issues.filter(function (i) { return i.level === 'error'; }).length;
    return '<div style="margin-top:12px"><b style="font-size:13px">校验结果</b>' +
      (errs ? '<span class="tag warn" style="margin-left:7px">' + errs + ' 项阻止写入</span>' : '') +
      '<div style="margin-top:8px">' + rows + '</div></div>';
  }

  function itemsHtml(step, items) {
    if (!items.length) return '<div class="note warn" style="margin-top:12px"><span class="ni">⚠</span>' +
      '<div>本次起草未产生条目。stub 模式下仅能依据解析事实生成占位条目；接入模型后才会产出实质判断。</div></div>';
    var fields = {
      4: [['citekey', 'citekey'], ['authors', '作者'], ['title', '文献标题'], ['source', '来源与日期'], ['question', '研究问题'],
          ['method', '方法'], ['results', '主要结果'], ['cite_point', '可引用点'], ['doi', 'DOI']],
      6: [['dimension', '维度'], ['manuscript_claim', '稿件主张（原文）'], ['verdict', '判定'],
          ['gap', '差距'], ['suggestion', '建议']],
      7: [['title', '问题标题'], ['location', '位置（可回指）'], ['problem', '问题'],
          ['suggestion', '建议'], ['confidence', '置信度']],
      9: [['section', '归类（共识/争议/空白/规范/伦理）'], ['claim', '条目内容'], ['confidence', '置信度']]
    }[step] || [['title', '标题']];
    return '<div style="margin-top:12px"><b style="font-size:13px">草案条目（' + items.length +
      ' 条，可直接编辑后再写入）</b><div style="margin-top:9px">' +
      items.map(function (it, i) {
        return '<div class="card" data-li="' + i + '" style="margin:0 0 9px;box-shadow:none;border-color:var(--line)">' +
          '<div class="card-body">' +
          ((it.unverified || it.evidence_unresolved)
            ? '<span class="tag warn" style="margin-bottom:8px;display:inline-block">未验证 / 需人工确认</span>' : '') +
          fields.map(function (f) {
            var k = f[0], v = it[k];
            if (typeof v === 'object' && v !== null) {
              try { v = JSON.stringify(v); } catch (e) { v = String(v); }
            }
            return '<div class="field" style="margin-top:7px"><label>' + esc(f[1]) + '</label>' +
              '<input class="input li-' + k + '" value="' + esc(v === undefined || v === null ? '' : v) + '" /></div>';
          }).join('') +
          ((it.evidence && it.evidence.length) || step === 9
            ? '<div class="field" style="margin-top:7px"><label>' +
              (step === 9 ? '证据（只能填库内 citekey，用、分隔。禁止库外引用）'
                          : '证据引用（段落号 / doi:…）') + '</label>' +
              '<input class="input li-evidence" value="' + esc(it.evidence.join('、')) + '" /></div>' : '') +
          '</div></div>';
      }).join('') + '</div>';
  }

  function collectItems() {
    return $$('[data-li]').map(function (el) {
      var o = {};
      $$('.input', el).forEach(function (inp) {
        var cls = Array.prototype.find.call(inp.classList, function (c) { return c.indexOf('li-') === 0; });
        if (!cls) return;
        var k = cls.slice(3);
        if (k === 'evidence') {
          o.evidence = inp.value.split(/[、,，]/).map(function (s) { return s.trim(); }).filter(Boolean);
        } else {
          o[k] = inp.value;
        }
      });
      return o;
    });
  }

  /* ------------------------------------------------------------ 领域知识库（Step 9） */
  window.loadLibStats = function () {
    var el = $('#libStats');
    if (!el) return;
    if (M.mode !== 'real') {
      el.innerHTML = '<div class="note info"><div>演示模式：未连接本地服务。</div></div>';
      return;
    }
    req('/library').then(function (j) {
      var s = j.stats || Object();
      var papers = s.papers || [];
      var secs = s.by_section || Object();
      var wsCards = (j.cards_all || []).length - (s.cards || 0);
      var secTxt = Object.keys(secs).map(function (k) { return k + ' ' + secs[k]; }).join(' / ');
      var pTxt = papers.map(function (p) {
        return esc(p.title || p.id) + '（' + esc(p.date || '') + '）';
      }).join('；');
      el.innerHTML =
        '<table class="tbl"><tbody>' +
          '<tr><td>领域</td><td><b>' + esc(s.slug || '未命名') + '</b></td></tr>' +
          '<tr><td>已收录稿件</td><td>' + papers.length + ' 篇' +
          (pTxt ? '　<span style="color:var(--muted);font-size:12px">' + pTxt + '</span>' : '') + '</td></tr>' +
          '<tr><td>知识卡片</td><td><b>' + num(s.cards) + '</b> 张' +
          (wsCards > 0 ? '　<span style="font-size:12px">另有本篇 ' + wsCards + ' 张尚未入库</span>' : '') + '</td></tr>' +
          '<tr><td>实体条目</td><td>' + num(s.claims) + ' 条' +
          (secTxt ? '　<span style="color:var(--muted);font-size:12px">' + esc(secTxt) + '</span>' : '') + '</td></tr>' +
          '<tr><td>最近更新</td><td>' + esc(s.updated || '—') + '</td></tr>' +
          '<tr><td>库目录</td><td><code style="font-size:12px">' + esc(s.dir || '') + '</code></td></tr>' +
          '<tr><td>入库时注入模型</td><td>' + num(j.context_chars) + ' 字（下一篇起草时自动带上）</td></tr>' +
          '</tbody></table>' +
          (j.kb && j.kb.length
            ? '<div style="margin-top:10px"><b style="font-size:13px">领域知识库总表（前 1800 字）</b>' +
              '<pre class="pre" style="max-height:280px;overflow:auto;margin-top:7px">' +
              esc(j.kb.slice(0, 1800)) + '</pre></div>'
            : '<div class="note info" style="margin-top:10px"><span class="ni">i</span><div>' +
              '库还是空的。先完成 Step 4 建卡并写入，再回本步并入本篇知识卡，即可入库。' +
              '</div></div>');
    }).catch(function (e) {
      el.innerHTML = '<div class="note warn"><span class="ni">!</span><div>' + esc(e.message) + '</div></div>';
    });
  };

  window.openLibKb = function () {
    if (M.mode !== 'real') { toast('演示模式无本地文件'); return; }
    req('/library').then(function (j) {
      if (!j.kb) { toast('领域知识库还没有内容，先在 Step 4 建卡并入库'); return; }
      window.open(API + '/library/file', '_blank');
    }).catch(function (e) { toast(e.message); });
  };

  window.libMerge = function () {
    if (M.mode !== 'real') { toast('演示模式'); return; }
    if (!confirm('把本篇工作区的知识卡直接并入领域知识库？每张卡片都是已核验文献的题录与归纳，不经模型；入库后总表会重建。')) return;
    jpost('/library/merge', Object()).then(function (j) {
      toast(j.msg || '已入库');
      loadLibStats();
      updateLibBadge();
      refreshTree();
    }).catch(function (e) { toast('入库失败：' + e.message); });
  };

  /* ---- 模型起草（自动触发，步骤内无按钮） ----
     Step 4 / 6：起草 → 自动过 validate 闸门 → 通过即写入，被拦下则如实显示拦截原因；
     Step 7    ：起草 → 只把条目填进下方表单，定性与提交仍由审稿人决定；
     Step 9    ：起草 → 停在闸门前，由人工点「并入领域知识库」（共享知识库不静默改写）。 */
  function runModelDraft(step) {
    var out = $('#llmRun' + step);
    if (!out) { M.autoDone[step] = false; return; }
    out.innerHTML = '<div class="note info"><span class="ni">⏳</span><div><b>模型起草中…</b>（自动调用' +
      (M.llm && M.llm.privacy_hint ? ' · ' + esc(M.llm.privacy_hint) : '') + '）</div></div>';
    jpost('/llm/draft', { step: step }).then(function (j) {
      var sent = j.sent || {};
      M.sentPreview = M.sentPreview || {};
      M.sentPreview[step] = j.sent || null;
      _lastIssues[step] = j.issues || [];
      out.innerHTML =
        '<div class="note ok"><span class="ni">✅</span><div><b>模型起草完成</b>：' +
        esc((j.provider || '') + (j.model ? ' / ' + j.model : '')) +
        '　发送 ' + num(sent.segments) + ' 段 / ' + num(sent.chars) + ' 字' +
        '<br/><span style="color:var(--muted)">' + esc(sent.policy || '') + '</span></div></div>' +
        issuesHtml(j.issues);
      var items = j.items || [];
      if (step === 7) {
        fillIssuesFromModel(items);
      } else if (step === 9) {
        out.insertAdjacentHTML('beforeend', itemsHtml(step, items) +
          '<div class="row" style="margin-top:12px;gap:9px;flex-wrap:wrap">' +
          btnP('并入领域知识库（过闸门）', 'llmApply(9)') +
          btn('查看领域知识库全文', 'openLibKb()') + '</div>' +
          '<label style="display:flex;gap:7px;align-items:center;margin-top:11px;font-size:13px;color:var(--muted)">' +
          '<input type="checkbox" id="forceChk9" /> 我知道存在未验证项，允许带风险写入（标注保留）</label>');
        if (!items.length) out.insertAdjacentHTML('beforeend',
          '<div class="note warn" style="margin-top:9px"><span class="ni">⚠</span><div>' +
          '模型本次未提出可入库条目，可改用下方「不经模型，直接并入本篇知识卡（人工兜底）」。</div></div>');
      } else {
        out.insertAdjacentHTML('beforeend', itemsHtml(step, items) +
          '<label style="display:flex;gap:7px;align-items:center;margin-top:11px;font-size:13px;color:var(--muted)">' +
          '<input type="checkbox" id="forceChk' + step + '" /> 我知道存在未验证项，允许带风险写入（标注保留）</label>');
        if (items.length) window.llmApply(step);           // 4 / 6：校验通过即写入
        else out.insertAdjacentHTML('beforeend',
          '<div class="note warn" style="margin-top:9px"><span class="ni">⚠</span><div>' +
          '模型未产生条目（未接入模型时不会产出实质判断）。</div></div>');
      }
      refreshTree();
    }).catch(function (e) {
      M.autoDone[step] = false;                            // 失败可重试：重新进入本步会再试一次
      if (out) out.innerHTML = errCard(e.message) +
        '<div class="note info" style="margin-top:9px"><span class="ni">ⓘ</span><div>' +
        '请在左栏「模型接口」检查服务地址 / 密钥 / 模型名，然后重新进入本步。</div></div>';
    });
  }

  /* Step 7：模型只填表，定性与提交仍归审稿人 */
  function fillIssuesFromModel(items) {
    var box = $('#issues');
    var out = $('#llmRun7');
    if (!box || !out) return;
    if (!items.length) {
      out.insertAdjacentHTML('beforeend',
        '<div class="note warn" style="margin-top:9px"><span class="ni">⚠</span><div>' +
        '模型本次未产生问题条目，请自行在下方表单填写。</div></div>');
      return;
    }
    $$('[data-issue]', box).forEach(function (el) {        // 清掉空白占位行
      if (!vtrim('.i-title', el) && !vtrim('.i-prob', el)) el.remove();
    });
    items.forEach(function (it) {
      box.insertAdjacentHTML('beforeend', issueRow());
      var el = box.lastElementChild;
      var set = function (sel, v) { var e = $(sel, el); if (e && v) e.value = v; };
      set('.i-title', it.title); set('.i-loc', it.location);
      set('.i-prob', it.problem); set('.i-sug', it.suggestion);
      set('.i-conf', it.confidence);
      if (it.evidence && it.evidence.length) {
        var p = $('.i-prob', el);
        if (p) p.value = (p.value + '　[证据：' + it.evidence.join('、') + ']').trim();
      }
    });
    out.insertAdjacentHTML('beforeend',
      '<div class="note info" style="margin-top:9px"><span class="ni">ⓘ</span><div>已把 <b>' + items.length +
      '</b> 条填入下方「主要问题」表单。请逐条复核定性与证据，再点「生成意见草稿 + 引用自检」。</div></div>' +
      '<div class="row" style="margin-top:9px;gap:9px">' + btn('跳到表单复核', 'scrollToIssues()') + '</div>');
    toast('模型已填入 ' + items.length + ' 条问题，请逐条复核');
  }

  window.scrollToIssues = function () {
    var box = $('#issues');
    if (box) box.scrollIntoView({ behavior: 'smooth', block: 'center' });
  };

  /* 手动重跑入口（仅当已有产物时出现在本步提示里，不改变「左栏唯一配置」的设计） */
  window.llmRedraft = function (step) {
    M.autoDone[step] = false;
    var o = $('#llmOut' + step);
    if (o) o.innerHTML = '';
    runModelDraft(step);
  };

  window.llmApply = function (step) {
    var out = $('#llmOut' + step);
    var items = collectItems();
    if (!items.length) { toast('没有可写入的条目'); return; }
    var force = $('#forceChk' + step) && $('#forceChk' + step).checked;
    if (out) out.insertAdjacentHTML('beforeend',
      '<div class="note info" style="margin-top:10px"><span class="ni">⏳</span><div>正在校验并写入…</div></div>');
    var payload = null;
    if (step === 7) {           // Step 7 沿用表单里的审稿人判断，模型只提供问题条目
      payload = {
        summary: vtrim('.d-summary') || '（模型起草，审稿人未填写概要）',
        decision: vtrim('.d-decision') || '大修',
        minor: val('.d-minor').split('\n').map(function (s) { return s.trim(); })
          .filter(Boolean).map(function (s) { return { title: s }; }),
        pending: ['模型起草内容需审稿人逐条复核后才可提交。']
      };
    }
    jpost('/llm/apply', { step: step, items: items, force: !!force, payload: payload }).then(function (j) {
      if (j.blocked) {
        if (out) out.insertAdjacentHTML('afterbegin',
          '<div class="note warn" style="margin-top:10px"><span class="ni">⛔</span><div><b>已阻止写入。</b>' +
          esc(j.msg || '') + '</div></div>' + issuesHtml(j.issues));
        toast('校验未通过，已阻止写入');
        return;
      }
      if (out) out.insertAdjacentHTML('afterbegin',
        '<div class="note ok" style="margin-top:10px"><span class="ni">✅</span><div><b>已写入</b>：' +
        (j.written || []).map(function (p) { return '<code>' + esc(p) + '</code>'; }).join('、') +
        ((j.logs || []).length ? '<br/><span style="color:var(--muted)">' + esc(j.logs.join('；')) + '</span>' : '') +
        '</div></div>');
      refreshTree();
      toast('已写入 ' + (j.written || []).length + ' 个文件');
      if (step === 9) {
        if (j.report) {
          if (out) out.insertAdjacentHTML('afterbegin',
            '<div class="note info" style="margin-top:10px"><span class="ni">♻</span><div>' +
            '入库结果：新增卡片 <b>' + ((j.report.added || []).length) + '</b> 张' +
            ((j.report.existing || []).length ? '、已存在 ' + j.report.existing.length + ' 张' : '') +
            '（重命名 ' + ((j.report.renamed || []).length) + ' 张）｜新增实体条目 <b>' +
            num(j.report.claims_added) + '</b> 条' +
            (j.report.claims_rejected ? '、拒收 ' + j.report.claims_rejected + ' 条' : '') +
            '<br/><span style="color:var(--muted)">知识库已重建：' + esc(j.report.kb_path || '') +
            '。下一篇同领域稿件起草时会自动带上这些积累。</span></div></div>');
        }
        loadLibStats();
        updateLibBadge();
      }
      if (M.llm) loadLlm();
      if (step === 7 && j.citation_check) {
        M.cite = j.citation_check;
        if (out) out.insertAdjacentHTML('beforeend', '<div class="row" style="margin-top:9px;gap:9px">' +
          btn('进入 Step 8 · 引用自检 →', 'go(8)') + '</div>');
      }
      if (step === 4) {
        if (out) out.insertAdjacentHTML('beforeend', '<div class="row" style="margin-top:9px;gap:9px">' +
          btn('进入 Step 5 · 领域知识库 →', 'go(5)') + '</div>');
      }
    }).catch(function (e) {
      if (out) out.insertAdjacentHTML('afterbegin', errCard(e.message));
    });
  };

  window.addToForm = function () {
    var box = $('#issues');
    if (!box) { toast('请先进入 Step 7'); return; }
    var items = collectItems();
    if (!items.length) return;
    items.forEach(function (it) {
      box.insertAdjacentHTML('beforeend', issueRow());
      var el = box.lastElementChild;
      var set = function (sel, v) { var e = $(sel, el); if (e && v) e.value = v; };
      set('.i-title', it.title); set('.i-loc', it.location);
      set('.i-prob', it.problem); set('.i-sug', it.suggestion);
      if (it.evidence && it.evidence.length) {
        var e = $('.i-prob', el);
        if (e) e.value = (e.value + '　[证据：' + it.evidence.join('、') + ']').trim();
      }
    });
    toast('已填入 ' + items.length + ' 条到下方表单，请逐条复核后生成意见');
    var f = $('#issues');
    if (f) f.scrollIntoView({ behavior: 'smooth', block: 'center' });
  };

  /* ------------------------------------------------------------ 模型层各步面板 */
  function renderModelStep(n) {
    var meta = {
      4: ['精读建知识卡', '模型只在已核验文献的题录与稿件段落上做归纳；每条卡片必须能回指 doi: 或 P<编号>。'],
      6: ['稿件对标（九维度）', '把稿件主张与已核验文献逐维度对照，判定与差距必须带证据；无证据即降级。'],
      9: ['知识库更新', '把本篇新增的共识/争议/空白并入领域知识库（按领域跨稿件累积）；' +
          '每条入库条目必须引用库内已有 citekey，引用库外引用会被拒绝入库。']
    }[n] || ['模型层', ''];
    var tag = '<span class="tag info">模型层接管 · 产出过闸门</span>';
    var html = head('Step ' + n + ' / 10 · ' + meta[0] + '　' + tag, meta[0],
      meta[1] + (M.slug ? '' : '　（尚未创建审稿任务，请先完成 Step 0）'));
    if (n === 9) {
      html += '<div class="note info" style="margin-bottom:12px"><span class="ni">♻</span><div>' +
        '<b>这是循环的闭合点</b>：本步把知识卡并入<b>按领域跨稿件共享</b>的知识库，' +
        '下一篇同领域稿件起草时会自动带着它。入库前仍过闸门：引用库外 citekey 的条目会被拒收。' +
        '</div></div>' +
        '<div class="card" style="margin-bottom:12px"><div class="cardhd">领域知识库现状</div>' +
        '<div class="card-body" id="libStats"><div class="note info"><span class="ni">⏳</span>' +
        '<div>读取中…</div></div></div></div>' +
        '<div class="row" style="gap:9px;flex-wrap:wrap;margin-bottom:4px">' +
        btn('查看领域知识库全文', 'openLibKb()') +
        btn('不经模型，直接并入本篇知识卡（人工兜底）', 'libMerge()') +
        '</div>';
    }
    html += modelNotice(n) + runBox(n) + outBox(n);
    setPanel(n, html);
    if (n === 9) loadLibStats();          // 必须等面板挂载后再拉库状态
    var side = { 4: '04_knowledge_base/domain-kb.md', 5: '04_knowledge_base/domain-kb.md',
                 6: '06_benchmark/benchmark.md' }[n];
    if (side && $('#llmOut' + n)) {
      readWs(side).then(function (t) {
        if (!t) return;
        $('#llmOut' + n).insertAdjacentHTML('beforeend',
          '<div style="margin-top:12px"><b style="font-size:13px">当前产物 preview：' + esc(side) + '</b>' +
          pre(t, 300) + '</div>');
      });
    }
    if (n === 4) {
      req('/tree').then(function (j) {
        var cards = (j.files || []).filter(function (f) { return f.indexOf('03_cards/') === 0; });
        if (!cards.length || !$('#llmOut4')) return;
        $('#llmOut4').insertAdjacentHTML('beforeend',
          '<div class="note info" style="margin-top:12px"><span class="ni">ⓘ</span><div>已有知识卡 ' +
          cards.length + ' 张：' + cards.map(function (c) { return '<code>' + esc(c.replace('03_cards/', '')) + '</code>'; }).join('、') +
          '</div></div>');
      }).catch(function () { /* 忽略 */ });
    }
    loadLlm().then(function () {          // 接入状态下才可能自动调用，未接入只留提示
      refreshStepNotice();
      maybeAutoDraft(n);
    });
  }

  function renderStep7Model() {
    var p = P(7);
    if (!p) return;
    var head_ = $('.page-head', p);
    if (!head_ || $('#llmRun7')) return;
    head_.insertAdjacentHTML('afterend', modelNotice(7) + runBox(7));
    loadLlm().then(function () {
      refreshStepNotice();
      maybeAutoDraft(7);
    });
  }

  /* ------------------------------------------------------------ 接管入口 */
  function bindReal() {
    var drop = $('#drop');
    if (drop) {
      drop.onclick = null;
      drop.addEventListener('click', function () { ensureInput().click(); });
      drop.addEventListener('drop', function (e) {
        var f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
        if (f) handleFile(f);
      });
    }
    var btns = $$('.topbar-right .btn');
    if (btns[0]) {
      btns[0].onclick = function () {
        refreshTree();
        toast('工作区已刷新：' + (M.slug || '尚未创建（先上传稿件）'));
      };
    }
    if (btns[1]) {
      btns[1].onclick = function () {
        if (M.draft) window.downloadDraft();
        else { go(7); toast('请先在 Step 7 生成意见草稿'); }
      };
    }
    $$('.chips').forEach(function (b) {
      b.addEventListener('click', function () { setTimeout(syncDecisions, 0); });
    });
    syncDecisions();
  }

  var _go = window.go;
  window.go = function (n) {
    _go(n);
    M.curStep = n;
    if (M.mode !== 'real') return;
    if (n === 0) r4s1();
    if (n === 1) r4s2();
    if (n === 2) r4s3();
    if (n === 3) r4s4();
    updateRailMeters();
    refreshTree();
  };

  /* 右栏进度条：只显示工作区里真实存在的数字 */
  function updateRailMeters() {
    req('/refs').then(function (j) {
      var n = j.total || 0;
      var bar = $('#mRefBar'), val = $('#mRefVal');
      if (bar) bar.style.width = n ? '100%' : '0%';
      if (val) val.textContent = n ? (n + ' 条') : '未抽取';
    }).catch(function () {});
    req('/kb').then(function (j) {
      var w = j.with_abstract || 0, c = j.count || 0;
      var bar = $('#mAbsBar'), val = $('#mAbsVal');
      if (bar) bar.style.width = c ? Math.round(w / c * 100) + '%' : '0%';
      if (val) val.textContent = c ? (w + ' / ' + c) : '未建库';
    }).catch(function () {});
  }

  /* ==================== 四步框架（新） ==================== */

  function r4s1() {
    function paint() {
      var tag = $('#s1FileTag'), row = $('#s1FileRow');
      if (M.file && M.file.name) {
        if (tag) { tag.className = 'tag ok'; tag.textContent = '已上传'; }
        if (row) {
          row.innerHTML = '<div class="file-row" style="padding:10px 12px">' +
            '<div class="file-ico">📄</div><div><div class="file-name">' + esc(M.file.name) + '</div>' +
            '<div class="file-meta">' + (M.file.size ? fmtSize(M.file.size) : '已在本工作区') + '</div></div></div>';
        }
      } else if (tag) {
        tag.className = 'tag gray';
        tag.textContent = '待上传';
      }
      if (M.meta) {
        var dEl = $('#fDomain');
        var dom = (M.meta.domain || '').trim();
        if (dEl && !dEl.value && dom && dom !== '待填写' && dom !== '未分类') dEl.value = dom;
        var sEl = $('#fScene');
        if (sEl && M.meta.scenario && sEl.value !== M.meta.scenario) sEl.value = M.meta.scenario;

        // 语言芯片跟随 meta（英文稿不该默认「中文」）
        if (M.meta.language) {
          var lbox = document.getElementById('fLang');
          if (lbox) {
            var wantEn = /^en/i.test(M.meta.language) || /english/i.test(M.meta.language);
            $$('.chip', lbox).forEach(function (c) {
              var isEn = /english/i.test(c.dataset.val || '');
              c.classList.toggle('on', wantEn ? isEn : !isEn);
            });
          }
        }
        // 右栏「审稿上下文」同步真实值
        var rd = $('#railDomain');
        if (rd && dom) rd.textContent = dom;
        var rs = $('#railScene');
        if (rs && M.meta.scenario) rs.textContent = M.meta.scenario;
        var rl = $('#railLang');
        if (rl && M.meta.language) rl.textContent = M.meta.language;
        var rf = $('#railFocus');
        if (rf && (M.meta.focus || []).length) rf.textContent = M.meta.focus.join(' · ');
      }
    }
    // 首次进入时 M.meta/M.file 可能还没加载，主动拉一次
    if (!M.meta || !M.file) {
      req('/state').then(function (st) {
        if (st && st.current) {
          M.meta = st.current;
          if (st.current.manuscript) {
            M.file = { name: st.current.manuscript, size: 0 };
            setFile(st.current.manuscript);
          }
          if (st.current.title) setTitle(st.current.title);
        }
        paint();
      }).catch(paint);
    } else {
      paint();
    }
  }

  window.saveStep1 = function () {
    var domain = (val('#fDomain') || '').trim();
    var msg = $('#s1Msg');
    if (!domain) { toast('请先填写文章类别'); return; }
    if (msg) msg.textContent = '保存中…';
    jpost('/meta', {
      domain: domain,
      scenario: val('#fScene'),
      language: pick('fLang')[0] || '中文',
      focus: pick('fFocus')
    }).then(function (j) {
      M.meta = j.meta;
      if (msg) msg.textContent = '已保存 · 归档目录 _library/' + j.domain_slug + '/';
      toast('已保存');
      go(1);
    }).catch(function (e) {
      if (msg) msg.textContent = '';
      toast('保存失败：' + e.message);
    });
  };

  /* ---------- Step 2 ---------- */

  function renderPicked(list) {
    var box = $('#s2Picked');
    if (!box) return;
    if (!list || !list.length) {
      box.innerHTML = '<div class="note info"><span class="ni">i</span><div>还没有抽取。点上方「抽取参考文献」。</div></div>';
      return;
    }
    var rows = list.map(function (r) {
      return '<tr><td>' + r.no + '</td><td>' + esc(r.type) + '</td><td>' + esc(r.lang) + '</td>' +
        '<td>' + esc(r.title || '（未解析出标题）') + '</td>' +
        '<td class="muted small">' + esc(r.venue || '') + ' ' + esc(r.year || '') + '</td></tr>';
    }).join('');
    box.innerHTML = '<div class="muted small" style="margin-bottom:7px">本次抽中 ' + list.length + ' 篇</div>' +
      '<table class="tbl"><thead><tr><th style="width:44px">#</th><th style="width:56px">类型</th>' +
      '<th style="width:56px">语种</th><th>标题</th><th style="width:200px">出处</th></tr></thead><tbody>' +
      rows + '</tbody></table>';
  }

  function paintRefs(j) {
    var st = j.stats || {};
    $('#s2Total').textContent = st.total || 0;
    $('#s2Abstractable').textContent = st.abstractable || 0;
    $('#s2Lang').textContent = ((st.by_lang || {}).zh || 0) + ' / ' + ((st.by_lang || {}).en || 0);
    renderPicked(j.picked || []);
  }

  var _autoRefsBusy = false;

  function autoExtractRefs(manuscript) {
    if (_autoRefsBusy) return;
    _autoRefsBusy = true;
    var msg = $('#s2Msg'), box = $('#s2Picked');
    if (msg) msg.textContent = '正在解析参考文献（来源：' + (manuscript || '当前稿件') + '）…';
    if (box) box.innerHTML = '<div class="note info"><span class="ni">⏳</span><div>首次进入本步，正在解析稿件参考文献，约 1–3 秒…</div></div>';
    jpost('/refs/extract', { n: parseInt(val('#s2N'), 10) || 10 }).then(function (j) {
      _autoRefsBusy = false;
      if (!j.ok) {
        if (msg) msg.textContent = '';
        if (box) box.innerHTML = '<div class="note warn"><span class="ni">!</span><div>解析失败：' + esc(j.error || '') + '</div></div>';
        return;
      }
      paintRefs(j);
      if (msg) {
        msg.textContent = '已自动解析 ' + ((j.stats || {}).total || 0) + ' 条（来源：' + (j.manuscript || '') + '）';
      }
    }).catch(function (e) {
      _autoRefsBusy = false;
      if (msg) msg.textContent = '';
      if (box) box.innerHTML = errCard(e.message);
    });
  }

  function r4s2() {
    req('/refs').then(function (j) {
      var nm = $('#s2SourceName'), dm = $('#s2SourceDomain');
      if (nm) nm.textContent = j.manuscript || '（未上传）';
      if (dm) dm.textContent = j.domain || '—';
      if (j.extracted) {
        paintRefs(j);
        if (j.n && $('#s2N')) $('#s2N').value = j.n;
        if (j.seed !== null && j.seed !== undefined && $('#s2Seed')) $('#s2Seed').value = j.seed;
      } else if (j.manuscript) {
        // 上传完第一次进本步：自动解析一遍，让概览直接出现
        autoExtractRefs(j.manuscript);
      } else {
        renderPicked([]);
      }
    }).catch(function () {});
    refreshKb2();
    s2Progress();
    if (window.s2CnkiCheck) window.s2CnkiCheck();
  }

  window.s2CnkiCheck = function () {
    var el = $('#s2CnkiTxt');
    if (el) el.textContent = '检查中…';
    req('/cnki/status').then(function (j) {
      if (!el) return;
      if (j.logged_in) {
        el.textContent = '已登录 · ' + (j.account || '') + (j.org ? '（' + j.org + '）' : '');
        el.style.color = 'var(--ok, #15803d)';
      } else if (j.session) {
        el.textContent = '会话在，但未登录 · 点「重新登录知网」';
        el.style.color = 'var(--warn, #b45309)';
      } else {
        el.textContent = '未连接 · 点「重新登录知网」打开窗口后登录';
        el.style.color = 'var(--warn, #b45309)';
        el.title = j.error || '';
      }
    }).catch(function (e) {
      if (el) { el.textContent = '检查失败：' + e.message; el.style.color = 'var(--warn, #b45309)'; }
    });
  };

  window.s2CnkiLogin = function () {
    var el = $('#s2CnkiTxt');
    if (el) { el.textContent = '正在打开知网窗口…'; el.style.color = 'var(--ink-2)'; }
    toast('正在打开知网窗口，请稍候…');
    jpost('/cnki/login', {}).then(function (j) {
      if (!j.ok) {
        toast('打开失败：' + (j.error || ''));
        if (el) { el.textContent = '打开失败：' + (j.error || ''); el.style.color = 'var(--warn, #b45309)'; }
        return;
      }
      if (j.logged_in) {
        if (el) { el.textContent = '已登录 · ' + (j.account || '') + (j.org ? '（' + j.org + '）' : ''); el.style.color = 'var(--ok, #15803d)'; }
        toast('登录态仍然有效，无需重新登录');
      } else {
        if (el) { el.textContent = '窗口已打开，请在浏览器里登录，完成后点「检查状态」'; el.style.color = 'var(--warn, #b45309)'; }
        toast('请在弹出的浏览器窗口里扫码 / 机构登录');
        setTimeout(function () { if (window.s2CnkiCheck) window.s2CnkiCheck(); }, 30000);
      }
    }).catch(function (e) {
      toast('打开失败：' + e.message);
      if (el) { el.textContent = '打开失败：' + e.message; el.style.color = 'var(--warn, #b45309)'; }
    });
  };

  window.s2Extract = function () {
    var n = parseInt(val('#s2N'), 10) || 10;
    var seedRaw = (val('#s2Seed') || '').trim();
    var msg = $('#s2Msg');
    if (msg) msg.textContent = '抽取中…';
    jpost('/refs/extract', { n: n, seed: seedRaw === '' ? null : seedRaw }).then(function (j) {
      if (!j.ok) { if (msg) msg.textContent = ''; toast('抽取失败：' + (j.error || '')); return; }
      var st = j.stats || {};
      $('#s2Total').textContent = st.total || 0;
      $('#s2Abstractable').textContent = st.abstractable || 0;
      $('#s2Lang').textContent = ((st.by_lang || {}).zh || 0) + ' / ' + ((st.by_lang || {}).en || 0);
      renderPicked(j.picked || []);
      if (msg) {
        msg.textContent = '共 ' + (st.total || 0) + ' 条，抽中 ' + (j.picked || []).length +
          ' 篇　来源：' + (j.manuscript || '未知稿件');
      }
      toast('抽取完成');
    }).catch(function (e) {
      if (msg) msg.textContent = '';
      toast('抽取失败：' + e.message);
    });
  };

  var _s2Timer = null;

  window.s2Fetch = function () {
    jpost('/abstracts/fetch', { engine: 'auto' }).then(function (j) {
      toast('已开始抓取 ' + j.total + ' 篇');
      if (_s2Timer) clearInterval(_s2Timer);
      _s2Timer = setInterval(function () {
        s2Progress().then(function (finished) {
          if (finished) { clearInterval(_s2Timer); _s2Timer = null; refreshKb2(); toast('抓取结束'); }
        });
      }, 2500);
      s2Progress();
    }).catch(function (e) { toast('启动失败：' + e.message); });
  };

  window.s2Progress = function () {
    return req('/abstracts/progress').then(function (p) {
      var bar = $('#s2FetchBar'), msg = $('#s2FetchMsg');
      if (msg) {
        msg.textContent = p.running ? ('抓取中 ' + p.done + '/' + p.total)
          : (p.done ? ('已结束 · 成功 ' + p.succeeded + ' / 未获取 ' + p.failed) : '');
      }
      if (bar) {
        var pct = p.total ? Math.round(p.done / p.total * 100) : 0;
        var rows = (p.rows || []).map(function (r) {
          var ok = r.status === '已获取';
          return '<tr><td>' + (ok ? '✅' : '❌') + '</td><td>' + r.no + '</td>' +
            '<td>' + esc((r.title || '').slice(0, 48)) + '</td>' +
            '<td class="muted small">' + esc(ok ? (r.chars + ' 字 · ' + r.source) : (r.note || r.status)) + '</td></tr>';
        }).join('');
        bar.innerHTML = (p.done || p.running
          ? '<div class="meter" style="margin-bottom:10px"><span class="m-lb">抓取进度</span>' +
            '<span class="m-tr"><i style="width:' + pct + '%"></i></span>' +
            '<span class="m-v">' + p.done + '/' + p.total + '</span></div>'
          : '') +
          (rows ? '<table class="tbl"><thead><tr><th style="width:38px"></th><th style="width:44px">#</th>' +
            '<th>标题</th><th style="width:260px">结果</th></tr></thead><tbody>' + rows + '</tbody></table>' : '');
      }
      return !p.running;
    }).catch(function () { return true; });
  };

  function refreshKb2() {
    req('/kb').then(function (j) {
      if ($('#s2InKb')) $('#s2InKb').textContent = j.with_abstract + ' / ' + j.count;
      if ($('#s2KbStat')) {
        $('#s2KbStat').textContent = j.count
          ? ('卡片 ' + j.count + ' 篇 · 已获摘要 ' + j.with_abstract + ' 篇 · 未获取 ' + (j.count - j.with_abstract) + ' 篇')
          : '尚未建立';
      }
      if ($('#s2KbMd')) $('#s2KbMd').textContent = j.kb_md || '（尚未生成）';
    }).catch(function () {});
  }

  window.s2BuildKb = function () {
    jpost('/kb/build', {}).then(function (j) {
      refreshKb2();
      toast('知识库已重建：' + j.with_abstract + ' 篇有摘要');
    }).catch(function (e) { toast(e.message); });
  };

  /* ---------- Step 3 ---------- */

  function r4s3() {
    req('/kb').then(function (j) {
      var tag = $('#s3KbTag'), st = $('#s3KbStat');
      if (j.with_abstract > 0) {
        if (tag) { tag.className = 'tag ok'; tag.textContent = '已就绪'; }
        if (st) st.textContent = '本篇知识库收录 ' + j.count + ' 篇文献，其中 ' + j.with_abstract +
          ' 篇有摘要，可交给模型阅读。' + (j.count - j.with_abstract > 0
            ? '（另有 ' + (j.count - j.with_abstract) + ' 篇未获取，已按你的要求跳过不补）' : '');
      } else {
        if (tag) { tag.className = 'tag warn'; tag.textContent = '未就绪'; }
        if (st) st.textContent = '还没有任何摘要，请回 Step 2 抓取。';
      }
    }).catch(function () {});
  }

  function renderReview(rv) {
    var out = $('#s3Out');
    if (!out) return;
    var probs = (rv.problems || []).map(function (p, i) {
      return '<div class="note warn" style="margin-top:9px"><span class="ni">' + (i + 1) + '</span><div>' +
        '<b>' + esc(p.title || '') + '</b>' + (p.location ? '　<span class="muted small">' + esc(p.location) + '</span>' : '') +
        '<br/>' + esc(p.problem || '') +
        (p.suggestion ? '<br/><span style="color:var(--brand)">建议：' + esc(p.suggestion) + '</span>' : '') +
        (p.confidence ? '<br/><span class="muted small">置信度：' + esc(p.confidence) + '</span>' : '') +
        '</div></div>';
    }).join('');
    var merits = (rv.strengths || []).map(function (s) {
      return '<li>' + esc(s) + '</li>';
    }).join('');
    out.innerHTML =
      '<div class="note ok"><span class="ni">✅</span><div><b>结论</b>：' + esc(rv.summary || '（无）') +
      (rv.decision ? '<br/>建议决定：<b>' + esc(rv.decision) + '</b>' : '') + '</div></div>' +
      (merits ? '<div style="margin-top:12px"><b>优点</b><ul style="margin:6px 0 0 20px">' + merits + '</ul></div>' : '') +
      (probs ? '<div style="margin-top:14px"><b>问题清单</b>' + probs + '</div>' : '') +
      (rv.review_md ? '<div style="margin-top:16px"><b>完整审稿意见</b>' +
        '<pre class="pre" style="max-height:460px;overflow:auto;margin-top:8px">' + esc(rv.review_md) + '</pre></div>' : '');
  }

  var _lastReview = null;

  window.s3Draft = function () {
    var out = $('#s3Out'), msg = $('#s3Msg');
    if (out) out.innerHTML = '<div class="note info"><span class="ni">⏳</span><div>模型正在读知识库并起草意见，约 20–60 秒…</div></div>';
    if (msg) msg.textContent = '调用中…';
    jpost('/review/draft', {}).then(function (j) {
      _lastReview = j.review;
      renderReview(j.review || {});
      if (msg) msg.textContent = '已保存到 ' + j.md_path + '（知识库 ' + num(j.kb_chars) + ' 字入模型）';
      toast('审稿意见已生成');
    }).catch(function (e) {
      if (out) out.innerHTML = errCard(e.message);
      if (msg) msg.textContent = '';
    });
  };

  window.s3OpenKb = function () {
    go(1);
    setTimeout(function () {
      var el = $('#s2KbMd');
      if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }, 120);
  };

  window.s3Save = function () {
    if (!_lastReview) { toast('还没有生成意见'); return; }
    toast('已写入 03_review/review.md');
  };

  /* ---------- Step 4 ---------- */

  function r4s4() {
    s4RefreshLib();
    llm2Tag();
    if ($('#lm2Base') && !$('#lm2Base').value && window.llm2Load) window.llm2Load();
  }

  window.s4RefreshLib = function () {
    req('/kb').then(function (j) {
      var box = $('#s4LibNow');
      if (!box) return;
      var slug = (M.meta && M.meta.domain) ? M.meta.domain.replace(/\s+/g, '').replace(/·/g, '-') : '未分类';
      box.innerHTML = '<table class="tbl"><tbody>' +
        '<tr><td>归档类别</td><td><b>' + esc((M.meta && M.meta.domain) || '未填写') + '</b></td></tr>' +
        '<tr><td>归档目录</td><td><code>_library/' + esc(slug) + '/</code></td></tr>' +
        '<tr><td>本篇知识库</td><td><b>' + j.with_abstract + '</b> 篇文献摘要<br/>' +
        '<span class="muted small">留在本篇 02_abstracts/，不复制进总库</span></td></tr>' +
        '</tbody></table>';
    }).catch(function () {});
  };

  window.s4Archive = function () {
    var msg = $('#s4Msg');
    var summary = (val('#s4Summary') || '').trim();
    var verdict = (val('#s4Verdict') || '').trim();
    if (!summary) {
      toast('沉淀总结不能为空 —— 先点「第二模型复审」自动生成，或自己写一段');
      return;
    }
    var exp = { 复审结论: verdict, 沉淀总结: summary };
    if (msg) msg.textContent = '写入中…';
    jpost('/archive', { experience: exp }).then(function (j) {
      if (msg) msg.textContent = j.msg;
      toast('已沉淀到总知识库');
      s4RefreshLib();
    }).catch(function (e) {
      if (msg) msg.textContent = '';
      toast('写入失败：' + e.message);
    });
  };

  /* ---------- Step 4：第二模型复审 ---------- */

  window.llm2Load = function () {
    req('/llm2/status').then(function (j) {
      var tag = $('#lm2Tag'), out = $('#lm2Out');
      if ($('#lm2Base') && j.base_url) $('#lm2Base').value = j.base_url;
      if ($('#lm2Model') && j.configured && j.model) $('#lm2Model').value = j.model;
      if (tag) {
        tag.className = 'tag ' + (j.configured ? 'ok' : 'gray');
        tag.textContent = j.configured ? (j.model || '已配置') : '未配置';
      }
      if (out) {
        out.innerHTML = j.configured
          ? '<div class="note ok" style="margin-top:8px"><span class="ni">✓</span><div>复审模型：' + esc(j.model || '') + '</div></div>'
          : '';
      }
      llm2Tag();
    }).catch(function () {});
  };

  window.llm2Save = function () {
    var out = $('#lm2Out');
    if (out) out.innerHTML = '<div class="note info"><span class="ni">⏳</span><div>保存并测试连接…</div></div>';
    jpost('/llm2/config', {
      base_url: val('#lm2Base'), api_key: val('#lm2Key'), model: val('#lm2Model')
    }).then(function (j) {
      if (out) {
        out.innerHTML = j.connected
          ? '<div class="note ok"><span class="ni">✓</span><div>复审模型已接入：' + esc(j.model || '') + '</div></div>'
          : '<div class="note warn"><span class="ni">!</span><div>已保存，但连接失败：' + esc(j.error || '') + '</div></div>';
      }
      if (j.connected) $('#lm2Key').value = '';
      llm2Load();
    }).catch(function (e) {
      if (out) out.innerHTML = errCard(e.message);
    });
  };

  function llm2Tag() {
    req('/llm2/status').then(function (j) {
      var t = $('#s4Llm2Tag');
      if (t) {
        t.className = 'tag ' + (j.configured ? 'ok' : 'gray');
        t.textContent = j.configured ? ('复审模型：' + (j.model || '')) : '复审模型未配置';
      }
    }).catch(function () {});
  }

  function renderSecond(rv, j) {
    var out = $('#s4SecondOut');
    if (!out) return;
    var dis = (rv.disagreements || []).map(function (d, i) {
      return '<div class="note warn" style="margin-top:8px"><span class="ni">' + (i + 1) + '</span><div>' +
        '<b>' + esc(d.point || '') + '</b><br/>' + esc(d.reason || '') +
        (d.suggestion ? '<br/><span style="color:var(--brand)">改法：' + esc(d.suggestion) + '</span>' : '') +
        '</div></div>';
    }).join('');
    var agr = (rv.agreements || []).map(function (x) { return '<li>' + esc(x) + '</li>'; }).join('');
    var mis = (rv.missed || []).map(function (x) { return '<li>' + esc(x) + '</li>'; }).join('');
    out.innerHTML =
      '<div class="note ok"><span class="ni">✓</span><div><b>复审结论：' + esc(rv.verdict || '—') + '</b>' +
      (rv.confidence ? '　<span class="muted small">置信度 ' + esc(rv.confidence) + '</span>' : '') +
      '<br/><span class="muted small">复审模型：' + esc((j && j.model) || '') + '</span></div></div>' +
      (agr ? '<div style="margin-top:10px"><b>同意的判断</b><ul style="margin:6px 0 0 20px">' + agr + '</ul></div>' : '') +
      (dis ? '<div style="margin-top:12px"><b>不同意的判断</b>' + dis + '</div>' : '') +
      (mis ? '<div style="margin-top:12px"><b>初审漏掉的问题</b><ul style="margin:6px 0 0 20px">' + mis + '</ul></div>' : '') +
      (rv.summary ? '<div style="margin-top:14px"><b>沉淀总结</b><div class="note info" style="margin-top:6px">' +
        '<span class="ni">Σ</span><div>' + esc(rv.summary) + '</div></div></div>' : '');
  }

  window.s4Second = function () {
    var out = $('#s4SecondOut'), msg = $('#s4SecondMsg');
    if (out) out.innerHTML = '<div class="note info"><span class="ni">⏳</span><div>复审模型正在读初审意见与稿件，约 30–90 秒…</div></div>';
    if (msg) msg.textContent = '调用中…';
    jpost('/review/second', {}).then(function (j) {
      var rv = j.review || {};
      renderSecond(rv, j);
      if ($('#s4Verdict')) $('#s4Verdict').value = rv.verdict || '';
      if ($('#s4Summary')) $('#s4Summary').value = rv.summary || '';
      if (msg) msg.textContent = '已存 ' + j.md_path + '（' + (j.model || '') + '）';
      toast('复审完成，总结已填入下方');
    }).catch(function (e) {
      if (out) out.innerHTML = errCard(e.message);
      if (msg) msg.textContent = '';
    });
  };

  window.s4OpenSecond = function () {
    window.open(API + '/file?path=' + encodeURIComponent('03_review/second-review.md'), '_blank');
  };

  window.confirmSearch = function () {
    if (M.mode !== 'real') {
      toast('检索计划已确认，正在调用 OpenAlex / Crossref / 智慧芽 MCP…（演示）');
      _go(3);
      return;
    }
    var plan = collectPlan();
    if (!plan.queries.length) { toast('请至少填写一条检索词'); return; }
    if (!plan.sources.length) { toast('请至少选择一个已接入的数据源'); return; }
    runSearch(plan);
  };

  window.finish = function () {
    $$('.step').forEach(function (s) { s.dataset.state = 'done'; });
    if (M.mode !== 'real') { toast('本轮审稿资产已写入知识库（演示）'); return; }
    _go(9);
    refreshTree();
    toast('本轮产物已落盘：05_review/review-report.md · citation-check.md');
  };

  /* ------------------------------------------------------------ 恢复上次状态 */
  function hashStep() {
    var m = /step=(\d+)/.exec(location.hash || '');
    return m ? parseInt(m[1], 10) : null;
  }
  function readWs(path) {
    return req('/file?path=' + encodeURIComponent(path)).then(function (j) {
      return j.content;
    }).catch(function () { return null; });
  }
  function restore() {
    var jobs = [];
    jobs.push(readWs('01_parse/manuscript_parse.json').then(function (t) {
      if (t) {
        M.parse = JSON.parse(t);
        M.parseResp = { parse: M.parse, manuscript: M.meta ? M.meta.manuscript : '',
          json_path: '01_parse/manuscript_parse.json', md_path: '01_parse/manuscript-outline.md',
          log: '（从已有工作区恢复）' };
      }
    }));
    jobs.push(readWs('02_search/results.json').then(function (t) {
      if (t) { var d = JSON.parse(t); M.rows = d.rows || []; M.searchStats = d.stats || Object.create(null); }
    }));
    jobs.push(readWs('02_search/doi-verification.json').then(function (t) {
      if (t) { M.verify = JSON.parse(t); }
    }));
    jobs.push(readWs('05_review/review-report.md').then(function (t) {
      if (t) { M.draft = M.draft || { path: '05_review/review-report.md', content: t, citation_path: null }; }
    }));
    jobs.push(readWs('05_review/citation-check.md').then(function (t) { if (t) M.cite = t; }));
    return Promise.all(jobs).catch(function () { /* 无历史产物 */ });
  }

  /* ------------------------------------------------------------ 启动 */
  /* ------------------------------------------------------------ 右栏知识库状态 */
  function mountLibRail() {
    var anchor = $('#railFocus');
    if (!anchor || $('#railLib')) return;
    var row = anchor.closest('.ctx-item').cloneNode(true);
    row.querySelector('b').textContent = '知识库';
    var sp = row.querySelector('span');
    sp.id = 'railLib';
    sp.textContent = '读取中…';
    anchor.closest('.ctx-item').parentNode.appendChild(row);
  }

  window.updateLibBadge = function () {
    var el = $('#railLib');
    if (!el || M.mode !== 'real') return;
    req('/library').then(function (j) {
      var s = j.stats || Object();
      var n = (s.papers || []).length;
      el.textContent = n
        ? (n + ' 篇稿件 / ' + (s.cards || 0) + ' 张卡片 / ' + (s.claims || 0) + ' 条实体' +
           '（' + (s.updated || '') + ' 更新）')
        : '空库（完成 Step 4 → Step 9 后按领域累积）';
    }).catch(function () { el.textContent = '未接入'; });
  };

  function boot() {
    if (location.protocol === 'file:') {
      M.mode = 'demo';
      badge();
      return;
    }
    req('/state').then(function (st) {
      M.mode = 'real';
      M.state = st;
      if (st.current && st.current.slug) {
        M.slug = st.current.slug;
        M.meta = st.current;
        if (st.current.title) setTitle(st.current.title);
        if (st.current.manuscript) M.file = { name: st.current.manuscript, size: 0 };
        if (st.current.manuscript) setFile(st.current.manuscript);
      }
      badge();
      bindReal();
      refreshTree();
      markMock();
      try { r4s1(); updateRailMeters(); if (window.llm2Load) window.llm2Load(); } catch (e) { /* 首屏填充 */ }
      loadLlm().then(function () {
        // 未接入模型时，首次进入自动展开左栏「模型接口」，引导用户接入自己的 API
        if ((!M.llm || !M.llm.connected)) {
          var sm = $('#sideModel');
          if (sm) sm.classList.add('open');
        }
      });
      var step = hashStep();
      restore().then(function () {
        if (M.file) {
          var row = $('.panel[data-panel="0"] .file-row');
          if (row && M.file.name) {
            $('.file-name', row).textContent = M.file.name;
            $('.file-meta', row).textContent = '已写入 00_manuscript/ · 未发表稿';
            var t = $('.right .tag', row);
            if (t) { t.className = 'tag ok'; t.textContent = '已上传'; }
          }
        }
        if (M.verify.length) {
          var vmap = Object.create(null);
          M.verify.forEach(function (r) { vmap[(r.doi || '').toLowerCase()] = !!r.verified; });
          M.rows.forEach(function (r) { r.verify = vmap[(r.doi || '').toLowerCase()] ? '已核验' : '未验证'; });
          var passed = M.verify.filter(function (r) { return r.verified; }).length;
          setMeter('文献核验', Math.round(passed / M.verify.length * 100));
        }
        if (M.parse) {
          setMeter('稿件覆盖', Math.min(100, Math.round((M.parse.sections_detected || []).length / 8 * 100)));
        }
        // 真实模式下不展示未接入能力的虚假百分比
        setMeter('同位稿件', 0, '未接入');
        setMeter('规范对照', 0, '未接入');
        mountLibRail();
        updateLibBadge();
        if (step !== null && step > 0) window.go(step);
      });
      toast(st.current && st.current.slug
        ? ('已连接本地服务 · 继续工作区 ' + st.current.slug)
        : '已连接本地服务 · Step 0 可直接上传稿件');
    }).catch(function (err) {
      M.mode = 'demo';
      badge();
      if (window.console) console.warn('[real-mode] 未连接本地服务，保持演示模式：', err.message);
    });
  }
  boot();
})();
