/* 先秦史料查询 —— 中文史料全文检索 + 人工检查台（vanilla，无依赖）
 * 路由：#/ home · #/search?q=… · #/p/{id} · #/books · #/book/{id} · #/file/{id} ·
 *       #/read/{id}?row=N（读全文） · #/pending · #/about
 * 文案中文；代码英文。所有列表分页，不一次取全库。
 * 出处只展示数据库真实字段（无则「暂无」），绝不按文件名猜章节；上下文是同文件真实相邻记录。 */
"use strict";

const $ = (s, r = document) => r.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g,
  c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const num = v => (typeof v === "number" ? v : parseInt(String(v).replace(/,/g, ""), 10) || 0);
const jp = (s, fb) => { try { return s ? JSON.parse(s) : fb; } catch { return fb; } };
const fam = f => (f === "sbck" ? "四部丛刊（SBCK）" : (f || "tls"));

/* 唯一的取数接缝。两种模式在这里分流，其余代码一行都不知道自己跑在哪：
 *   · API 模式 —— 本地 `python -m api.main` 在跑，原样 fetch（第一到第四阶段的行为）；
 *   · 静态模式 —— 公开站上没有 Python 进程，由 engine/static_api.js 在浏览器里
 *     重放同一组接口，数据结构、错误消息都与真 API 一致（对拍见
 *     scripts/site/check_engine.py 的 static-api 检查）。
 * 模式由 boot.js 探测一次，此后不再逐请求回退 —— 详见 boot.js 的说明。 */
async function api(path) {
  const boot = window.HistoryAIBoot ? await window.HistoryAIBoot : { mode: "api" };
  if (boot.mode === "api") {
    const r = await fetch(path);
    const j = await r.json().catch(() => ({ error: `响应非 JSON（HTTP ${r.status}）` }));
    if (!r.ok || j.error) throw new Error(j.error || `HTTP ${r.status}`);
    return j;
  }
  if (!boot.site) throw new Error(boot.error || "静态模式：数据不可用");
  return boot.site.get(path);   // 出错时抛 Error(消息)，与上面同契约
}
const qs = obj => { const u = new URLSearchParams(); for (const k in obj)
  if (obj[k] !== "" && obj[k] != null) u.set(k, obj[k]); return u.toString(); };

/* 空结果的归因（第六点三阶段）。**只在本地 API 模式下问**：静态模式（公开演示与
 * 本地静态导出）没有 Python 进程，回答不了「语料里到底有没有这个串」——那要全表
 * 计数。那边保持原有文案与横幅（boot.js），本函数返回 null，调用方什么都不做。
 * 出错一律吞掉：归因是锦上添花，不能让它把「没有结果」变成「检索失败」。 */
async function tryDiagnose(q, book, edition) {
  const boot = window.HistoryAIBoot ? await window.HistoryAIBoot : { mode: "api" };
  if (boot.mode !== "api" || !q) return null;
  // book/edition 必须一起传：归因的「收录范围」是**按这次检索的范围**算的。
  // 只传 q 会让「在《北齊書》里搜不到」被答成「全库 19 部里没有」——
  // 那正是本阶段要消灭的那种含糊话（§25 情况三）。
  try { return await api(`/api/diagnose?${qs({ q, book, edition })}`); }
  catch (e) { return null; }
}

/* 语料收录进度（第六点三阶段）。同样是**只在本地 API 模式问**：真实书单不能进
 * 发布产物（发布闸门判据②），演示站上根本没有这条路由。返回 null 时调用方少画
 * 一块，不报错。快照过期与否由页面自己标注生成时间。 */
async function tryCatalog() {
  const boot = window.HistoryAIBoot ? await window.HistoryAIBoot : { mode: "api" };
  if (boot.mode !== "api") return null;
  try {
    const c = await api("/api/catalog");
    return c && c.available ? c : null;
  } catch (e) { return null; }
}

/* ---------- 会话缓存（stats 60 秒；books 列表本会话内） ---------- */
let statsCache = { t: 0, v: null };
async function getStats() {
  const now = Date.now();
  if (!statsCache.v || now - statsCache.t > 60000)
    statsCache = { t: now, v: await api("/api/stats") };
  return statsCache.v;
}
let booksCache = null;
async function getBooks() {
  if (!booksCache) booksCache = await api("/api/books");
  return booksCache;
}

/* ---------- 文本渲染 ---------- */
// 富文本：<pb:> 可藏可显；¶ 灰；&KR…; 橙；注释候选整段紫
function rich(text, opts = {}) {
  const { pb = true, cand = false } = opts;
  const re = /(<pb:[^>]*>)|(¶)|(&KR[A-Za-z0-9]+;)/g;
  const parts = [];
  let last = 0, m;
  while ((m = re.exec(text))) {
    if (m.index > last) parts.push(esc(text.slice(last, m.index)));
    if (m[1]) parts.push(pb ? `<span class="pbmark">${esc(m[1])}</span>` : "");
    else if (m[2]) parts.push(`<span class="para">¶</span>`);
    else parts.push(`<span class="chip kr">${esc(m[3])}</span>`);
    last = m.index + m[0].length;
  }
  parts.push(esc(text.slice(last)));
  return `<span class="${cand ? "wrap-cand" : ""}">${parts.join("")}</span>`;
}
// 已在转义文本上高亮检索词（检索词已转繁）
function mark(escapedText, terms) {
  const pats = [...new Set(terms)].map(t => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"))
    .filter(Boolean);
  if (!pats.length) return escapedText;
  return escapedText.replace(new RegExp("(" + pats.join("|") + ")", "g"),
    "<mark>$1</mark>");
}
/* ---------- 工具 ---------- */
function chip(txt, cls, title) {
  return `<span class="chip ${cls || ""}"${title ? ` title="${esc(title)}"` : ""}>${esc(txt)}</span>`;
}
function pagerBlock(total, page, pageSize, toFn) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const cur = Math.min(page, pages);
  return `<div class="pager">
    <span>共 <b>${num(total).toLocaleString()}</b> 条 · 第 ${cur} / ${pages} 页</span>
    <span class="spacer"></span>
    <select id="pgSize" title="每页条数" onchange="setPageSize(this.value)">
      ${[20, 50, 100].map(n => `<option value="${n}" ${pageSize === n ? "selected" : ""}>${n}</option>`).join("")}
    </select>
    <button class="btn" onclick="${toFn}(${cur - 1})" ${cur <= 1 ? "disabled" : ""}>← 上一页</button>
    <button class="btn" onclick="${toFn}(${cur + 1})" ${cur >= pages ? "disabled" : ""}>下一页 →</button>
  </div>`;
}
// 每页条数改变：按当前页类型重载（各页保留各自状态）
window.onSize = v => {
  const { seg } = parseHash();
  if (seg[0] === "search") { searchState.pageSize = v; searchState.page = 1; doSearch(); }
  else if (seg[0] === "pending") { pv.pageSize = v; pv.page = 1; pvGo(1); }
  else if (seg[0] === "file" && fv.tab === "recs") { fv.pageSize = v; fv.page = 1; loadRecsPage(); }
};
function setPageSize(v) { window.onSize(+v); }

// source_ref JSON：对象或数组 → 取第一段可读出处
function srcRefOf(s) {
  const arr = Array.isArray(s) ? s : (s ? [s] : []);
  return arr.find(x => x && (x.raw || x.section_ref)) || null;
}
// SELECT * 行 → 前端统一形态（解析 JSON 列）
function rowNorm(r) {
  return { ...r, special_chars: jp(r.special_chars_json, []),
           source_ref: jp(r.source_ref_json, null) };
}
function dataTable(m) {
  const f = (k, v) => `<tr><td class="mono">${k}</td><td>${v == null || v === ""
    ? '<span class="quiet">暂无</span>' : esc(v)}</td></tr>`;
  const sp = m.special_chars || [];
  const sr = srcRefOf(m.source_ref);
  const ctxloc = [m.juan, m.section, m.subsection, m.division, m.ab].filter(Boolean).join(" · ");
  return `<div class="overflow"><table class="dev">
    ${f("passage_id", m.passage_id)}${f("book / file", `${m.book_id} · ${m.file_id}`)}
    ${f("kind 记录类型", m.kind)}${f("layer 层级", m.layer)}${f("status 状态", m.status)}
    ${f("row_no / seq 行号", m.row_no != null ? `${m.row_no} / ${m.seq}` : null)}
    ${f("char_start–end 行内偏移", m.char_start != null && m.char_end != null
      ? `${m.char_start}–${m.char_end}` : null)}
    ${f("卷/篇/节/部字段", ctxloc || null)}
    ${f("text_orig 原文（显示依据）", m.text_orig)}
    ${f("normalized_text 检索文本", m.normalized_text)}
    ${f("pb_raw 页码原样", m.pb_raw)}${f("pb_block / pb_page", m.pb_block != null
      ? `${m.pb_block}${m.pb_page ? ` · ${m.pb_page}` : ""}` : null)}
    ${f("special_chars 缺字码", sp.length ? sp.join(" ") : null)}
    ${f("source_ref 出处", sr ? (sr.raw || "") + (sr.section_ref ? ` → ${sr.section_ref}` : "") : null)}
    ${f("notes 备注", jp(m.notes_json, null) ? JSON.stringify(jp(m.notes_json, null)) : null)}
    ${f("sha256 文件校验", m.sha256)}${f("file_name 原文件", m.file_name)}
  </table></div>`;
}

