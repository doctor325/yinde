/* 在 Node 里按浏览器的加载方式跑引擎脚本（仅供一致性验证，不随站点发布）。
 *
 * 引擎是**经典脚本**（挂 window.YindeEngine，不是 ES module）：这样
 * GitHub Pages 不必关心 .mjs 的 MIME、也不需要打包器，浏览器直接 <script> 引入。
 * Node 这边用 vm 造一个带 window 的上下文，按同样顺序执行同一批文件——
 * 验证的就是浏览器将要跑的那份代码，不是另写一份。
 */
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";

const ENGINE_DIR = new URL("../../frontend/engine/", import.meta.url);

// 加载顺序 = index.html 里的 <script> 顺序。新增引擎文件时必须同步这里。
export const ENGINE_FILES = [
  "zh_table.js",
  "zh.js",
  "dual_text.js",
  "corpus.js",
  "engine.js",
  "entities.js",
  "question.js",
  "query_expansion.js",
  "ranking.js",
  "result_block.js",
  "aggregate.js",
  "retrieve.js",
  "static_api.js",
];

export function loadEngine(files = ENGINE_FILES) {
  const ctx = vm.createContext({ window: {}, console });
  for (const f of files) {
    const src = fs.readFileSync(new URL(f, ENGINE_DIR), "utf8");
    vm.runInContext(src, ctx, { filename: "engine/" + f });
  }
  return ctx.window.YindeEngine;
}

/** 读打包语料（列式 + 字典编码），返回 {columns, dicts, rows}。
 *  这里只做最小解码：字典列换成原值，够验证用。正式的载入逻辑在 engine/corpus.js。 */
export function loadCorpus(path) {
  const d = JSON.parse(fs.readFileSync(path, "utf8"));
  const { columns, dict_columns: dictCols, dicts, rows } = d;
  const isDict = new Set(dictCols);
  return {
    format: d.format,
    columns,
    dicts,
    rows,
    index: Object.fromEntries(columns.map((c, i) => [c, i])),
    /** 与 corpus.js 的 Corpus.cell 同一口径：字典列的 -1 还原成 null。 */
    get(row, col) {
      const v = row[columns.indexOf(col)];
      if (!isDict.has(col)) return v;
      return v >= 0 ? dicts[col][v] : null;
    },
  };
}

/** 按浏览器的加载方式构造一个 Corpus：与 boot.js 读到的东西相同。
 *  dir 是**文件系统路径**（不需要 file:// URL）。 */
export function loadStaticData(dir, NS = loadEngine()) {
  const read = (f) => JSON.parse(fs.readFileSync(path.join(dir, f), "utf8"));
  // sections.json 可缺省：旧的数据目录里没有它（与 boot.js 的 loadJsonOr 同口径）。
  const sections = fs.existsSync(path.join(dir, "sections.json"))
    ? read("sections.json") : [];
  return new NS.Corpus(read("corpus.json"), read("books.json"),
                       read("files.json"), sections);
}

/** 按浏览器的方式启动 boot.js，返回它判定出的模式与它写进 DOM 的文字。
 *
 *  boot.js 是浏览器专有的（要 document / fetch），所以不在 ENGINE_FILES 里，
 *  引擎一致性对拍覆盖不到它。但「本站不含真实史料，搜不到真实人名是正常的」
 *  这条提示只由它发出 —— 历史上正是缺这句话，才有人把公开演示站的空结果
 *  误读成「搜索引擎有问题」。所以单独给它一个冒烟检查。
 *
 *  dir 是数据目录的**文件系统路径**；apiUp=true 时假装本地 API 在跑。
 */
export function loadSiteBoot(dir, { apiUp = false } = {}) {
  const els = {};
  const mk = (id) => (els[id] = {
    id, innerHTML: "", className: "", hidden: true,
    classList: { add() {}, remove() {} },
  });
  const banner = mk("modeBanner");
  mk("view");
  mk("statsMini");
  const footer = { innerHTML: "" };

  const document = {
    getElementById: (id) => els[id] || null,
    querySelector: (sel) => (sel === "footer" ? footer : null),
    body: { classList: { add() {}, remove() {} } },
    addEventListener() {},
  };

  const mkRes = (status, body) => ({
    ok: status >= 200 && status < 300,
    status,
    json: async () => JSON.parse(body),
    text: async () => body,
  });
  const fetchStub = async (url) => {
    const clean = String(url).replace(/^\//, "");
    if (apiUp && clean === "api/stats") {
      return mkRes(200, JSON.stringify({ books: 1, files: 1, records: 1 }));
    }
    try {
      return mkRes(200, fs.readFileSync(path.join(dir, clean), "utf8"));
    } catch (e) {
      return mkRes(404, "not found");   // 演示站上 /api/* 就是 404，正是降级信号
    }
  };

  const ctx = vm.createContext({ window: {}, document, fetch: fetchStub, console });
  for (const f of ENGINE_FILES) {
    vm.runInContext(fs.readFileSync(new URL(f, ENGINE_DIR), "utf8"), ctx,
                    { filename: "engine/" + f });
  }
  const boot = new URL("../../frontend/boot.js", import.meta.url);
  vm.runInContext(fs.readFileSync(boot, "utf8"), ctx, { filename: "boot.js" });
  return ctx.window.YindeBoot.then((r) => ({
    mode: r.mode, dir: r.dir, kind: r.kind || null, error: r.error || null,
    banner: banner.innerHTML, bannerClass: banner.className,
    bannerHidden: banner.hidden, footer: footer.innerHTML,
  }));
}

/** 码位数（不是 UTF-16 单元数），对应 Python 的 len()。 */
export const cplen = (s) => [...s].length;

/** 转成 utf-16-be 字节，用于跨语言摘要。孤立代理项原样写出（Python 用 surrogatepass）。 */
export function utf16beBytes(s) {
  return Buffer.from(s, "utf16le").swap16();
}
