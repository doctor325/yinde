/* Separate edition reader. Remote markup is never inserted as HTML. */
function punctuatedSourceLink(url, label) {
  try {
    const u = new URL(url);
    if (u.protocol !== 'https:' || !['zh.wikisource.org', 'creativecommons.org'].includes(u.hostname)) return esc(label);
    return `<a href="${esc(u.href)}" target="_blank" rel="noopener noreferrer">${esc(label)}</a>`;
  } catch (_) { return esc(label); }
}

function punctuatedText(blocks) {
  return blocks.map((b, index) => {
    if (b.kind === 'provenance') return '';
    if (b.kind === 'image') {
      if (!/^data:image\/(?:png|jpeg|gif);base64,[A-Za-z0-9+/=]+$/.test(b.data || '')) return '<span class="hit-notes">〔来源图片暂不可显示〕</span>';
      return `<img class="punctuated-source-image${b.inline ? ' inline' : ''}" src="${b.data}" alt="${esc(b.alt || '来源图片代字')}" title="来源原图，未替换为猜测文字" data-source-sha="${esc(b.sha256)}">`;
    }
    if (b.kind === 'table') {
      if (!['table','thead','tbody','tfoot','tr','td','th','caption'].includes(b.tag)) return '';
      if (b.tag === 'table') return b.closing ? '</table></div>' : '<div class="punctuated-table-scroll" tabindex="0" aria-label="史料表格，可横向滚动"><table class="punctuated-table">';
      if (b.closing) return `</${b.tag}>`;
      const spans = ['td','th'].includes(b.tag) ? ['rowspan','colspan'].filter(k => Number.isInteger(b[k]) && b[k] > 0 && b[k] < 1000).map(k => ` ${k}="${b[k]}"`).join('') : '';
      return `<${b.tag}${spans}>`;
    }
    const content = `<span id="punctuated-block-${index}">${readingTextHtml(b.text, {pb:false})}</span>`;
    if (b.kind === 'annotation') return `<span class="punctuated-note" title="来源中的注释，单独显示">〈${content}〉</span>`;
    if (b.kind === 'glyph') return `<span class="chip kr" title="来源缺字或私用区字，保留编码，显示可能不一致">${content}</span>`;
    if (b.kind === 'heading') return `<h2>${content}</h2>`;
    if (b.kind === 'editorial') return `<aside class="collection-note">${content}</aside>`;
    return content;
  }).join('');
}

function locatePunctuatedSource(params) {
  if (!/^\d+$/.test(String(params.block ?? ''))) return;
  const target=document.getElementById(`punctuated-block-${params.block}`);
  if (!target) return;
  target.classList.add('punctuated-location');target.scrollIntoView({block:'center'});
  const span=target.querySelector('[data-reading-source]');
  const start=Number(params.start),end=Number(params.end);
  if (!span || !Number.isInteger(start) || !Number.isInteger(end) || start<0 || end<=start) return;
  const chars=Array.from(span.textContent);
  if (end>chars.length) return;
  // Python source offsets count Unicode code points; DOM Range counts UTF-16.
  const first=chars.slice(0,start).join('').length,last=chars.slice(0,end).join('').length;
  const walker=document.createTreeWalker(span,NodeFilter.SHOW_TEXT);
  const range=document.createRange();let offset=0,opened=false,node;
  while((node=walker.nextNode())){
    const limit=offset+node.textContent.length;
    if(!opened && first<limit){range.setStart(node,first-offset);opened=true;}
    if(opened && last<=limit){
      range.setEnd(node,last-offset);
      const selection=window.getSelection();selection.removeAllRanges();selection.addRange(range);
      window.scrollBy(0,range.getBoundingClientRect().top-window.innerHeight/3);
      break;
    }
    offset=limit;
  }
}

function punctuatedInventory(b) {
  const keys = ['downloaded','readable','viewable_count','source_text_count','source_issue_count','unavailable_count'];
  const known = keys.every(k => Number.isInteger(b[k]) && b[k] >= 0)
    && b.viewable_count === b.readable + b.source_text_count + b.source_issue_count
    && b.downloaded === b.viewable_count + b.unavailable_count;
  if (!known) return `<span>已下载 ${num(b.downloaded)} 篇 · 标点正文 ${num(b.readable)} 篇</span><small>其余篇目的阅读状态见目录</small>`;
  const details = [`标点正文 ${num(b.readable)}`];
  if (b.source_text_count) details.push(`短篇或低标点 ${num(b.source_text_count)}`);
  if (b.source_issue_count) details.push(`来源排版异常 ${num(b.source_issue_count)}`);
  if (b.unavailable_count) details.push(`转换未完成 ${num(b.unavailable_count)}`);
  return `<span>已下载 ${num(b.downloaded)} 篇 · 可打开 ${num(b.viewable_count)} 篇</span><small>${details.join(' · ')}</small>`;
}

