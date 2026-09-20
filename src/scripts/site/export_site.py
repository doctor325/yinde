"""导出静态站点数据：SQLite（只读）→ frontend/data/。

产物目录是**本地完整版**的静态数据，含真实语料正文：已被 .gitignore 排除，
永不入库，也永不进入 GitHub Pages 发布产物（发布产物 = git 跟踪的 frontend/ 文件集，
见 scripts/site/check_publish.py）。公开站用的是 frontend/data-demo/（自制演示数据）。

## 产物

    corpus.json      全部 passages，列式打包 + 字典编码，按 LIKE 路径的顺序排列
    sections.json    篇名区间表（file_id, label, first_row, …），篇名检索与回填用
    stats.json       /api/stats 响应（已剥离本机绝对路径）
    books.json       /api/books 响应
    book_files.json  每本书的 /api/books/<id>/files 响应
    files.json       每个文件的 /api/files/<id> 响应
    raw/<id>.json    每个文件的 /api/files/<id>/raw 响应（原文对照）

## 为什么不自己重写这些接口的 SQL

statistics / books / files 的聚合在 api/db.py 里，raw 的「覆盖标注」在 api/main.py 里，
都是几十行有细节的代码。这里**进程内起真正的 api.main 服务器，用 HTTP 抓响应**，
于是静态数据与本地版逐字段同源——不存在「另写一份聚合然后两边慢慢漂移」的问题。
只有语料本身（无对应接口）才直接读库。

## normalized_text 不导出

它可由 text_orig 精确重算，规则经实测确认（不是读文档猜的）：

    normalized_text 非空  ⟺  kind == 'passage' 且 make_normalized 非空

（structure.make_normalized 只有 5 行；segmentation 的 _base 对所有行都调它，
但 _emit_comment_block / _emit_part_block 随后把 comment、part 行显式置 None。）
本脚本导出时**逐行断言**全部 224,822 行的重算结果与库中值完全相同，因此 JS 侧重算
是有据可依的，不是「看起来应该一样」。断言失败即中止导出——第一次运行就是这么
发现上面这条规则的（我原先以为 comment 行也有 normalized_text）。

## bm25：三个量里有两个必须导出

JS 侧要逐位复现 FTS5 的 bm25，需要三样东西：

  * **dl**（每行的 token 数）—— 可由 normalized_text 现算，不必导出：
        trigram  dl = max(0, len(normalized_text) - 2)        实测 0/224,822 不符
        bigram   dl = Σ max(0, 各空白分段长度 - 1)
  * **df**（词的文档频率）—— 与查询词有关，JS 侧扫描时算并缓存；
  * **n_row 与 n_token**（两个全局总量）—— **导不出，只能从库里取**。

最后一条是本次实测发现的：管线建 `passages_fts` 时先 `rebuild` 全表、再把 passage
行插一遍（sqlite_store.py:189-197），FTS5 把重插当成替换（倒排与 %_docsize 都是干净的），
但 bm25 用的两个总量被**算了两遍**：

    n_row   = 224,822 + 203,308 = 428,130
    n_token = Σdl + Σdl          = 2,194,094     →  avgdl = 5.124831243…

`passages_bg` 是独立表、只插一次，没有这个翻倍，总量就是影子表的字面值。
两套算法见 scripts/site/bm25.py，**都必须随数据下发**：它们编码的是索引的建法，
从导出的行里推不出来（照着行数算会得到 4.879625，与现网实际使用的 5.124831 差 5%，
排序会悄悄变形）。

导出时跑 `bm25.verify_against_sqlite()` 拿真实 `bm25()` 逐行对拍，不符即中止 ——
建表方式一旦改动，这里会明确失败，而不是安静地导出一份分数不对的数据。
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api import db as api_db  # noqa: E402
from scripts.pipeline import config  # noqa: E402
from scripts.pipeline import structure  # noqa: E402
from scripts.site import bm25  # noqa: E402

OUT_DIR = config.HISTORY_AI_DIR / "frontend" / "data"

# 字典编码的上限：取值少且短的列才值得建字典（长文本列直接原样存）。
DICT_MAX_DISTINCT = 5000
DICT_MAX_LEN = 64

# 不参与字典编码的列：正文本身，取值几乎全不同。
RAW_COLUMNS = {"text_orig", "normalized_text"}

# 明确不导出的列：JS 侧由 text_orig 重算（导出时逐行断言，见 check_normalized）。
DERIVED_COLUMNS = {"normalized_text"}

# 本机绝对路径的形态：盘符、UNC、POSIX 根。任何一个出现在产物里都是泄漏。
ABS_PATH_RE = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\|/)")
LEAK_WORDS = ("MyCode", "HistoryLibrary", "kanripo", "history.db")


# --------------------------------------------------------------- 本机 API

def _quiet_handler():
    """api.main.Handler 的安静版（导出几百次请求，不需要刷屏日志）。"""
    from api.main import Handler

    class _Quiet(Handler):
        def log_message(self, fmt, *args):  # noqa: D102
            pass

    return _Quiet


class LocalApi:
    """进程内起 api.main 服务器，用 HTTP 取响应。用法：with LocalApi() as api: api.get(...)"""

    def __init__(self) -> None:
        from http.server import ThreadingHTTPServer
        self._srv = ThreadingHTTPServer(("127.0.0.1", 0), _quiet_handler())
        self._port = self._srv.server_address[1]
        self._thread = threading.Thread(target=self._srv.serve_forever, daemon=True)

    def __enter__(self) -> "LocalApi":
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._srv.shutdown()
        self._srv.server_close()
        self._thread.join(timeout=5)

    def get(self, path: str, allow_error: bool = False):
        """allow_error=True 时把 4xx/5xx 的 JSON 错误体也返回 —— 一致性检查要拿
        真 API 的 {"error": ...}（404/400）与静态版抛出的错误消息对拍。"""
        url = f"http://127.0.0.1:{self._port}{path}"
        try:
            with urllib.request.urlopen(url) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if not allow_error:
                raise
            return json.loads(e.read().decode("utf-8"))


# --------------------------------------------------------------- 安全网

def scrub(obj, where: str):
    """递归检查：产物里不得出现本机绝对路径或语料目录名。发现即中止。

    这是最后一道网。上游还有两道：.gitignore 排除 frontend/data/，
    以及 check_publish.py 检查 git 跟踪文件集。
    """
    if isinstance(obj, dict):
        for k, v in obj.items():
            scrub(v, f"{where}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            scrub(v, f"{where}[{i}]")
    elif isinstance(obj, str):
        if ABS_PATH_RE.match(obj):
            raise SystemExit(f"泄漏检查失败：{where} 是本机绝对路径：{obj[:120]!r}")
        for w in LEAK_WORDS:
            if w in obj:
                raise SystemExit(f"泄漏检查失败：{where} 含 {w!r}：{obj[:120]!r}")


# --------------------------------------------------------------- 语料打包

def check_normalized(text_orig: str, kind: str, layer: str, actual) -> str | None:
    """逐行断言 normalized_text 可由 text_orig 重算；不一致立即中止导出。

    真实规则（本脚本第一次运行时报错才暴露出来的，不是读文档猜的）：

        normalized_text 非空  ⟺  kind == 'passage' 且 make_normalized 非空

    segmentation 的 _base() 对所有行都调 make_normalized，但紧接着
    _emit_comment_block（segmentation.py:233）与 _emit_part_block（:191）
    把 comment/part 行**显式覆盖为 None**。实测：kind<>'passage' 却非空的 0 行；
    kind='passage' 而重算与库中不符的 0 行（203,308 行全对）。

    因此 JS 侧的规则同样简单：只有 passage 行需要重算。这个断言在导出时逐行跑，
    覆盖全部 224,822 行，所以 JS 侧重算是有据可依的。
    """
    want = structure.make_normalized(text_orig, kind, layer)
    if kind != "passage":
        want = None
    if want != actual:
        raise SystemExit(
            f"normalized_text 重算不一致（导出中止）：\n"
            f"  text_orig={text_orig!r}\n  kind={kind!r} layer={layer!r}\n"
            f"  库中={actual!r}\n  重算={want!r}")
    return want


def packed_columns(cur) -> list[str]:
    cols = [r[1] for r in cur.execute("PRAGMA table_info(passages)")]
    return [c for c in cols if c not in DERIVED_COLUMNS]


def choose_dict_columns(cur, cols: list[str]) -> list[str]:
    """一次全表扫描算出各列的取值数与最大长度，挑出值得字典编码的列。

    规则：非正文列、取值数 <= DICT_MAX_DISTINCT 且每个取值长度 <= DICT_MAX_LEN。
    取值太多或太长时建字典反而更大，不如原样存。
    """
    parts = []
    for c in cols:
        if c in RAW_COLUMNS:
            continue
        parts.append(f'COUNT(DISTINCT "{c}") AS d_{c}')
        parts.append(f'MAX(LENGTH("{c}")) AS l_{c}')
    row = cur.execute(f"SELECT {', '.join(parts)} FROM passages").fetchone()
    keys = row.keys()
    out = []
    for c in cols:
        if c in RAW_COLUMNS:
            continue
        n = row[f"d_{c}"]
        m = row[f"l_{c}"]
        if n and n <= DICT_MAX_DISTINCT and (m or 0) <= DICT_MAX_LEN:
            out.append(c)
    return out


def build_dicts(cur, dict_cols: list[str]) -> dict[str, list[str]]:
    """每列取值排序后作为字典；行的值换成下标，null 记 -1。"""
    dicts: dict[str, list[str]] = {}
    for c in dict_cols:
        vals = [r[0] for r in cur.execute(
            f'SELECT DISTINCT "{c}" FROM passages WHERE "{c}" IS NOT NULL '
            f'ORDER BY "{c}"')]
        dicts[c] = vals
    return dicts


# 导出闸门用的探针词：两条路径各取几个，都是语料里确实存在的词。
# 只在导出时用来跟真实 bm25() 对拍，不进产物。
PROBE_TRIGRAM = ("齊桓公", "晉文公", "秦穆公", "百里奚", "楚成王")
PROBE_BIGRAM = ("管仲", "齊桓", "晉文", "秦穆", "百里")


def bm25_stats(cur) -> dict:
    """导出 bm25 所需的全局总量（见 scripts/site/bm25.py 与模块头注释）。

    这两个量编码的是**索引的建法**（trigram 表被 rebuild+重插，总量翻倍），
    从导出的行里推不出来，因此必须落盘。顺带拿真实 bm25() 逐行对拍：
    不一致就中止导出，绝不产出「分数悄悄不一样」的数据。
    """
    out = {}
    for name, table, probe, totals in (
            ("trigram", "passages_fts", PROBE_TRIGRAM, bm25.totals_rebuilt_twice),
            ("bigram", "passages_bg", PROBE_BIGRAM, bm25.totals_single_build)):
        n_row, n_token = totals(table, cur)
        bad = bm25.verify_against_sqlite(table, cur, probe, n_row, n_token)
        if bad:
            raise SystemExit(
                f"{table} 的 bm25 与公式不符，导出中止（建表方式可能已改）：\n  "
                + "\n  ".join(bad[:5]))
        out[name] = {"table": table, "k1": bm25.K1, "b": bm25.B,
                     "n_row": n_row, "n_token": n_token,
                     "avgdl": n_token / n_row}
        print(f"  bm25[{name}]  n_row={n_row:,}  n_token={n_token:,}  "
              f"avgdl={n_token / n_row:.9f}  探针 {len(probe)} 词全部吻合")
    return out


def write_corpus(cur, out_path: Path) -> dict:
    """流式写 corpus.json：一次只驻留一个分块，避免 22 万行 × 25 列撑爆内存。"""
    cols = packed_columns(cur)
    dict_cols = choose_dict_columns(cur, cols)
    dicts = build_dicts(cur, dict_cols)
    code_of = {c: {v: i for i, v in enumerate(dicts[c])} for c in dict_cols}

    idx = {c: i for i, c in enumerate(cols)}
    i_text, i_kind, i_layer = (idx["text_orig"], idx["kind"], idx["layer"])
    i_file = idx["file_id"]

    # bm25 的两个总量必须**在下面的大 SELECT 之前**算：它内部要跑若干查询，
    # 而 cur 一旦开始迭代大结果集，再 execute 就会把结果集重置（第一版就是这么
    # 得到一份 0 行的 corpus.json 的 —— 所以下面还有一道行数闸门兜底）。
    bm = bm25_stats(cur)
    expect = cur.execute("SELECT count(*) FROM passages").fetchone()[0]
    print(f"  待打包 {expect:,} 行")

    # 顺序 = LIKE 路径的顺序（engine.run_search 的 like 分支）。
    # JS 侧因此可以直接用数组顺序当 LIKE 顺序，不必再排一次。
    sql = ("SELECT " + ",".join(f'p."{c}"' for c in cols) + ", p.normalized_text "
           "FROM passages p JOIN files f ON f.file_id = p.file_id "
           "JOIN books b ON b.book_id = f.book_id "
           "ORDER BY b.book_id, f.file_no, p.row_no, p.seq")
    cur.execute(sql)

    n_rows = 0
    file_span: dict[int, list[int]] = {}     # file_id -> [首行下标, 行数]（供统计与自检）
    tmp = out_path.with_suffix(".json.part")
    with tmp.open("w", encoding="utf-8", newline="\n") as fh:
        head = {
            "format": "historyai-corpus/1",
            # 数据来源标签，只作说明用（前端读了但不判断）。默认是真实语料的
            # 本地库；演示数据构建时由 make_demo_data.py 用环境变量覆盖 ——
            # 一份要公开发布的产物不该自称来自 Kanripo。
            "source": os.environ.get("HISTORY_SITE_SOURCE") or "kanripo-local",
            "columns": cols,
            "dict_columns": dict_cols,
            "dicts": dicts,
            "derived": {
                "normalized_text":
                    "kind=='passage' ? make_normalized(text_orig) : null"
                    "（structure.make_normalized；导出时已逐行断言）",
                "file_span": "file_id -> [起, 止) 行下标，由 file_id 列现算",
                "dl": "trigram: max(0, 码位数-2)；bigram: bg 列的 unicode61 "
                      "token 数。两者都由 frontend/engine/engine.js 现算，"
                      "scripts/site/check_engine.py 的 dl 检查对影子表全表对拍",
                "bm25": "n_row / n_token 编码的是索引的**建法**（trigram 表被 "
                        "rebuild + 重插，总量翻倍），从行里推不出来，故随包下发；"
                        "脚本侧见 scripts/site/bm25.py",
            },
            "bm25": bm,
        }
        # 头部要写成同一个对象的开头：先去掉 json.dumps 收尾的 }，再续 "rows"。
        # （第一版就是漏了这一步，产出的文件不是合法 JSON。）
        fh.write(json.dumps(head, ensure_ascii=False, separators=(",", ":"))[:-1])
        fh.write(',"rows":[')

        first = True
        while True:
            chunk = cur.fetchmany(4000)
            if not chunk:
                break
            for r in chunk:
                actual = r[len(cols)]
                check_normalized(r[i_text], r[i_kind], r[i_layer], actual)
                vals = []
                for i, c in enumerate(cols):
                    v = r[i]
                    if c in code_of:
                        vals.append(-1 if v is None else code_of[c][v])
                    else:
                        vals.append(v)
                span = file_span.setdefault(r[i_file], [n_rows, 0])
                span[1] += 1
                fh.write(("" if first else ",") + json.dumps(
                    vals, ensure_ascii=False, separators=(",", ":")))
                first = False
                n_rows += 1
            if n_rows % 40000 < 4000:
                print(f"    {n_rows:,} 行…", flush=True)
        fh.write("]}")
    # 行数闸门：少一行都说明上面某个环节吞掉了结果集（例如游标被嵌套查询重置）。
    # 缺了它，产物会是一份「看起来正常、其实空掉」的文件。
    if n_rows != expect:
        raise SystemExit(f"打包行数不符：写出 {n_rows:,} 行，库里有 {expect:,} 行 —— "
                         f"已删除半成品 {tmp}")
    tmp.replace(out_path)
    return {"rows": n_rows, "columns": cols, "dict_columns": dict_cols,
            "file_span": file_span, "bm25": bm}


def write_json(obj, path: Path) -> int:
    body = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    path.write_text(body, encoding="utf-8", newline="\n")
    return len(body.encode("utf-8"))


def write_sections(cur, out_path: Path) -> int:
    """导出篇名区间表。763 行，很小。

    为什么单独成文件而不是塞进 corpus.json 的头部：发布闸门（check_publish.py）
    对 corpus.json 只逐行查 text_orig 那一列，塞进头部等于开一条不受检的通道
    ——「演示站里混进真实篇名」这件事必须有人能查出来，所以它得是一个闸门
    认识的独立文件。

    区间（first_row）而非「每个 passage 的篇名」：归属关系不在 passages.section
    里（那一列只标在标题行上，不向下传播），只在 sections.first_row 的区间里。
    JS 侧据此二分回填，见 frontend/engine/result_block.js 的 sectionIndex。
    """
    rows = cur.execute(
        "SELECT s.file_id, s.label, s.first_row, s.division, "
        "       f.book_id, f.file_no, b.family "
        "FROM sections s JOIN files f ON f.file_id = s.file_id "
        "JOIN books b ON b.book_id = f.book_id "
        "WHERE s.file_id IS NOT NULL AND s.label IS NOT NULL "
        "  AND s.first_row IS NOT NULL "
        "ORDER BY s.file_id, s.first_row").fetchall()
    out = [{"file_id": r["file_id"], "label": r["label"],
            "first_row": r["first_row"], "division": r["division"],
            "book_id": r["book_id"], "file_no": r["file_no"],
            "family": r["family"]} for r in rows]
    return write_json(out, out_path)


# --------------------------------------------------------------- 各接口落盘

def export_api(out_dir: Path) -> dict[str, int]:
    """抓取各只读接口并存盘。返回 {文件名: 字节数}。"""
    sizes: dict[str, int] = {}
    with LocalApi() as api:
        stats = api.get("/api/stats")
        # /api/stats 会带出本机语料库绝对路径（api/main.py 的 d["library"]），
        # 以及 import_runs 的整行（含 library 列）。两个都剥掉。
        stats.pop("library", None)
        if isinstance(stats.get("run"), dict):
            stats["run"].pop("library", None)
        sizes["stats.json"] = write_json(stats, out_dir / "stats.json")
        print(f"  stats.json        {stats['books']} 书 / {stats['files']} 文件 / "
              f"{stats['records']:,} 记录")

        books = api.get("/api/books")
        sizes["books.json"] = write_json(books, out_dir / "books.json")
        print(f"  books.json        {len(books)} 本")

        book_files = {}
        files = {}
        raw_dir = out_dir / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        n_lines = 0
        for b in books:
            bid = b["book_id"]
            fl = api.get(f"/api/books/{bid}/files")
            book_files[bid] = fl
            for f in fl:
                fid = f["file_id"]
                files[fid] = api.get(f"/api/files/{fid}")
                raw = api.get(f"/api/files/{fid}/raw?start=1&end={10**9}")
                n_lines += len(raw["lines"])
                write_json(raw, raw_dir / f"{fid}.json")
            print(f"  {bid:<10} files={len(fl):<4} raw 累计 {n_lines:,} 行", flush=True)
        sizes["book_files.json"] = write_json(book_files, out_dir / "book_files.json")
        sizes["files.json"] = write_json(files, out_dir / "files.json")
        for k, v in (("stats.json", stats), ("books.json", books),
                     ("book_files.json", book_files), ("files.json", files)):
            scrub(v, k)
    return sizes


# --------------------------------------------------------------- main

def main(argv: list[str]) -> int:
    args = argv[1:]
    skip_api = "--skip-api" in args
    skip_corpus = "--skip-corpus" in args
    rest = [a for a in args if not a.startswith("--")]
    out_dir = Path(rest[0]) if rest else OUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"导出到 {out_dir}")
    print("  数据库（只读）:", config.DB_PATH)
    if not Path(config.DB_PATH).is_file():
        print("  失败：数据库不存在，请先跑管线。")
        return 1

    sizes: dict[str, int] = {}
    info = None
    if not skip_api:
        print("\n[1/2] 只读接口（进程内起 api.main，HTTP 抓取）…")
        sizes.update(export_api(out_dir))
    if not skip_corpus:
        print("\n[2/3] 语料打包（流式写入）…")
        conn = api_db.connect()
        try:
            info = write_corpus(conn.cursor(), out_dir / "corpus.json")
        finally:
            conn.close()
        sizes["corpus.json"] = (out_dir / "corpus.json").stat().st_size
        print(f"  corpus.json       {info['rows']:,} 行 / {len(info['columns'])} 列 / "
              f"字典列 {len(info['dict_columns'])} / {len(info['file_span'])} 个文件")
    # 篇名区间表**总是**写：它只有 763 行，跳过它只会留下一份与语料不同步的
    # 陈旧文件，而「陈旧」正是最难发现的那种错。
    print("\n[3/3] 篇名区间表…")
    conn = api_db.connect()
    try:
        sizes["sections.json"] = write_sections(
            conn.cursor(), out_dir / "sections.json")
    finally:
        conn.close()
    n_sec = len(json.loads((out_dir / "sections.json").read_text(encoding="utf-8")))
    print(f"  sections.json     {n_sec:,} 个篇名区间")

    if sizes:
        print("\n产物：")
        for name, n in sorted(sizes.items()):
            print(f"  {name:<16} {n/1048576:8.2f} MB")
    raw_dir = out_dir / "raw"
    if raw_dir.is_dir():
        n_raw = len(list(raw_dir.glob("*.json")))
        raw_total = sum(p.stat().st_size for p in raw_dir.glob("*.json"))
        print(f"  {'raw/*.json':<16} {raw_total/1048576:8.2f} MB  （{n_raw} 个文件）")
        total = sum(sizes.values()) + raw_total
        print(f"  合计约            {total/1048576:8.2f} MB（本地数据，不入库）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
