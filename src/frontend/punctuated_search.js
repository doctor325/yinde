/* Primary source edition search. Older witnesses remain a separate route. */
function searchSimplified(text) {
  return window.YindeEngine?.zh?.toSimplified ? window.YindeEngine.zh.toSimplified(text) : text;
}

function unifiedSearchStrategy(query, legacy) {
  if (['keyword','expanded','evidence','relation','hybrid','semantic'].includes(legacy)) return legacy;
  const text=searchSimplified(query);
  const question = /[？?]\s*$|何时|何年|何月|何日|何地|何处|为何|何故|如何|何人|何事|什么|怎么|怎样|是否|多少|几[年月日次岁]|哪里|哪儿|哪[年月日位个次些]|谁|吗\s*$/.test(text);
  return question ? 'evidence' : 'keyword';
}

function phraseOnlyInQuotes(phrase, source) {
  const closing={'「':'」','『':'』','“':'”'};
  const quotes=[];
  const positions=[];
  let outer=0, normalized='';
  for (const char of searchSimplified(source)) {
    if (closing[char]) {
      if (!quotes.length) outer+=1;
      quotes.push(closing[char]);
    } else if ('」』”'.includes(char)) {
      if (quotes.pop()!==char) return false;
    } else if (/\p{L}|\p{N}/u.test(char)) {
      normalized+=char;
      for (let i=0;i<char.length;i++) positions.push(quotes.length?outer:0);
    }
  }
  // A passage may end in mid-speech. Only trust an opening mark we saw in the passage.
  let found=false;
  for (let start=normalized.indexOf(phrase);start>=0;start=normalized.indexOf(phrase,start+1)) {
    const inQuote=positions[start];
    if (!inQuote || positions.slice(start,start+phrase.length).some(pos=>pos!==inQuote)) return false;
    found=true;
  }
  return found;
}

function phraseExpansion(query, explicitStrategy, page, result) {
  if (explicitStrategy || page!==1 || result.mode!=='keyword' || !result.book_available
      || result.biographies?.length || !/^\p{Script=Han}{6,48}$/u.test(query)) return '';
  if (result.total===0) return 'empty';
  if (result.reference_expansion) return 'annotation';
  const hits=result.hits;
  const phrase=searchSimplified(query);
  if (hits.length && hits.length===result.total &&
      hits.every(hit=>phraseOnlyInQuotes(phrase,hit.chunk.text_orig))) return 'quoted';
  return '';
}