/* ---------- 路由 ---------- */
function parseHash() {
  const h = location.hash.replace(/^#/, "") || "/";
  const [path, q] = h.split("?");
  const params = {};
  if (q) for (const [k, v] of new URLSearchParams(q)) params[k] = v;
  return { path, seg: path.split("/").filter(Boolean), params };
}
function navActive() {
  const key = parseHash().seg[0] || "home";
  document.querySelectorAll("nav a").forEach(a =>
    a.classList.toggle("active",
      a.dataset.nav === key || (key === "p" && a.dataset.nav === "search")));
}
async function route() {
  navActive();
  const { seg, params } = parseHash();
  const view = $("#view");
  try {
    if (!seg.length || seg[0] === "home") return await homeView();
    if (seg[0] === "search") return await searchView(params);
    if (seg[0] === "ask") return await askView(params);
    if (seg[0] === "p") return await passageView(+seg[1], params);
    if (seg[0] === "books") return await booksView();
    if (seg[0] === "coverage") return await coverageView();
    if (seg[0] === "book") return await bookView(seg[1]);
    if (seg[0] === "file") return await fileView(+seg[1], params);
    if (seg[0] === "read") return await readView(+seg[1], params);
    if (seg[0] === "pending") return await pendingView();
    if (seg[0] === "about") return await aboutView();
    view.innerHTML = `<div class="card">页面不存在：${esc(seg.join("/"))}</div>`;
  } catch (e) {
    view.innerHTML = `<div class="card"><b>出错了</b><br>${esc(e.message)}<br>
      <a class="backlink" href="#/">← 回首页</a></div>`;
  }
}
window.addEventListener("hashchange", route);

/* ================= 首页 ================= */
async function homeView() {
  const view = $("#view");
  const [st, books] = await Promise.all([getStats(), getBooks()]);
  let html = `<div class="hero">
    <h1>先秦史料查询</h1>
    <div class="sub">已收录 ${books.length} 部史书 · ${num(st.passages).toLocaleString()} 条正文 — 检索人物、事件、地名或原文词句</div>
    <div class="searchline">
      <input id="q" placeholder="输入关键词，如：齐桓公　城濮之战　孔子" autocomplete="off">
      <button class="btn primary" onclick="homeSearch()">搜索</button>
    </div>
    <div class="examples"><b>试试：</b>
      <a onclick="homeSearch('齐桓公')">齐桓公</a><a onclick="homeSearch('管仲')">管仲</a>
      <a onclick="homeSearch('晋文公')">晋文公</a><a onclick="homeSearch('伍子胥')">伍子胥</a>
      <a onclick="homeSearch('苏秦')">苏秦</a><a onclick="homeSearch('张仪')">张仪</a>
      <a onclick="homeSearch('城濮')">城濮</a><a onclick="homeSearch('长勺')">长勺</a>
      <a onclick="homeSearch('鸿门宴')">鸿门宴</a>
    </div>
  </div>
  <div class="home-books">
    <h2>${books.length} 部史书</h2><div class="book-grid">`;
  for (const b of books) {
    html += `<a class="book-card" href="#/book/${encodeURIComponent(b.book_id)}">
      <div class="t">《${esc(b.title)}》</div>
      <div class="n">${esc(b.book_id)} · ${esc(fam(b.family))}</div>
      <div class="m"><span>正文 ${num(b.main_passages).toLocaleString()}</span>
        <span>${b.pending ? `待确认 ${num(b.pending).toLocaleString()}` : "无待确认"}</span></div>
    </a>`;
  }
  html += `</div>
    <p class="quiet" style="margin-top:14px">共 ${num(st.records).toLocaleString()} 条原文记录 ·
      文本以原始文件为准；数据库由解析管线重建，原始史料文件零改动。
      <a class="backlink" href="#/about">项目说明 →</a></p>
  </div>`;
  view.innerHTML = html;
  const qi = $("#q");
  qi.addEventListener("keydown", e => { if (e.key === "Enter") homeSearch(); });
  qi.focus();
}
function homeSearch(word) {
  const v = word ?? $("#q").value.trim();
  if (!v) { $("#q").focus(); return; }
  location.hash = `#/search?q=${encodeURIComponent(v)}`;
}

/* ================= 全文检索 ================= */
/* 第三阶段：一个结果 = 一个「史料片段」（Result Block），不再是单条短句。
 * 正文仍是数据库里的 text_orig 原样相接——不生成、不润色、不补标点。 */
const BLOCK_MODES = [["short", "简短"], ["standard", "标准"], ["long", "完整"]];
const MODE_TIP = {
  short: "简短：每段尽量收敛到约 120 字，适合快速扫读",
  standard: "标准：每段尽量给到约 420 字，能读出一件事的来龙去脉",
  long: "完整：每段尽量给到约 1200 字，接近一整段史料的完整体量",
};
let searchState = { q: "", book: "", edition: "", page: 1, pageSize: 20,
                    mode: "standard", text: "orig", total: 0, hitTotal: 0,
                    terms: [], simpleTerms: [], results: [], items: [] };
let searchLoading = false;
/* 在册未入库的书（第六点四阶段 §25 情况四）。数据来自 /api/catalog 的 planned；
 * 演示模式下拿不到目录，集合为空 —— 于是公开站的下拉里只有演示书，
 * **真实书名一个都不进产物**（§22）。 */
let plannedBooks = [];
/* 这本书在不在「在册未入库」里？**判据只有这一处**。判错的方向不对称：
 * 判成「在库」→ 送去 /api/search → engine 抛「未识别的史书」→ 用户看到错误框；
 * 判成「未入库」→ 送去 diagnose → 用户看到「这部史书尚未收录」。后者才是真话。
 * 空 id 一律 false（「全部史书」不能被判成某本未入库的书）。 */
const isPlannedBook = (id, list = plannedBooks) =>
  !!id && (list || []).some(p => p.book_id === id);

async function searchView(params) {
  const view = $("#view");
  const books = await getBooks();
  // §25 情况四：把「在册未入库」的书也列进下拉。**必须能选中它们** —— 否则
  // 「这部史书尚未收录」这句话在浏览器里根本没有入口，「情况四」也就无从验收。
  const cat = await tryCatalog();
  plannedBooks = (cat && cat.planned) || [];
  searchState = { ...searchState, q: params.q ?? "", book: params.book ?? "",
                  edition: params.edition ?? "", page: +(params.page || 1),
                  mode: params.mode || searchState.mode || "standard",
                  text: params.text || searchState.text || "orig",
                  total: 0, hitTotal: 0, results: [], items: [] };
  view.innerHTML = `<div class="searchbar">
      <input id="sq" type="text" value="${esc(searchState.q)}"
        placeholder="人物 · 事件 · 地名 · 原文词句">
      <select id="sbook">
        <option value="">全部史书</option>
        ${books.map(b => `<option value="${esc(b.book_id)}"
          ${searchState.book === b.book_id ? "selected" : ""}>《${esc(b.title)}》</option>`).join("")}
        ${plannedBooks.length ? `<optgroup label="尚未收录（搜不到）">${
          plannedBooks.map(p => `<option value="${esc(p.book_id)}"
            ${searchState.book === p.book_id ? "selected" : ""}>《${esc(p.title)}》</option>`).join("")
        }</optgroup>` : ""}
      </select>
      <select id="sedition">
        <option value="">全部版本</option>
        <option value="sbck" ${searchState.edition === "sbck" ? "selected" : ""}>四部丛刊（SBCK）</option>
        <option value="tls" ${searchState.edition === "tls" ? "selected" : ""}>tls</option>
      </select>
      <label class="modepick" title="每个史料片段展示多长；只影响取多少相邻原文，不改动原文">
        显示长度
        <select id="smode" onchange="onModeChange(this.value)">
          ${BLOCK_MODES.map(([v, t]) => `<option value="${v}"
            ${searchState.mode === v ? "selected" : ""}>${t}</option>`).join("")}
        </select>
      </label>
      <label class="modepick" title="${esc(TEXT_MODE_TIP[searchState.text])}">
        字体
        <select id="stext" onchange="onTextChange(this.value)">
          ${TEXT_MODES.map(([v, t]) => `<option value="${v}"
            ${searchState.text === v ? "selected" : ""}>${t}</option>`).join("")}
        </select>
      </label>
      <button class="btn primary" onclick="doSearch()">搜索</button>
    </div>
    <div id="result"><div class="load">输入关键词开始检索<br>例：「齐桓公」「城濮」「殽之战」均可；空结果可换用更短词（如「殽」）</div></div>`;
  $("#sq").addEventListener("keydown", e => { if (e.key === "Enter") doSearch(); });
  if (searchState.q) doSearch();
}
// 显示长度改变：记住选择并重搜（只取当前页，避免整库重跑两次历史记录）
window.onModeChange = v => {
  searchState.mode = v || "standard";
  try { localStorage.setItem("historyai.mode", searchState.mode); } catch (e) { /* 隐私模式 */ }
  if (searchState.q) doSearch(1);
};
window.onTextChange = v => {
  searchState.text = v || "orig";
  try { localStorage.setItem("historyai.text", searchState.text); } catch (e) { /* 隐私模式 */ }
  // 字体切换不重新请求：繁简两份正文响应里都已经带回来了（见 search.dual_text）。
  if (searchState.q) renderResults(searchState.lastRes);
};
try { const m = localStorage.getItem("historyai.mode");
      if (BLOCK_MODES.some(x => x[0] === m)) searchState.mode = m; } catch (e) { /* 忽略 */ }
try { const t = localStorage.getItem("historyai.text");
      if (TEXT_MODES.some(x => x[0] === t)) searchState.text = t; } catch (e) { /* 忽略 */ }

function doSearch(page) {
  if (searchLoading) return;
  if (page != null) searchState.page = page;
  const sq = $("#sq"); if (sq) searchState.q = sq.value.trim();
  const sb = $("#sbook"); if (sb) searchState.book = sb.value;
  const se = $("#sedition"); if (se) searchState.edition = se.value;
  const sm = $("#smode"); if (sm) searchState.mode = sm.value;
  const sx = $("#stext"); if (sx) searchState.text = sx.value;
  if (!searchState.q) { $("#sq").focus(); return; }
  const rp = { q: searchState.q, book: searchState.book, edition: searchState.edition,
               page: searchState.page, page_size: searchState.pageSize,
               mode: searchState.mode, text: searchState.text };
  history.replaceState(null, "", `#/search?${qs(rp)}`);
  const box = $("#result");
  // §25 情况四：**在册未入库的书不送去 /api/search** —— engine.resolve_book 只认
  // 库里的书，会抛「未识别的史书」（engine 没错，它的职责就只管库里的书）。
  // 正确答案是 diagnose 的四态之一 not_imported：「这部史书尚未收录」，还带上
  // 它有多少卷。送去 /api/search 的话，用户看到的是一个错误框 —— 那等于把
  // 「我还没收这本书」说成了「这次检索坏了」。
  if (isPlannedBook(searchState.book)) {
    box.innerHTML = `<div class="load">正在查收录范围…</div>`;
    renderNotImported(box, rp);
    return;
  }
  box.innerHTML = `<div class="load">正在查找史料…</div>`;
  searchLoading = true;
  api(`/api/search?${qs(rp)}`).then(res => { searchLoading = false; renderResults(res); })
    .catch(e => { searchLoading = false;
      box.innerHTML = `<div class="card"><b>检索失败</b><br>${esc(e.message)}</div>`; });
}
function renderResults(res) {
  const box = $("#result");
  if (!res) return;                      // 字体切换时若还没有结果，什么都不做
  searchState.lastRes = res;
  searchState.simpleTerms = res.terms_simplified || [];
  searchState.total = res.total; searchState.page = res.page;
  searchState.hitTotal = num(res.hit_total);
  searchState.results = res.results || []; searchState.items = [];
  const terms = (res.q_traditional || "").split(/\s+/).filter(Boolean);
  searchState.terms = terms;          // 展开更多上下文时也要继续高亮
  if (!res.results || !res.results.length) {
    box.innerHTML = `<div class="empty"><b>没有找到相关史料</b><br>
      换个写法（如「殽之战」→「殽」）、缩短关键词，或先选「全部史书」再搜</div>`;
    renderDiagnosis(box, res);
    return;
  }
  const scope = [];
  if (res.book !== "全部") scope.push("限定所选史书");
  if (res.edition !== "全部") scope.push("限定所选版本");
  const hits = num(res.hit_total);
  const blocks = num(res.total);
  // 说清两种计数：片段是翻页单位，原始命中是检索命中条数。两者之差
  // 正是「同一段史料被拆成许多短句」的程度，如实展示，不掩饰。
  let html = `<div class="resulthead">
      共 <b>${blocks.toLocaleString()}</b> 段史料${scope.length ? `（${scope.join("、")}）` : ""}
      · 第 ${(res.page - 1) * res.page_size + 1}–${Math.min(blocks, res.page * res.page_size)} 段
      <span class="quiet">· 原始命中 ${hits.toLocaleString()} 条，已按上下文合并为完整片段</span>
      ${res.truncated ? `<br><span class="quiet">命中过多，本次只组装了前 ${blocks.toLocaleString()} 段</span>` : ""}
      ${res.section_truncated ? `<br><span class="quiet">命中的篇名过多，篇名结果只列了前若干条（正文结果不受影响）</span>` : ""}
    </div>`;
  html += partialBanner(res.scope);
  searchState.results.forEach((b, i) => {
    html += blockCard(b, terms, i, searchState.text, searchState.simpleTerms); });
  html += pagerBlock(blocks, res.page, res.page_size, "doSearch");
  html += `<p class="quiet">每段正文由数据库原始记录（<span class="mono">text_orig</span>）依序相接，
    不改写、不补标点；相邻原文只按同一文件内的真实次序读取，不作任何推测。
    简体输入自动转繁体匹配（齐桓公 → 齊桓公）。</p>`;
  box.innerHTML = html;
}

/* 空结果的逐层归因（第六点三阶段 6.3-H）。判据与测试报告同源（search/diagnose.py），
 * 所以页面上说「语料尚未收录」时，`tests/recall.py` 的报告里也是同一句话。
 * 只在本地 API 模式下拿得到（见 tryDiagnose）；拿不到就保持上面那句通用提示。 */
async function renderDiagnosis(box, res) {
  const d = await tryDiagnose(res.q || searchState.q, res.book, res.edition);
  // 竞态：等回来时用户可能已经搜了别的 —— 那一格已经被覆盖，别再写回去。
  if (!d || !d.summary || searchState.lastRes !== res) return;
  const mark = ok => (ok === true ? "✓" : (ok === false ? "✗" : "–"));
  // 标题由**归因给的 result_status** 决定，不在这里另判一次（§7~§9）。
  // 六点三时这里写死「没有找到相关史料」—— 那句话在 5 部残缺正史和 9 部未导入
  // 正史上都是错的：它把「我们没有」说成了「史书里没有」。§28 的门面就是这一段。
  const st = d.result_status;
  const head = st === "partial_no_hit" ? "所收版本不完整，未收录的部分无从判断"
    : st === "not_imported" ? "这部史书尚未收录"
    : st === "complete_no_hit" ? "所收版本内没有找到"
    : "没有找到相关史料";
  let html = `<div class="empty"><b>${esc(head)}</b><br>${esc(d.summary)}</div>`;
  html += scopeCard(d.scope);
  if ((d.layers || []).length) {
    html += `<div class="card"><b>逐层排查</b>
      <div class="quiet">从「语料在册」到「块组装」，每层给结论与证据：</div>
      <ul class="diag">${d.layers.map(l =>
        `<li><span class="mono">${mark(l.ok)} ${esc(l.title)}</span> ${esc(l.detail)}</li>`
      ).join("")}</ul></div>`;
  }
  if ((d.suggestions || []).length) {
    html += `<div class="card"><b>可以试试</b><ul class="diag">${d.suggestions
      .map(s => `<li>${esc(s)}</li>`).join("")}</ul></div>`;
  }
  box.innerHTML = html;
}

/* §25 情况四：查一部**在册未入库**的史书。不调 /api/search（理由见 doSearch），
 * 直接问 diagnose 并复用 renderDiagnosis —— 四态标题、收录范围卡、逐层排查都在
 * 那一处；两处各写一套，早晚会给出两个说法。
 *
 * 这里只塞一个最小 res：renderDiagnosis 只用 q/book/edition 三个字段，以及
 * lastRes 的同一性做竞态守卫（先记下这个 res，守卫才放行）。 */
function renderNotImported(box, rp) {
  const res = { q: rp.q, book: rp.book, edition: rp.edition };
  searchState.lastRes = res;
  renderDiagnosis(box, res);
}

/* 有结果时的残缺提示（第六点四阶段 §28）。
 *
 * **这是本阶段最容易漏的一半**：§25 的四种情况全部是「搜不到」时说什么，
 * 而一个搜到了的用户同样会误判 —— 搜《北齊書》得到 1 段「高歡」，他自然
 * 以为那就是北齊書里关于高歡的全部，而这本书只有 35/50 卷，缺的 15 卷
 * 根本不在检索范围里。**结果是对的，结论是错的。**
 *
 * 只在限定了单一史书时出现（服务端只在那种情况下给 scope），且只在残缺时画：
 * 每页都挂一条免责声明，说多了等于没说。完整史书不给。 */
function partialBanner(sc) {
  const b = sc && sc.book;
  if (!b || b.volume_status !== "partial") return "";
  const miss = b.expected_missing || [];
  return `<div class="card"><b>⚠ 所收版本不完整</b>
    <div class="quiet">《${esc(b.title)}》的这部底本只收到
      <b>${esc(b.range_text || `${b.declared}/${b.expected} 卷`)}</b>${
      miss.length ? `（缺 ${miss.length} 卷）` : ""}。
      <b>下面的结果全部来自已收录的卷</b>；未收录的部分没有参与检索，
      所以这不等于「${esc(b.title)}里只有这些」。
      <a class="backlink" href="#/coverage">收录范围 →</a></div></div>`;
}

/* 收录范围卡（第六点四阶段 §6/§25）。**一张卡只讲一件事：这次搜的范围里，
 * 我们手上到底有多少卷。** 数来自 search/diagnose.py 的 scope_status()，
 * 与 #/coverage 页、tests/recall.py 同源；页面不自己算，也不算第二遍。
 *
 * 为什么残缺书必须显示这一块：用户搜《北齊書》搜不到「齊桓公」，页面若只说
 * 「没有找到」，他得到的结论是「北齊書里没有齊桓公」。而事实是我们只有 35 卷，
 * 卷三十六以后根本没进来 —— 那 15 卷里有没有，我们不知道。§28：
 * 「宁可明确告诉用户我的史料不完整，也不能让用户误以为史书中没有」。 */
function scopeCard(sc) {
  if (!sc) return "";
  const b = sc.book;
  const rows = [];
  if (b && b.title) {
    let line = `<b>《${esc(b.title)}》</b>`;
    if (b.edition) line += ` <span class="quiet">· ${esc(b.edition)}</span>`;
    if (b.range_text) line += ` · 已收 <b>${esc(b.range_text)}</b>`;
    else if (b.upstream_text) line += ` · ${esc(b.upstream_text)}`;
    rows.push(`<li>${line}</li>`);
    if (b.volume_status === "partial") {
      const miss = (b.expected_missing || []);
      rows.push(`<li class="quiet">这是<b>上游数字化</b>的边界，不是检索出错：
        这部底本只到卷 ${b.declared}，通行本共 ${b.expected} 卷${
          miss.length ? `（未收 ${miss.length} 卷，如卷 ${miss.slice(0, 6).join("、")}${
            miss.length > 6 ? " 等" : ""}）` : ""}。</li>`);
    }
    if (b.caveat) rows.push(`<li class="quiet">${esc(b.caveat)}</li>`);
  } else if (sc.books_in_scope) {
    rows.push(`<li>检索范围：全库 <b>${sc.books_in_scope}</b> 部</li>`);
    const inc = sc.incomplete || [];
    if (inc.length) rows.push(`<li><b>${inc.length} 部底本残缺</b>：${
      inc.map(x => `《${esc(x.title)}》${esc(x.range_text || "卷数不详")}`).join("、")}
      <br><span class="quiet">这些书未收录的部分没有参与检索。</span></li>`);
    const ni = sc.not_imported || [];
    if (ni.length) rows.push(`<li class="quiet">另有 ${ni.length} 部正史尚未导入${
      ni.length <= 8 ? `：${ni.map(t => `《${esc(t)}》`).join("、")}` : ""}。</li>`);
  }
  if (!rows.length) return "";
  return `<div class="card"><b>本次检索的收录范围</b>
    <div class="quiet">来自语料目录与库内实测卷号（不是文件数）。</div>
    <ul class="diag">${rows.join("")}</ul></div>`;
}

/* ---------- 史料片段卡片 ---------- */
// layer 是第一阶段已判定的字段，这里只做**如实转述**，不重新判定任何归属。
// 带注候选的片段本来就短（原刊行内括注常只有十几个字），说清楚来由，
// 免得看起来像「检索结果不全」。
/* 这一块**是怎么**命中的（第六点一阶段）。「篇名命中」与「正文命中」不是一回事：
 * 搜「秦始皇本紀」时正文里一处都没有，命中的是篇名本身。不标出来，读者会看到
 * 一个「没有高亮、也没有命中计数」的片段，合理地读成「搜到了却什么都没有」。 */
const MATCH_TIP = {
  section: ["篇名命中",
    "命中的是篇名，正文里没有这个词；这里给的是该篇开头，可继续向下读"],
  both: ["正文 + 篇名命中", "这一篇既命中了篇名，正文里也出现了检索词"],
};
const LAYER_TIP = {
  commentary_candidate: ["原刊括注 · 待确认",
    "原书行内括号里的内容，是否为注文待人工确认；系统不自动归属任何注者，也不与正文混排"],
  preface: ["序跋", "第一阶段判定的序/跋部分，与正文分列"],
  appendix: ["附录", "第一阶段判定的附录部分"],
  backmatter: ["后附", "第一阶段判定的书末附载内容"],
  toc: ["目录", "第一阶段判定的目录部分"],
  main: ["正文", ""],
};
/* 繁简双轨渲染（第四阶段）。原文那一路永远照旧渲染，简体只是**附加**的一行：
 * 两者分别高亮（简体用接口给的 terms_simplified），不做任何位置对齐上的猜测。
 * 转换不可靠时接口会把 text_simplified 回退成原文并置 simplified_ok=false，
 * 这里就不再声称它是简体。 */
function dualTextHtml(b, tmode, terms, sterms) {
  const origHtml = mark(rich(b.text || ""), terms);
  if (tmode !== "simplified" && tmode !== "both")
    return `<div class="blk-text">${origHtml}</div>`;
  if (b.simplified_ok === false) {
    return `<div class="blk-text">${origHtml}</div>
      <div class="hit-notes">这段文字的系统转换结果不可靠（转换前后字数不一致），
        这里按原文显示，不提供简体。</div>`;
  }
  const simpHtml = mark(rich(b.text_simplified || ""), sterms || []);
  const note = `简体由程序按系统字符表转换（改动 ${num(b.simplified_chars)} 字），
    仅供阅读；原文以繁体为准。`;
  if (tmode === "simplified")
    return `<div class="blk-text simple">${simpHtml}</div>
      <div class="hit-notes">${note}</div>`;
  return `<div class="blk-text">${origHtml}</div>
    <div class="blk-text simple"><span class="dualtag">简</span>${simpHtml}</div>
    <div class="hit-notes">${note}</div>`;
}

function blockCard(b, terms, idx, tmode, sterms) {
  const loc = [b.juan, b.section, b.subsection,
    b.division && `${b.division}部`, b.ab && `经传${b.ab}`].filter(Boolean).join(" · ");
  const pageSpan = b.pb_first
    ? (b.pb_last && b.pb_last !== b.pb_first ? `第 ${b.pb_first}–${b.pb_last} 页` : `第 ${b.pb_first} 页`)
    : null;
  const ly = LAYER_TIP[b.layer] || (b.layer ? [b.layer, ""] : null);
  const mt = MATCH_TIP[b.match_type] || null;
  const btns = [];
  if (b.more_before) btns.push(`<button class="btn" onclick="expandBlock(${idx},'before',this)">← 继续向上文</button>`);
  if (b.more_after) btns.push(`<button class="btn" onclick="expandBlock(${idx},'after',this)">继续向下文 →</button>`);
  btns.push(`<a class="btn" href="#/p/${b.hit_passage_id}?ctx=1">定位到命中句 · 单条原文</a>`);
  // 读全文（§17）：搜索页只是「找到哪里」，读整篇要换一页——那一页没有表格、
  // 没有行号，只有连续的正文，落点就在这一段的起始行。
  // 说的是「全文」而不是「本篇全文」：那一页按**文件**连续读（先秦一文件即一卷），
  // 篇的边界在那里并没有标出来，写「本篇」会承诺一个页面上不存在的范围。
  if (b.file_id && b.row_first != null)
    btns.push(`<a class="btn" href="#/read/${b.file_id}?row=${b.row_first}"
      title="按原文件连续读全文，从这一段的开头进入">查看全文</a>`);
  return `<div class="card blk${b.layer && b.layer !== "main" ? " blk-side" : ""}" id="blk${idx}">
    <div class="hit-head">
      <span class="hit-book">《${esc(b.book_title || "暂无")}》</span>
      ${loc ? `<span class="hit-loc">${esc(loc)}</span>` : `<span class="chip structure">卷/篇暂无</span>`}
      ${pageSpan ? chip(pageSpan, "page") : ""}
      ${ly ? chip(ly[0], b.layer === "commentary_candidate" ? "cand" : "structure", ly[1]) : ""}
      ${mt ? chip(mt[0], "cand", mt[1]) : ""}
      ${b.match_count > 1
        ? chip(`本段命中 ${num(b.match_count)} 处`, "main", "该片段内命中检索词的记录条数")
        : chip("命中 1 处", "main")}
    </div>
    ${b.layer === "commentary_candidate"
      ? `<div class="hit-notes">（原刊括号内容 — 是否注文待人工确认，系统不作正文判定）</div>` : ""}
    ${dualTextHtml(b, tmode, terms, sterms)}
    <div class="hit-meta">
      <span>出处：${esc(b.juan || "暂无")}${b.section ? ` · ${esc(b.section)}` : ""}${b.subsection ? ` · ${esc(b.subsection)}` : ""}</span>
      <span>版本：${esc(fam(b.family))}</span>
      <span>原文件：${esc(b.file_name || "暂无")}</span>
      <span>片段：${num(b.n_passages)} 条记录 · ${num(b.n_chars)} 字</span>
    </div>
    <div class="hit-actions">${btns.join("")}</div>
  </div>`;
}

/* 展开更多上下文：继续从数据库读同一文件内的真实相邻记录，不生成任何文本。
 * 以片段首/末记录为锚点向外取，因此新读到的行与已显示部分不重叠。 */
window.expandBlock = async function (idx, dir, btn) {
  const b = searchState.results[idx];
  if (!b) return;
  const box = $("#blk" + idx);
  const textEl = box && box.querySelector(".blk-text");
  if (!textEl) return;
  const label = btn.textContent;
  btn.disabled = true; btn.textContent = "正在读取原文…";
  // 锚点方向与请求方向同名：向前展开要从片段首条往「前」问。
  const rp = { direction: dir, count: 40 };
  if (dir === "before") rp.before_passage_id = b.first_passage_id;
  else rp.after_passage_id = b.last_passage_id;
  try {
    const r = await api(`/api/blocks/${b.hit_passage_id}?${qs(rp)}`);
    if (!r.rows || !r.rows.length) { btn.textContent = "已读到本段开头"; btn.disabled = true; return; }
    const add = document.createElement("span");
    add.className = dir === "before" ? "blk-add lead" : "blk-add";
    add.innerHTML = mark(rich(r.rows.map(x => x.text_orig || "").join("")), searchState.terms);
    if (dir === "before") textEl.insertBefore(add, textEl.firstChild);
    else textEl.appendChild(add);
    // 更新锚点与状态，让按钮可反复点（读到尽头则换成提示）
    if (dir === "before") {
      b.first_passage_id = r.rows[0].passage_id;
      b.more_before = !r.reaches_head && r.added > 0;
    } else {
      b.last_passage_id = r.rows[r.rows.length - 1].passage_id;
      b.more_after = !r.reaches_tail && r.added > 0;
    }
    b.n_chars = num(b.n_chars) + r.rows.reduce((s, x) => s + (x.text_orig || "").length, 0);
    b.n_passages = num(b.n_passages) + r.rows.length;
    const meta = box.querySelector(".hit-meta");
    if (meta) meta.lastElementChild.textContent = `片段：${b.n_passages} 条记录 · ${b.n_chars} 字`;
    if (dir === "before" ? b.more_before : b.more_after) {
      btn.textContent = dir === "before" ? "← 继续向上文" : "继续向下文 →";
      btn.disabled = false;
    } else {
      btn.textContent = dir === "before" ? "已读到本段开头" : "已读到本段末尾";
      btn.disabled = true;
    }
  } catch (e) {
    btn.textContent = label; btn.disabled = false;
    alert("读取相邻原文失败：" + e.message);
  }
};

/* ================= 史料详情页（阅读式） ================= */
async function passageView(id, params) {
  const view = $("#view");
  view.classList.add("reading");
  let d;
  try { d = await api(`/api/passages/${id}`); }
  catch (e) { view.classList.remove("reading");
    view.innerHTML = `<div class="card"><b>记录不存在</b><br>${esc(e.message)}<br>
      <a class="backlink" href="#/search">← 返回检索</a></div>`; return; }
  let ctx = null;
  try { ctx = await api(`/api/passages/${id}/context`); }
  catch (e) { /* 上下文端点失败时详情与数据面板仍可用 */ }
  if (!ctx) {
    view.innerHTML = `<div class="card"><b>上下文不可用</b>（该记录可能刚被重建移除，或端点异常）<br>
      <a class="backlink" href="#/search">← 返回检索</a></div>`;
    view.classList.remove("reading");
    return;
  }
  const it = ctx.current;
  const loc = [it.juan, it.section, it.subsection,
    it.division && `${it.division}部`, it.ab && `经传${it.ab}`].filter(Boolean).join(" · ");
  const sr = srcRefOf(it.source_ref);
  const m = { ...d, book_title: it.book_title, file_name: it.file_name,
              sha256: it.file_sha256, special_chars: it.special_chars,
              source_ref: it.source_ref };
  view.innerHTML = `<a class="backlink" href="javascript:history.back()">← 返回</a>
    <div class="card" style="margin-top:8px">
      <div class="passage-top">
        <span class="hit-book">《${esc(it.book_title)}》</span>
        ${loc ? `<span class="hit-loc">${esc(loc)}</span>` : `<span class="chip structure">卷/篇暂无</span>`}
        ${it.page ? chip(`第 ${esc(it.page)} 页`, "page") : ""}
        ${it.commentary_candidate ? chip("原刊括号内容 · 待确认", "cand") : ""}
        <span class="quiet">原文行 ${it.row_no} · ${esc(fam(it.family))}</span>
      </div>
      <div class="readingtext">${rich(it.text_orig, { pb: false })}</div>
      <div class="hit-meta">
        <span>出处：《${esc(it.book_title)}》${it.juan ? ` · ${esc(it.juan)}` : ""}${it.section ? ` · ${esc(it.section)}` : ""}${it.subsection ? ` · ${esc(it.subsection)}` : ""}</span>
        <span>版本：${esc(fam(it.family))}</span>
        <span>原文件：${esc(it.file_name || "暂无")}</span>
        ${sr ? `<span title="${esc(sr.raw || "")}">出处注：${esc((sr.raw || "").slice(0, 60))}</span>` : ""}
        <span class="quiet mono">${esc(it.book_id)} · 行 ${it.row_no}</span>
      </div>
    </div>
    <div id="ctxzone"></div>
    <details class="card"><summary class="sum">数据详情（默认折叠）</summary>${dataTable(m)}</details>`;
  renderCtx(ctx);
  if (params.ctx != null) setTimeout(() => {
    const z = $("#ctxzone"); if (z) z.scrollIntoView({ behavior: "smooth", block: "start" });
  }, 60);
  view.classList.remove("reading");
}
function renderCtx(ctx) {
  const z = $("#ctxzone");
  if (!z) return;
  const item = (x, cur) => `<div class="ctx-item ${cur ? "ctx-cur" : ""}"
      onclick="location.hash='#/p/${x.passage_id}${cur ? '?ctx=1' : ''}'">${rich(x.text_orig, { pb: false })}</div>`;
  const para = xs => xs.length ? `<div class="ctxlist">${xs.map(x => item(x)).join("")}</div>`
    : `<p class="quiet">（该文件内无相邻记录）</p>`;
  z.innerHTML = `<div class="ctxcap">— 上文 · 同文件前 ${ctx.before.length} 段 —</div>
    ${para(ctx.before)}
    ${item(ctx.current, true)}
    <div class="ctxcap">— 下文 · 同文件后 ${ctx.after.length} 段 —</div>
    ${para(ctx.after)}
    <p class="quiet">上下文取自同文件真实相邻原文记录（同一原始文件，按行序），非任何模型补写；点击任意段跳转。</p>`;
}

/* ================= 数据检查（保留第一阶段检查台） ================= */
async function booksView() {
  const view = $("#view");
  const books = await getBooks();
  let html = `<a class="backlink" href="#/">← 首页</a>
    <h3>数据检查 · ${books.length} 部史书</h3>
    <p class="quiet">原始文件解析入库的忠实度检查；普通阅读请用顶部「全文检索」。</p>
    <div class="overflow"><table class="dev"><tr>
      <th>书名</th><th>书号</th><th>版本</th><th>文件</th><th>正文</th>
      <th>待确认</th><th>出处块</th><th>缺字行</th><th></th></tr>`;
  for (const b of books) {
    html += `<tr><td><b>《${esc(b.title)}》</b></td><td class="mono">${esc(b.book_id)}</td>
      <td>${chip(fam(b.family), b.family === "sbck" ? "structure" : "")}</td>
      <td>${num(b.files).toLocaleString()}</td><td>${num(b.main_passages).toLocaleString()}</td>
      <td>${b.pending ? chip(num(b.pending).toLocaleString(), "cand") : "—"}</td>
      <td>${num(b.src_blocks).toLocaleString()}</td><td>${num(b.kr_rows).toLocaleString()}</td>
      <td><a class="backlink" href="#/book/${encodeURIComponent(b.book_id)}">文件列表 →</a></td></tr>`;
  }
  html += `</table></div>
    <p class="quiet">「待确认」= 状态待定的原刊括号注候选（國語/戰國策），见<a class="backlink" href="#/pending">待确认注释</a>。</p>`;
  view.innerHTML = html;
}

/* -------- 收录进度（#/coverage 顶部，6.3-I）--------
 * 「这一页只讲已经入库的书有没有归篇」不够 —— 用户真正会问的是「《宋書》为什么
 * 搜不到」。所以顶部先回答覆盖：按时代分组，每组「已收录 / 在册总数」。
 *
 * 三个状态是**算出来的，不是标的**：该时代在册的书写全在库里＝已收录；一部都没
 * 入库＝未开始；两头都不占＝部分。数据来自 /api/catalog（即 manifest 的五态快照，
 * 判据只有一份），cat 为 null 时整块不画。 */
const STATUS_CN = { verified: "已核验", partial: "残缺", imported: "已入库",
                    warning: "有告警", failed: "失败", planned: "未开始" };
function eraProgress(cat) {
  if (!cat) return "";
  const groups = cat.by_era_group || {};
  const names = Object.keys(groups);
  if (!names.length) return "";
  const planned = cat.planned || [];
  const rows = names.map(name => {
    const g = groups[name] || {};
    const inLib = num(g.in_library), plan = num(g.planned);
    const total = inLib + plan;
    const pct = total ? inLib / total * 100 : 0;
    const [txt, cls] = plan === 0 ? ["已收录", "main"]
      : (inLib === 0 ? ["未开始", ""] : ["部分", "warn"]);
    const pend = planned.filter(p => (p.era_group || "未分组") === name);
    const pendHtml = pend.map(p =>
      `《${esc(p.title)}》${p.on_disk ? "（已下载）" : ""}`).join("");
    const tip = pend.length ? `尚未收录：${pend.map(p => p.title).join("、")}`
      : "该书系的在册书目都在库里";
    return `<div class="era-row">
      <span class="era-name">${esc(name)}</span>
      <span class="era-bar" title="${esc(tip)}"><i style="width:${pct.toFixed(1)}%"></i></span>
      <span class="era-num mono">${inLib} / ${total} 部</span>
      ${chip(txt, cls)}
      <span class="era-pend quiet">${pendHtml}</span></div>`;
  }).join("");
  const counts = cat.counts || {};
  // partial 是 6.4 加的第六态：底本卷不全（上游只数字化到某一卷）。
  // 它必须出现在这里 —— 少了它，5 部残缺正史会从这一行里凭空消失。
  const five = ["verified", "partial", "imported", "warning", "failed"]
    .filter(k => num(counts[k]))
    .map(k => `${STATUS_CN[k] || k} ${num(counts[k])}`).join("、");
  const stamp = String(cat.generated_at || "").slice(0, 16).replace("T", " ");
  let warn = "";
  if ((cat.not_in_catalog || []).length)
    warn += `<p class="quiet">库里有、语料目录未登记：${cat.not_in_catalog.map(t => `《${esc(t)}》`).join("")}
      —— 补进 corpus_catalog.json，否则它的状态永远停在「已入库」。</p>`;
  if ((cat.uncatalogued_dirs || []).length)
    warn += `<p class="quiet">磁盘上有未登记的书目录：<span class="mono">${cat.uncatalogued_dirs.map(esc).join("、")}</span></p>`;
  return `<div class="card">
    <b>收录进度（按时代）</b>
    <span class="quiet">${five ? `库内 ${cat.in_library} 部：${five}；` : ""}在册
      ${cat.in_library + planned.length} 部（语料目录 v${cat.catalog_version}）。
      快照 ${esc(stamp)} UTC —— 这是审计脚本跑出来的结果，不是实时查询。</span>
    ${rows}
    <p class="quiet">「部分」＝该时代还有在册未入库的书；进度条填的是已入库的比例。
      未入库的书任何检索都搜不到 —— 搜不到某个词时，先看它属不属于哪个「未开始」的时代。</p>
    ${warn}</div>`;
}

/* -------- 篇名覆盖（#/coverage）：Section Coverage Audit 的页面版 --------
 * 数据全部来自 /api/books（静态演示模式下是 data-demo/books.json）——**不新增
 * 任何发布文件、不动 check_publish 白名单**。字段与 scripts/pipeline/manifest.py
 * 的审计表同源同判据，两处给同一个答案（改一处必须改另一处）。
 *
 * 覆盖率的算法是 section 的**区间模型**：一条 section 覆盖它所在文件内自 first_row
 * 起至该文件下一条 section（最后一条到文件末）。所以口径是「正文行有没有落在本
 * 文件第一条 section 之后」，与 passages.section 字段无关。缺口有三类成因
 * （结构行 / 源转录缺卷首题 / 篇题识别失败），页面上直说，不粉饰成一个百分比。
 */
async function coverageView() {
  const view = $("#view");
  const books = await getBooks();
  const cat = await tryCatalog();
  // book_id → 卷级行。卷数只在语料快照里（/api/catalog），书单（/api/books）
  // 没有这个字段；这里按 book_id 关联，不另取一次数、也不在前端算覆盖率。
  const volMap = {};
  ((cat && cat.books) || []).forEach(b => { volMap[b.book_id] = b.volume || null; });
  const body = books.reduce((s, b) => s + num(b.body_rows), 0);
  const covered = books.reduce((s, b) => s + num(b.covered_rows), 0);
  const secs = books.reduce((s, b) => s + num(b.sections), 0);
  const worst = books.reduce((a, b) =>
    (a === null || num(b.section_coverage) < num(a.section_coverage)) ? b : a, null);
  let html = `<a class="backlink" href="#/books">← 数据检查</a>
    <h3>篇名覆盖审计 · ${books.length} 部史书</h3>
    ${eraProgress(cat)}
    ${volumeTotalsCard(cat)}
    <p class="quiet">这一页回答两个不同的问题，左右两栏分开答：
      <b>「每卷正文有没有一个篇名可归」</b>（篇名覆盖，我们的解析质量，能修）
      与<b>「这部书我们到底收了多少卷」</b>（卷级覆盖，上游给了多少，修不了）。
      归了篇，篇名检索与「定位到命中句」才知道自己在哪一篇里；没归篇的正文照样能
      全文检索，只是没有篇名。</p>
    <div class="overflow"><table class="dev"><tr>
      <th>书名</th><th>版本</th><th>篇名</th><th>卷</th><th>正文行</th><th>已归篇</th>
      <th>篇名覆盖</th><th>状态</th>
      <th>已收/应有卷</th><th>卷覆盖率</th><th>卷状态</th><th></th></tr>`;
  for (const b of books) {
    const pct = (num(b.section_coverage) * 100).toFixed(1) + "%";
    html += `<tr><td><b>《${esc(b.title)}》</b></td>
      <td>${chip(fam(b.family), b.family === "sbck" ? "structure" : "")}</td>
      <td>${num(b.sections).toLocaleString()}</td>
      <td>${num(b.juans) ? num(b.juans).toLocaleString() : "—"}</td>
      <td>${num(b.body_rows).toLocaleString()}</td>
      <td>${num(b.covered_rows).toLocaleString()}</td>
      <td class="mono">${pct}</td>
      <td>${covChip(b.coverage_status)}</td>
      ${volCells(volMap[b.book_id])}
      <td><a class="backlink" href="#/book/${encodeURIComponent(b.book_id)}">文件列表 →</a></td></tr>`;
  }
  html += `</table></div>
    <p class="quiet">合计 ${secs.toLocaleString()} 条篇名区间，覆盖
      ${covered.toLocaleString()} / ${body.toLocaleString()} 行正文
      （${(body ? covered / body * 100 : 100).toFixed(1)}%）。</p>
    <div class="card"><b>覆盖不到的行是什么</b>
      <ul class="quiet">
        <li><b>结构行</b>——卷首的撰者/注者署名、總目、四庫叢書题。每文件一两行，
          本就不属于任何一篇。</li>
        <li><b>源转录缺卷首题</b>——个别文件的首行直接是正文，卷名只在卷末版心题里
          出现，整卷无所归。这是源文件如此，不硬凑。</li>
        <li><b>篇题识别失败</b>——这才是要修的 bug，也是本页 <span class="chip fail">FAIL</span>
          判据（整本书一条篇名都没认出来）要抓的。</li>
      </ul></div>`;
  if (worst && num(worst.section_coverage) < 0.99) {
    html += `<p class="quiet">当前覆盖最低：<b>《${esc(worst.title)}》</b>
      ${(num(worst.section_coverage) * 100).toFixed(1)}%
      （${num(worst.uncovered_rows).toLocaleString()} 行未归篇）。</p>`;
  }
  view.innerHTML = html;
}
/* 卷级覆盖（第六点四阶段 §6）。**只加不改**：这一页原有的篇名覆盖口径一字未动，
 * 卷级信息是新增的三列 + 一块总账，因为它们回答的是另一个问题。 */
function volCells(v) {
  // undefined（这一格没有数据）与 known:false（有数据，但这部书没有卷级模型）
  // 是两回事：前者是**我们没拿到**（静态演示模式、或快照缺这本书），后者是
  // 「这部底本本来就不按卷分」。混成一格会让演示站上每本书都显示「无卷级模型」，
  // 那是句假话。发布产物里没有真实卷数（§22），所以这条路径必然走到。
  if (v === undefined || v === null) {
    return `<td class="quiet">—</td><td class="quiet">—</td>
      <td class="quiet" title="本地 API 模式才有卷级数据（真实卷数不进发布产物）">未提供</td>`;
  }
  if (!v.known) return `<td class="quiet">—</td><td class="quiet">—</td>
    <td>${chip("无卷级模型", "", "该底本以篇为单位，文件头 JUAN 是文件序号不是卷次")}</td>`;
  const av = v.available, ex = v.expected;
  const have = (av == null) ? "—" : num(av).toLocaleString();
  const want = ex ? num(ex).toLocaleString() : "—";
  const pct = (v.coverage_ratio == null) ? "—"
    : (num(v.coverage_ratio) * 100).toFixed(1) + "%";
  return `<td class="mono">${have} / ${want}</td>
    <td class="mono">${pct}</td>
    <td>${volChip(v)}</td>`;
}
function volChip(v) {
  const s = v.status;
  if (s === "partial") {
    const miss = num(v.expected) - num(v.available);
    return chip("残缺", "warn",
      `上游数字化只到卷 ${v.declared}，通行本共 ${v.expected} 卷（缺 ${miss} 卷）`
      + ` —— 这是上游的边界，不是我们漏了，也不是检索出错`);
  }
  if (s === "complete") {
    const gap = num(v.expected) - num(v.available);
    // 「完整」不等于「每一卷都被机器数到」。魏書显示 113/114 却是完整的：
    // 卷一百五在源文件里被拆成「之一…之四」，文件头认不出卷题，那 1 卷是人
    // 逐卷豁免的（catalog 的 volume_notes）。差额不解释就等于说我们丢了一卷。
    const why = gap > 0
      ? `底本卷数与通行本相符；卷号差额 ${gap} 卷已在语料目录里逐卷说明（源文件如此）`
      : v.witness === "declared"
        ? "底本卷数与通行本相符 —— 该底本无卷级证据，此卷数是人工登记的"
        : "底本卷数与通行本相符，机器逐卷核对过";
    return chip("完整", "main", why);
  }
  if (s === "unknown") return chip("无卷级模型", "");
  return chip(s || "—", "");
}

/* 卷级总账（§4/§13）。数字来自 manifest 的 volume_totals，页面不重算。
 * 「文件数量 ≠ 卷数量」这句话必须跟着数字走 —— 否则 1013/1275 会被读成
 * 「我们的语料是残的」或者更糟「史书里没有」。 */
function volumeTotalsCard(cat) {
  if (!cat || !cat.available) return "";
  if (cat.volume_available === false) {
    return `<div class="card"><b>卷级覆盖</b>
      <div class="quiet">卷级快照不可用：${esc(cat.volume_reason || "未知原因")}
        —— 卷数一栏显示「—」，不代表这些书没有卷，只代表现在读不到卷级信息。</div></div>`;
  }
  const t = cat.volume_totals || {}, lib = t.in_library || {}, pl = t.planned || {};
  if (!lib.books) return "";
  const pct = lib.ratio == null ? "—" : (num(lib.ratio) * 100).toFixed(1) + "%";
  const bs = lib.by_status || {};
  const stamp = String(cat.volume_generated_at || cat.generated_at || "")
    .slice(0, 16).replace("T", " ");
  const up = lib.upstream_partial || [];
  return `<div class="card"><b>卷级覆盖（库内 ${lib.books} 部）</b>
    <p>已收 <b>${num(lib.available).toLocaleString()}</b> /
      应有 <b>${num(lib.expected).toLocaleString()}</b> 卷
      <span class="mono">（${pct}）</span>
      <span class="quiet">· 完整 ${num(bs.complete)} 部、残缺 ${num(bs.partial)} 部、
        无卷级模型 ${num(bs.unknown)} 部 · 快照 ${esc(stamp)} UTC</span></p>
    ${up.length ? `<p class="quiet"><b>${up.length} 部底本残缺</b>：
      ${up.map(t => `《${esc(t)}》`).join("")}
      —— 上游数字化本身只到某一卷为止（Kanripo 就是这样）。搜索这些书时，
      未收录的卷不在检索范围里，页面会说「所收版本不完整」而不是「书里没有」。</p>` : ""}
    ${num(pl.books) ? `<p class="quiet">另有 ${num(pl.books)} 部正史尚未导入
      （登记 ${num(pl.expected).toLocaleString()} 卷）
      ${(pl.upstream_partial || []).length ? `，其中
        ${pl.upstream_partial.map(t => `《${esc(t)}》`).join("")} 上游本身也不全` : ""}。</p>` : ""}
    <p class="quiet">口径：应有卷数 = <b>通行本</b>卷数（外证，稳定）；已收卷数 =
      库内<b>实测卷号数</b>。是卷号口径，<b>不是文件数</b> —— Kanripo 的
      <span class="mono">_000.txt</span> 是目録、考證文件不是卷，北齊書 36 个文件
      只是 35 卷，後漢書 125 个文件只是 120 卷。${esc(cat.volume_note || "")}</p>
  </div>`;
}

function covChip(status) {
  const s = status || "OK";
  if (s === "FAIL") return chip("FAIL", "fail", "整本书没有一条篇名——篇名检索不可用");
  if (s === "WARN") return chip("WARN", "warn", "缺口超过 10%，需要人看一眼");
  return chip("OK", "main");
}

async function bookView(bookId) {
  const view = $("#view");
  const [files, books] = await Promise.all(
    [api(`/api/books/${encodeURIComponent(bookId)}/files`), getBooks()]);
  if (!files.length) { view.innerHTML = `<div class="card">该书无文件</div>`; return; }
  const b0 = books.find(x => x.book_id === bookId) || {};
  let html = `<a class="backlink" href="#/books">← 数据检查</a>
    <h3>《${esc(b0.title || bookId)}》 · ${files.length} 个原始文件
      <span class="quiet">${esc(bookId)} · ${esc(fam(b0.family || ""))}</span></h3>
    <div class="overflow"><table class="dev"><tr>
      <th>原始文件</th><th>页范围</th><th>正文</th><th>括号注</th><th>序/跋/目</th>
      <th>待确认</th><th>记录</th><th>sha256</th><th>检查</th></tr>`;
  for (const f of files) {
    html += `<tr><td class="mono">${esc(f.file_name)}</td>
      <td class="mono">${esc(f.pb_range || "—")}</td><td>${num(f.main).toLocaleString()}</td>
      <td>${f.commentary ? chip(num(f.commentary).toLocaleString(), "cand") : "—"}</td>
      <td>${(f.preface || f.back) ? `序${f.preface}/跋${f.back}` : "—"}</td>
      <td>${f.pending ? chip(num(f.pending).toLocaleString(), "cand") : "—"}</td>
      <td>${num(f.records).toLocaleString()}</td><td class="mono">${esc((f.sha256 || "").slice(0, 10))}…</td>
      <td><a class="backlink" href="#/file/${f.file_id}">检查</a> ·
          <a class="backlink" href="#/file/${f.file_id}?tab=raw">原文对照</a></td></tr>`;
  }
  html += `</table></div>`;
  view.innerHTML = html;
}

/* -------- 文件页（解析记录 | 原文对照 两页签） -------- */
const fv = { id: 0, page: 1, pageSize: 50, kind: "", layer: "", status: "", q: "", tab: "recs",
             rawStart: 1 };
async function fileView(fileId, params) {
  fv.id = fileId; fv.page = 1;
  fv.kind = params.kind ?? ""; fv.layer = params.layer ?? "";
  fv.status = params.status ?? ""; fv.q = ""; fv.rawStart = 1;
  fv.tab = params.tab === "raw" ? "raw" : "recs";
  let f;
  try { f = await api(`/api/files/${fileId}`); }
  catch (e) { $("#view").innerHTML = `<div class="card">文件加载失败：${esc(e.message)}</div>`; return; }
  const meta = f.meta || {};
  const kls = (f.kind_layer_counts || []).slice(0, 16);
  const spec = fv.status ? chip(`仅看待确认：${fv.status}`, "cand") : "";
  $("#view").innerHTML = `<a class="backlink" href="#/book/${encodeURIComponent(f.book_id)}">← 返回文件列表</a>
    <div class="card" style="margin-top:6px">
      <div class="passage-top"><b class="mono" style="font-size:15px">${esc(f.file_name)}</b>
        <span class="quiet">${esc(f.book_id)}</span>${spec}
        <span class="quiet mono">sha ${esc((f.sha256 || "").slice(0, 12))}…</span>
      </div>
      <div class="quiet" style="margin-top:4px">${Object.entries(meta).slice(0, 12)
        .map(([k, v]) => chip(`${k} ${v}`, "structure")).join(" ")}
        ${f.pb_range ? chip(`页 ${esc(f.pb_range)}`, "page") : ""}</div>
      <div class="quiet" style="margin-top:6px">${kls.map(k =>
        chip(`${k.kind}/${k.layer}/${k.status} ${num(k.n).toLocaleString()}`, "main")).join(" ")}</div>
    </div>
    <div class="controls">
      <button class="btn ${fv.tab === "recs" ? "primary" : ""}" onclick="switchFTab('recs')">解析记录</button>
      <button class="btn ${fv.tab === "raw" ? "primary" : ""}" onclick="switchFTab('raw')">原文对照</button>
      <span class="quiet" style="font-size:13px">原件逐行 vs 解析层；行号可回溯 library 原件。</span>
    </div>
    <div id="fbody"><div class="load">正在加载…</div></div>`;
  if (fv.tab === "recs") loadRecsPage();
  else loadRawPage();
}
function switchFTab(tab) {
  if (tab === fv.tab) return;
  fv.tab = tab;
  history.replaceState(null, "", `#/file/${fv.id}?tab=${tab}`);
  const body = $("#fbody");
  body.innerHTML = `<div class="load">正在加载…</div>`;
  const v = $("#view");
  const btns = v.querySelectorAll(".controls > .btn");
  btns.forEach(b => b.classList.toggle("primary", b.textContent === (tab === "recs" ? "解析记录" : "原文对照")));
  if (tab === "recs") loadRecsPage(); else loadRawPage();
}
function fCtlRow(total) {
  const opts = (vals, cur) => `<option value="">全部</option>` + vals.map(v =>
    `<option value="${v}" ${v === cur ? "selected" : ""}>${v}</option>`).join("");
  return `<div class="controls">
    <label>类型</label><select id="f-kind">${opts(["page", "heading", "comment", "part", "noise", "passage"], fv.kind)}</select>
    <label>层级</label><select id="f-layer">${opts(["main", "preface", "appendix", "backmatter", "toc", "structure", "commentary_candidate", "unknown"], fv.layer)}</select>
    <label>状态</label><select id="f-status">${opts(["ok", "pending_commentary", "pending_section", "pending_line"], fv.status)}</select>
    <input id="f-q" placeholder="行内文本（原件）" value="${esc(fv.q)}" style="width:170px">
    <button class="btn primary" onclick="applyF()">查询</button></div>`;
}
async function loadRecsPage() {
  const fb = $("#fbody");
  if (!fb) return;
  const rp = qs({ kind: fv.kind, layer: fv.layer, status: fv.status, q: fv.q || undefined,
                  offset: (fv.page - 1) * fv.pageSize, limit: fv.pageSize });
  let d;
  try { d = await api(`/api/files/${fv.id}/passages?${rp}`); }
  catch (e) { fb.innerHTML = `<div class="card">加载失败：${esc(e.message)}</div>`; return; }
  fb.innerHTML = pagerBlock(d.total, fv.page, fv.pageSize, "fGo") + fCtlRow(d.total);
  const tb = document.createElement("div");
  tb.className = "overflow";
  let html = `<table class="dev"><tr><th>行号</th><th>类型/层</th><th>状态</th>
    <th>上下文</th><th>文本（text_orig 原样）</th><th>数据</th><th>阅读</th></tr>`;
  for (const r of d.rows) {
    const m = rowNorm(r);
    const cand = m.layer === "commentary_candidate";
    const ctx = [m.juan && `卷${m.juan}`, m.section && `篇${m.section}`,
                 m.subsection && `节${m.subsection}`, m.division,
                 m.ab && `经传${m.ab}`].filter(Boolean).join(" · ");
    html += `<tr><td class="row-no">${m.row_no}</td>
      <td>${chip(m.kind, m.kind === "passage" ? "main" : "structure")}
          ${chip(m.layer, cand ? "cand" : "main")}</td>
      <td>${m.status === "ok" ? chip("正常", "main") : chip(m.status, "pending")}</td>
      <td class="quiet">${esc(ctx || "—")}</td>
      <td><div class="celltext">${rich(m.text_orig, { pb: true, cand })}</div></td>
      <td><details class="dev"><summary>数据</summary>${dataTable(m)}</details></td>
      <td><a class="backlink" href="#/p/${m.passage_id}?ctx=1">上下文 →</a></td></tr>`;
  }
  html += `</table>`;
  tb.innerHTML = html;
  fb.appendChild(tb);
  for (const id of ["f-kind", "f-layer", "f-status"]) {
    const el = $("#" + id); if (el) el.onchange = applyF;
  }
  const q = $("#f-q"); if (q) q.addEventListener("keydown", e => { if (e.key === "Enter") applyF(); });
}
function applyF() {
  fv.kind = $("#f-kind").value; fv.layer = $("#f-layer").value;
  fv.status = $("#f-status").value; fv.q = $("#f-q").value.trim();
  fv.page = 1; loadRecsPage();
}
function fGo(p) { fv.page = Math.max(1, p); loadRecsPage(); }

async function loadRawPage() {
  const fb = $("#fbody");
  if (!fb) return;
  let d;
  try { d = await api(`/api/files/${fv.id}/raw?start=${fv.rawStart}&end=${fv.rawStart + 199}`); }
  catch (e) { fb.innerHTML = `<div class="card">加载失败：${esc(e.message)}</div>`; return; }
  fv.rawStart = d.start;
  let html = `<div class="controls">
      <button class="btn" onclick="rawGo(${fv.rawStart - 200})" ${fv.rawStart <= 1 ? "disabled" : ""}>← 前 200 行</button>
      <label>从行</label><input id="rawgo" type="number" value="${fv.rawStart}" min="1" max="${d.total_lines}" style="width:90px">
      <button class="btn" onclick="rawJump()">跳转</button>
      <button class="btn" onclick="rawGo(${fv.rawStart + 200})" ${fv.rawStart + 199 >= d.total_lines ? "disabled" : ""}>后 200 行 →</button>
      <span class="quiet">共 ${num(d.total_lines).toLocaleString()} 行 · 原件（library 只读）</span></div>
    <p class="quiet">文件头与空行不入库（管线设计使然）；紫色标注 = 该行拆出括号注候选。</p>
    <div class="overflow"><table class="dev"><tr><th>行</th><th>标注</th><th>原件</th></tr>`;
  for (const l of d.lines) {
    const anns = l.annotation.map(a => chip(a, a.includes("pending") || a.includes("commentary_candidate")
      ? "cand" : a.includes("文件头") ? "structure" : "main")).join(" ");
    html += `<tr><td class="row-no">${l.no}</td><td>${anns}</td>
      <td><div class="celltext" style="max-width:640px">${rich(l.text, { pb: false })}</div></td></tr>`;
  }
  html += `</table></div>`;
  fb.innerHTML = html;
}
function rawJump() { rawGo(Math.max(1, parseInt($("#rawgo").value, 10) || 1)); }
function rawGo(n) { fv.rawStart = Math.max(1, n); loadRawPage(); }

/* ================= 阅读全文（#/read/{file_id}?row=N） ================= */
/* 任务书 §17：搜索页负责「找到哪里」，这一页负责「读全部内容」。
 *
 * 与「检查台」（#/file/）的分工：检查台是逐条核对的**表格**（类型/层/状态/行号
 * 一列不少），这一页是**读**的——连续正文，一页 500 条，没有列头、没有行号。
 * 两者看着像，用途相反，所以是两条路由，不是同一个页面的两个标签。
 *
 * 只渲染 kind='passage' 的记录，与结果片段的取舍一致：标题行、`# src:` 解析
 * 元数据行不是史料正文，混进阅读流里会读成正文的一部分。跳过的条数如实写在页顶。
 */
const rv = { id: 0, offset: 0, limit: 500, total: 0, anchorRow: null, info: null };

/** 第一条 row_no >= target 的记录在文件里的**下标**。
 *
 *  为什么不直接按行号算下标：page/heading/comment 层记录也占位置，第 N 行的
 *  下标不是 N（史記文件 82 有 17,410 行、22,000+ 条记录）。而接口只认 offset，
 *  所以按 (row_no, seq) 升序二分找 —— 记录本来就是按这个序返回的。
 *  代价是约 log2(条数) ≈ 15 次 limit=1 的请求，比「从头翻到目标页」少一个量级。 */
async function offsetOfRow(fileId, target) {
  let lo = 0, hi = Math.max(0, rv.total - 1);
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    const d = await api(`/api/files/${fileId}/passages?offset=${mid}&limit=1`);
    rv.total = num(d.total);
    if (!d.rows || !d.rows.length) break;
    if (num(d.rows[0].row_no) < target) lo = mid + 1; else hi = mid;
  }
  return lo;
}

