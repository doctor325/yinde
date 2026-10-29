/* Primary source edition search. Older witnesses remain a separate route. */
async function punctuatedSearchView(params, revision, semantic = false) {
  if (params.source === 'original' || params.edition) return false;
  const boot = window.YindeBoot ? await window.YindeBoot : {mode:'api'};
  if (revision !== routeRevision) return true;
  if (boot.mode !== 'api') return false;
  let status;
  try { status = await api('/api/punctuated/search/status'); }
  catch (_) { status = {ready:false,reason:'标点版检索服务暂不可用，请使用更新后的桌面版，或明确选择原底本检索。'}; }
  if (revision !== routeRevision) return true;
  const route = semantic ? 'semantic' : 'search';
  const original = `#/${route}?${qs({q:params.q,book:params.book,source:'original'})}`;
  const view = $('#view');
  if (!status.ready) {
    view.innerHTML = `<h1>标点版检索</h1><p>${esc(status.reason)}</p><a class="btn" href="${original}">检索原底本</a>`;
    return true;
  }
  const books = await getBooks();
  if (revision !== routeRevision) return true;
  view.innerHTML = `<div class="page-heading"><p class="eyebrow">标点版优先 · 独立版本</p>
    <h1>${semantic ? '按含义寻找史料' : '查找标点史料'}</h1>
    <p>结果直接来自维基文库录入本，保留来源标点。原底本供另行对照，版本之间可能有异文。</p></div>
    <form id="punctuatedSearchForm" class="searchbar"><div class="search-main">
      <input id="punctuatedSearchQuery" aria-label="检索标点史料" maxlength="300" required value="${esc(params.q || '')}" placeholder="${semantic ? '描述想查的人物、事件或关系' : '人物、地名或原文词句；空格连接多个词'}">
      <button class="btn primary">查找史料</button></div><div class="controls">
      <label>史书 <select id="punctuatedSearchBook"><option value="">全部标点史书</option>${books.map(b=>`<option value="${esc(b.book_id)}" ${params.book===b.book_id?'selected':''}>${esc(b.title)}${status.manifest.book_chunks[b.book_id]?'':'（暂无标点版）'}</option>`).join('')}</select></label>
      ${semantic ? '<label>方式 <select id="punctuatedSearchStrategy"><option value="expanded">语义＋规则扩展</option><option value="evidence">问题解析＋上下文核查</option><option value="relation">语义＋关系线索（实验）</option><option value="hybrid">语义＋关键词</option><option value="semantic">仅语义</option></select></label>' : ''}
      <a href="${original}">改查原底本</a></div></form>
    <p class="quiet">覆盖 ${num(status.manifest.books)} 部、${num(status.manifest.pages)} 篇。可检索不代表篇卷完整或已校勘${semantic ? '；模型仅在本机运行，不生成回答' : ''}。</p>
    <div id="punctuatedSearchResult" aria-live="polite"></div>`;
  const chosen = $('#punctuatedSearchStrategy');
  if (chosen && ['expanded','evidence','relation','hybrid','semantic'].includes(params.strategy)) chosen.value=params.strategy;
  async function search(page=1) {
    const query=$('#punctuatedSearchQuery').value.trim();
    if (!query) return;
    if (historyQaRequest) historyQaRequest.abort();
    const controller=historyQaRequest=new AbortController();
    const box=$('#punctuatedSearchResult');
    const current=()=>revision===routeRevision && historyQaRequest===controller && box===$('#punctuatedSearchResult');
    const args={q:query,book:$('#punctuatedSearchBook').value,strategy:semantic?$('#punctuatedSearchStrategy').value:'keyword',page};
    history.replaceState(null,'',`#/${route}?${qs(args)}`);
    box.setAttribute('aria-busy','true');box.textContent=semantic?'正在检索标点史料，首次查询需准备本地模型与检索缓存，可能较慢…':'正在查找标点史料…';
    try {
      const result=await api('/api/punctuated/search?'+qs(args),{signal:controller.signal});
      if (!current()) return;
      box.innerHTML=punctuatedSearchResults(result);
      for (const button of box.querySelectorAll('[data-search-page]')) button.addEventListener('click',()=>search(Number(button.dataset.searchPage)));
    } catch (error) {
      if (current() && error.name!=='AbortError') box.textContent=error.message;
    } finally { if(current())box.setAttribute('aria-busy','false'); }
  }
  $('#punctuatedSearchForm').addEventListener('submit',event=>{event.preventDefault();return search();});
  if(params.q)await search(Math.max(1,Number(params.page)||1));
  return true;
}

