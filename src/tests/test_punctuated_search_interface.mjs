import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const nodes=new Map(), handlers={};
for(const id of ['view','punctuatedSearchForm','punctuatedSearchQuery','punctuatedSearchBook','punctuatedSearchStrategy','punctuatedSearchResult'])
  nodes.set('#'+id,{innerHTML:'',textContent:'',value:'',setAttribute(){},querySelectorAll(){return [];},addEventListener(type,fn){handlers[id+':'+type]=fn;}});
const context=vm.createContext({console,URL,URLSearchParams,AbortController,DOMException,
  window:{addEventListener(){}},document:{querySelector:s=>nodes.get(s)||null,querySelectorAll:()=>[]},
  localStorage:{getItem(){return null;}},location:{hash:'#/search'},history:{replaceState(){}}});
for(const file of ['engine/zh_table.js','engine/zh.js','engine/dual_text.js','punctuated.js','punctuated_search.js','app.js']){
  let source=fs.readFileSync(new URL('../frontend/'+file,import.meta.url),'utf8');
  if(file==='app.js')source=source.replace(/\nroute\(\);\nheaderStats\(\);\nfootBooks\(\);\s*$/,'\n');
  vm.runInContext(source,context);
}
const run=s=>vm.runInContext(s,context);
const ready={ready:true,manifest:{books:1,pages:1,book_chunks:{a:1}}};
const pending=[];
run('getBooks=async()=>[{book_id:"a",title:"甲書"},{book_id:"b",title:"尚无标点"}];globalThis.install=f=>{api=f}')(async path=>{
  if(path.endsWith('/status'))return ready;
  return new Promise((resolve,reject)=>pending.push({path,resolve,reject}));
});
assert.equal(await run('punctuatedSearchView({source:"original"},0)'),false);
assert.equal(await run('punctuatedSearchView({edition:"sbck"},0)'),false);
await run('searchView({},0,location.hash)');
assert.ok(nodes.get('#view').innerHTML.includes('搜索史料'));
assert.ok(!nodes.get('#view').innerHTML.includes('id="punctuatedSearchStrategy"'));
assert.ok(nodes.get('#view').innerHTML.includes('尚无标点（暂无标点版）'));
const fixture=label=>({query:'甲乙',book:'a',mode:'keyword',page:1,page_size:20,total:1,book_available:true,elapsed_ms:1,hits:[{
  chunk:{page_id:9,chunk_id:2,book_id:'a',book_title:label,title:'卷一',layer:'annotation',status:'partial_render',reason:'源标记',
    text_orig:'甲乙丙，國語。<script>',parts:[{block_index:3,start:0,end:10}],source_url:'javascript:bad()',history_url:'https://zh.wikisource.org/a',
    license_url:'https://creativecommons.org/licenses/by-sa/4.0/',attribution:'署名',revision_id:8,source_sha256:'hash'}}]});
run('globalThis.put=x=>{globalThis.result=x}')(fixture('<img>'));
const html=run('punctuatedSearchResults(result)');
assert.ok(html.includes('back=%23%2Fsearch'));
for(const expected of ['&lt;img&gt;','甲乙丙，国语。','来源注释','来源排版异常','#/punctuated/9?block=3','source=original','未确认逐句对应'])assert.ok(html.includes(expected),expected);
assert.ok(!html.includes('href="javascript:'));assert.ok(!html.includes('<script>'));
assert.ok(!html.includes('语义相似分'));
assert.ok(!html.includes('复制原文引用'));
const splitLink=new URL(run('punctuatedHitLink({...result.hits[0].chunk, parts:[{block_index:3,start:1,end:3},{block_index:4,start:0,end:2}]}, "#/search?q=甲乙")').slice(1),'http://localhost');
assert.deepEqual(JSON.parse(splitLink.searchParams.get('ranges')),[[3,1,3],[4,0,2]]);
assert.equal(splitLink.searchParams.get('back'),'#/search?q=甲乙');
assert.equal(splitLink.searchParams.get('rev'),'8');
assert.equal(splitLink.searchParams.get('sha'),'hash');
const activityFixture=fixture('甲書');
activityFixture.hits[0].evidence_audit={status:'clues_present',activity_evidence:{person:'王某',
  identity:{quote:'文皇帝諱某',source_url:'https://zh.wikisource.org/a'},
  citations:{当时行动:{quote:'詔諸郡募兵',page_id:9,revision_id:8,source_sha256:'hash',ranges:[[3,2,8]]}}}};