async function readView(fileId, params) {
  const view = $("#view");
  let f;
  try { f = await api(`/api/files/${fileId}`); }
  catch (e) { view.innerHTML = `<div class="card">文件加载失败：${esc(e.message)}</div>`; return; }
  rv.id = fileId; rv.info = f; rv.anchorRow = params.row ? num(params.row) : null;
  const d0 = await api(`/api/files/${fileId}/passages?limit=1&offset=0`);
  rv.total = num(d0.total);
  rv.offset = rv.anchorRow ? await offsetOfRow(fileId, rv.anchorRow) : 0;
  rv.offset = Math.max(0, Math.min(rv.offset, Math.max(0, rv.total - 1)));
  view.innerHTML = `<a class="backlink" href="#/file/${fileId}">← 解析记录 / 原文对照（检查台）</a>
    <div class="card">
      <div class="passage-top"><b style="font-size:15px">${esc(f.file_name)}</b>
        <span class="quiet">${esc(f.book_id)}</span></div>
      <div class="quiet" style="margin-top:4px">连续正文，一页 ${rv.limit} 条记录
        （只显示 <span class="mono">kind=passage</span> 的史料正文）。
        ${searchState.text !== "orig"
          ? "本页按原文（繁体）显示，不带繁简双轨。" : ""}</div>
    </div>
    <div id="rbody"><div class="load">正在加载…</div></div>`;
  loadReadPage();
}

