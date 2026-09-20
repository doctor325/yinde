"""本地只读 API（stdlib http.server，零第三方依赖）。

启动：  python -m api.main [port]          （默认 8600，仅绑 127.0.0.1）
浏览器打开 http://127.0.0.1:8600/

第一阶段路由（保留）：
  /                           前端页面（frontend/ 静态文件）
  /api/stats                  全局统计（含 fts 索引状态）
  /api/books                  书列表（含聚合计数）
  /api/books/<book_id>/files  某书文件列表
  /api/files/<file_id>        文件详情（头部 meta + kind/layer/status 分布）
  /api/files/<file_id>/passages?kind=&layer=&status=&q=&offset=&limit=
  /api/files/<file_id>/raw?start=&end=   原文对照（从 library 读取原件 + 解析覆盖标注）
  /api/passages/<passage_id>  单条记录详情

第二阶段新增：
  /api/search?q=&book=&edition=&page=&page_size=   全文检索（FTS5 + 短词路径）
  /api/passages/<id>/context?before=&after=        真实相邻上下文（非 AI）

第三阶段新增：
  /api/search?q=&level=block&mode=short|standard|long
      level=block（默认）按「史料片段 Result Block」返回；
      level=passage 保留第二阶段逐条命中契约（任务书 §十七）。
  /api/blocks/<id>?direction=before|after|both&count=&before_passage_id=&after_passage_id=
      按需继续读真实相邻正文，用于「展开更多上下文」。

第六点三阶段新增：
  /api/diagnose?q=&book=&edition=&page=&page_size=
      搜不到时的**逐层归因**（语料在册 → 库内正文 → 文本命中 → 索引可达 → 检索
      返回 → 块组装），并给出八维分类与「尚未收录哪些书」。与 tests/recall.py
      共用 search/diagnose.py 一份实现，页面上的说法与测试报告里的说法一致。
  /api/catalog
      语料收录进度（catalog × 库内实况的五态快照，供 #/coverage 的时代进度条）。
      **只此一处**：静态演示模式没有这条路由，真实书单也就进不了发布产物。

第四阶段新增：
  /api/search?q=<自然语言问题>&level=question&mode=standard&text=orig|simplified|both
      自然语言提问 → 问题分析 → 实体/意图识别 → 古代表达扩展 → 召回 → 排序
      → 事件级聚合（events）→ 不同史书对照（sources）。
      **不生成答案**（§2.3）：返回的每一条都是语料原文，text 就是 text_orig。
      参数 `mode=question` 也接受（任务书里是这么写的），等价于 level=question；
      此时显示长度回到默认 standard —— mode 这个参数名在第三阶段已经是
      「显示长度」（short/standard/long），两种含义不能同时占一个参数。
      `text` 控制繁简双轨：orig（默认，只看繁体）/ simplified / both。
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.pipeline import config  # noqa: E402
from scripts.pipeline import volume  # noqa: E402
from scripts.pipeline.kanripo_header import split_header  # noqa: E402
from api import db as api_db  # noqa: E402
from search import context as search_ctx  # noqa: E402
from search import diagnose as search_diagnose  # noqa: E402
from search import dual_text  # noqa: E402
from search import engine as search_engine  # noqa: E402
from search import retrieve as search_retrieve  # noqa: E402
from search import result_block  # noqa: E402

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
ROUTE_RE = {
    "stats": re.compile(r"^/api/stats$"),
    "books": re.compile(r"^/api/books$"),
    "search": re.compile(r"^/api/search$"),
    "diagnose": re.compile(r"^/api/diagnose$"),
    "catalog": re.compile(r"^/api/catalog$"),
    "book_files": re.compile(r"^/api/books/([^/]+)/files$"),
    "file": re.compile(r"^/api/files/(\d+)$"),
    "passages": re.compile(r"^/api/files/(\d+)/passages$"),
    "raw": re.compile(r"^/api/files/(\d+)/raw$"),
    "passage": re.compile(r"^/api/passages/(\d+)$"),
    "context": re.compile(r"^/api/passages/(\d+)/context$"),
    "block_expand": re.compile(r"^/api/blocks/(\d+)$"),
}

# file_id -> (header_len, 原文行列表, decode_err)——懒加载缓存（library 只读）
_raw_cache: dict[int, tuple] = {}


def _raw_lines(file_id: int):
    if file_id in _raw_cache:
        return _raw_cache[file_id]
    conn = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT origin_path FROM files WHERE file_id = ?",
                       (file_id,)).fetchone()
    conn.close()
    if not row:
        raise KeyError(file_id)
    path = config.LIBRARY_DIR / row["origin_path"]
    text = path.read_bytes().decode("utf-8", errors="replace")
    header, _body = split_header(text)
    header_len = len(header.raw_header.splitlines()) if header.raw_header else 0
    lines = text.splitlines()
    _raw_cache[file_id] = (header_len, lines)
    return _raw_cache[file_id]


# 语料目录快照缓存（按 manifest 文件的 mtime 失效）
_catalog_cache: dict[str, tuple] = {}


def _catalog_snapshot() -> dict:
    """语料收录进度（6.3-I）+ 卷级覆盖（6.4-E）：回放审计快照里的 catalog 段 +
    每本书的六态 + 卷数/底本。

    **为什么不在这里重算五态**：判据在 `manifest.merge_catalog()`（coverage_status
    × FTS 索引探针 × `recall_verified` 人工确认位），这里再算一遍就是第二套说法，
    两处迟早不一致 —— 6.2 的教训是「同一件事只留一个实现」。所以本接口读的是
    `data/metadata/corpus_manifest.json`，并把它的生成时间原样带出：页面自己说明
    这份快照有多新，过期的责任落到看见的人头上，而不是被接口悄悄掩掉。

    **卷级数字走 `volume.read_snapshot()`，不在这里从 manifest 里再抠一遍**：
    卷级词汇（complete/partial/unknown + expected/declared/available 三个数）只有
    volume.py 一个实现，搜索诊断（search/diagnose.py）读的是同一个快照 ——
    页面说「《北齊書》35/50 卷」，搜索说「这本书只收到卷三十五」，必须是同一个数。

    快照不存在（没跑过 manifest）时返回 `available: False` + 原因，前端只少画一块
    进度条，不报错。卷级快照单独缺失时同书按 `volume_known: False` 处理，
    照实说「不知道全不全」，**不默认 complete**。
    """
    path = config.METADATA_DIR / "corpus_manifest.json"
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {"available": False,
                "reason": "还没有审计快照 —— 先跑一次 "
                          "python -m scripts.pipeline.manifest"}
    vpath = volume.snapshot_path()
    try:
        vmtime = vpath.stat().st_mtime
    except OSError:
        vmtime = 0.0
    hit = _catalog_cache.get("snap")
    if hit and hit[0] == (mtime, vmtime):
        return hit[1]
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return {"available": False, "reason": f"审计快照读不出来：{e}"}
    cat = d.get("catalog") or {}
    vsnap = volume.read_snapshot()
    # 只挑页面要用的字段：manifest 的整行里还有 gap_files 之类的明细，
    # 原样回放会让每次翻页多传几十 KB 没人看的东西。
    def _vol(bid, title):
        s = volume.scope_of(vsnap, bid, title)
        return {
            "status": s["status"], "expected": s["expected"],
            "declared": s["declared"], "available": s["available"],
            "coverage_ratio": s["coverage_ratio"], "witness": s["witness"],
            "known": s["known"], "note": s["note"],
        }
    books = [{
        "book_id": b.get("book_id"), "title": b.get("title"),
        "era_group": b.get("era_group"), "dynasty": b.get("dynasty"),
        "status": b.get("status"), "coverage_status": b.get("coverage_status"),
        "section_coverage": b.get("section_coverage"),
        "recall_verified": b.get("recall_verified", False),
        "catalog_gap": b.get("catalog_gap", False),
        "indexed": (b.get("indexed") or {}).get("ok"),
        "edition": b.get("edition"),
        "family": b.get("family"),
        "editions": b.get("editions") or [],
        "volume": _vol(b.get("book_id"), b.get("title")),
    } for b in (d.get("books") or [])]
    snap = {
        "available": True,
        "generated_at": d.get("generated_at"),
        "catalog_version": cat.get("catalog_version"),
        "counts": cat.get("counts") or {},
        "in_library": len(books),
        "by_era_group": cat.get("by_era_group") or {},
        "planned": cat.get("planned") or [],
        "not_in_catalog": cat.get("not_in_catalog") or [],
        "on_disk_not_imported": cat.get("on_disk_not_imported") or [],
        "uncatalogued_dirs": cat.get("uncatalogued_dirs") or [],
        # 卷级总账 + 口径说明。note 必须跟着数字走（§28）：页面上的 1013/1275
        # 一旦离开「这是卷号口径、且 5 部书上upstream 本身就残缺」这句话，
        # 就会被读成「我们的语料是残的」或者更糟——「史书里没有」。
        "volume_totals": d.get("volume_totals") or {},
        "volume_available": vsnap["available"],
        "volume_reason": vsnap["reason"],
        "volume_generated_at": vsnap["generated_at"],
        "volume_note": (
            "应有卷数 = 通行本卷数（外证，稳定）；已收卷数 = 库内实测卷号数。"
            "**卷号口径，不是文件数**：上表里的 5 部书上游本身就只数字化到某一卷"
            "为止，那是上游的事实，不是我们漏了。"),
        "books": books,
    }
    _catalog_cache["snap"] = ((mtime, vmtime), snap)
    return snap


class Handler(BaseHTTPRequestHandler):
    server_version = "HistoryAI/0.1"

    # ------------------------------------------------------------ helpers
    def _send(self, code: int, body: bytes, ctype: str = "application/json; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def _err(self, msg: str, code: int = 404):
        self._json({"error": msg}, code)

    def _q(self, key: str, default=None, cast=str):
        v = self._q_params.get(key)
        if v is None:
            return default
        try:
            return cast(v)
        except ValueError:
            return default

    def _static(self, rel: str):
        if rel in ("", "/"):
            rel = "index.html"
        # 只允许 frontend 目录内的普通文件，防目录穿越
        target = (FRONTEND_DIR / rel).resolve()
        if not str(target).startswith(str(FRONTEND_DIR.resolve())) or not target.is_file():
            return self._err("not found", 404)
        body = target.read_bytes()
        ctype = {"html": "text/html; charset=utf-8",
                 "js": "text/javascript; charset=utf-8",
                 "css": "text/css; charset=utf-8",
                 "svg": "image/svg+xml"}.get(target.suffix.lstrip("."),
                                             "application/octet-stream")
        self._send(200, body, ctype)

    # ------------------------------------------------------------ dispatch
    def do_GET(self):  # noqa: N802
        path, _, qs = self.path.partition("?")
        self._q_params = {}
        from urllib.parse import unquote
        for kv in qs.split("&"):
            if "=" in kv:
                k, v = kv.split("=", 1)
                self._q_params[unquote(k)] = unquote(v)
        if not path.startswith("/api/"):
            return self._static(path.lstrip("/"))

        for name, rx in ROUTE_RE.items():
            m = rx.match(path)
            if not m:
                continue
            handler = getattr(self, f"_h_{name}", None)
            if handler:
                return handler(m.group(1) if m.groups() else None)
        return self._err("unknown api path", 404)

    # ------------------------------------------------------------ handlers
    def _with_cur(self, fn):
        conn = api_db.connect()
        try:
            out = fn(conn)
        finally:
            conn.close()
        return out

    def _h_stats(self, _):
        def fn(conn):
            cur = conn.cursor()
            d = {}
            d["books"] = cur.execute("SELECT COUNT(*) FROM books").fetchone()[0]
            d["files"] = cur.execute("SELECT COUNT(*) FROM files").fetchone()[0]
            d["src_refs"] = cur.execute(
                "SELECT COUNT(*) FROM source_references").fetchone()[0]
            r = cur.execute(
                "SELECT COUNT(*) AS records,"
                " COUNT(*) FILTER (WHERE kind='passage') AS passages,"
                " COUNT(*) FILTER (WHERE status LIKE 'pending%') AS pending,"
                " COUNT(*) FILTER (WHERE kind='passage' AND normalized_text IS NOT NULL"
                "                 AND normalized_text <> '') AS fts_docs"
                " FROM passages").fetchone()
            d["records"] = r["records"]; d["passages"] = r["passages"]
            d["pending"] = r["pending"]
            d["run"] = dict(cur.execute(
                "SELECT * FROM import_runs ORDER BY ran_at DESC LIMIT 1").fetchone() or {})
            d["library"] = str(config.LIBRARY_DIR)
            d["fts"] = {
                "ok": search_engine.fts_tokenizer(cur) is not None,
                "tokenizer": search_engine.fts_tokenizer(cur),
                "docs": r["fts_docs"],
                "bigram": search_engine.has_bigram_fts(cur),
            }
            return d
        self._json(self._with_cur(fn))

    def _h_search(self, _):
        page = self._q("page", 1, int)
        page_size = self._q("page_size", search_engine.PAGE_SIZE_DEFAULT, int)
        # 第三阶段：默认返回 Result Block（史料片段）；level=passage 保留
        # 第二阶段「逐条命中」契约（调试/对照用，任务书 §十七 要求不删除）。
        level = (self._q("level") or "block").lower()
        mode = self._q("mode") or result_block.DEFAULT_MODE
        text_mode = (self._q("text") or dual_text.DEFAULT_MODE).lower()
        # 任务书把第四阶段的入口写成 mode=question。但 mode 在第三阶段已经是
        # 「显示长度」（short/standard/long），一个参数名不能有两种含义 ——
        # 收到 mode=question 就切到提问模式，显示长度回默认值。
        if mode.lower() == "question":
            level, mode = "question", result_block.DEFAULT_MODE
        if level == "passage":
            fn = lambda c: search_engine.run_search(     # noqa: E731
                c.cursor(), self._q("q") or "", self._q("book"),
                self._q("edition"), page, page_size)
        elif level == "block":
            fn = lambda c: result_block.search_result_blocks(   # noqa: E731
                c.cursor(), self._q("q") or "", self._q("book"),
                self._q("edition"), page, page_size, mode, text_mode)
        elif level == "question":
            fn = lambda c: self._question(c.cursor(), mode, text_mode)  # noqa: E731
        else:
            return self._err("level 只能是 block、passage 或 question", 400)
        try:
            out = self._with_cur(fn)
        except ValueError as e:
            return self._err(str(e), 400)
        # 收录范围随结果一起回（6.4-§28）。三条约束：
        #   ① 只在 **level=block** 给 —— 那是用户真看的视图；level=passage 是
        #      第二阶段留下的调试契约（逐条命中），往那儿加字段是噪音，而且
        #      scripts/site/check_engine.py 的 static-api 会逐键对拍那条路径。
        #   ② 只在**限定了单一史书**时给 —— 那是用户在说「我就要看这本书」，
        #      也是「这本书不全」最容易被误读成「史书里没有」的时候。
        #      全库检索不给：每次搜索都挂免责声明，说多了等于没说。
        #   ③ 只在真的有结果时给 —— 空结果的四态归因走 /api/diagnose，那里更细。
        # 代价：一次 resolve_book + 读一次内存里的卷级快照，**没有全表扫描**。
        if (level == "block" and isinstance(out, dict) and out.get("total")
                and out.get("book") and out["book"] != "全部"):
            out["scope"] = self._with_cur(
                lambda c: search_diagnose.hit_scope(c.cursor(), out["book"]))
        self._json(out)

    def _h_diagnose(self, _):
        """搜不到时的逐层归因（6.3-H）。只读，与 tests/recall.py 共用一份实现。

        代价：一次调用最多跑 3 次全表 `instr` 计数（每次本地实测 ~0.35s）。
        这是**诊断**路径的代价 —— 它只在用户想知道「为什么没有」时才跑，
        正常检索路径一行没改。
        """
        q = (self._q("q") or "").strip()
        if not q:
            return self._err("请提供搜索关键词 q", 400)
        try:
            out = self._with_cur(lambda c: search_diagnose.diagnose(
                c.cursor(), q, book=self._q("book"), edition=self._q("edition"),
                page=self._q("page", 1, int),
                page_size=self._q("page_size", search_engine.PAGE_SIZE_DEFAULT, int)))
        except ValueError as e:
            return self._err(str(e), 400)
        self._json(out)

    def _question(self, cur, mode: str, text_mode: str) -> dict:
        """自然语言提问（第四阶段）。**只检索，不生成答案**（§2.3）。

        返回的每个片段都带 `text`（原文 text_orig，一字不改）、`text_simplified`
        （派生的简体）、`why`（为什么判它相关，逐项得分）、`book_title`（哪部史书）。
        系统不会替用户下结论：语料里没有就如实说没有（见 notes）。
        """
        q = (self._q("q") or "").strip()
        if not q:
            raise ValueError("请提供问题")
        if mode not in result_block.MODE_LIMITS:
            raise ValueError(f"未知的显示长度：{mode}"
                             f"（可用：{'、'.join(result_block.MODE_LIMITS)}）")
        if text_mode not in dual_text.MODES:
            raise ValueError(f"未知的繁简方式：{text_mode}"
                             f"（可用：{'、'.join(dual_text.MODES)}）")
        res = search_retrieve.retrieve(cur, q)
        out = search_retrieve.as_dict(res, cur=cur, mode=mode, text_mode=text_mode)
        # 检索词的简体形态：前端在「只看简体/繁简对照」里要用它高亮。
        # 用的是和正文**同一个**转换函数，两边不会出现两套简体。
        out["terms_simplified"] = sorted({
            dual_text.simplify(t)["text"] for t in out["expanded"]["all_terms"]})
        out.update({"q": q, "level": "question", "mode": mode,
                    "text_mode": text_mode,
                    "disclaimer": "以下均为语料原文片段，系统只做检索与聚合，"
                                  "不生成、不改写、不摘要（第四阶段不含 AI 作答）。"})
        return out

    def _h_block_expand(self, pid):
        """按需展开史料片段上下文（任务书 §二十）。

        pid 是基准记录；before_passage_id / after_passage_id 传片段首/末记录
        时，从片段边界继续向外读，取到的行不会与已显示部分重叠。
        """
        direction = self._q("direction") or "both"
        count = self._q("count", 20, int)
        try:
            out = self._with_cur(lambda c: result_block.expand_block(
                c.cursor(), int(pid), direction, count,
                self._q("before_passage_id", None, int),
                self._q("after_passage_id", None, int)))
        except KeyError:
            return self._err("no such passage", 404)
        except ValueError as e:
            return self._err(str(e), 400)
        self._json(out)

    def _h_context(self, pid):
        before = self._q("before", 3, int)
        after = self._q("after", 3, int)
        try:
            out = self._with_cur(lambda c: search_ctx.get_context(
                c.cursor(), int(pid), before, after))
        except KeyError:
            return self._err("no such passage", 404)
        except ValueError:
            return self._err("非法参数", 400)
        self._json(out)

    def _h_books(self, _):
        self._json(self._with_cur(lambda c: api_db.list_books(c.cursor())))

    def _h_catalog(self, _):
        """语料收录进度（6.3-I）。只读审计快照，不碰数据库。"""
        self._json(_catalog_snapshot())

    def _h_book_files(self, book_id):
        self._json(self._with_cur(lambda c: api_db.list_files(c.cursor(), book_id)))

    def _h_file(self, file_id):
        out = self._with_cur(lambda c: api_db.get_file(c.cursor(), int(file_id)))
        if out is None:
            return self._err("no such file")
        self._json(out)

    def _h_passages(self, file_id):
        limit = min(self._q("limit", 100, int), 500)
        offset = max(self._q("offset", 0, int), 0)
        out = self._with_cur(lambda c: api_db.list_passages(
            c.cursor(), int(file_id),
            kind=self._q("kind"), layer=self._q("layer"), status=self._q("status"),
            q=self._q("q"), offset=offset, limit=limit))
        self._json(out)

    def _h_passage(self, pid):
        out = self._with_cur(lambda c: api_db.get_passage(c.cursor(), int(pid)))
        if out is None:
            return self._err("no such passage")
        self._json(out)

    def _h_raw(self, file_id):
        fid = int(file_id)
        start = max(self._q("start", 1, int), 1)
        end = max(self._q("end", start + 99, int), start)
        try:
            header_len, lines = _raw_lines(fid)
        except KeyError:
            return self._err("no such file")
        # 行窗口钳在文件范围内（start 超尾 → 从最后一行起，防越界）
        end = min(end, len(lines))
        start = min(start, len(lines))
        if end < start:
            end = start
        # 覆盖标注：行号落在该段的记录（再往前取 100 行以便识别跨行块的开头）
        from_lo = max(1, start - 100)
        conn = api_db.connect()
        recs = conn.execute(
            "SELECT row_no, seq, kind, layer, status, text_orig FROM passages "
            "WHERE file_id = ? AND row_no >= ? AND row_no <= ? ORDER BY row_no, seq",
            (fid, from_lo, end)).fetchall()
        conn.close()
        coverage: dict[int, list[dict]] = {}
        for r in recs:
            n_lines = r["text_orig"].count("\n") + 1
            for k in range(n_lines):
                coverage.setdefault(r["row_no"] + k, []).append({
                    "start": k == 0, "seq": r["seq"], "kind": r["kind"],
                    "layer": r["layer"], "status": r["status"],
                })
        out_lines = []
        for no in range(start, end + 1):
            ann = []
            if no <= header_len:
                ann.append("文件头")
            elif not lines[no - 1].strip() and config.PARA_CHAR not in lines[no - 1]:
                ann.append("空行(不入库)")
            else:
                for c in coverage.get(no, []):
                    tag = f"{c['kind']}/{c['layer']}" if c["start"] else "·(并入上块)"
                    if c["start"] and c["status"] != "ok":
                        tag += f"/{c['status']}"
                    ann.append(tag)
            out_lines.append({"no": no, "text": lines[no - 1], "annotation": ann})
        self._json({"file_id": fid, "header_lines": header_len,
                    "total_lines": len(lines), "start": start, "end": end,
                    "lines": out_lines})

    def log_message(self, fmt, *args):  # 安静日志
        print(f"[api] {self.address_string()} {fmt % args}")


def main(port: int = 8600):
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"HistoryAI 前端: http://127.0.0.1:{port}/  （Ctrl+C 退出）")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n停止")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 8600)