async function punctuatedSearchView(params, revision, semantic = false) {
  if (params.source === 'original' || params.edition) return false;
  const boot = window.YindeBoot ? await window.YindeBoot : {mode:'api'};
  if (revision !== routeRevision) return true;
  if (boot.mode !== 'api') return false;
  let status;
  try { status = await api('/api/punctuated/search/status'); }
  catch (_) { status = {ready:false,reason:'标点版检索服务暂不可用，请使用更新后的桌面版，或明确选择原底本检索。'}; }
  if (revision !== routeRevision) return true;
  const route = 'search';
  const original = `#/${route}?${qs({q:params.q,book:params.book,source:'original'})}`;
  const view = $('#view');
  if (!status.ready) {
    view.innerHTML = `<h1>标点版检索</h1><p>${esc(status.reason)}</p><a class="btn" href="${original}">检索原底本</a>`;
    return true;
  }
  const books = await getBooks();
  if (revision !== routeRevision) return true;
  view.innerHTML = `<div class="page-heading"><p class="eyebrow">标点版优先 · 独立版本</p>
    <h1>搜索史料</h1>
    <p>输入词句或直接提问，查找有出处的史料。</p></div>
    <form id="punctuatedSearchForm" class="searchbar"><div class="search-main">
      <input id="punctuatedSearchQuery" aria-label="搜索史料或提问" maxlength="300" required value="${esc(params.q || '')}" placeholder="输入人物、事件、原文或问题">
      <button class="btn primary">搜索</button></div><details><summary>检索选项</summary><div class="controls">
      <label>史书 <select id="punctuatedSearchBook"><option value="">全部标点史书</option>${books.map(b=>`<option value="${esc(b.book_id)}" ${params.book===b.book_id?'selected':''}>${esc(b.title)}${status.manifest.book_chunks[b.book_id]?'':'（暂无标点版）'}</option>`).join('')}</select></label>
      <a href="${original}">原底本对照检索</a></div></details></form>
    <p class="quiet">覆盖 ${num(status.manifest.books)} 部、${num(status.manifest.pages)} 篇。未命中不代表原书没有记载。</p>
    <div id="punctuatedSearchResult" aria-live="polite"></div>`;
  async function search(page=1) {
    const query=$('#punctuatedSearchQuery').value.trim();
    if (!query) return;
    if (historyQaRequest) historyQaRequest.abort();
    const controller=historyQaRequest=new AbortController();
    const box=$('#punctuatedSearchResult');
    const current=()=>revision===routeRevision && historyQaRequest===controller && box===$('#punctuatedSearchResult');
    const strategy=unifiedSearchStrategy(query,params.strategy);
    const args={q:query,book:$('#punctuatedSearchBook').value,strategy,page:strategy==='keyword'?page:1};
    history.replaceState(null,'',`#/${route}?${qs({...args,strategy:params.strategy})}`);
    box.setAttribute('aria-busy','true');
    box.textContent=strategy==='evidence'?'正在查找史料并比较上下文，首次加载本地模型可能较慢…':
      strategy==='keyword'?'正在查找史料…':'正在寻找相关史料，首次加载本地模型可能较慢…';
    try {
      let result=await api('/api/punctuated/search?'+qs(args),{signal:controller.signal});
      if (!current()) return;
      const expansion=phraseExpansion(query,params.strategy,page,result);
      let literalResults='', expansionNote='';
      if (expansion) {
        box.textContent=expansion==='empty'?'未找到直接匹配，正在查找相关史料…':
          '字面匹配来自引语或注释，正在查找其他记载…';
        try {
          const supplemental=await api('/api/punctuated/search?'+qs({...args,strategy:'evidence'}),{signal:controller.signal});
          if (!current()) return;
          if (supplemental.hits.length) {
            if (expansion!=='empty') literalResults='<details class="literal-search-matches"><summary>查看字面匹配</summary>'+punctuatedSearchResults(result)+'</details>';
            result=supplemental;
          }
        } catch (error) {
          if (!current() || error.name==='AbortError') return;
          expansionNote='<p class="quiet">相关史料补查暂不可用。</p>';
        }
      }
      box.innerHTML=punctuatedSearchResults(result)+expansionNote+literalResults;
      bindPunctuatedCitations(box);
      for (const button of box.querySelectorAll('[data-search-page]')) button.addEventListener('click',()=>search(Number(button.dataset.searchPage)));
    } catch (error) {
      if (current() && error.name!=='AbortError') box.textContent=error.message;
    } finally { if(current())box.setAttribute('aria-busy','false'); }
  }
  $('#punctuatedSearchForm').addEventListener('submit',event=>{event.preventDefault();return search();});
  if(params.q)await search(Math.max(1,Number(params.page)||1));
  return true;
}

function punctuatedHitLink(chunk, returnTo) {
  const part=chunk.parts?.[0];
  const ranges=chunk.parts?.length>1?JSON.stringify(chunk.parts.map(p=>[p.block_index,p.start,p.end])):undefined;
  return `#/punctuated/${Number(chunk.page_id)}?${qs({block:part?.block_index,start:part?.start,end:part?.end,back:returnTo,
    ranges,rev:chunk.revision_id,sha:chunk.source_sha256})}`;
}