async function loadReadPage() {
  const rb = $("#rbody");
  if (!rb) return;
  let d;
  try { d = await api(`/api/files/${rv.id}/passages?offset=${rv.offset}&limit=${rv.limit}`); }
  catch (e) { rb.innerHTML = `<div class="card">加载失败：${esc(e.message)}</div>`; return; }
  rv.total = num(d.total);
  const rows = d.rows || [];
  const body = rows.filter(r => r.kind === "passage");
  const skipped = rows.length - body.length;
  const shownFrom = rv.offset + 1, shownTo = rv.offset + rows.length;
  const atHead = rv.offset <= 0, atTail = shownTo >= rv.total;
  let html = `<div class="controls">
      <button class="btn" onclick="readGo(${rv.offset - rv.limit})" ${atHead ? "disabled" : ""}>← 上一页</button>
      <label>跳到行</label><input id="rgo" type="number" min="1" style="width:100px">
      <button class="btn" onclick="readJump()">跳转</button>
      <button class="btn" onclick="readGo(${rv.offset + rv.limit})" ${atTail ? "disabled" : ""}>下一页 →</button>
      <span class="quiet">第 ${shownFrom.toLocaleString()}–${shownTo.toLocaleString()} 条 /
        共 ${rv.total.toLocaleString()} 条</span></div>
    ${skipped ? `<p class="quiet">本页另有 ${skipped} 条非正文记录（标题/注释/解析元数据），
      不在阅读流里显示；要看它们请用「检查台」。</p>` : ""}
    <div class="read-body">`;
  let hitAnchor = false;
  for (const r of body) {
    const isAnchor = rv.anchorRow !== null && !hitAnchor && num(r.row_no) >= rv.anchorRow;
    if (isAnchor) hitAnchor = true;
    html += `<span class="read-rec${isAnchor ? " read-anchor" : ""}"
      ${isAnchor ? 'id="readAnchor"' : ""}>${rich(r.text_orig || "")}</span>`;
  }
  if (!body.length) html += `<p class="quiet">本页没有正文记录。</p>`;
  html += `</div>`;
  if (rv.anchorRow !== null) {
    html += `<p class="quiet">${hitAnchor
      ? `已定位到第 ${rv.anchorRow} 行（黄底那一条）。`
      : `第 ${rv.anchorRow} 行不在本页范围内。`}</p>`;
  }
  rb.innerHTML = html;
  const a = $("#readAnchor");
  if (a && a.scrollIntoView) a.scrollIntoView({ block: "center" });
}

