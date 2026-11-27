// Run: node src/tests/test_search_history.mjs
// Exercises the real history helpers in app.js without network, corpus, or DOM.
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const app = fs.readFileSync(new URL("../frontend/app.js", import.meta.url), "utf8")
  .replace(/\nroute\(\);\nheaderStats\(\);\nfootBooks\(\);\s*$/, "\n");
new vm.Script(fs.readFileSync(new URL("../frontend/punctuated_search.js", import.meta.url), "utf8"));

function harness({ desktop = false, serverHistory = [] } = {}) {
  const store = new Map();
  const gets = [];
  const posts = [];
  const context = vm.createContext({
    URLSearchParams, AbortController, DOMException, console,
    location: { hash: "#/search" },
    document: { querySelector: () => null, querySelectorAll: () => [] },
    localStorage: {
      getItem: key => store.has(key) ? store.get(key) : null,
      setItem: (key, value) => store.set(key, String(value)),
    },
    history: { replaceState() {} },
    fetch: async (path, options = {}) => {
      assert.equal(path, "/api/desktop/search-history");
      if ((options.method || "GET") === "GET") {
        gets.push(path);
        return { ok: true, json: async () => ({ history: serverHistory }) };
      }
      posts.push(JSON.parse(options.body));
      return { ok: true, json: async () => ({ saved: true }) };
    },
    window: {
      YindeBoot: Promise.resolve({ mode: "api" }),
      addEventListener() {},
      confirm: () => true,
    },
  });
  if (desktop) context.window.YindeDesktopMode = "orig";
  vm.runInContext(app, context);
  const run = code => vm.runInContext(code, context);
  return { run, store, gets, posts, context };
}

{
  const h = harness();
  const list = JSON.parse(h.run(`JSON.stringify(sanitizeSearchHistory([
    {q:" 甲 ",kind:"punctuated",at:1},
    {q:"",kind:"punctuated"},
    {q:"乙",kind:"unknown"},
    {q:"甲",kind:"punctuated",at:2},
    ...Array.from({length:120},(_,i)=>({q:"q"+i,kind:"punctuated",at:i+3}))
  ]))`));
  assert.equal(list.length, 100);
  assert.equal(list[0].q, "甲");
  assert.equal(list[0].at, 1);
  assert.equal(list.filter(x => x.q === "甲").length, 1);
  console.log("PASS sanitization: trim, dedupe, cap 100, drop invalid entries");
}

{
  const h = harness();
  await h.run(`recordSearchHistory({q:"陈寿的字",kind:"punctuated",book:"",bookLabel:"全部标点史书",strategy:"evidence",at:1700000000000})`);
  let stored = JSON.parse(h.store.get("historyai.searchHistory"));
  assert.equal(stored.length, 1);
  assert.equal(stored[0].q, "陈寿的字");
  assert.equal(stored[0].strategy, "evidence");
  assert.equal(h.run("window.YindeSearchHistory[0].q"), "陈寿的字");
  await h.run(`recordSearchHistory({q:"陈寿的字",kind:"punctuated",book:"",bookLabel:"全部标点史书",strategy:"evidence",at:1700000001000})`);
  stored = JSON.parse(h.store.get("historyai.searchHistory"));
  assert.equal(stored.length, 1);
  assert.equal(stored[0].at, 1700000001000);
  console.log("PASS web mode: localStorage persists and a repeat moves to the top");
}

{
  const h = harness();
  const punct = h.run(`historyHref({q:"陈寿的字",kind:"punctuated",book:"",edition:"",strategy:"evidence"})`);
  assert.ok(punct.startsWith("#/search?q="));
  assert.ok(punct.includes("strategy=evidence"));
  const orig = h.run(`historyHref({q:"陈寿的字",kind:"original",book:"jinshu",edition:"sbck",strategy:"original"})`);
  assert.ok(orig.includes("source=original"));
  assert.ok(orig.includes("book=jinshu"));
  assert.ok(orig.includes("edition=sbck"));
  console.log("PASS links preserve strategy and original edition");
}

{
  const item = { q: "旧查询", kind: "punctuated", book: "", bookLabel: "全部标点史书",
                 edition: "", strategy: "keyword", at: 1700000000000 };
  const h = harness({ desktop: true, serverHistory: [item] });
  const loaded = await h.run("loadSearchHistory()");
  assert.equal(loaded.length, 1);
  assert.equal(loaded[0].q, "旧查询");
  assert.equal(h.gets.length, 1);
  await h.run(`recordSearchHistory({q:"新查询",kind:"original",book:"jinshu",bookLabel:"《晋书》",edition:"sbck",strategy:"original",at:1700000001000})`);
  await h.run("searchHistorySave");
  assert.equal(h.posts.length, 1);
  const posted = h.posts[0].history;
  assert.equal(posted.length, 2);
  assert.equal(posted[0].q, "新查询");
  assert.equal(posted[0].kind, "original");
  assert.equal(posted[0].edition, "sbck");
  assert.equal(posted[1].q, "旧查询");
  const html = h.run("searchHistoryHtml()");
  assert.ok(html.includes("最近搜索"));
  assert.ok(html.includes("新查询"));
  assert.ok(html.includes("清空历史"));
  console.log("PASS desktop mode: GET restores, POST saves canonical entries, panel renders");
}

{
  const h = harness({ desktop: true });
  await h.run("loadSearchHistory()");
  await Promise.all([
    h.run(`recordSearchHistory({q:"并发一",kind:"punctuated",book:"",bookLabel:"全部标点史书",edition:"",strategy:"keyword",at:1700000000001})`),
    h.run(`recordSearchHistory({q:"并发二",kind:"punctuated",book:"",bookLabel:"全部标点史书",edition:"",strategy:"keyword",at:1700000000002})`),
  ]);
  await h.run("searchHistorySave");
  const posted = h.posts.at(-1).history;
  assert.ok(posted.some(x => x.q === "并发一"));
  assert.ok(posted.some(x => x.q === "并发二"));
  console.log("PASS concurrent records merge instead of overwriting");
}

{
  const h = harness({ desktop: true });
  await h.run("loadSearchHistory()");
  h.run(`saveSearchHistory([{q:"待清空",kind:"punctuated",book:"",bookLabel:"全部标点史书",edition:"",strategy:"keyword",at:1700000000000}])`);
  await h.run("searchHistorySave");
  h.run("window.clearSearchHistory()");
  await h.run("searchHistorySave");
  assert.equal(h.posts.at(-1).history.length, 0);
  assert.ok(h.run('searchHistoryHtml().includes("hidden")'));
  console.log("PASS clear persists an empty list and hides the panel");
}