function punctuatedDissuasionEvidence(hit, returnTo) {
  const evidence = hit.evidence_audit?.attached_evidence?.find(item => item.support === 'dissuasion');
  if (!evidence) return '';
  const chunks = [hit.chunk, ...(hit.context_chunks || [])];
  const labels = { speaker: '发言人', recipient: '受议者', action: '所议行动', detail: '理由' };
  const items = Object.entries(labels).map(([key, label]) => {
    const ref = evidence[key];
    const chunk = chunks.find(item => item.chunk_id === ref.chunk_id);
    if (!chunk) return '';
    let offset = 0;
    const parts = [];
    for (const part of chunk.parts || []) {
      const size = part.end - part.start;
      const start = Math.max(ref.start, offset) - offset;
      const end = Math.min(ref.end, offset + size) - offset;
      if (start < end) parts.push({ block_index: part.block_index, start: part.start + start, end: part.start + end });
      offset += size;
    }
    const quote = readingTextHtml(ref.quote, { pb: false });
    const link = parts.length ? ' · <a href="' + punctuatedHitLink({ ...chunk, parts }, returnTo) + '">定位原文</a>' : '';
    return '<dt>' + label + '</dt><dd>' + quote + link + '</dd>';
  });
  return '<dl class="dissuasion-evidence">' + items.join('') + '</dl>';
}

function punctuatedPolityEvidence(evidence, returnTo, advice=false) {
  if (!evidence) return '';
  const supported = evidence.status === 'supported';
  const label = supported ? '有原文依据' : '尚未确认';
  const entries = Object.entries(evidence.citations);
  const primary = ['劝谏理由', '出兵对手', '事发时在位', '政权名称依据'];
  if (advice) primary.unshift('劝谏人', '所劝对象', '所议行动', '原文叙述的撤城背景');
  if (!evidence.citations['事发时在位']) primary.push('战事纪年', '即位记载', '在位终点');
  const item = (role, ref) => {
    const parts = ref.ranges.map(([block_index, start, end]) => ({block_index, start, end}));
    const href = punctuatedHitLink({...ref, parts}, returnTo);
    return `<dt>${esc(role)}</dt><dd>${readingTextHtml(ref.quote,{pb:false})} · <a href="${href}">定位原文</a></dd>`;
  };
  const selected = primary.filter(role => evidence.citations[role])
    .map(role => item(role, evidence.citations[role])).join('');
  const more = entries.filter(([role]) => !primary.includes(role))
    .map(([role, ref]) => item(role, ref)).join('');
  const date = supported && evidence.year != null ? `<p>${esc(evidence.era)}（${num(evidence.year)} 年），当时的对手是${esc(evidence.polity)}的${esc(evidence.ruler)}。</p>` : '';
  return `<section class="polity-evidence" data-status="${esc(evidence.status)}">
    <p><strong>${label}</strong> · ${esc(evidence.message)}</p>${date}
    ${selected ? `<dl class="dissuasion-evidence">${selected}</dl>` : ''}
    ${more ? `<details><summary>${advice?'其他出处':'身份、在位与战事互证'}</summary><dl class="dissuasion-evidence">${more}</dl></details>` : ''}
    </section>`;
}

function punctuatedActivityEvidence(evidence, returnTo) {
  if (!evidence) return '';
  const entries = Object.entries(evidence.citations).map(([label, ref]) => {
    const parts = ref.ranges.map(([block_index, start, end]) => ({block_index, start, end}));
    const href = punctuatedHitLink({...ref, parts}, returnTo);
    return `<dt>${esc(label)}</dt><dd>${readingTextHtml(ref.quote,{pb:false})} · <a href="${href}">定位原文</a></dd>`;
  }).join('');
  const identity = evidence.identity;
  return `<section class="polity-evidence" data-status="supported">
    <p><strong>有原文依据</strong> · ${esc(evidence.person)}${esc(evidence.summary || '在这次围城中的行动')}</p>
    <dl class="dissuasion-evidence">${entries}
      ${identity ? `<dt>人物出处</dt><dd>${readingTextHtml(identity.quote,{pb:false})} · ${punctuatedSourceLink(identity.source_url,'本纪固定版本')}</dd>` : ''}
    </dl></section>`;
}