function readGo(off) {
  rv.offset = Math.max(0, Math.min(off, Math.max(0, rv.total - 1)));
  rv.anchorRow = null;              // 手动翻页后不再声称「已定位」
  loadReadPage();
}
async function readJump() {
  const n = parseInt($("#rgo").value, 10);
  if (!n || n < 1) return;
  rv.anchorRow = n;
  rv.offset = await offsetOfRow(rv.id, n);
  loadReadPage();
}

/* ================= 待确认注释（原刊括号） ================= */
const pv = { bookId: "", fileId: 0, page: 1, pageSize: 50 };
async function pendingView() {
  const view = $("#view");
  const books = await getBooks();
  const withCand = books.filter(b => b.pending > 0);
  if (!withCand.length) {
    view.innerHTML = `<div class="card">当前没有待确认注释</div>`; return;
  }
  if (!pv.bookId || !withCand.some(b => b.book_id === pv.bookId)) pv.bookId = withCand[0].book_id;
  view.innerHTML = `<a class="backlink" href="#/books">← 数据检查</a>
    <h3>待确认注释（原刊括号）</h3>
    <p class="quiet">國語/戰國策 行内圆括号是否为注文尚未人工确认：状态保持 pending_commentary，
    系统绝不自动归属韦昭/高诱等注者。此页逐条核对括号切分与「是否注文」，结论由人工记录；
    展示与判定一律以 text_orig（含原刊括号）为准。</p>
    <div class="controls">
      <label>史书</label><select id="pv-book">${withCand.map(b =>
        `<option value="${esc(b.book_id)}" ${b.book_id === pv.bookId ? "selected" : ""}>
         《${esc(b.title)}》（待确认 ${num(b.pending).toLocaleString()}）</option>`).join("")}</select>
      <label>文件</label><select id="pv-file"></select>
      <button class="btn primary" onclick="pvGo(1)">查看</button>
      <span class="quiet">「查看」列出仅状态待确认的括号片段</span>
    </div><div id="pvbody"><div class="load">正在加载…</div></div>`;
  const bs = $("#pv-book");
  bs.onchange = async () => { pv.bookId = bs.value; pv.fileId = 0;
    pv.page = 1; await fillFileSel(); pvGo(1); };
  await fillFileSel();
  if (!pv.fileId) {
    const files = await api(`/api/books/${encodeURIComponent(pv.bookId)}/files`);
    const cand = files.filter(f => f.commentary > 0);
    pv.fileId = (cand.length ? cand[0] : files[0]).file_id;
    await fillFileSel();
  }
  pvGo(1);
}
async function fillFileSel() {
  const files = await api(`/api/books/${encodeURIComponent(pv.bookId)}/files`);
  const fsel = $("#pv-file");
  if (!fsel) return;
  fsel.innerHTML = files.map(f => `<option value="${f.file_id}" ${f.file_id === pv.fileId ? "selected" : ""}>
    ${esc(f.file_name)}${f.commentary ? `（括号注 ${num(f.commentary).toLocaleString()}）` : ""}</option>`).join("");
  fsel.onchange = () => { pv.fileId = +fsel.value; pv.page = 1; pvGo(1); };
}
async function pvGo(page) {
  pv.page = page;
  const body = $("#pvbody");
  if (!body) return;
  const files = await api(`/api/books/${encodeURIComponent(pv.bookId)}/files`);
  const cur = files.find(f => f.file_id === pv.fileId) || files[0];
  if (!cur) { body.innerHTML = `<div class="card">无文件可选</div>`; return; }
  body.innerHTML = `<div class="load">正在加载…</div>`;
  const rp = qs({ layer: "commentary_candidate", status: "pending_commentary", kind: "passage",
                  offset: (pv.page - 1) * pv.pageSize, limit: pv.pageSize });
  const d = await api(`/api/files/${pv.fileId}/passages?${rp}`);
  let html = pagerBlock(d.total, pv.page, pv.pageSize, "pvGo");
  html += `<div class="overflow"><table class="dev"><tr><th>行号</th><th>片段</th>
    <th>文本（原刊括号原样，紫色 = 候选）</th><th>数据</th><th>阅读</th></tr>`;
  for (const r of d.rows) {
    const m = rowNorm(r);
    html += `<tr><td class="row-no">${m.row_no}</td><td>${chip("括号注候选", "cand")}</td>
      <td><div class="celltext">${rich(m.text_orig, { pb: false, cand: true })}</div></td>
      <td><details class="dev"><summary>数据</summary>${dataTable(m)}</details></td>
      <td><a class="backlink" href="#/p/${m.passage_id}?ctx=1">看上下文 →</a></td></tr>`;
  }
  html += `</table></div>
    <p class="quiet">核对方法：点「阅读」打开上下文页对照前后文，判断该括号是否注文；
    状态改动只由人工完成，系统不自动变更任何 status。</p>`;
  body.innerHTML = html;
}