async function punctuatedView(pageId, params, revision) {
  const view = $('#view');
  const boot = window.YindeBoot ? await window.YindeBoot : {mode:'api'};
  if (revision !== routeRevision) return;
  if (boot.mode !== 'api') {
    view.innerHTML = '<h1>标点本书库</h1><p>标点本保存在本机项目内，请使用桌面版阅读。</p>';
    return;
  }
  if (pageId) {
    const d = await api(`/api/punctuated/chapter/${encodeURIComponent(pageId)}`);
    if (revision !== routeRevision) return;
    const rendering = d.blocks.find(b => b.kind === 'provenance');
    view.innerHTML = `<a class="backlink" href="#/punctuated?${qs({book:d.book_id})}">← 返回本书目录</a>
      <div class="page-heading"><p class="eyebrow">维基文库录入本 · 独立版本</p><h1>${esc(d.title)}</h1></div>
      <p class="collection-note">按该来源的标点显示，未逐字校勘；与原收录底本可能有异文。顶部可切换原文与简体阅读。</p>
      <p>${punctuatedSourceLink(d.source_url,'来源固定版本')} · ${punctuatedSourceLink(d.history_url,'贡献者与修订历史')} · ${punctuatedSourceLink(d.license_url,'CC BY-SA 4.0')}</p>
      ${['insufficient_punctuation','partial_render'].includes(d.status) ? `<p class="collection-note">${esc(d.reason)}。以下为原页可读取的文字，未计入标点阅读篇数。</p>` : ''}
      ${['readable','insufficient_punctuation','partial_render'].includes(d.status) ? `<div class="read-body punctuated-body">${punctuatedText(d.blocks)}</div>` :
        `<div class="empty">原文件已下载，当前阅读转换失败：${esc(d.reason)}。可打开上方固定来源查看。</div>`}
      <details class="maintenance"><summary>版本与来源</summary><p>${esc(d.attribution)}</p>
        <p>修订 ${num(d.revision_id)} · ${esc(d.revision_timestamp)} · 校验 ${esc(d.source_sha256)}</p>
        ${rendering ? `<p>按维基文库官方渲染快照读取 · ${esc(rendering.rendered_at)}。正文修订固定，模板按当时服务器版本展开；快照校验 ${esc(rendering.html_sha256)}。</p>${rendering.warnings?.length ? `<p>${rendering.warnings.map(w=>esc(typeof w === 'string' ? w : JSON.stringify(w))).join('；')}</p>` : ''}` : ''}
        ${d.reason ? `<p>${esc(d.reason)}</p>` : ''}</details>`;
    locatePunctuatedSource(params);
    return;
  }
  if (!params.book) {
    const d = await api('/api/punctuated/books');
    if (revision !== routeRevision) return;
    view.innerHTML = `<a class="backlink" href="#/books">← 原收录书库</a><div class="page-heading"><h1>标点本书库</h1>
      <p>维基文库独立版本 · 保留原有句读</p></div>
      <p class="collection-note">${d.search_index_ready ? '此版本已接入主检索与本地语义检索。' : '新增版本可在各书内查找原文词句，尚未纳入语义搜索。'}下载篇数不等于已校勘卷数；原底本的缺卷状态保持不变。</p>
      ${!d.available ? '<div class="empty">标点本正在准备，尚未完成入库。</div>' :
        `<div class="book-grid">${d.books.map(b => `<a class="book-card" href="#/punctuated?${qs({book:b.book_id})}">
          <div class="t">《${esc(b.title)}》</div><div class="punctuated-inventory">${punctuatedInventory(b)}</div></a>`).join('')}</div>`}`;
    return;
  }
  const d = await api(`/api/punctuated/chapters?book=${encodeURIComponent(params.book)}&q=${encodeURIComponent(params.q || '')}&page=${encodeURIComponent(params.page || 1)}`);
  if (revision !== routeRevision) return;
  const name = esc(d.book.title), current = num(d.page);
  view.innerHTML = `<a class="backlink" href="#/punctuated">← 标点本书库</a><div class="page-heading"><h1>《${name}》</h1>
    <div class="punctuated-inventory">${punctuatedInventory(d.book)}</div></div>
    <p class="collection-note">这里查找标点正文的连续词句，支持简繁输入。短篇、低标点和来源排版异常的篇目可从目录打开。</p>
    <form id="punctuatedSearch" class="controls"><input id="punctuatedQuery" aria-label="查找本书原文" maxlength="100" value="${esc(params.q || '')}" placeholder="查找本书原文词句">
      <button class="btn primary" type="submit">查找</button><a class="btn" href="#/punctuated?${qs({book:params.book})}">全部篇目</a></form>
    <div class="file-list">${d.rows.map(r => `<a class="file-item" href="#/punctuated/${num(r.page_id)}"><strong>${esc(r.title)}</strong>
      <small>${r.status === 'readable' ? '有标点 · 未逐字校勘' : r.status === 'insufficient_punctuation' ? '可打开 · 短篇或低标点' : r.status === 'partial_render' ? '可打开 · 来源排版异常' : '已下载 · 阅读转换未完成'}</small></a>`).join('') || '<p>当前标点正文未找到该词句。</p>'}</div>
    <div class="controls">${current > 1 ? `<a class="btn" href="#/punctuated?${qs({book:params.book,q:params.q,page:current-1})}">上一页</a>` : ''}
      <span>共 ${num(d.total)} 篇 · 第 ${current} 页</span>
      ${current*d.page_size < d.total ? `<a class="btn" href="#/punctuated?${qs({book:params.book,q:params.q,page:current+1})}">下一页</a>` : ''}</div>`;
  $('#punctuatedSearch').addEventListener('submit', e => {
    e.preventDefault(); location.hash = '#/punctuated?' + qs({book:params.book,q:$('#punctuatedQuery').value.trim()});
  });
}