function punctuatedHitContext(hit, returnTo) {
  const c=hit.chunk;
  const scope=['page_id','book_id','revision_id','source_sha256','layer','heading'];
  const neighbors=(hit.context_chunks||[]).filter(n=>n.chunk_id!==c.chunk_id && scope.every(key=>n[key]===c[key]));
  if(!neighbors.length)return '';
  const ordered=[...neighbors,c].sort((a,b)=>a.chunk_id-b.chunk_id);
  return `<details><summary>展开相邻原文</summary>${ordered.map(n=>{
    const label=n.chunk_id===c.chunk_id?'命中片段':n.chunk_id<c.chunk_id?'前文':'后文';
    return `<p class="quiet">${label} · <a href="${punctuatedHitLink(n,returnTo)}">在篇章中定位</a></p>
      <p class="blk-text">${readingTextHtml(n.text_orig,{pb:false})}</p>`;
  }).join('')}</details>`;
}

function punctuatedSearchResults(result) {
  // Carry the complete search state with the source link, including pagination
  // and strategy. This also works after refreshing or opening a new tab.
  const returnTo=/^#\/(?:search|semantic|history-qa)(?:\?|$)/.test(location.hash)
    ? location.hash : `#/${result.mode==='keyword'?'search':'semantic'}?${qs({q:result.query,book:result.book,strategy:result.mode,page:result.page})}`;
  const old=`#/search?${qs({q:result.query,book:result.book,source:'original'})}`;
  if (!result.book_available) return `<p>这部史书尚未收录标点版本。</p><a class="btn" href="${old}">检索所收原底本</a>`;
  const pipeline=result.evidence_pipeline;
  const hasClues=pipeline && result.hits.some(hit=>{
    const evidence=hit.advice_evidence || hit.polity_evidence;
    return evidence ? evidence.status==='supported' : hit.evidence_audit?.status==='clues_present';
  });
  let html=pipeline?'':`<p class="quiet">${result.mode==='keyword'?`共 ${num(result.total)} 个匹配片段 · 第 ${num(result.page)} 页`:`语义候选 ${num(result.candidate_counts.semantic)} 个 · 关键词／规则候选 ${num(result.candidate_counts.keyword)} 个 · 展示前 ${result.hits.length} 个`}
    · ${num(result.elapsed_ms)} ms</p>`;
  const biographies=result.biographies||[];
  if(biographies.length){
    html+='<h2>人物本纪</h2><p class="quiet">依据本纪开篇的庙号与名讳定位。打开可阅读本卷的连续记载。</p>';
    for(const entry of biographies){
      const continued=(entry.continuations||[]).map(volume=>
        `<li><a href="#/punctuated/${Number(volume.page_id)}?${qs({back:returnTo})}">${esc(volume.title)} · ${esc(volume.label)}</a>
        · ${punctuatedSourceLink(volume.toc_source_url,'目录依据')}</li>`).join('');
      html+=`<article class="card"><h3><a href="#/punctuated/${Number(entry.page_id)}?${qs({back:returnTo})}">${esc(entry.title)}</a></h3><p>${readingTextHtml(entry.quote,{pb:false})}</p>${continued?`<ul>${continued}</ul>`:''}</article>`;
    }
  }
  if(pipeline){
    html+=`<p class="quiet evidence-pipeline-note" data-pipeline-version="${esc(pipeline.version||'')}">${hasClues
      ? '部分史料包含所问要素和句式线索，请结合出处核对。'
      : '本次找到的史料中，尚未确认有片段能回答这个问题。'}</p>`;
    if(pipeline.grounded_fallback?.hypothesis)html+='<p class="quiet">问题尚未完整解析，本次补充了有原文落点的词语线索。</p>';
    if(pipeline.candidate_scan.truncated)html+='<p>主体候选达到扫描上限，可能遗漏史料。</p>';
  }
  if(result.relation_experiment){
    const experiment=result.relation_experiment;
    html+=`<p class="quiet relation-experiment-note">关系线索实验：${experiment.selection==='baseline_fallback'?'未识别到足够的关系线索，本次使用原有语义＋规则扩展结果。':'结合人物、动作、对象与句式寻找候选；尚不能可靠判断人物归属、否定或因果。'}</p>`;
    if(experiment.candidate_scan.truncated)html+='<p class="quiet">关系候选达到本次扫描上限，结果可能遗漏相关史料。</p>';
  }
  if(pipeline && result.hits.length)html+='<h2>史料片段</h2>';
  for(const hit of result.hits){
    const c=hit.chunk;
    // This is a phrase search in another witness, not a claimed passage alignment.
    const phrase=(c.text_orig.match(/[\u3400-\u9fff]{4,}/)||[''])[0].slice(0,10)||result.query;
    const compare=`#/search?${qs({q:phrase,book:c.book_id,source:'original'})}`;
    const read=punctuatedHitLink(c,returnTo);
    const missing=hit.evidence_audit?.missing||[];
    const evidence=hit.advice_evidence || hit.polity_evidence;
    const focus=(!evidence && punctuatedDissuasionEvidence(hit,returnTo)) || (hit.evidence_focus?.selected?`<p class="quiet">重点比较的原文</p><p>${readingTextHtml(hit.evidence_focus.text,{pb:false})}</p>`:'');
    html+=`<article class="card punctuated-search-hit"><h3>《${esc(c.book_title)}》 ${esc(c.title)}</h3>
      <p class="quiet">标点版 · 维基文库录入本 · ${c.layer==='annotation'?'来源注释':'正文'}${c.status==='partial_render'?' · 来源排版异常':c.status==='insufficient_punctuation'?' · 短篇或低标点':''}</p>
      ${hit.channels?.includes('biography')?'<p class="quiet">按本纪记载的姓名与庙号找到</p>':''}
      <div class="blk-text">${readingTextHtml(c.text_orig,{pb:false})}</div>
      ${punctuatedPolityEvidence(evidence,returnTo,Boolean(hit.advice_evidence))}
      ${punctuatedActivityEvidence(hit.evidence_audit?.activity_evidence,returnTo)}
      ${c.status!=='readable'?`<p class="quiet">${esc(c.reason)}</p>`:''}
      ${pipeline||hit.semantic_score==null?'':`<p class="quiet">语义相似分 ${Number(hit.semantic_score).toFixed(3)}（不是正确概率）</p>`}
      <div class="controls"><a class="btn read-link" href="${read}">标点原文与上下文</a>${punctuatedSourceLink(c.source_url,'固定版本出处')}${punctuatedCitation(c,c.text_orig,c.layer)}</div>
      ${punctuatedHitContext(hit,returnTo)}
      <details class="source-detail"><summary>版本信息与底本对照</summary><p>${punctuatedSourceLink(c.history_url,'修订历史')} · ${punctuatedSourceLink(c.license_url,'CC BY-SA 4.0')}</p>
      <p>${esc(c.attribution)}</p><p>修订 ${num(c.revision_id)} · 原文件校验 ${esc(c.source_sha256)} · 片段 ${num(c.chunk_id)}</p>
      <p><a href="${compare}">在原底本查找此句</a>。按片段文字检索，未确认逐句对应。</p></details>
      ${hit.evidence_audit?`<details class="evidence-audit"><summary>检索线索</summary><p>${missing.length?`${esc(missing.join('；'))}。规则未识别不代表原文没有记载。`:'检索规则找到了所问要素和句式线索。'}${hit.context_truncated?' 相关性比较使用的文字有截断。':''}</p>${focus}</details>`:''}</article>`;
  }
  if(!result.hits.length && !biographies.length)html+=`<p>标点版本内未找到匹配片段；这不代表史书没有记载。</p><a href="${old}">改查原底本</a>`;
  if(result.mode==='keyword')html+=`<div class="controls">${result.page>1?`<button class="btn" data-search-page="${result.page-1}">上一页</button>`:''}${result.page*result.page_size<result.total?`<button class="btn" data-search-page="${result.page+1}">下一页</button>`:''}</div>`;
  if(result.query_plan)html+=`<details><summary>本次规则线索</summary><p>${esc((result.query_plan.literal_hypotheses||(result.query_plan.clues||[]).map(c=>c.text)).join('、'))}</p><p>规则和相似性仅用于找候选，人物归属与事件关系尚未核实。</p></details>`;
  if(result.trace_id)html+=`<details><summary>本机查询记录</summary><p>${esc(result.trace_id)}</p></details>`;
  return html;
}