/* ================= 项目说明 ================= */
async function aboutView() {
  const view = $("#view");
  const [st, books] = await Promise.all([getStats(), getBooks()]);
  const fts = st.fts || {};
  const bookRows = books.map(b => `<tr><td>《${esc(b.title)}》</td>
    <td>${num(b.files).toLocaleString()}</td><td>${num(b.main_passages).toLocaleString()}</td>
    <td>${b.pending ? num(b.pending).toLocaleString() : "—"}</td>
    <td>${num(b.src_blocks).toLocaleString()}</td></tr>`).join("");
  view.innerHTML = `<h3>项目说明</h3>
    <div class="card"><h3>这套系统是什么</h3>
      <p>对已收录的 ${books.length} 部史料（${books.map(b => b.title).join("、")}，共
      ${books.reduce((s, b) => s + (b.files || 0), 0).toLocaleString()} 个原始文件）做忠实
      解析、存库与检索的本地工具。定位：找到真正古籍原文，并说清「出自哪本书哪一卷哪一行」。</p>
      <p>原则：<b>原始史料 &gt; 解析 &gt; 数据库 &gt; 检索 &gt; AI</b>。检索与上下文来自数据库真实记录，
      不接入任何模型，不做猜测性改写；拿不准就标待确认。</p>
      <p class="quiet">國語/戰國策 行内圆括号注一律标「待确认」，系统不自动判定韦昭注/高诱注——
      原刊括号内容 · 待确认，由人工逐条核对。</p></div>
    <div class="card"><h3>当前数据</h3>
      <div class="overflow"><table class="dev"><tr>
        <th>史书</th><th>txt 文件</th><th>正文记录</th><th>待确认</th><th>出处块</th></tr>
        ${bookRows}</table></div>
      <p class="quiet">全库 ${num(st.records).toLocaleString()} 条记录；正文 ${num(st.passages).toLocaleString()} 条；
      原件 SHA-256 校验通过；全文索引 ${fts.docs ? num(fts.docs).toLocaleString() : 0} 条（${esc(fts.tokenizer || "未建")}）</p></div>
    <div class="card"><h3>使用提示</h3>
      <p>· 检索输入简体自动转繁体匹配（「齐桓公」≡「齊桓公」）。<br>
      · 每张史料的「出处」取自数据库真实字段：书名/卷/篇/节/版本/原文件/页码；确实没有的显示「暂无」——
      系统不用文件名猜章节。<br>
      · 「查看原文 · 上下文」展示该句在同文件中的真实相邻记录（前 3 / 后 3），可判断上下文义。<br>
      · 紫色「括号注候选」为原刊行内括号切出片段，是否注文待人工确认；系统不变更其状态。<br>
      · 本页为本地只读：数据库可随时从原始文件重建（<span class="mono">python -m scripts.pipeline.run_all</span>），
      原始史料零写入。</p></div>
    <div class="card"><h3>开发检查入口</h3>
      <p><a class="backlink" href="#/books">数据检查</a>（逐书/逐文件统计与行内核对）·
      <a class="backlink" href="#/pending">待确认注释</a>（逐条过括号注）·
      文件内「原文对照」页签 = 原件与解析层逐行对账。</p></div>`;
}

