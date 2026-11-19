import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

// Use the same form, routing and renderer as the desktop. Replace the DOM
// and wire fetch to the real authenticated loopback API.
const { entry, query } = JSON.parse(readFileSync(0, 'utf8'));
const origin = new URL(entry).origin;
const bootstrap = await fetch(entry, { redirect: 'manual' });
assert.equal(bootstrap.status, 302);
const cookie = bootstrap.headers.get('set-cookie').split(';', 1)[0];
await bootstrap.body?.cancel();

const nodes = {};
let submit;
for (const id of ['view', 'punctuatedSearchForm', 'punctuatedSearchQuery',
  'punctuatedSearchBook', 'punctuatedSearchResult']) {
  nodes['#' + id] = {
    value: '', innerHTML: '', textContent: '',
    setAttribute() {},
    querySelectorAll() { return []; },
    addEventListener(type, fn) {
      if (id === 'punctuatedSearchForm' && type === 'submit') submit = fn;
    },
  };
}
let searchResult;
let requestPath;
const location = { hash: '#/search' };
const context = vm.createContext({
  console, URL, URLSearchParams, AbortController, DOMException, location,
  window: { addEventListener() {} },
  document: { querySelector: selector => nodes[selector] ?? null, querySelectorAll: () => [] },
  localStorage: { getItem() { return null; } },
  history: { replaceState(_, __, path) { location.hash = path; } },
  async fetch(path, options) {
    const url = new URL(path, origin);
    assert.equal(url.origin, origin);
    const response = await fetch(url, { ...options, headers: { Cookie: cookie }, redirect: 'error' });
    if (url.pathname === '/api/punctuated/search') {
      assert.equal(requestPath, undefined, 'Each case submits exactly once');
      requestPath = path;
      searchResult = await response.clone().json();
    }
    return response;
  },
});

for (const name of ['engine/zh_table.js', 'engine/zh.js', 'engine/dual_text.js',
  'punctuated.js', 'punctuated_search.js', 'app.js']) {
  let source = readFileSync(new URL('../../frontend/' + name, import.meta.url), 'utf8');
  if (name === 'app.js') {
    source = source.replace(/\nroute\(\);\nheaderStats\(\);\nfootBooks\(\);\s*$/, '\n');
  }
  vm.runInContext(source, context);
}
await vm.runInContext('searchView({}, 0, location.hash)', context);
assert.equal(typeof submit, 'function', 'Default search should render the punctuated form');
nodes['#punctuatedSearchQuery'].value = query;
const started = performance.now();
await submit({ preventDefault() {} });
const form_ms = performance.now() - started;
const resultNode = nodes['#punctuatedSearchResult'];
assert.equal(searchResult?.mode, 'evidence', searchResult?.error || resultNode.textContent);
assert.equal(searchResult.query, query);
const rendered = [...resultNode.innerHTML.matchAll(/· 片段 ([\d,]+)<\/p>/g)]
  .map(match => Number(match[1].replaceAll(',', '')));
assert.deepEqual(rendered, searchResult.hits.map(hit => hit.chunk.chunk_id));
const evidenceLinks = [...resultNode.innerHTML.matchAll(/<dl class="dissuasion-evidence">([^]*?)<\/dl>/g)]
  .map(group => [...group[1].matchAll(/<dt>([^<]+)<\/dt><dd>([^]*?) · <a href="([^"]+)">定位原文<\/a>/g)]
    .map(([, label, quote, href]) => ({ label, quote, href })));
console.log(JSON.stringify({ requestPath, form_ms, rendered, evidenceLinks, result: searchResult }));