run('globalThis.put=x=>{globalThis.result=x}')(activityFixture);
const activityHtml=run('punctuatedSearchResults(result)');
assert.ok(activityHtml.includes('王某在这次围城中的行动'));
assert.ok(activityHtml.includes('当时行动'));
assert.ok(activityHtml.includes('本纪固定版本'));
assert.ok(activityHtml.includes('#/punctuated/9?block=3&start=2&end=8'));
const experiment={...fixture('甲書'),mode:'relation',candidate_counts:{semantic:1,keyword:0},
  query_plan:{clues:[{text:'<script>'}]},relation_experiment:{selection:'baseline_fallback',candidate_scan:{truncated:true}}};
run('globalThis.put=x=>{globalThis.result=x}')(experiment);
const experimentalHtml=run('punctuatedSearchResults(result)');
assert.ok(experimentalHtml.includes('本次使用原有语义＋规则扩展结果'));
assert.ok(experimentalHtml.includes('达到本次扫描上限'));
assert.ok(experimentalHtml.includes('&lt;script&gt;'));assert.ok(!experimentalHtml.includes('<script>'));
const evidence={...fixture('甲書'),mode:'evidence',candidate_counts:{semantic:60,keyword:20},
  evidence_pipeline:{version:'context-evidence-v2',audit_clues_present:0,candidate_counts:{context_scored:57},
    candidate_scan:{truncated:false},grounded_fallback:{hypothesis:'甲乙'}},
};
evidence.hits[0].evidence_audit={status:'insufficient',missing:['缺少<script>线索'],
  causal_event_explanations:[{explanation:'待核的原因摘要'}]};