/* ================= 提问（第四阶段） ================= */
/* 用户直接问一句自然语言，系统做的是：问题分析 → 实体/意图识别 → 古代表达扩展
 * → 召回 → 排序 → 事件级聚合 → 不同史书对照。
 * **不生成答案**：页面上出现的每一段正文都是数据库里的 text_orig 原样相接，
 * 系统只负责把线索摆出来，判断留给读者。 */
const TEXT_MODES = [["orig", "只看繁体"], ["simplified", "只看简体"],
                    ["both", "繁简对照"]];
const TEXT_MODE_TIP = {
  orig: "原文原样。史料本来的字形，一字不改",
  simplified: "程序按系统字符表转换的简体，只作阅读辅助；原文仍是繁体",
  both: "上行繁体原文、下行程序转换的简体，可逐字对照",
};
const INTENT_LABEL = {
  death: "死亡", flight: "出亡/流亡", duration: "时长", birth: "出身",
  identity: "身份", relation: "关系", event: "事件", reason: "原因",
  time: "时间", place: "地点",
};
let askState = { q: "", mode: "standard", text: "orig", loading: false,
                 terms: [], simpleTerms: [], blocks: [] };

async function askView(params) {
  const view = $("#view");
  askState = { ...askState, q: params.q ?? "", mode: params.mode || "standard",
               text: params.text || "orig" };
  view.innerHTML = `<div class="searchbar">
      <input id="aq" type="text" value="${esc(askState.q)}"
        placeholder="用一句话问，如：齐桓公是什么时候死的？">
      <label class="modepick" title="${esc(MODE_TIP.standard)}">显示长度
        <select id="amode" onchange="onAskMode(this.value)">
          ${BLOCK_MODES.map(([v, t]) => `<option value="${v}"
            ${askState.mode === v ? "selected" : ""}>${t}</option>`).join("")}
        </select>
      </label>
      <label class="modepick" title="${esc(TEXT_MODE_TIP[askState.text])}">字体
        <select id="atext" onchange="onAskText(this.value)">
          ${TEXT_MODES.map(([v, t]) => `<option value="${v}"
            ${askState.text === v ? "selected" : ""}>${t}</option>`).join("")}
        </select>
      </label>
      <button class="btn primary" onclick="doAsk()">提问</button>
    </div>
    <div id="result"><div class="load">直接问一句试试：<br>
      「齐桓公是什么时候死的？」「晋文公重耳流亡了多少年？」「商鞅变法是怎么回事？」<br>
      <span class="quiet">系统只检索并聚合史料原文，不生成答案；语料里没有的会如实说没有。</span></div></div>`;
  const aq = $("#aq");
  aq.addEventListener("keydown", e => { if (e.key === "Enter") doAsk(); });
  if (askState.q) doAsk(); else aq.focus();
}
window.onAskMode = v => {
  askState.mode = v || "standard";
  if (askState.q) doAsk();
};
window.onAskText = v => {
  askState.text = v || "orig";
  // 字体切换不重新请求：繁简两份文本响应里都已经带回来了（见 dual_text）。
  if (askState.last) renderAsk(askState.last);
};

