import fs from 'node:fs';
import assert from 'node:assert/strict';
import { loadEngine } from '../scripts/site/loader.mjs';
const data = JSON.parse(fs.readFileSync(0, 'utf8'));
const ns = loadEngine();
const corpus = new ns.Corpus(data.packed, data.books, data.files, data.sections);
for (const c of data.cases) {
  const actual = ns.engine.runSearch(corpus, c.q, { variants: true, book: c.book, pageSize: 100 });
  assert.deepEqual(Array.from(actual.items, r => r.passage_id).sort((a,b)=>a-b), c.expected, c.q);
  assert.equal(actual.mode, 'like');
  assert.deepEqual(JSON.parse(JSON.stringify(actual)), c.result, `all passage fields ${c.q}`);
  const hits = ns.resultBlock.fetchHits(corpus, ns.zh.toTraditional(c.q), c.book || null, null, true, c.q);
  assert.deepEqual(Array.from(hits, r=>r[0]).sort((a,b)=>a-b), c.expected, `block ${c.q}`);
  const blocks = ns.resultBlock.searchResultBlocks(corpus, c.q, {variants:true, book:c.book});
  assert.equal(blocks.hit_total, c.expected.length);
  assert.equal(blocks.exec_mode, 'like');
  assert.deepEqual(JSON.parse(JSON.stringify(blocks)), c.blocks, `all block fields ${c.q}`);
  assert.equal(ns.resultBlock.execMode(corpus, ns.zh.toTraditional(c.q), true, c.q), 'like');
}
const section = ns.resultBlock.sectionHits(corpus, ns.zh.toTraditional(data.sectionQuery), null, null,
  ns.resultBlock.sectionIndex(corpus), true, data.sectionQuery);
assert.equal(section.hits.length, 1, 'original mixed-script section label must survive');
console.log(`JS PASS: ${data.cases.length} keyword/block cases + mixed-script section`);
