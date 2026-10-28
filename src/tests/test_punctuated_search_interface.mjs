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
  return new Promise(resolve=>pending.push({path,resolve}));
});
assert.equal(await run('punctuatedSearchView({source:"original"},0)'),false);
assert.equal(await run('punctuatedSearchView({edition:"sbck"},0)'),false);
await run('searchView({},0,location.hash)');
assert.ok(nodes.get('#view').innerHTML.includes('查找标点史料'));
assert.ok(nodes.get('#view').innerHTML.includes('尚无标点（暂无标点版）'));
const fixture=label=>({query:'甲乙',book:'a',mode:'keyword',page:1,page_size:20,total:1,book_available:true,elapsed_ms:1,hits:[{
  chunk:{page_id:9,chunk_id:2,book_id:'a',book_title:label,title:'卷一',layer:'annotation',status:'partial_render',reason:'源标记',
    text_orig:'甲乙丙，國語。<script>',parts:[{block_index:3,start:0,end:10}],source_url:'javascript:bad()',history_url:'https://zh.wikisource.org/a',
    license_url:'https://creativecommons.org/licenses/by-sa/4.0/',attribution:'署名',revision_id:8,source_sha256:'hash'}}]});
run('globalThis.put=x=>{globalThis.result=x}')(fixture('<img>'));
const html=run('punctuatedSearchResults(result)');
for(const expected of ['&lt;img&gt;','甲乙丙，国语。','来源注释','来源排版异常','#/punctuated/9?block=3','source=original','未确认逐句对应'])assert.ok(html.includes(expected),expected);
assert.ok(!html.includes('href="javascript:'));assert.ok(!html.includes('<script>'));
assert.ok(!html.includes('语义相似分'));
const experiment={...fixture('甲書'),mode:'relation',candidate_counts:{semantic:1,keyword:0},
  query_plan:{clues:[{text:'<script>'}]},relation_experiment:{selection:'baseline_fallback',candidate_scan:{truncated:true}}};
run('globalThis.put=x=>{globalThis.result=x}')(experiment);
const experimentalHtml=run('punctuatedSearchResults(result)');
assert.ok(experimentalHtml.includes('本次使用原有语义＋规则扩展结果'));
assert.ok(experimentalHtml.includes('达到本次扫描上限'));
assert.ok(experimentalHtml.includes('&lt;script&gt;'));assert.ok(!experimentalHtml.includes('<script>'));
await run('punctuatedSearchView({strategy:"relation"},0,true)');
assert.equal(nodes.get('#punctuatedSearchStrategy').value,'relation');
assert.ok(nodes.get('#view').innerHTML.indexOf('value="expanded"')<nodes.get('#view').innerHTML.indexOf('value="relation"'));
await run('punctuatedSearchView({},0)');
nodes.get('#punctuatedSearchQuery').value='旧查询';
const first=handlers['punctuatedSearchForm:submit']({preventDefault(){}});
nodes.get('#punctuatedSearchQuery').value='新查询';
const second=handlers['punctuatedSearchForm:submit']({preventDefault(){}});
assert.equal(pending.length,2);assert.ok(pending[1].path.includes(encodeURIComponent('新查询')));
pending[1].resolve(fixture('新结果'));await second;
pending[0].resolve(fixture('旧结果'));await first;
assert.ok(nodes.get('#punctuatedSearchResult').innerHTML.includes('新结果'));
assert.ok(!nodes.get('#punctuatedSearchResult').innerHTML.includes('旧结果'));
run('globalThis.install=f=>{api=f}')(async()=>({ready:false,reason:'索引过期'}));
await run('punctuatedSearchView({},0)');
assert.ok(nodes.get('#view').innerHTML.includes('索引过期'));assert.ok(nodes.get('#view').innerHTML.includes('source=original'));
run('globalThis.install=f=>{api=f}')(async()=>{throw Error('offline');});
assert.equal(await run('punctuatedSearchView({},0)'),true);
assert.ok(nodes.get('#view').innerHTML.includes('标点版检索服务暂不可用'));
console.log('PASS punctuated primary search: independent edition, safe provenance, literal source, fallback, race, default routing');