function doAsk() {
  if (askState.loading) return;
  const aq = $("#aq"); if (aq) askState.q = aq.value.trim();
  const am = $("#amode"); if (am) askState.mode = am.value;
  const at = $("#atext"); if (at) askState.text = at.value;
  if (!askState.q) { $("#aq").focus(); return; }
  const rp = { q: askState.q, level: "question", mode: askState.mode,
               text: askState.text };
  history.replaceState(null, "", `#/ask?${qs(rp)}`);
  const box = $("#result");
  box.innerHTML = `<div class="load">正在分析问题、查找相关史料…</div>`;
  askState.loading = true;
  api(`/api/search?${qs(rp)}`)
    .then(res => { askState.loading = false; renderAsk(res); })
    .catch(e => { askState.loading = false;
      box.innerHTML = `<div class="card"><b>提问失败</b><br>${esc(e.message)}</div>`; });
}

function askAnalysisCard(res) {
  const qq = res.question || {};
  const ents = (qq.entities || []).map(e =>
    chip(`${e.entity}`, "main",
         `问句里写作「${e.matched}」，权重 ${e.weight}：${e.why}`)).join(" ");
  const ints = (qq.intents || []).map(i =>
    chip(INTENT_LABEL[i] || i, "structure", `意图 ${i}`)).join(" ");
  const groups = (res.expanded && res.expanded.groups) || [];
  const rows = groups.map(g => `<tr><td class="mono">${esc(g.role)}</td>
      <td>${(g.terms || []).map(t => `<span class="tok">${esc(t)}</span>`).join("")}</td>
      <td class="quiet">${esc(g.note || "")}</td></tr>`).join("");
  return `<details class="card" open><summary class="sum">系统从这个问题里读出了什么
      <span class="quiet">（全部由确定性规则得出，可逐条复核）</span></summary>
    <div class="askqa">
      <div><b>问句</b>：${esc(res.q || "")}</div>
      <div><b>繁简统一</b>：${esc((qq.text && qq.text !== res.q)
        ? `${res.q} → ${qq.text}` : "无需转换")}</div>
      <div><b>人物实体</b>：${ents || `<span class="quiet">未识别到人物实体</span>`}</div>
      <div><b>意图</b>：${ints || `<span class="quiet">未识别到明确意图</span>`}</div>
      ${qq.years && qq.years.length ? `<div><b>纪年</b>：${esc(qq.years.join("、"))}</div>` : ""}
    </div>
    ${rows ? `<div class="overflow"><table class="dev">
      <tr><th>检索词组</th><th>扩展出的词（组内任一命中）</th><th>依据</th></tr>${rows}</table></div>` : ""}
    <p class="quiet">扩展出的每个词都在语料里实际出现，系统不生成任何语料里没有的词。</p>
  </details>`;
}

function renderAsk(res) {
  askState.last = res;
  const box = $("#result");
  const ag = res.aggregation || { events: [], sources: [], notes: [] };
  const notes = (res.notes || []).concat(ag.notes || []);
  const terms = (res.expanded && res.expanded.all_terms) || [];
  askState.terms = terms;
  askState.simpleTerms = res.terms_simplified || [];
  // expandBlock 靠 searchState.results[idx] 取片段，这里把事件里的片段按同一
  // 顺序摊平，序号与页面上的 blk{i} 一一对应，展开上下文不用另写一套。
  askState.blocks = [];
  ag.events.forEach(ev => (ev.blocks || []).forEach(b => askState.blocks.push(b)));
  searchState.results = askState.blocks;
  searchState.terms = terms;

  let html = askAnalysisCard(res);
  html += `<div class="asknotes">${notes.map(n =>
    `<div class="quiet">· ${esc(n)}</div>`).join("")}</div>`;

  if (!ag.events.length) {
    html += `<div class="card"><b>语料中没有找到相关史料</b><br>
      系统如实返回空，不编造答案。可以换个问法，或改用
      <a class="backlink" href="#/search?q=${encodeURIComponent(res.q || "")}">全文检索</a>。<br>
      <span class="quiet">这句话说的是<b>我们的语料</b>里没有，不是「史书上没有」——
      语料收了多少部、多少卷，见
      <a class="backlink" href="#/coverage">收录范围</a>。</span></div>`;
    box.innerHTML = html;
    return;
  }

  const nBlocks = ag.n_blocks || askState.blocks.length;
  html += `<div class="resulthead">
      共 <b>${ag.events.length}</b> 件事 · ${num(nBlocks)} 段史料 ·
      涉及 <b>${ag.sources.length}</b> 部史书
      <span class="quiet">· 同一件事在不同史书里各自成条，系统不合并、不裁决异同</span>
    </div>`;
  html += `<div class="srclist">${ag.sources.map(s =>
    `<span class="chip structure" title="该书的记载各自成条">《${esc(s.book_title)}》
      ${num(s.n_events)} 件事</span>`).join("")}</div>`;

  let i = 0;
  ag.events.forEach((ev, ei) => {
    const loc = [ev.juan, ev.section, ev.subsection,
      ev.division && `${ev.division}部`].filter(Boolean).join(" · ");
    html += `<div class="eventcard">
      <div class="evhead">
        <span class="evno">${ei + 1}</span>
        <span class="hit-book">《${esc(ev.book_title || "暂无")}》</span>
        ${loc ? `<span class="hit-loc">${esc(loc)}</span>` : `<span class="chip structure">卷/篇暂无</span>`}
        ${chip(`相关度 ${num(ev.relevance)}`, "main", "第四阶段打分：实体/意图/同段共现逐项相加")}
        ${ev.match_count > 1 ? chip(`本段命中 ${num(ev.match_count)} 处`, "main") : ""}
        <span class="quiet">${whyText(ev)}</span>
      </div>`;
    ev.blocks.forEach(b => {
      html += blockCard(b, terms, i, askState.text, askState.simpleTerms); i += 1; });
    html += `</div>`;
  });
  html += `<p class="quiet">以上正文全部来自数据库 <span class="mono">text_orig</span>，
    按同一文件内的真实次序相接，不改写、不补标点、不摘要。相关度是排序用的分数，
    不是对史料可信度的评判。简体为程序按系统字符表转换所得，原文以繁体为准。</p>`;
  box.innerHTML = html;
}

// 把打分明细翻成人话：为什么这一段被判为相关。
// 按分值从高到低取前几项 —— 明细的键名本身就是说明（「实体:管仲」「问句原词:重耳」
// 「2个实体同段」），分值最高的那条才是判它相关的首要理由。
function whyText(ev) {
  const d = (ev.blocks[0] && ev.blocks[0].why && ev.blocks[0].why.detail) || {};
  const parts = Object.keys(d).sort((a, b) => d[b] - d[a]).slice(0, 4);
  return parts.length ? `判为相关：${parts.join("；")}` : "";
}

/* ---------- 启动 ---------- */
async function headerStats() {
  try {
    const st = await getStats();
    const el = $("#statsMini");
    if (el) el.textContent = `${st.books} 部书 · ${st.files} 文件 · ${num(st.records).toLocaleString()} 记录`;
  } catch (e) { /* 服务器未就绪时静默 */ }
}

/* 页脚书目（6.3-I）：原先写死 7 个书名，每加一本书它就说一次假话。改成按数据填。
 * **只在本地 API 模式填**：演示模式的页脚由 boot.js 换成「本站不含真实史料」的
 * 声明（那里一个真实书名都不许出现），静态导出同理 —— 两处都不填，页脚只剩
 * 那句通用说明，宁可不列书名，也不列一份错的。 */
async function footBooks() {
  const boot = window.HistoryAIBoot ? await window.HistoryAIBoot : { mode: "api" };
  const el = $("#footBooks");
  if (!el || boot.mode !== "api") return;
  try {
    const books = await getBooks();
    el.textContent = books.map(b => b.title).join(" · ") + "　|　";
  } catch (e) { /* 取不到就留空，通用说明那句话还在 */ }
}
route();
headerStats();
footBooks();
