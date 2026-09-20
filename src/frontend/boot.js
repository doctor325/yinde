/* 启动：判定运行模式，静态模式下把数据装进内存。
 *
 * 一个前端，两种模式，零配置
 * --------------------------
 *   · **API 模式**：本地 `python -m api.main` 在跑 → 沿用原来的 `/api/*`，
 *     与第一到第四阶段完全一致，一个字节都不变。
 *   · **静态模式**：`/api/*` 不可用（GitHub Pages 上就是 404）→ 读导出好的
 *     静态 JSON，由 frontend/engine/static_api.js 在浏览器里重放同一组接口。
 *
 * 判定只做一次（探测 `/api/stats`），结果挂在 window.YindeBoot 上：
 * app.js 的 api() 等这个 promise，之后按 mode 决定走哪条路。**不做「先试后降级」**
 * 的逐请求回退 —— 那会让每个请求都先撞一次 404，而且把「真 API 报错」和
 * 「没有 API」混为一谈（本地 API 对非法参数返回 400，那是真错误，不该降级）。
 *
 * 数据目录二选一，先 `data/` 后 `data-demo/`
 * ------------------------------------------
 * `data/` 是本地完整语料的导出（**含真实正文，永不入库、永不发布**），
 * `data-demo/` 是随仓库发布的**自制演示数据**（MIT）。本地静态托管时前者存在，
 * 线上只有后者 —— 同一份代码，靠目录是否存在决定，不需要配置开关。
 * 将来数据来源问题解决了，导出一次放进 `data/`，公开版就自动具备完整检索。
 */
(function () {
  "use strict";

  const NS = window.YindeEngine;
  const DATA_DIRS = [
    { dir: "data/", kind: "local", note: "本地导出数据（完整语料）" },
    { dir: "data-demo/", kind: "demo", note: "自制演示数据（非本项目语料）" },
  ];

  /** 探测真 API：能拿到一份像样的 /api/stats 就算在。 */
  async function probeApi() {
    try {
      const r = await fetch("/api/stats", { cache: "no-store" });
      if (!r.ok) return false;
      const j = await r.json();
      // 真正的 /api/stats 一定带 books / files / records 三个数（api/main.py 的
      // _h_stats）。Pages 的 404 页、静态服务器目录列表、代理的登录页都过不了这关。
      return !!(j && typeof j.books === "number" && typeof j.files === "number"
                && typeof j.records === "number");
    } catch (e) {
      return false;   // 网络失败、不是 JSON、跨域被拦 —— 都算没有 API
    }
  }

  /** 找可用的数据目录：stats.json 能取到且是 JSON 才算数。 */
  async function pickDir() {
    for (const cand of DATA_DIRS) {
      try {
        const r = await fetch(cand.dir + "stats.json", { cache: "no-store" });
        if (!r.ok) continue;
        const txt = await r.text();
        JSON.parse(txt);            // Pages 的 404 页可能是 200 的 HTML
        return cand;
      } catch (e) { /* 继续找下一个 */ }
    }
    return null;
  }

  async function loadJson(url) {
    const r = await fetch(url);
    if (!r.ok) throw new Error(`${url} 取不到（HTTP ${r.status}）`);
    return r.json();
  }

  /** 可缺省的数据文件：取不到就用 fallback。
   *
   *  用在**后加的**文件上（sections.json 是第六点一阶段才有的）。已有的数据目录
   *  （比如线上那份、别人 clone 下来的旧 data/）里没有它，硬失败会让整个站点
   *  在「升级了代码、还没重新导出数据」时无法启动。缺了它篇名检索就退化成
   *  「没有篇名数据」，而不是整站打不开。 */
  async function loadJsonOr(url, fallback) {
    try {
      return await loadJson(url);
    } catch (e) {
      return fallback;
    }
  }

  function banner(text, kind) {
    const el = document.getElementById("modeBanner");
    if (!el) return;
    el.className = "modebanner " + (kind || "");
    el.innerHTML = text;
    el.hidden = false;
    document.body.classList.add("static-mode");
  }

  function loadMsg(text) {
    const v = document.getElementById("view");
    if (v) v.innerHTML = `<div class="load">${text}</div>`;
  }

  window.YindeBoot = (async function () {
    if (await probeApi()) {
      return { mode: "api", dir: null, site: null };
    }

    const pick = await pickDir();
    if (!pick) {
      // 没有 API 也没有数据：如实说，不假装能检索。
      banner("静态模式：未找到数据目录（<span class='mono'>data/</span> 或 "
             + "<span class='mono'>data-demo/</span>），检索不可用。", "warn");
      return { mode: "static", dir: null, site: null, error: "静态模式：未找到数据目录" };
    }

    loadMsg(`正在载入${pick.kind === "demo" ? "演示" : ""}语料…`);
    const base = pick.dir;
    const [stats, books, files, bookFiles, corpus, sections] = await Promise.all([
      loadJson(base + "stats.json"), loadJson(base + "books.json"),
      loadJson(base + "files.json"), loadJson(base + "book_files.json"),
      loadJson(base + "corpus.json"),
      // 篇名区间表：后加的文件，旧数据目录里没有（见 loadJsonOr）。
      loadJsonOr(base + "sections.json", []),
    ]);
    const c = new NS.Corpus(corpus, books, files, sections);
    const site = new NS.staticApi.Site({
      corpus: c, books, files, bookFiles, stats,
      // 原文对照是**按需**取：整份 raw 目录几十 MB，不能进启动路径。
      raw: async (fid) => {
        try {
          const r = await fetch(base + "raw/" + fid + ".json");
          return r.ok ? await r.json() : null;
        } catch (e) {
          return null;
        }
      },
    });
    NS.site = site;

    if (pick.kind === "demo") {
      // 这条横幅要说清「边界」和「误判」两件事。
      // 第六阶段就是这么被误判的：在公开站搜「楚庄王」是空的，于是断定搜索引擎
      // 有问题——而真实语料里楚庄王有 41 段。空的原因是本站根本没有真实史料。
      // 光说「不含真实史料」不够，必须点破那个错误推论：搜不到真实人名是正常的。
      banner("公开演示模式 · 本站<strong>不含任何真实史料</strong>，所以"
             + "<strong>搜不到「秦始皇」「楚庄王」这类真实人名是正常的</strong>，"
             + "不是检索故障。检索用的是一份自制的演示样例（MIT，见 "
             + "<span class='mono'>data-demo/</span>）。"
             + "真实史料检索请<a href='https://github.com/doctor325/yinde"
             + "/blob/main/src/README.md'>在本地部署完整版</a>。", "demo");
      // 页脚那行列出的是真实书目，演示模式下它是假的——不换掉就是在误导。
      const foot = document.querySelector("footer");
      if (foot) {
        foot.innerHTML = "本站为公开演示，数据是自制样例（MIT），不含真实史料";
      }
    } else {
      banner("本地静态模式 · 数据来自 <span class='mono'>frontend/data/</span>"
             + "（本机导出，未经过 Python API）。", "local");
    }
    return { mode: "static", dir: base, kind: pick.kind, site };
  })().catch((e) => {
    banner("启动失败：" + String((e && e.message) || e), "warn");
    return { mode: "static", dir: null, site: null,
             error: String((e && e.message) || e) };
  });
})();