function punctuatedSearchResults(result) {
  const old=`#/search?${qs({q:result.query,book:result.book,source:'original'})}`;
  if (!result.book_available) return `<p>这部史书尚未收录标点版本。</p><a class="btn" href="${old}">检索所收原底本</a>`;
  let html=`<p class="quiet">${result.mode==='keyword'?`共 ${num(result.total)} 个匹配片段 · 第 ${num(result.page)} 页`:`语义候选 ${num(result.candidate_counts.semantic)} 个 · 关键词／规则候选 ${num(result.candidate_counts.keyword)} 个 · 展示前 ${result.hits.length} 个`}
    · ${num(result.elapsed_ms)} ms</p>`;
  if(result.evidence_pipeline){
    html+=`<p class="quiet evidence-pipeline-note" data-pipeline-version="${esc(result.evidence_pipeline.version||'')}">合并多路候选，结合相邻原文比较了 ${num(result.evidence_pipeline.candidate_counts?.context_scored||0)} 个片段。前20条中 ${num(result.evidence_pipeline.audit_clues_present)} 条包含所问要素与句式线索；这不是已核实的答案。</p>`;
    if(result.evidence_pipeline.grounded_fallback?.hypothesis)html+='<p class="quiet">问题尚未完整解析，本次补充了有原文落点的词语线索；没有据此确认人物身份或事件关系。</p>';
    if(result.evidence_pipeline.candidate_scan.truncated)html+='<p>主体候选达到扫描上限，可能遗漏史料。</p>';
  }
  if(result.relation_experiment){
    const experiment=result.relation_experiment;
    html+=`<p class="quiet relation-experiment-note">关系线索实验：${experiment.selection==='baseline_fallback'?'未识别到足够的关系线索，本次使用原有语义＋规则扩展结果。':'结合人物、动作、对象与句式寻找候选；尚不能可靠判断人物归属、否定或因果。'}</p>`;
    if(experiment.candidate_scan.truncated)html+='<p class="quiet">关系候选达到本次扫描上限，结果可能遗漏相关史料。</p>';
  }
  for(const hit of result.hits){
    const c=hit.chunk;
    // This is a phrase search in another witness, not a claimed passage alignment.
    const phrase=(c.text_orig.match(/[\u3400-\u9fff]{4,}/)||[''])[0].slice(0,10)||result.query;
    const compare=`#/search?${qs({q:phrase,book:c.book_id,source:'original'})}`;
    const part=c.parts[0];
    const read=`#/punctuated/${Number(c.page_id)}?${qs({block:part.block_index,start:part.start,end:part.end})}`;
    const focus=hit.evidence_focus?.selected?`<details><summary>本次重点比较的原文</summary><p>${readingTextHtml(hit.evidence_focus.text,{pb:false})}</p><p>结合前后各最多60字比较相关性；这段文字不是生成的答案。</p></details>`:'';
    html+=`<article class="card punctuated-search-hit"><h3>《${esc(c.book_title)}》 ${esc(c.title)}</h3>
      <p class="quiet">标点版 · 维基文库录入本 · ${c.layer==='annotation'?'来源注释':'正文'}${c.status==='partial_render'?' · 来源排版异常':c.status==='insufficient_punctuation'?' · 短篇或低标点':''}</p>
      <div class="blk-text">${readingTextHtml(c.text_orig,{pb:false})}</div>
      ${c.status!=='readable'?`<p class="quiet">${esc(c.reason)}</p>`:''}
      ${hit.semantic_score==null?'':`<p class="quiet">语义相似分 ${Number(hit.semantic_score).toFixed(3)}（不是正确概率）</p>`}
      ${focus}
      ${hit.evidence_audit?`<p class="evidence-audit">${hit.evidence_audit.status==='clues_present'?'包含所问要素与句式线索（未核实）':'自动核查尚未确认是否回答问题'}</p><details><summary>核查依据与相邻原文</summary>${hit.evidence_audit.missing.length?`<p>${esc(hit.evidence_audit.missing.join('；'))}。规则未识别不代表原文没有记载。</p>`:''}${(hit.context_chunks||[]).filter(n=>n.chunk_id!==c.chunk_id).map(n=>`<p>${readingTextHtml(n.text_orig,{pb:false})}</p><a href="#/punctuated/${Number(n.page_id)}?${qs({block:n.parts[0].block_index,start:n.parts[0].start,end:n.parts[0].end})}">定位此段</a>`).join('')||'<p>没有同层相邻片段。</p>'}<p>模型比较相关性，不核实历史事实。${hit.context_truncated?'模型输入存在截断。':''}</p></details>`:''}
      <div class="controls"><a class="btn read-link" href="${read}">标点原文与上下文</a><a href="${compare}">在原底本查找此句</a></div>
      <details class="source-detail"><summary>出处与版本</summary><p>${punctuatedSourceLink(c.source_url,'固定修订来源')} · ${punctuatedSourceLink(c.history_url,'修订历史')} · ${punctuatedSourceLink(c.license_url,'CC BY-SA 4.0')}</p>
      <p>${esc(c.attribution)}</p><p>修订 ${num(c.revision_id)} · 原文件校验 ${esc(c.source_sha256)} · 片段 ${num(c.chunk_id)}</p>
      <p>原底本链接按此句文字另行检索，未确认逐句对应；两版可能有异文，不能仅凭无标点认定更权威。</p></details></article>`;
  }
  if(!result.hits.length)html+=`<p>标点版本内未找到匹配片段；这不代表史书没有记载。</p><a href="${old}">改查原底本</a>`;
  if(result.mode==='keyword')html+=`<div class="controls">${result.page>1?`<button class="btn" data-search-page="${result.page-1}">上一页</button>`:''}${result.page*result.page_size<result.total?`<button class="btn" data-search-page="${result.page+1}">下一页</button>`:''}</div>`;
  if(result.query_plan)html+=`<details><summary>本次规则线索</summary><p>${esc((result.query_plan.literal_hypotheses||(result.query_plan.clues||[]).map(c=>c.text)).join('、'))}</p><p>规则和相似性仅用于找候选，人物归属与事件关系尚未核实。</p></details>`;
  if(result.trace_id)html+=`<details><summary>本机查询记录</summary><p>${esc(result.trace_id)}</p></details>`;
  return html;
}