const hitChunk=evidence.hits[0].chunk;
hitChunk.source_url='https://zh.wikisource.org/w/index.php?oldid=8';
evidence.hits[0].context_chunks=[
  {...hitChunk,chunk_id:3,text_orig:'续段正文',parts:[{block_index:4,start:0,end:4}]},
  {...hitChunk,chunk_id:1,text_orig:'此前正文',parts:[{block_index:2,start:1,end:5}]},
  {...hitChunk,chunk_id:4,revision_id:7,text_orig:'另一修订内容'},
];
evidence.hits[0].evidence_focus={selected:true,text:'甲乙丙，國語。<script>'};
run('globalThis.put=x=>{globalThis.result=x}')(evidence);
const evidenceHtml=run('punctuatedSearchResults(result)');
assert.ok(evidenceHtml.includes('尚未确认有片段能回答这个问题'));
assert.ok(evidenceHtml.includes('<h2>史料片段</h2>'));
assert.ok(!evidenceHtml.includes('<h2>可能回答问题</h2>'));
assert.ok(evidenceHtml.includes('data-pipeline-version="context-evidence-v2"'));
assert.ok(evidenceHtml.includes('<summary>检索线索</summary>'));
assert.ok(evidenceHtml.includes('规则未识别不代表原文没有记载'));
assert.ok(evidenceHtml.includes('问题尚未完整解析'));
assert.ok(evidenceHtml.includes('重点比较的原文'));
assert.ok(evidenceHtml.includes('缺少&lt;script&gt;线索'));assert.ok(!evidenceHtml.includes('<script>'));
assert.match(evidenceHtml,/href="https:\/\/zh\.wikisource\.org\/w\/index\.php\?oldid=8"[^>]*>固定版本出处<\/a>[\s\S]*<summary>展开相邻原文<\/summary>/);
assert.match(evidenceHtml,/data-citation="&quot;甲乙丙，國語。&lt;script&gt;[^\"]*来源注释 · 修订 8[^\"]*oldid=8[^\"]*">复制原文引用/);
assert.match(evidenceHtml,/<summary>展开相邻原文<\/summary>[\s\S]*此前正文[\s\S]*命中片段[\s\S]*续段正文/);
assert.match(evidenceHtml,/#\/punctuated\/9\?block=2&start=1&end=5&back=%23%2Fsearch/);
assert.ok(!evidenceHtml.includes('另一修订内容'));
assert.ok(!evidenceHtml.includes('待核的原因摘要'));
await run('punctuatedSearchView({strategy:"relation"},0,true)');
assert.ok(!nodes.get('#view').innerHTML.includes('id="punctuatedSearchStrategy"'));
assert.equal(run('unifiedSearchStrategy("甲公何时去世？")'),'evidence');
assert.equal(run('unifiedSearchStrategy("甲公於何處即位")'),'evidence');
assert.equal(run('unifiedSearchStrategy("甲公即位")'),'keyword');
assert.equal(run('unifiedSearchStrategy("甲公")'),'keyword');
assert.equal(run('unifiedSearchStrategy("甲公", "relation")'),'relation');
await run('punctuatedSearchView({},0)');
nodes.get('#punctuatedSearchQuery').value='甲公何时去世';
nodes.get('#punctuatedSearchBook').value='a';
const question=handlers['punctuatedSearchForm:submit']({preventDefault(){}});
const request=new URL(pending[0].path,'http://localhost');
assert.equal(request.searchParams.get('strategy'),'evidence');
assert.equal(request.searchParams.get('book'),'a');
const possible=fixture('可能结果').hits[0];
possible.evidence_audit={status:'clues_present',missing:[]};
pending.shift().resolve({...evidence,hits:[...evidence.hits,possible]});
await question;
const grouped=nodes.get('#punctuatedSearchResult').innerHTML;
assert.match(grouped,/<h2>史料片段<\/h2>[\s\S]*甲書[\s\S]*可能结果/);
assert.ok(!grouped.includes('<h2>可能回答问题</h2>'));
assert.ok(!grouped.includes('尚未确认有片段能回答这个问题'));
nodes.get('#punctuatedSearchQuery').value='甲公出兵围攻城池';
const expansion=handlers['punctuatedSearchForm:submit']({preventDefault(){}});
const empty={...fixture(''), hits:[], total:0, book_available:true};
const literal=pending.shift();
assert.equal(new URL(literal.path,'http://localhost').searchParams.get('strategy'),'keyword');
literal.resolve(empty);
await new Promise(setImmediate);
const expanded=pending.shift();
assert.equal(new URL(expanded.path,'http://localhost').searchParams.get('strategy'),'evidence');
assert.equal(new URL(expanded.path,'http://localhost').searchParams.get('book'),'a');
expanded.resolve(evidence);
await expansion;
assert.ok(nodes.get('#punctuatedSearchResult').innerHTML.includes('重点比较的原文'));
run('globalThis.empty=()=>({mode:"keyword",total:0,book_available:true})');
assert.equal(run('phraseExpansion("甲公出兵围攻城池", "keyword", 1, empty())'),'');
assert.equal(run('phraseExpansion("甲公出兵围攻城池", null, 2, empty())'),'');
assert.equal(run('phraseExpansion("甲公出兵围攻城池", null, 1, {...empty(),total:1,hits:[]})'),'');
assert.equal(run('phraseExpansion("甲公出兵围攻城池", null, 1, {...empty(),biographies:[{}]})'),'');
assert.equal(run('phraseExpansion("「甲公出兵围攻城池」", null, 1, empty())'),'');
const phrase='甲公出兵围攻城池';
const quoted=fixture('引语原文');
quoted.hits[0].chunk.text_orig='乙公曰：「昔甲公出兵，圍攻城池。此所謂';
run('globalThis.putQuoted=x=>{globalThis.quoted=x}')(quoted);
assert.equal(run('phraseExpansion("甲公出兵围攻城池", null, 1, quoted)'),'quoted');
assert.equal(run('phraseExpansion("甲公出兵围攻城池", null, 1, {...quoted,total:2})'),'');
assert.ok(run('phraseOnlyInQuotes("甲公出兵围攻城池","乙公曰：「忽聞『甲公出兵圍攻城池』。」")'));
assert.ok(!run('phraseOnlyInQuotes("甲公出兵围攻城池","甲公出兵，圍攻城池。乙公曰：「昔甲公出兵圍攻城池。」")'));
assert.ok(!run('phraseOnlyInQuotes("甲公出兵围攻城池","曰：「甲公出兵」「圍攻城池。」")'));
nodes.get('#punctuatedSearchQuery').value=phrase;
const quotedSearch=handlers['punctuatedSearchForm:submit']({preventDefault(){}});
pending.shift().resolve(quoted);
await new Promise(setImmediate);
assert.equal(new URL(pending[0].path,'http://localhost').searchParams.get('strategy'),'evidence');
pending.shift().resolve(evidence);
await quotedSearch;
assert.match(nodes.get('#punctuatedSearchResult').innerHTML,/<summary>查看字面匹配<\/summary>[\s\S]*引语原文/);
const unavailableExpansion=handlers['punctuatedSearchForm:submit']({preventDefault(){}});
pending.shift().resolve(quoted);
await new Promise(setImmediate);
pending.shift().reject(new Error('本机模型未就绪'));
await unavailableExpansion;
assert.ok(nodes.get('#punctuatedSearchResult').innerHTML.includes('引语原文'));
assert.ok(nodes.get('#punctuatedSearchResult').innerHTML.includes('相关史料补查暂不可用'));
nodes.get('#punctuatedSearchQuery').value='旧查询';
const first=handlers['punctuatedSearchForm:submit']({preventDefault(){}});
nodes.get('#punctuatedSearchQuery').value='新查询';
const second=handlers['punctuatedSearchForm:submit']({preventDefault(){}});
assert.equal(pending.length,2);assert.ok(pending[1].path.includes(encodeURIComponent('新查询')));
assert.equal(new URL(pending[1].path,'http://localhost').searchParams.get('strategy'),'keyword');
const [oldReply,newReply]=pending.splice(0);
newReply.resolve(fixture('新结果'));await second;
oldReply.resolve(fixture('旧结果'));await first;
assert.ok(nodes.get('#punctuatedSearchResult').innerHTML.includes('新结果'));
assert.ok(!nodes.get('#punctuatedSearchResult').innerHTML.includes('旧结果'));
nodes.get('#punctuatedSearchQuery').value='甲公出兵围攻城池';
const obsolete=handlers['punctuatedSearchForm:submit']({preventDefault(){}});
const obsoleteLiteral=pending.shift();
nodes.get('#punctuatedSearchQuery').value='新查询';
const current=handlers['punctuatedSearchForm:submit']({preventDefault(){}});
pending.shift().resolve(fixture('新结果'));
await current;
obsoleteLiteral.resolve(empty);
await obsolete;
assert.equal(pending.length,0);
assert.ok(nodes.get('#punctuatedSearchResult').innerHTML.includes('新结果'));
run('globalThis.install=f=>{api=f}')(async()=>({ready:false,reason:'索引过期'}));
await run('punctuatedSearchView({},0)');
assert.ok(nodes.get('#view').innerHTML.includes('索引过期'));assert.ok(nodes.get('#view').innerHTML.includes('source=original'));
run('globalThis.install=f=>{api=f}')(async()=>{throw Error('offline');});
assert.equal(await run('punctuatedSearchView({},0)'),true);
assert.ok(nodes.get('#view').innerHTML.includes('标点版检索服务暂不可用'));
console.log('PASS punctuated primary search: independent edition, safe provenance, literal source, fallback, race, default routing');
