"""引擎一致性验证 —— Python 侧驱动（§18）。

对拍方式：同一份输入，Python 原版与 JS 移植各算一遍，比结果。
两侧都不直接搬运中间结果——各自读**同一个** frontend/data/corpus.json，
所以在数据这一点上不存在「喂给 JS 的和 Python 看到的不是一回事」。

比摘要而不是比全量文本：两边的 JSON 转义规则不同（尤其孤立代理项），
摘要按**码位字节**（UTF-16-BE）规范化，绕开差异；摘要不同时才回退到逐条明细。

用法：
    python -m scripts.site.check_engine            # 全部检查
    python -m scripts.site.check_engine dual-text  # 只跑某一项
"""
from __future__ import annotations

import contextlib
import datetime
import hashlib
import io
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api import db as api_db  # noqa: E402
from scripts.pipeline import config  # noqa: E402
from search import dual_text, engine, entities, question, query_expansion, zh  # noqa: E402

HERE = Path(__file__).resolve().parent
MJS = HERE / "check_engine.mjs"
CORPUS = config.HISTORY_AI_DIR / "frontend" / "data" / "corpus.json"
DB_PATH = str(config.DB_PATH)

# 这些样本上 Python 与 JS 必须**逐字节相同**，不需要「已知差异」豁免：
# 逐字查表与整串转换在 dual_text 会采纳的范围（长度不变）内已由 gen_zh_table.py
# 穷举证明等价。
#
# 最后几条带**星形字**（扩展 B 的 𫝊/𤣥）。历史上这里刻意只用 BMP 内的字，
# 因为 Python 侧那时会把含星形字的串截断（尾巴少一截、单字甚至只剩孤立代理项），
# JS 只好照抄这个 bug 才「一致」。根因修好后两边都原样放行星形字，于是把它们
# 摆进样本——这类回归不该再靠「样本里别出现星形字」来回避。
#
# **不得因罕见而删除**（计划书 6.3-F）：删掉这几条不会让本脚本变红，只会让
# 星形字回归重新变成「靠样本里不出现星形字来回避」。`tests/test_unicode_corpus.py`
# 用一条断言钉住它们仍在样本表里。
ASTRAL_SAMPLES = [
    "漢書敘𫝊第七十下", "顏師古注𤣥成", "𫝊", "𤣥", "a𫝊b龍",
]
MULTI_SAMPLES = [
    "齐桓公", "齊桓公", "国语", "國語", "晉文公重耳", "城濮之战",
    "商鞅变法", "秦穆公和百里奚", "董卓", "管仲相齊", "齊桓公卒",
    "頭髮", "理髮", "後來", "裡面", "一隻", "樹幹", "鐘錶",
] + ASTRAL_SAMPLES


def preflight() -> None:
    """参考实现必须是真正的 Windows 转换，不能是恒等回退。

    非 Windows 上 `zh.to_simplified` 恒等返回原文，此时对拍会「全部通过」，
    但那个通过毫无意义。宁可在这里明确失败。
    """
    if sys.platform != "win32":
        raise SystemExit(
            f"一致性验证只在 Windows 上有意义：当前 sys.platform={sys.platform!r}，"
            f"search/zh.py 会退化为恒等函数，对拍结果没有信息量。")
    if not zh._get_map_fn():
        raise SystemExit("search/zh.py 未取到 kernel32.LCMapStringEx，参考实现是恒等回退，"
                         "对拍无意义。")
    if shutil.which("node") is None:
        raise SystemExit("找不到 node，无法运行 JS 侧。")
    check_script_order()


def check_script_order() -> None:
    """index.html 的 <script> 顺序必须与 loader.mjs 的 ENGINE_FILES 逐项相同。

    自检是在 Node 里按 ENGINE_FILES 的顺序跑的，浏览器按 index.html 的顺序跑。
    两者一旦不一致，**自检通过而线上是坏的** —— 这正是最不该出现的那种失败：
    证据说没事，用户看到白屏。所以在这里直接比字符串，不等出错再查。
    """
    html = (config.HISTORY_AI_DIR / "frontend" / "index.html").read_text(encoding="utf-8")
    in_html = re.findall(r'<script src="engine/([^"]+)"', html)
    src = (HERE / "loader.mjs").read_text(encoding="utf-8")
    m = re.search(r"ENGINE_FILES\s*=\s*\[(.*?)\]", src, re.S)
    in_loader = re.findall(r'"([^"]+)"', m.group(1)) if m else []
    # boot.js 是浏览器专有（要 document / fetch），不在 ENGINE_FILES 里。
    if in_html != in_loader:
        only_html = [f for f in in_html if f not in in_loader]
        only_loader = [f for f in in_loader if f not in in_html]
        raise SystemExit(
            "index.html 的引擎脚本与 loader.mjs 的 ENGINE_FILES 不一致 —— "
            "自检跑的不是浏览器将跑的那份代码：\n"
            f"  index.html 独有：{only_html}\n  ENGINE_FILES 独有：{only_loader}\n"
            f"  index.html 顺序：{in_html}\n  ENGINE_FILES 顺序：{in_loader}")


def run_node(cmd: str, **opts) -> dict:
    args = ["node", str(MJS), cmd]
    for k, v in opts.items():
        args += [f"--{k}", str(v)]
    p = subprocess.run(args, capture_output=True, text=True,
                       encoding="utf-8", errors="surrogatepass")
    if p.returncode != 0:
        raise SystemExit(f"node 侧失败（{cmd}）：\n{p.stdout}\n{p.stderr}")
    return json.loads(p.stdout.strip().splitlines()[-1])


def load_corpus() -> tuple[list[str], list[list], dict[str, list[str]]]:
    """读打包语料，返回 (列名, 行, 字典)。kind 等字典列在两侧解码方式一致。"""
    with CORPUS.open(encoding="utf-8") as fh:
        d = json.load(fh)
    return d["columns"], d["rows"], d["dicts"]


def decode(row: list, cols: list[str], dicts: dict, col: str):
    """与前端 corpus.js 的 Corpus.cell 同一口径：字典列的 -1 还原成 None。"""
    v = row[cols.index(col)]
    if col not in dicts:
        return v
    return dicts[col][v] if v >= 0 else None


def py_digest_passages() -> dict:
    """Python 侧：对全部 kind='passage' 行跑 dual_text.simplify，出摘要。"""
    cols, rows, dicts = load_corpus()
    h = hashlib.sha256()
    n = ok = fallback = changed = 0
    for r in rows:
        if decode(r, cols, dicts, "kind") != "passage":
            continue
        pid = decode(r, cols, dicts, "passage_id")
        res = dual_text.simplify(decode(r, cols, dicts, "text_orig"))
        n += 1
        ok += 1 if res["ok"] else 0
        fallback += 0 if res["ok"] else 1
        changed += res["changed"]
        h.update(f"{pid}|{1 if res['ok'] else 0}|{res['changed']}|".encode())
        h.update(res["text"].encode("utf-16-be", "surrogatepass"))
        h.update(b"\n")
    return {"rows": n, "ok": ok, "fallback": fallback, "changed": changed,
            "digest": h.hexdigest()}


def check_dual_text() -> bool:
    print("① dual_text.simplify：全部 passage 正文（Python vs JS）")
    py = py_digest_passages()
    js = run_node("dual-text-corpus", corpus=CORPUS)
    for k in ("rows", "ok", "fallback", "changed"):
        flag = "OK" if py[k] == js[k] else "**不符**"
        print(f"   {k:<10} py={py[k]:<12,} js={js[k]:<12,} {flag}")
    same = py["digest"] == js["digest"]
    print(f"   摘要 {'一致' if same else '**不一致**'}")
    if not same:
        print(f"     py {py['digest']}\n     js {js['digest']}")
        print("     提示：摘要不同但计数全对时，差异在正文内容；"
              "改用 samples 子命令逐条定位。")
    return same and all(py[k] == js[k] for k in ("rows", "ok", "fallback", "changed"))


def check_zh() -> bool:
    """zh.js 的接线检查：样本 = 语料全部单字 + 固定的 BMP 多字串。

    单字样本上「JS 逐字查表」与「Python 整串调用」按构造必须相等
    （字符表就是这么生成的）；多字 BMP 样本两者也必然相等（无代理对截断）。
    这条检查抓的是接线错误：转义写错、按 UTF-16 单元遍历、键编码不对。
    """
    print("② zh.js 接线（语料全部单字 + 固定多字样本）")
    cols, rows, dicts = load_corpus()
    chars = sorted({c for r in rows
                    for c in decode(r, cols, dicts, "text_orig")})
    astral = [c for c in chars if ord(c) > 0xFFFF]
    samples = chars + MULTI_SAMPLES
    print(f"   样本 {len(samples):,} 个（单字 {len(chars):,}，其中星形 {len(astral)}）")

    tmp = config.HISTORY_AI_DIR / "data" / "_zh_samples.json"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps(samples, ensure_ascii=False), encoding="utf-8")
    try:
        js = run_node("zh-table", samples=tmp)
    finally:
        tmp.unlink(missing_ok=True)

    py_t = [zh.to_traditional(s) for s in samples]
    py_s = [zh.to_simplified(s) for s in samples]
    bad_t = [i for i, (a, b) in enumerate(zip(py_t, js["toTraditional"])) if a != b]
    bad_s = [i for i, (a, b) in enumerate(zip(py_s, js["toSimplified"])) if a != b]
    print(f"   简→繁（toTraditional）不符 {len(bad_t)} / {len(samples):,}")
    print(f"   繁→简（toSimplified） 不符 {len(bad_s)} / {len(samples):,}")
    for i in (bad_t + bad_s)[:5]:
        print(f"     样本 {samples[i]!r}：py={py_t[i]!r}/{py_s[i]!r} "
              f"js={js['toTraditional'][i]!r}/{js['toSimplified'][i]!r}")
        print(f"       （码位 {[hex(ord(c)) for c in samples[i]]}）")
    return not bad_t and not bad_s


def check_norm() -> bool:
    """normalized_text 重算：JS 现算 vs **数据库真值**，全 224,822 行。

    这条检查针对的是 corpus.js 的 makeNormalized。它不是「照抄一遍 Python 函数」
    就算完：Python 的 str.strip() 与 JS 的 String.trim() 的空白集合**不一样**
    （Python 多 U+001C-1F、U+0085，JS 多 U+FEFF），差一个字符就会让某些行的
    normalized_text 不同，进而让检索命中集合悄悄少一片。只有和库里的真值逐行
    对拍才能确认这一点。
    """
    print("③ normalized_text 重算：JS vs 数据库真值（全表）")
    conn = api_db.connect()
    try:
        rows = conn.execute(
            "SELECT passage_id, normalized_text FROM passages ORDER BY passage_id")
        h = hashlib.sha256()
        n = non_null = 0
        for pid, nt in rows:
            n += 1
            non_null += 1 if nt is not None else 0
            h.update(f"{pid}|{-1 if nt is None else 1}|".encode())
            if nt is not None:
                h.update(nt.encode("utf-16-be", "surrogatepass"))
            h.update(b"\n")
        digest = h.hexdigest()
        counts = {
            "files": conn.execute("SELECT COUNT(*) FROM files").fetchone()[0],
            "books": conn.execute("SELECT COUNT(*) FROM books").fetchone()[0],
            "passageIdx": conn.execute(
                "SELECT COUNT(*) FROM passages WHERE kind='passage'").fetchone()[0],
        }
    finally:
        conn.close()

    js = run_node("norm-corpus", corpus=CORPUS)
    checks = [("rows", n, js["rows"]), ("nonNull", non_null, js["nonNull"])]
    checks += [(k, v, js[k]) for k, v in counts.items()]
    checks += [("contiguous", True, js["contiguous"]),
               ("byIdSize", n, js["byIdSize"])]
    all_ok = True
    for k, py, js_v in checks:
        ok = py == js_v
        all_ok = all_ok and ok
        print(f"   {k:<11} py={py:<12,} js={js_v:<12,} {'OK' if ok else '**不符**'}")
    same = digest == js["digest"]
    all_ok = all_ok and same
    print(f"   摘要 {'一致' if same else '**不一致**'}")
    if not same:
        print(f"     py {digest}\n     js {js['digest']}")
    return all_ok


# 检索对拍的固定查询集（单一事实来源：Python 侧写，JS 侧读同一份）。
# 覆盖三条路径、书/版本过滤、分页、空结果、超多命中、繁简两种写法。
QUERIES = [
    # (关键词, 书, 版本, 页, 每页)
    ("齊桓公", None, None, 1, 20),          # fts
    ("管仲", None, None, 1, 20),            # bigram
    ("齊", None, None, 1, 20),              # 单字 → like
    ("之", None, None, 2, 10),              # 超多命中 + 分页
    ("齊桓公 管仲", None, None, 1, 20),      # 多词 fts
    ("齊桓公 管", None, None, 1, 20),        # 长词 + 短词
    ("秦穆公 百里奚", None, None, 1, 20),
    ("秦穆公", "史记", None, 1, 20),         # 简体书名
    ("秦穆公", "史記", None, 1, 20),         # 繁体书名
    ("秦穆公", "KR2a0001", None, 1, 20),     # 书号
    ("秦穆公", "《史記》", None, 1, 20),      # 带书名号
    ("秦穆公", None, "sbck", 1, 20),         # 版本过滤
    ("秦穆公", None, "tls", 1, 20),
    ("城濮之战", None, None, 1, 20),         # 4 字
    ("城濮", None, None, 1, 20),
    ("晉文公重耳", None, None, 1, 20),
    ("齐桓公", None, None, 1, 20),           # 简体输入（zh 转繁）
    ("國語", None, None, 1, 20),
    ("董卓", None, None, 1, 20),             # 语料里没有 → 空结果
    ("齊桓公", None, None, 3, 7),            # 靠后的分页
    ("齊桓公", None, None, 1, 100),          # 每页上限
    # --- 下面几条专门压 bigram 的 unicode61 语义（见 engine.js 头注释）---
    # 这两种写法命中集合**不相等**：。管 命中的是「管」单独成词的行，
    # LIKE '%。管%' 是 0 行。移植要是图省事写成子串，这几条会立刻红。
    ("。管", None, None, 1, 20),
    ("管。", None, None, 1, 20),
    ("亡", None, None, 1, 20),   # 汉字 + 分隔符：token 退化成单字
    ("。，", None, None, 1, 20),             # 全分隔符 → 空短语 → 空集
    ("(( ", None, None, 1, 20),
    ("KR", None, None, 1, 20),               # 纯拉丁 2 字：考 ASCII 大小写折叠
    ("1管", None, None, 1, 20),
]


def py_search(queries: list) -> list:
    conn = api_db.connect()
    try:
        out = []
        for q, book, edition, page, size in queries:
            try:
                out.append(engine.run_search(conn, q, book, edition, page, size))
            except ValueError as e:
                out.append({"error": str(e)})
        return out
    finally:
        conn.close()


def _safe(v) -> str:
    """打印用：孤立代理项在 stdout 上会炸，先转义。"""
    return repr(v).encode("utf-8", "backslashreplace").decode("utf-8")


def diff_result(py: dict, js: dict, path: str, out: list[str], limit: int = 6) -> None:
    """逐字段比：命中集合与出处字段必须完全一致（score 不在 _shape_row 里，
    它只影响 items 的顺序，所以顺序不符会在 passage_id 序列上直接暴露）。"""
    if len(out) > limit:
        return
    if isinstance(py, dict) and isinstance(js, dict):
        for k in sorted(set(py) | set(js)):
            if k not in py or k not in js:
                out.append(f"{path}.{k}: 只有一侧有 py={k in py} js={k in js}")
            else:
                diff_result(py[k], js[k], f"{path}.{k}", out, limit)
    elif isinstance(py, list) and isinstance(js, list):
        if len(py) != len(js):
            out.append(f"{path}: 条数 py={len(py)} js={len(js)}")
        for i in range(min(len(py), len(js))):
            diff_result(py[i], js[i], f"{path}[{i}]", out, limit)
    elif py != js:
        out.append(f"{path}: py={_safe(py)} js={_safe(js)}")


def check_search() -> bool:
    print("⑤ engine.run_search：固定查询集（Python 对 SQLite vs JS 对内存语料）")
    tmp = config.HISTORY_AI_DIR / "data" / "_queries.json"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps(
        [{"q": q, "book": b, "edition": e, "page": p, "page_size": s}
         for q, b, e, p, s in QUERIES], ensure_ascii=False), encoding="utf-8")
    try:
        js = run_node("search", corpus=CORPUS, queries=tmp)["results"]
    finally:
        tmp.unlink(missing_ok=True)
    py = py_search(QUERIES)

    ok = True
    modes = {}
    for (q, book, edition, page, size), a, b in zip(QUERIES, py, js):
        label = f"{q!r} book={book} edition={edition} p={page}/{size}"
        if "error" in a or "error" in b:
            same = a.get("error") == b.get("error")
            print(f"   {label:<52} 报错 {'一致' if same else '**不符**'}")
            ok = ok and same
            continue
        diffs: list[str] = []
        diff_result(a, b, "结果", diffs)
        # total / mode / 前几条 passage_id 单独打出来，便于一眼看形态
        modes[a["mode"]] = modes.get(a["mode"], 0) + 1
        ids_a = [x["passage_id"] for x in a["items"]][:4]
        ids_b = [x["passage_id"] for x in b["items"]][:4]
        flag = "OK" if not diffs else "**不符**"
        print(f"   {label:<52} mode={a['mode']:<7} total={a['total']:<7} "
              f"前4={ids_a} {flag}")
        if ids_b != ids_a:
            print(f"       JS 前4={ids_b}")
        for d in diffs[:6]:
            print(f"       {d}")
        ok = ok and not diffs
    print(f"   路径分布：{modes}")
    return ok


def check_dl() -> bool:
    """两条 dl 推导 vs 数据库影子表，全表。

    JS 侧按推导现算，Python 侧直接读 `%_docsize`（库里存的 dl 是 FTS5 自己
    写进去的真值），逐行比。不在 bigram 影子表里的行按 0 计 —— 那些行没有任何
    相邻两字组，本来就不会被计分（管线也不插它们）。
    """
    print("④ dl 推导：JS 现算 vs 数据库影子表（全表）")
    conn = api_db.connect()
    try:
        tg = {i: int.from_bytes(b, "big") for i, b in
              conn.execute("SELECT id, sz FROM passages_fts_docsize")}
        bg = {i: int.from_bytes(b, "big") for i, b in
              conn.execute("SELECT id, sz FROM passages_bg_docsize")}
        rows = conn.execute("SELECT passage_id FROM passages ORDER BY passage_id")
        h = hashlib.sha256()
        n = tri_zero = bg_zero = tri_total = bg_total = 0
        for (pid,) in rows:
            tri = tg.get(pid, 0)
            b = bg.get(pid, 0)
            n += 1
            tri_zero += 1 if tri == 0 else 0
            bg_zero += 1 if b == 0 else 0
            tri_total += tri
            bg_total += b
            h.update(f"{pid}|{tri}|{b}|".encode())
        digest = h.hexdigest()
    finally:
        conn.close()

    js = run_node("dl-corpus", corpus=CORPUS)
    checks = [("rows", n, js["rows"]), ("triZero", tri_zero, js["triZero"]),
              ("bgZero", bg_zero, js["bgZero"]),
              ("triTotal", tri_total, js["triTotal"]),
              ("bgTotal", bg_total, js["bgTotal"])]
    all_ok = True
    for k, py, js_v in checks:
        ok = py == js_v
        all_ok = all_ok and ok
        print(f"   {k:<9} py={py:<12,} js={js_v:<12,} {'OK' if ok else '**不符**'}")
    same = digest == js["digest"]
    all_ok = all_ok and same
    print(f"   摘要 {'一致' if same else '**不一致**'}")
    if not same:
        print(f"     py {digest}\n     js {js['digest']}")
    return all_ok


# ------------------------------------------------------------ 实体层对拍

# 正文样本按这些 marker 从语料里取**第一条**（不是手抄），所以样本随语料走、
# 不随人走；换库时样本自动跟着换，不会悄悄失效。
ENTITY_MARKERS = ["桓公", "重耳", "楚公子", "秦穆公", "百里奚", "鄭穆公", "小白",
                  "晉文公", "繆公", "管子", "商鞅", "鮑叔"]

# 合成样本：压空串、无实体、纯短称、全称与短称同现这几种边界
ENTITY_EXTRA = ["", "齊桓公", "桓公問於管子", "重耳出亡十九年", "秦穆公與百里奚",
                "鄭穆公之子", "楚公子圍", "小白立為齊君", "文公", "趙盾"]

# occurs_in_corpus 的词：两字（走 2-gram 表）、两字但表里没有（走 LIKE）、
# 多字、单字、纯拉丁（考 ASCII 折叠）、以及 LIKE 的通配符本身
ENTITY_WORDS = ["重耳", "小白", "管夷吾", "百里奚", "桓公", "文公", "侯使", "公曰",
                "董卓", "齊桓公", "1管", "kr", "KR", "&KR0862;", "&kr0862;",
                "_", "%", "夷吾", "子"]

# 别名裁决：全称、唯一短称、歧义短称、上下文裁决、落不了地、空
ENTITY_ALIAS_CASES = [
    ("齊桓公", ""), ("晉文公", ""), ("百里奚", "秦穆公與百里奚"),
    ("重耳", ""), ("小白", ""), ("管子", ""), ("鮑叔", "鮑叔牙"),
    ("繆公", ""), ("夷吾", "晉"), ("夷吾", "管"), ("夷吾", ""),
    ("桓公", ""), ("桓公", "秦桓公"), ("桓公", "魯桓公"),
    ("文公", "晉"), ("穆公", "秦穆公"), ("公孫鞅", ""),
    ("董卓", ""), ("", ""), ("重耳", "重耳出亡"),
]
ENTITY_SHORT_CASES = [("桓公", ""), ("桓公", "魯"), ("文公", "晉"), ("穆公", "秦"),
                      ("重耳", ""), ("侯使", "")]
ENTITY_BARE_CASES = [("鄭穆公之子", "穆公"), ("秦穆公", "穆公"), ("穆公曰", "穆公"),
                     ("晉穆公", "穆公"), ("穆公", "穆公"), ("子穆公", "穆公"),
                     ("晉文公重耳", "文公"), ("文公", "文公"), ("", "穆公")]


def entity_samples() -> dict:
    """实体层的固定样本集（Python 侧写，JS 侧读同一份）。"""
    cols, rows, dicts = load_corpus()

    def kind_of(r):
        return decode(r, cols, dicts, "kind")

    def text_of(r):
        return decode(r, cols, dicts, "text_orig") or ""

    texts: list[str] = []
    seen: set[str] = set()

    def take(t: str) -> None:
        if t not in seen:
            seen.add(t)
            texts.append(t)

    for mark in ENTITY_MARKERS:
        for r in rows:
            if kind_of(r) == "passage" and mark in text_of(r):
                take(text_of(r))
                break
    # 星形字：Python 按码位切两字窗格、JS 按 UTF-16 单元切，差别只在这种行上出现
    for r in rows:
        if kind_of(r) == "passage" and any(ord(c) > 0xFFFF for c in text_of(r)):
            take(text_of(r))
            break
    # 最长的一条正文（跨片段多，实体重复出现的形态最复杂）
    longest = max((text_of(r) for r in rows if kind_of(r) == "passage"), key=len)
    take(longest)
    for t in ENTITY_EXTRA:
        take(t)

    words = list(ENTITY_WORDS)
    # 语料里实际存在的 &XXXX; 形态（考 ASCII 大小写折叠），按原文取
    for r in rows:
        m = re.search(r"&[A-Za-z0-9]{2,8};", text_of(r))
        if m:
            words.append(m.group(0))
            break
    return {"texts": texts, "words": words,
            "alias_cases": ENTITY_ALIAS_CASES,
            "short_cases": ENTITY_SHORT_CASES,
            "bare_cases": ENTITY_BARE_CASES}


# 问题集：analyze 与 expand 都跑这一份。挑的都是**会走岔路**的形态，不是随手举例。
#
# 注意：无论怎么挑，都覆盖不到 question.find_entities 的 weight=0.3 分支 ——
# 那一档在 Python 原版里**取不到**（find_in_text 落地失败时报的是 word 自己，
# 于是 ent != word 只可能是裁决成功，这里再问一次必然还是成功；全语料 2,937 个
# 命中对遍历核验，反例 0 个）。这是实测结论，不是样本没挑好，别白费力气加样本。
QUESTION_SAMPLES = [
    # --- 第四阶段验收问题原句 ---
    "管仲是怎么死的", "重耳流亡了多少年", "齊桓公与管仲是什么关系",
    "秦穆公和百里奚是什么关系", "商鞅变法是怎么回事", "董卓是什么人",
    "晉文公是谁",
    # --- 繁简：用户多半用简体问，语料是繁体 ---
    "齐桓公", "晋文公重耳", "卫鞅变法", "秦国为什么强大",
    "管仲是怎么死的。", "齊桓公是怎麼死的",
    # --- 短称：无上下文（裁决不了，0.3）vs 有上下文（0.7）vs 歧义（weak） ---
    "桓公", "桓公问于管子", "鲁桓公", "秦桓公", "文公", "晋文公",
    "夷吾", "夷吾管仲", "夷吾晋", "穆公", "秦穆公与百里奚",
    # --- 别名的三种档位：已核实且语料里有 / 语料里查无 / 多家共用 ---
    "重耳", "小白立为齐君", "管夷吾", "鲍叔牙", "公孙鞅", "卫鞅",
    "繆公", "缪公", "百里奚", "趙盾", "赵盾",
    # --- 意图：死亡 / 出亡 / 时长 / 出生 / 关系 / 事件 / 原因 / 时间 / 地点 / 身份 ---
    "重耳出亡", "重耳流亡十九年", "齐桓公卒", "齐桓公怎么死的",
    "齐桓公是哪年死的", "齐桓公死于何时", "管仲出生于哪里",
    "重耳是谁的儿子", "晋文公和赵盾是什么关系",
    "城濮之战谁赢了", "秦晋之战发生了什么", "晋国为什么灭亡",
    "齐桓公什么时候即位", "重耳在哪里流亡",
    # --- 纪年：在位纪年 / 公元前 ---
    "僖公三十年发生了什么", "周赧王元年", "公元前651年", "651 BC",
    "公元前 651 年", "651BC", "公元前651年管仲卒", "651 年 前",
    # --- 无实体兜底：主题词切分（含句尾泛称、功能短语、4/3/2 字窗口） ---
    "城濮之战", "长平之战是谁赢了", "这件事是怎么回事",
    "变法改革发生了什么", "董卓是什么人", "这件事",
    # --- 空 / 纯空白 / 纯标点 / 单字 ---
    "", "   ", " 管仲", "？", "之", "亡",
]


def question_samples() -> dict:
    """问题集的单一事实来源（Python 侧写，JS 侧读同一份）。"""
    return {"questions": QUESTION_SAMPLES}


def check_question() -> bool:
    """问题分析 + 检索式扩展：整棵结果树逐字段对拍。

    这条检查压的是 three 处最容易走样的地方：
      · triggered 的排除表窗口（「流亡了多少年」里的「亡」不算死亡意图）；
      · find_in_text → 权重分档（全称 1.0 / 语境裁决 0.7 / 候选 0.3）；
      · term_roles 的别名分档（重耳 走 alias 不走 weak）与 _topic_terms 的
        切词窗口。任何一处偏一点，问句的检索词表就整体换样。
    """
    print("⑦ 问题分析 + 检索式扩展：固定问题集（Python vs JS）")
    questions = question_samples()["questions"]
    tmp = config.HISTORY_AI_DIR / "data" / "_questions.json"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps(question_samples(), ensure_ascii=False),
                   encoding="utf-8")
    try:
        js = run_node("question", corpus=CORPUS, samples=tmp)
    finally:
        tmp.unlink(missing_ok=True)

    py_q = [question.analyze(s) for s in questions]
    pairs = [
        ("分析", [q.as_dict() for q in py_q], js["analyses"]),
        ("扩展", [query_expansion.expand(q).as_dict() for q in py_q],
         js["expansions"]),
    ]
    ok = True
    for label, py, jsr in pairs:
        # span 在 Python 侧是 tuple、JSON 里是 list，先过一遍 JSON 规范化
        # （浮点数不受影响：1.0 == 1 在 Python 里为真，而 notes 里的浮点
        #  写法已经由 pyFloat 对齐）。
        norm = json.loads(json.dumps(py, ensure_ascii=False))
        for i, (a, b) in enumerate(zip(norm, jsr)):
            diffs: list[str] = []
            diff_result(a, b, label, diffs, limit=4)
            if diffs:
                ok = False
                print(f"   [{i}] {_safe(questions[i])} **不符**")
                for d in diffs:
                    print(f"       {d}")
    print(f"   问题 {len(questions)} 条、"
          f"检索词组 {sum(len(e['all_terms']) for e in
                          (query_expansion.expand(q).as_dict() for q in py_q))} 个 —— "
          f"{'一致' if ok else '**有差异**'}")
    return ok


# 排序检查用的问题：每条针对一个打分档位，不是随手举例。
#   entity_exact + intent_hit + co_occur     管仲是怎么死的
#   multi_entity（两个规范名同段 +20）        秦穆公和百里奚是什么关系
#   entity_alias + asked_form（用户原词）     重耳流亡了多少年（同时压 asks_duration）
#   entity_weak（短称裁决不了，候选档）        桓公
#   intent_weak（兼用字，不触发共现加成）      重耳出亡
#   topic_hit（无人物实体时的兜底）           城濮之战谁赢了
#   like 命中含 % / _ 的字面量（转义路径）     公曰
RANKING_QUESTIONS = [
    "管仲是怎么死的", "秦穆公和百里奚是什么关系", "重耳流亡了多少年",
    "桓公", "重耳出亡", "城濮之战谁赢了", "公曰", "齊桓公与管仲是什么关系",
]


def ranking_samples() -> dict:
    """构造每题的候选池（用 retrieve._like_any 的**同一段 SQL**），连同全部检索词。

    池子在 Python 侧取好交给 JS，是为了让这条检查只压 ranking.js：
    召回路径已由 search 检查覆盖，再测一遍只会掩盖真正的差异来源。
    """
    from search import retrieve as ret

    conn = api_db.connect()
    cases = []
    try:
        cur = conn.cursor()
        for qtext in RANKING_QUESTIONS:
            q = question.analyze(qtext)
            exp = query_expansion.expand(q)
            g = lambda role: [t for grp in exp.groups if grp.role == role  # noqa: E731
                              for t in grp.terms]
            entity_terms, alias_terms = g("entity"), g("alias")
            weak_terms, intent_terms = g("weak"), g("intent")
            weak_intent, topic_terms = g("intent_weak"), g("topic")
            pool = ret._like_any(cur, entity_terms + alias_terms + weak_terms,
                                 ret.POOL_LIMIT)                        # noqa: SLF001
            if not pool:
                pool = ret._like_any(cur, topic_terms + intent_terms,
                                     ret.POOL_LIMIT)                    # noqa: SLF001
            cases.append({
                "q": qtext,
                "entity_terms": entity_terms, "alias_terms": alias_terms,
                "weak_terms": weak_terms, "intent_terms": intent_terms,
                "intent_weak": weak_intent, "topic_terms": topic_terms,
                "asks_duration": getattr(exp, "asks_duration", False),
                "asked_forms": [e["matched"] for e in q.entities if e.get("matched")],
                "entity_forms": [[list(x) for x in forms]
                                 for forms in ret._entity_forms(q)],   # noqa: SLF001
                "pool": [{"passage_id": c.passage_id, "file_id": c.file_id,
                          "row_no": c.row_no, "seq": c.seq,
                          "text_orig": c.text_orig, "book_id": c.book_id,
                          "book_title": c.book_title, "layer": c.layer}
                         for c in pool],
            })
    finally:
        conn.close()
    return {"cases": cases}


def py_rank(case: dict) -> list:
    """在同一批候选池上跑 Python 侧排序，输出与 JS 侧同形的明细。"""
    from search import ranking

    cands = [ranking.Candidate(**r) for r in case["pool"]]
    ranked = ranking.rank(
        cands, case["entity_terms"], case["alias_terms"], case["weak_terms"],
        case["intent_terms"], case["asks_duration"], case["intent_weak"],
        case["topic_terms"], case["asked_forms"],
        [[tuple(x) for x in forms] for forms in case["entity_forms"]])
    return [{"passage_id": c.passage_id, "score": c.score,
             "hits": c.hits, "detail": c.detail} for c in ranked]


def check_ranking() -> bool:
    """排序层：同一批候选池上的打分与排序（Python vs JS）。

    这条检查压的是「每个分项到底加了几分」和「命中理由怎么说」——
    分数错一位、detail 的键名差一个字，前端的「为什么相关」就会骗人。
    尤其注意 detail 里含有 `弱意图词['卒', '死']` 这种 **Python repr 形状的键**，
    照抄成 JS 的默认数组字符串（`卒,死`）在界面上看不出差别，但两边必须逐字一致。
    """
    print("⑧ 排序层：固定候选池上的打分与排序（Python vs JS）")
    samples = ranking_samples()
    sizes = [len(c["pool"]) for c in samples["cases"]]
    print(f"   问题 {len(samples['cases'])} 条，候选池大小 {sizes}")
    tmp = config.HISTORY_AI_DIR / "data" / "_ranking.json"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps(samples, ensure_ascii=False), encoding="utf-8")
    try:
        js = run_node("ranking", corpus=CORPUS, samples=tmp)["results"]
    finally:
        tmp.unlink(missing_ok=True)

    ok = True
    for case, jr in zip(samples["cases"], js):
        pr = py_rank(case)
        diffs: list[str] = []
        diff_result(pr, jr, "排序", diffs, limit=4)
        if diffs:
            ok = False
            print(f"   {_safe(case['q'])}（池 {len(case['pool'])}）**不符**")
            for d in diffs:
                print(f"       {d}")
        else:
            top = pr[0] if pr else None
            print(f"   {_safe(case['q']):<28} 池 {len(case['pool']):>5} "
                  f"首条 score={top['score'] if top else None} "
                  f"detail={_safe(top['detail']) if top else None}")
    print(f"   排序层 {'一致' if ok else '**有差异**'}")
    return ok


# Result Block 检查用的查询：在 check_search 那批之外，专门压组装规则。
#   mode 三档          片段长度上限不同（target/max_passages/max_chars 三条都用上）
#   text_mode 三种     繁简双轨只加字段，text 仍须是原样的 text_orig
#   单字 → like 路      该路 score 为 None，排序键退化成 0
#   超 600 命中         压全量组装（原 MAX_BLOCKS_PER_QUERY 截断已解除，见 6.1）
#   翻到后段页          压 total 的真实性与 has_more（之/齊 的块数远超 600）
RB_QUERIES = [
    # (关键词, 书, 版本, 页, 每页, 长度, 繁简)
    ("齊桓公", None, None, 1, 5, "standard", "orig"),
    ("齊桓公", None, None, 1, 5, "short", "orig"),
    ("齊桓公", None, None, 1, 5, "long", "orig"),
    ("齊桓公", None, None, 2, 5, "standard", "orig"),
    ("齊桓公", None, None, 1, 20, "standard", "simplified"),
    ("齊桓公", None, None, 1, 20, "standard", "both"),
    ("管仲", None, None, 1, 10, "standard", "orig"),
    ("管仲是怎么死的", None, None, 1, 5, "standard", "orig"),   # 带意图词的长句
    ("秦穆公 百里奚", None, None, 1, 10, "standard", "orig"),
    ("齊", None, None, 1, 3, "standard", "orig"),               # 单字 → like，score=None
    ("之", None, None, 1, 2, "short", "orig"),                  # 海量命中
    # 第 601–700 块：解除 600 上限**之前**这一段是拿不到的（旧实现只组装前 600
    # 条命中 → 328 块，翻到第 17 页就空了）。这条钉住「后段命中真的可达」。
    ("之", None, None, 7, 100, "short", "orig"),
    # 超 600 命中要**三条路径各压一次**：截断取的是命中表的前 600 条，而三方
    # 命中表的**产出顺序**不同（Python 靠 SQLite 扫 passages 的 rowid 序，
    # JS 靠 likeScan 的打包序）—— 只压 like 路的话，fts/bigram 路的顺序错了
    # 也看不出来。这三条分别落在 fts / bigram / like 三条路上。
    ("元年。", None, None, 1, 5, "standard", "orig"),            # fts     1069 命中
    ("元年。", None, None, 3, 5, "standard", "orig"),            # fts，截断后的分页
    ("大夫", None, None, 1, 5, "standard", "orig"),              # bigram  1443 命中
    ("王", "史记", None, 1, 5, "standard", "orig"),               # like    8832 命中（带书过滤）
    # 篇名命中（第六点一阶段）：三条形态各压一处
    ("秦始皇本紀", None, None, 1, 5, "standard", "orig"),  # 正文 0 命中、篇名 1 命中
    ("五帝本紀", None, None, 1, 5, "standard", "orig"),    # 正文+篇名同时命中 → 去重、both
    ("秦本紀", None, None, 1, 5, "standard", "orig"),      # 精确匹配要压过「秦始皇本紀」
    ("公", None, None, 1, 3, "standard", "orig"),         # 篇名被 MAX_SECTION_BLOCKS 截断
    ("城濮之战", None, None, 1, 5, "standard", "orig"),
    ("秦穆公", "史记", None, 1, 5, "standard", "orig"),          # 书+版本过滤下的组装
    ("秦穆公", None, "sbck", 1, 5, "standard", "orig"),
    ("董卓", None, None, 1, 5, "standard", "orig"),             # 空结果
    ("", None, None, 1, 5, "standard", "orig"),                 # 参数非法
    ("齊桓公", None, None, 0, 5, "standard", "orig"),            # 页码非法
    ("齊桓公", None, None, 1, 5, "bogus", "orig"),              # 长度非法
]

# 上下文展开用例：(passage_id, direction, count, before_pid, after_pid)
# passage_id 由 Python 侧现场取（见 rb_samples），保证两侧看到同一条记录。
RB_EXPAND_TEMPLATES = [
    ("before", 20, None, None),
    ("after", 20, None, None),
    ("both", 20, None, None),
    ("before", 3, None, None),      # 条数被人为收窄 → ends 应为 False（外面还有）
    ("after", 100, None, None),
]


def rb_samples() -> dict:
    """Result Block 的查询集 + 展开用例（Python 侧写，JS 侧读同一份）。"""
    conn = api_db.connect()
    try:
        cur = conn.cursor()
        # 展开用例的锚点：挑三个形态不同的片段（有段号的、无段号的、层为注释候选的）
        pids = [r["passage_id"] for r in cur.execute(
            "SELECT p.passage_id FROM passages p WHERE p.kind = 'passage' "
            "AND p.file_id = 82 AND p.row_no IN (4321, 4325, 4300) "
            "ORDER BY p.row_no LIMIT 3")]
        pids += [r["passage_id"] for r in cur.execute(
            "SELECT p.passage_id FROM passages p WHERE p.kind = 'passage' "
            "AND p.file_id = 94 AND p.row_no IN (1639, 1650) ORDER BY p.row_no")]
        if len(pids) < 5:                     # 语料换了也要有锚点，退而求其次
            pids = [r["passage_id"] for r in cur.execute(
                "SELECT passage_id FROM passages WHERE kind = 'passage' "
                "ORDER BY passage_id LIMIT 5")]
        expands = [{"passage_id": pids[i % len(pids)], "direction": d,
                    "count": c, "before_passage_id": b, "after_passage_id": a}
                   for i, (d, c, b, a) in enumerate(RB_EXPAND_TEMPLATES)]
    finally:
        conn.close()
    return {
        "queries": [{"q": q, "book": b, "edition": e, "page": p, "page_size": s,
                     "mode": m, "text_mode": t}
                    for q, b, e, p, s, m, t in RB_QUERIES],
        "expands": expands,
    }


def py_result_blocks(queries: list) -> list:
    from search import result_block as RB
    conn = api_db.connect()
    try:
        cur = conn.cursor()
        out = []
        for s in queries:
            try:
                out.append(RB.search_result_blocks(
                    cur, s["q"], s["book"], s["edition"], s["page"],
                    s["page_size"], s["mode"], s["text_mode"]))
            except ValueError as e:
                out.append({"error": str(e)})
        return out
    finally:
        conn.close()


def py_expand_blocks(expands: list) -> list:
    from search import result_block as RB
    conn = api_db.connect()
    try:
        cur = conn.cursor()
        out = []
        for s in expands:
            try:
                out.append(RB.expand_block(
                    cur, s["passage_id"], s["direction"], s["count"],
                    s["before_passage_id"], s["after_passage_id"]))
            except (ValueError, KeyError) as e:
                out.append({"error": str(e)})
        return out
    finally:
        conn.close()


def check_result_block() -> bool:
    """Result Block：组装边界、合并去重、繁简双轨、可展开标记、上下文展开。

    这条检查压的是第三阶段最容易走样的三处：
      · 行窗口语义（余量 + 上限截断）—— JS 若图省事返回全文件，片段长度、
        more_before/more_after 全都会变；
      · can_take 的参照行规则（参照永远是「最近一条真正并入的 passage」，
        不是刚扫到的那行）—— 差一步就会一路读穿到文件开头；
      · 合并后重算 reach_edges —— 不重算的话合并块永远显示不出可展开。
    """
    print("⑨ Result Block：组装 + 展开（Python vs JS）")
    samples = rb_samples()
    tmp = config.HISTORY_AI_DIR / "data" / "_rb.json"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps(samples, ensure_ascii=False), encoding="utf-8")
    try:
        js = run_node("result-block", corpus=CORPUS, samples=tmp)
    finally:
        tmp.unlink(missing_ok=True)

    py_q = py_result_blocks(samples["queries"])
    ok = True
    for spec, a, b in zip(samples["queries"], py_q, js["results"]):
        label = (f"{spec['q']!r} book={spec['book']} ed={spec['edition']} "
                 f"p={spec['page']}/{spec['page_size']} {spec['mode']}/{spec['text_mode']}")
        if "error" in a or "error" in b:
            same = a.get("error") == b.get("error")
            print(f"   {label:<58} 报错 {'一致' if same else '**不符**'}")
            if not same:
                print(f"       py={_safe(a.get('error'))} js={_safe(b.get('error'))}")
            ok = ok and same
            continue
        diffs: list[str] = []
        diff_result(a, b, "结果", diffs, limit=6)
        flag = "OK" if not diffs else "**不符**"
        print(f"   {label:<58} 命中 {a['hit_total']:<6} 片段 {a['total']:<5} "
              f"{a['exec_mode']:<7} {flag}")
        for d in diffs[:6]:
            print(f"       {d}")
        ok = ok and not diffs

    py_e = py_expand_blocks(samples["expands"])
    print(f"   上下文展开 {len(samples['expands'])} 例")
    for spec, a, b in zip(samples["expands"], py_e, js["expands"]):
        label = (f"pid={spec['passage_id']} {spec['direction']}/{spec['count']}")
        if "error" in a or "error" in b:
            same = a.get("error") == b.get("error")
            print(f"   {label:<58} 报错 {'一致' if same else '**不符**'}")
            if not same:
                print(f"       py={_safe(a.get('error'))} js={_safe(b.get('error'))}")
            ok = ok and same
            continue
        diffs = []
        diff_result(a, b, "展开", diffs, limit=6)
        flag = "OK" if not diffs else "**不符**"
        print(f"   {label:<58} 新增 {a['added']:<4} 到头部={a['reaches_head']} "
              f"到尾部={a['reaches_tail']} {flag}")
        for d in diffs[:6]:
            print(f"       {d}")
        ok = ok and not diffs

    print(f"   Result Block {'一致' if ok else '**有差异**'}")
    return ok


def diff_map(py: dict, js: dict, label: str, limit: int = 8) -> bool:
    """比两个映射（键→标量，或键→子映射）。差异逐条打出来，不静默。"""
    ok = True
    only_py = sorted(set(py) - set(js))
    only_js = sorted(set(js) - set(py))
    if only_py or only_js:
        ok = False
        print(f"   {label}: 键集合不同（仅 py {len(only_py)} 个，仅 js {len(only_js)} 个）")
        for k in only_py[:limit]:
            print(f"     仅 py 有 {_safe(k)} = {_safe(py[k])}")
        for k in only_js[:limit]:
            print(f"     仅 js 有 {_safe(k)} = {_safe(js[k])}")
    bad = sorted(k for k in set(py) & set(js) if py[k] != js[k])
    if bad:
        ok = False
        print(f"   {label}: {len(bad)} 个键取值不同")
        for k in bad[:limit]:
            print(f"     {_safe(k)}: py={_safe(py[k])} js={_safe(js[k])}")
    return ok


def diff_list(py: list, js: list, label: str, limit: int = 6) -> bool:
    """比两个序列，逐项对拍（用于样本集上的裁决结果）。"""
    if len(py) != len(js):
        print(f"   {label}: 条数 py={len(py)} js={len(js)}")
        return False
    bad = [i for i, (a, b) in enumerate(zip(py, js)) if a != b]
    if bad:
        print(f"   {label}: {len(bad)} / {len(py)} 项不同")
        for i in bad[:limit]:
            print(f"     [{i}] py={_safe(py[i])} js={_safe(js[i])}")
        return False
    return True


def check_entities() -> bool:
    """实体层：全语料扫描产物 + 固定样本上的裁决。

    这条检查压的是 entities.js 最容易走样的三处：
      · 两字窗格按**码位**还是按 UTF-16 单元切（星形字行）；
      · 短称分布是**合并**已核实别名还是替换（「桓公」「夷吾」的候选顺序）；
      · occurs_in_corpus 的 LIKE 语义（ASCII 折叠、通配符、NULL 与空串之别）。
    """
    print("⑥ 实体层：全语料扫描 + 固定样本裁决（Python vs JS）")
    samples = entity_samples()
    print(f"   样本：正文 {len(samples['texts'])} 条、词 {len(samples['words'])} 个、"
          f"裁决 {len(samples['alias_cases'])} 例")
    tmp = config.HISTORY_AI_DIR / "data" / "_entities.json"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps(samples, ensure_ascii=False), encoding="utf-8")
    try:
        js = run_node("entities", corpus=CORPUS, samples=tmp)
    finally:
        tmp.unlink(missing_ok=True)

    ok = True
    if list(entities.STATES) != js["states"]:
        print(f"   STATES 不同：py={entities.STATES} js={js['states']}")
        ok = False
    if list(entities.STATES_EXTRA) != js["statesExtra"]:
        print(f"   STATES_EXTRA 不同：py={entities.STATES_EXTRA} js={js['statesExtra']}")
        ok = False
    if entities.SHORT_MIN_BARE != js["shortMinBare"]:
        print(f"   SHORT_MIN_BARE 不同：py={entities.SHORT_MIN_BARE} "
              f"js={js['shortMinBare']}")
        ok = False

    ok &= diff_map(entities.curated(), js["curated"], "已核实实体表")
    ok &= diff_map(entities.ambiguous_heads(), js["ambiguous"], "歧义短称表")

    full, short, bare = entities._corpus_scan(DB_PATH)          # noqa: SLF001
    names = entities.corpus_names()
    print(f"   全称 {len(names)}、短称 {len(short)}、裸计数 {len(bare)}、"
          f"已认实体 {len(entities.known_entities())}")
    ok &= diff_map(names, js["full"], "语料全称表")
    ok &= diff_map(entities.short_forms(), js["short"], "短称分布表")
    ok &= diff_map(bare, js["bare"], "短称裸计数表")
    ok &= diff_list(entities.known_entities(), js["known"], "已知实体全称表")

    ok &= diff_list([entities.occurs_in_corpus(w) for w in samples["words"]],
                    js["occurs"], "occurs_in_corpus")
    ok &= diff_list([list(entities.resolve_alias(a, c)) for a, c in
                     samples["alias_cases"]], js["resolveAlias"], "resolve_alias")
    ok &= diff_list([entities.resolve_short(s, DB_PATH, c) for s, c in
                     samples["short_cases"]], js["resolveShort"], "resolve_short")
    ok &= diff_list([entities.bare_hit(t, s) for t, s in samples["bare_cases"]],
                    js["bareHit"], "bare_hit")
    ok &= diff_list([[[e, w, s, t] for e, w, s, t in entities.find_in_text(t, DB_PATH)]
                     for t in samples["texts"]],
                    [[[e, w, s, t] for e, w, s, t in r] for r in js["findInText"]],
                    "find_in_text")
    print(f"   实体层 {'一致' if ok else '**有差异**'}")
    return ok


# 事件聚合检查用的问题集。要覆盖到的形态：
#   多史书并列（齊桓公 / 重耳）、同书多篇、意图问句（管仲是怎么死的 → 跨批组装）、
#   双实体问句（秦穆公和百里奚…）、只有主题词没有实体（城濮之战 → 兜底池）、
#   空结果（董卓）。mode/text_mode 各换一档，确保繁简与长度只改字段不改结构。
AGG_QUESTIONS = [
    ("齊桓公", "standard", "orig"),
    ("重耳", "standard", "orig"),
    ("管仲", "standard", "orig"),
    ("管仲是怎么死的", "standard", "orig"),
    ("秦穆公和百里奚是什么关系", "standard", "orig"),
    ("城濮之战", "standard", "orig"),
    ("董卓", "standard", "orig"),
    ("齊桓公", "short", "orig"),
    ("齊桓公", "long", "both"),
]

# 序列化进样本的候选条数。aggregate 只用前 TOP_FOR_BLOCKS（40）条组装，
# 但第二条 note 要报「其余 N 条未展开」，所以样本比 40 多留几条，
# 让「picked < ranked」这个分支两侧都走到（两侧看到的是**同一份**截断列表）。
AGG_SAMPLE_TOP = 45


def agg_samples() -> dict:
    """事件聚合的用例：Python 侧跑完整 retrieve+rank，把候选序列化给 JS。"""
    from search import retrieve as R
    conn = api_db.connect()
    try:
        cur = conn.cursor()
        cases = []
        for q, mode, text_mode in AGG_QUESTIONS:
            res = R.retrieve(cur, q)
            cases.append({
                "q": q, "mode": mode, "text_mode": text_mode,
                "top": 40,
                "ranked": [{
                    "passage_id": c.passage_id, "file_id": c.file_id,
                    "row_no": c.row_no, "seq": c.seq,
                    "book_id": c.book_id, "book_title": c.book_title,
                    "text_orig": c.text_orig, "score": c.score,
                    "hits": list(c.hits), "detail": dict(c.detail),
                    "layer": c.layer,
                } for c in res.ranked[:AGG_SAMPLE_TOP]],
            })
        return {"cases": cases}
    finally:
        conn.close()


def py_aggregate(cases: list) -> list:
    from search import aggregate as agg
    from search import ranking
    conn = api_db.connect()
    try:
        cur = conn.cursor()
        out = []
        for cs in cases:
            ranked = [ranking.Candidate(**o) for o in cs["ranked"]]
            out.append(agg.aggregate(cur, ranked, cs["mode"], cs["top"],
                                     cs["text_mode"]))
        return out
    finally:
        conn.close()


def check_aggregate() -> bool:
    """事件聚合：block → 事件 → 跨史书对照。

    这条检查压的是聚合独有的几处规则：
      · `_cluster` 的分批（同文件里命中相隔超过半个行窗口就要分两批）——
        不分批的话窗口盖不住远处的命中，几条候选会被静默丢掉；
      · `_dedupe` 的「保宽的」包含判断 —— 改成并集就会拼出中间缺行的正文；
      · `_struct_key` 的合并条件（同文件 + 结构键完全相同）—— 放宽就会把
        不同篇卷的记录说成同一件事；
      · 事件排序的第二关键字是**名次**不是命中条数 —— 换了排序键，用户
        点开第一件事看到的就不是排名最高的那条命中了。

    候选池由 Python 侧给出（跑完整 retrieve+rank 再序列化），所以这条检查
    只压 aggregate.js 本身；召回与排序各自已有检查覆盖。
    """
    print("⑩ 事件聚合：block → 事件 → 跨史书对照（Python vs JS）")
    samples = agg_samples()
    tmp = config.HISTORY_AI_DIR / "data" / "_agg.json"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    # 这份样本里带着**正文**（候选的 text_orig），可能出现孤立代理项：
    # ensure_ascii=True 把它写成 \udXXX 转义，写文件与 JSON.parse 都稳当。
    tmp.write_text(json.dumps(samples, ensure_ascii=True), encoding="utf-8")
    try:
        js = run_node("aggregate", corpus=CORPUS, samples=tmp)["cases"]
    finally:
        tmp.unlink(missing_ok=True)

    py = py_aggregate(samples["cases"])
    ok = True
    for cs, a, b in zip(samples["cases"], py, js):
        label = f"{cs['q']!r} {cs['mode']}/{cs['text_mode']}"
        if "error" in b:
            ok = False
            print(f"   {label:<34} **JS 报错** {_safe(b['error'])}")
            continue
        diffs: list[str] = []
        diff_result(a, b, "聚合", diffs)
        if diffs:
            ok = False
            print(f"   {label:<34} 候选 {len(cs['ranked'])}  **不符**")
            for d in diffs:
                print(f"       {d}")
        else:
            print(f"   {label:<34} 候选 {len(cs['ranked']):>3}  "
                  f"事件 {len(a['events']):>2}  片段 {a['n_blocks']:>3}  "
                  f"史书 {len(a['sources'])}  首事件 "
                  f"{a['events'][0]['book_title'] if a['events'] else '—'} OK")
    print(f"   事件聚合 {'一致' if ok else '**有差异**'}")
    return ok


# 提问链路检查用的问题集：七个验收问题（§14）+ 几处边界。
# 「管仲和鲍叔牙」是多实体但**没有同段**的情形，专门压那句必须为真的否定结论；
# 空问题压 analyze 的早退分支（两侧都该给出「空问题」而不是崩）。
RETRIEVE_QUESTIONS = [
    ("齐桓公是怎么死的？", "standard", "orig"),
    ("管仲是怎么死的？", "standard", "orig"),
    ("重耳流亡", "standard", "orig"),
    ("商鞅变法", "standard", "orig"),
    ("秦穆公和百里奚", "standard", "orig"),
    ("管仲和鲍叔牙", "standard", "orig"),
    ("城濮之战", "standard", "orig"),
    ("董卓", "standard", "orig"),
    ("齊桓公", "long", "both"),
    ("管仲是怎么死的？", "short", "simplified"),
    ("", "standard", "orig"),
]

RETRIEVE_TOP = 20       # 与 api/main.py 的 as_dict 调用一致（results 只出前 20 条）

# 召回扫描的**直接**对拍用例：(词表, LIMIT)。
#
# 为什么单独压这一项：_like_any 的 SQL 没有 ORDER BY，靠 SQLite 扫 passages 给出
# rowid 序（= passage_id 升序），而 `LIMIT ?` 截的正是这个顺序。JS 侧 likeScan
# 走的是打包序，必须显式重排。这条契约在**整条提问链路**里压不到 ——
# 全语料上策展实体的召回池最大只有 642 条（晉文公/重耳），远够不着 POOL_LIMIT
# 的 2000，LIMIT 不生效，顺序就只能通过 ranking 的同分次序间接体现（样本里
# 没出现）。所以这里直接调 likeAny 比 passage_id 序列。
#
# 单字词表都故意选超 2000 命中的（之 33241 / 不 18360 / 王 15536），让 LIMIT
# 真的截断；'a' 则是 ASCII 大小写折叠的等价性探针（SQLite 的 LIKE 只折 ASCII，
# JS 侧走 asciiFold + 子串匹配，两者必须一致）。
RETRIEVE_SCANS = [
    (["之"], 2000),
    (["不"], 1500),
    (["王", "侯"], 2000),
    (["齊", "楚"], 800),
    (["a"], 2000),
    (["卒", "薨", "崩", "沒"], 300),
]


def py_retrieve(cases: list) -> list:
    from search import retrieve as R
    conn = api_db.connect()
    try:
        cur = conn.cursor()
        out = []
        for cs in cases:
            try:
                res = R.retrieve(cur, cs["q"])
                out.append(R.as_dict(res, cs["top"], cur, cs["mode"], True,
                                     cs["text_mode"]))
            except ValueError as e:
                out.append({"error": str(e)})
        return out
    finally:
        conn.close()


def py_like_any(scans: list) -> list:
    from search import retrieve as R
    conn = api_db.connect()
    try:
        cur = conn.cursor()
        return [[c.passage_id for c in R._like_any(cur, s["terms"], s["limit"])]
                for s in scans]
    finally:
        conn.close()


def check_retrieve() -> bool:
    """提问链路端到端：analyze → expand → 两池召回 → 排序 → 事件聚合。

    与前几条不同，这条**不给候选池**：Python 侧只给问题原文，召回与排序都在
    JS 侧自己跑。压的是 retrieve.js 独有的几处：
      · 两池召回（实体池 / 兜底池）的**顺序与 LIMIT 截断** —— SQL 的 LIMIT
        截的是 rowid 序，JS 若不按 passage_id 重排，池子满 2000 时两边装的是
        不同的段落；
      · 「同段」判定要用**全部写法**（别名也算），只看规范名会得出「语料里
        没有一段同时写着 A 与 B」这种**假的**否定结论；
      · as_dict 的字段形状（前端直接吃这个结构）。
    """
    print("⑪ 提问链路：召回 → 排序 → 聚合（question + query_expansion + "
          "ranking + aggregate 端到端）")
    cases = [{"q": q, "mode": m, "text_mode": t, "top": RETRIEVE_TOP}
             for q, m, t in RETRIEVE_QUESTIONS]
    scans = [{"terms": t, "limit": n} for t, n in RETRIEVE_SCANS]
    tmp = config.HISTORY_AI_DIR / "data" / "_retr.json"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    # 只有问题原文，没有正文 —— 用不着 ensure_ascii=True 的转义兜底，
    # 但保持一致更省心（两边读到的 JSON 完全一样）。
    tmp.write_text(json.dumps({"cases": cases, "scans": scans},
                              ensure_ascii=True), encoding="utf-8")
    try:
        js_out = run_node("retrieve", corpus=CORPUS, samples=tmp)
    finally:
        tmp.unlink(missing_ok=True)
    js = js_out["cases"]

    py = py_retrieve(cases)
    ok = True
    for spec, a, b in zip(cases, py, js):
        label = f"{spec['q']!r} {spec['mode']}/{spec['text_mode']}"
        if "error" in a or "error" in b:
            same = a.get("error") == b.get("error")
            print(f"   {label:<30} 报错 {'一致' if same else '**不符**'}")
            if not same:
                print(f"       py={_safe(a.get('error'))} js={_safe(b.get('error'))}")
            ok = ok and same
            continue
        diffs: list[str] = []
        diff_result(a, b, "结果", diffs, limit=4)
        agg_py = a.get("aggregation", {})
        if diffs:
            ok = False
            print(f"   {label:<30} 候选 {a['counts']['entity_pool']:>4}"
                  f"/{a['counts']['fallback_pool']:<4} **不符**")
            for d in diffs:
                print(f"       {d}")
        else:
            print(f"   {label:<30} 候选 {a['counts']['entity_pool']:>4}"
                  f"/{a['counts']['fallback_pool']:<4} "
                  f"事件 {len(agg_py.get('events', [])):>3}  "
                  f"史书 {len(agg_py.get('sources', []))}  "
                  f"note {len(a['notes'])}  OK")
    # 召回扫描本身：词表 → passage_id 序列（顺序 + LIMIT 截断都在里面）
    py_scan = py_like_any(scans)
    js_scan = js_out["scans"]
    for spec, a, b in zip(scans, py_scan, js_scan):
        if a == b:
            print(f"   扫描 {str(spec['terms']):<26} limit={spec['limit']:<5} "
                  f"命中 {len(a):>5}  首/末 {a[0] if a else '—'}"
                  f"/{a[-1] if a else '—'} OK")
        else:
            ok = False
            print(f"   扫描 {str(spec['terms']):<26} limit={spec['limit']:<5} "
                  f"**不符** py={len(a)} js={len(b)}")
            for i in range(min(len(a), len(b))):
                if a[i] != b[i]:
                    print(f"       首个不同位 [{i}] py={a[i]} js={b[i]}")
                    break
    print(f"   提问链路 {'一致' if ok else '**有差异**'}")
    return ok


def static_api_paths() -> list[str]:
    """静态分发器要对拍的 URL 清单（单一事实来源）。

    覆盖 10 条路由 × 正常/边界/错误三类。几个记录 id **从库里现查**而不是写死：
    写死会在换语料后悄悄指向不存在的记录，那时「两边都报 404」也算通过 ——
    检查还在跑，但已经什么都不测了。
    """
    conn = api_db.connect()
    try:
        cur = conn.cursor()
        one = lambda sql, *a: cur.execute(sql, a).fetchone()  # noqa: E731
        book = one("SELECT book_id FROM books ORDER BY book_id LIMIT 1")[0]
        fid = one("SELECT file_id FROM files ORDER BY file_id LIMIT 1")[0]
        # 行数最多的文件：raw 有内容可翻页，passages 有足够行数试 LIMIT/OFFSET
        big = one("SELECT file_id FROM passages GROUP BY file_id "
                  "ORDER BY COUNT(*) DESC LIMIT 1")[0]
        pid = one("SELECT passage_id FROM passages WHERE kind='passage' "
                  "ORDER BY passage_id LIMIT 1")[0]
        # 跨行块：**注释块/分段块**才有换行（正文一行一条记录），raw 的覆盖标注
        # 「一行一条目」全靠它，所以这条必须挑 comment。
        multi = one("SELECT passage_id FROM passages WHERE kind='comment' "
                    "AND text_orig LIKE '%' || char(10) || '%' "
                    "ORDER BY passage_id LIMIT 1")[0]
        other = one("SELECT passage_id FROM passages WHERE kind <> 'passage' "
                    "ORDER BY passage_id LIMIT 1")[0]
    finally:
        conn.close()

    Q = lambda s: quote(s, safe="")  # noqa: E731
    out = [
        # --- 元数据 -------------------------------------------------
        "/api/stats", "/api/books",
        f"/api/books/{book}/files", "/api/books/NOSUCHBOOK/files",
        f"/api/files/{fid}", "/api/files/99999999",
        # --- 文件内的记录列表：过滤、LIKE、分页、上下限 ---------------
        f"/api/files/{big}/passages",
        f"/api/files/{big}/passages?limit=5&offset=3",
        f"/api/files/{big}/passages?limit=9999",          # 上限 500
        # 负值 = SQLite 的 LIMIT -1 = 不限。挑小文件跑：big 不限量会是 30 MB
        # 的响应体，两边各序列化一次只为验证一个边界，不值得。
        f"/api/files/{fid}/passages?limit=-1&offset=2",
        f"/api/files/{big}/passages?limit=0",
        f"/api/files/{big}/passages?offset=100000",
        f"/api/files/{big}/passages?kind=passage&layer=main&status=ok",
        f"/api/files/{big}/passages?kind=",               # 空串 = 不过滤
        f"/api/files/{big}/passages?q={Q('王')}",
        f"/api/files/{big}/passages?q={Q('%')}",          # 未转义的通配符
        f"/api/files/{big}/passages?q={Q('_')}",
        f"/api/files/{big}/passages?q={Q('a%25b')}",      # 含 % 的普通串
        "/api/files/99999999/passages",
        # --- 原文对照（raw）：窗口、越界、缺文件 ---------------------
        f"/api/files/{fid}/raw",
        f"/api/files/{fid}/raw?start=10&end=20",
        f"/api/files/{fid}/raw?start=999999",
        f"/api/files/{fid}/raw?start=5&end=3",
        f"/api/files/{fid}/raw?start=0&end=2",
        f"/api/files/{fid}/raw?start=abc",                # int 失败回落默认
        f"/api/files/{big}/raw?start=2&end=4",
        "/api/files/99999999/raw",
        # --- 单条记录 + 上下文 --------------------------------------
        f"/api/passages/{pid}", f"/api/passages/{multi}",
        f"/api/passages/{other}", "/api/passages/99999999",
        f"/api/passages/{pid}/context",
        f"/api/passages/{pid}/context?before=0&after=0",
        f"/api/passages/{pid}/context?before=10&after=10",
        f"/api/passages/{pid}/context?before=99&after=-5",  # 钳到 [0,10]
        f"/api/passages/{multi}/context",
        f"/api/passages/{other}/context",
        "/api/passages/99999999/context",
        # --- 片段展开 -----------------------------------------------
        f"/api/blocks/{pid}", f"/api/blocks/{pid}?direction=after&count=3",
        f"/api/blocks/{pid}?direction=both&count=0",        # count 钳到 >=1
        f"/api/blocks/{pid}?direction=sideways",            # 非法 direction
        f"/api/blocks/{multi}",
        f"/api/blocks/{other}",                             # 非正文，展开不了
        "/api/blocks/99999999",
        # --- 检索：三条路径 × 分页 × 繁简 ----------------------------
        "/api/search",
        f"/api/search?q={Q('齊桓公')}",
        f"/api/search?q={Q('齊桓公')}&level=passage",
        f"/api/search?q={Q('齊桓公')}&level=passage&page=2&page_size=5",
        f"/api/search?q={Q('齊桓公')}&level=passage&page=0",      # 页码从 1 开始
        f"/api/search?q={Q('齊桓公')}&mode=short&text=simplified",
        f"/api/search?q={Q('齊桓公')}&mode=long&text=both&page_size=3",
        f"/api/search?q={Q('齊桓公')}&book={book}",
        f"/api/search?q={Q('齊桓公')}&edition=tls",
        f"/api/search?q={Q('大夫')}&level=passage",          # bigram 路径
        f"/api/search?q={Q('元年。')}&level=passage&page_size=3",  # 超 600 命中
        f"/api/search?q={Q('王')}&level=passage&book={book}",
        f"/api/search?q={Q('董卓')}",                        # 空结果
        f"/api/search?q={Q('')}&level=passage",
        f"/api/search?q={Q('齊桓公')}&level=bogus",          # 非法 level
        f"/api/search?q={Q('齊桓公')}&page_size=abc",        # int 失败回落默认
        f"/api/search?q=a+b",                                # `+` 保持字面量
        # --- 提问模式（第四阶段入口）--------------------------------
        f"/api/search?q={Q('齊桓公是怎么死的？')}&mode=question",
        f"/api/search?q={Q('齊桓公是怎么死的？')}&level=question&mode=long&text=both",
        f"/api/search?q={Q('管仲是怎么死的？')}&level=question&mode=short&text=simplified",
        f"/api/search?q={Q('城濮之战')}&level=question",
        f"/api/search?q={Q('董卓')}&level=question",
        f"/api/search?q={Q('')}&level=question",             # 请提供问题
        f"/api/search?q={Q('齊桓公')}&level=question&mode=bogus",
        f"/api/search?q={Q('齊桓公')}&level=question&text=bogus",
        # --- 未知道路 ------------------------------------------------
        "/api/nope", "/api/files/1/nope",
    ]
    return out


def check_static_api() -> bool:
    """静态分发器 vs 真 API：10 条路由的返回结构与错误消息逐字段对拍。

    公开站上没有 Python 进程，`/api/*` 全由 frontend/engine/static_api.js 在浏览器
    里重新实现。它一旦与 api/main.py 走偏，表现是「本地能看、线上不对」——
    而且是在公开站上。所以这条检查把整条链路（路由匹配、参数解析、错误消息、
    返回键集）拉出来对一遍：**真 API 的响应是唯一事实来源**。
    """
    print("⑫ 静态分发器：10 条路由 vs 真 API（Python http vs JS 内存语料）")
    from scripts.site.export_site import LocalApi  # 循环导入无害：只在调用时导入

    spec = static_api_paths()
    tmp = config.HISTORY_AI_DIR / "data" / "_static_paths.json"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    try:
        js = run_node("static-api", staticdir=CORPUS.parent, paths=tmp)
    finally:
        tmp.unlink(missing_ok=True)

    with LocalApi() as api:
        py = [api.get(p, allow_error=True) for p in spec]
    # /api/stats 会带出本机语料库绝对路径（api/main.py 的 d["library"]，以及
    # import_runs 整行里的同名列）。静态版**有意剥掉**这两个键 —— 那是纪律，
    # 不是差异 —— 所以对拍前先在 Python 侧剥掉。公开产物里永远不该有这个路径。
    for r in py:
        if isinstance(r, dict) and "library" in r:
            r.pop("library", None)
            if isinstance(r.get("run"), dict):
                r["run"].pop("library", None)

    ok = True
    n_diff = 0
    for path, a, b in zip(spec, py, js["results"]):
        diffs: list[str] = []
        diff_result(a, b, "响应", diffs, limit=4)
        if not diffs:
            mark = "错误体一致" if "error" in a else f"{len(json.dumps(a))} B"
            print(f"   {path[:78]:<78} {mark} OK")
            continue
        ok = False
        n_diff += 1
        print(f"   {path[:78]:<78} **不符**")
        for d in diffs:
            print(f"       {d[:200]}")
    print(f"   {len(spec)} 条路径，{len(spec) - n_diff} 条一致"
          f"{'，差异 ' + str(n_diff) + ' 条' if n_diff else ''}")
    return ok


def demo_paths() -> list[str]:
    """公开站**实际会走到**的路径。

    与 static_api_paths() 的区别：那一份求的是「边界与错误口径全覆盖」，靠真语料
    与真 API 对拍；这一份求的是「公开站上点得动的每一处都真的出得来东西」，
    所以只取正常路径，且查询词必须是演示文本里真有的人和事。
    """
    Q = quote
    return [
        "/api/stats", "/api/books", "/api/books/DM1a0001/files",
        "/api/books/DM2e0001/files", "/api/files/1", "/api/files/3",
        "/api/files/1/passages", "/api/files/3/passages",
        "/api/files/1/raw", "/api/files/3/raw",
        "/api/passages/4", "/api/passages/4/context",
        "/api/blocks/4",
        # 全文检索：单字 / 双字 / 三字 / 多词。**用默认 level**，因为 exec_mode
        # 只在 Result Block 这一档的响应里（level=passage 返回的是列表形状）。
        f"/api/search?q={Q('齊')}",
        f"/api/search?q={Q('重耳')}",
        f"/api/search?q={Q('齊桓公')}",
        f"/api/search?q={Q('管仲 桓公')}",
        f"/api/search?q={Q('齊桓公')}&text=simplified",
        f"/api/search?q={Q('齊桓公')}&text=both",
        f"/api/search?q={Q('城濮')}&edition=SBCK",
        # 篇名检索（第六点一阶段）：演示数据里有 7 个篇名（齊語/晉語/秦語…），
        # 这条路径走的是 sections.json 的区间表，不走 corpus.json 的正文列。
        f"/api/search?q={Q('齊語')}",
        # 提问模式（第四阶段入口）
        f"/api/search?q={Q('管仲是怎么死的？')}&mode=question",
        f"/api/search?q={Q('晉文公是怎么死的？')}&mode=question",
        f"/api/search?q={Q('齊桓公')}&level=question&mode=short",
        # 错误契约：真 API 报错，静态版必须**报同样的错**而不是静默成功
        "/api/nope",
        f"/api/search?q={Q('齊桓公')}&level=bogus",
    ]


def check_demo_site() -> bool:
    """演示数据集：公开站上点得动的每一处，都真的出得来东西。

    前一条检查（static-api）证明的是「JS 静态分发器与真 API 同口径」，用的**真实
    语料**。这一条问的是另一个问题：**仓库里那份要公开发布的 data-demo/，够不够
    撑起一个功能完整的公开站**。两者都通过，才谈得上「公开的是能用的 Demo」。

    这里不做跨语言对拍（没有 Python 侧的参照物），断言的是产物自身的性质：
    10 条路由都能应答、三种模式都出结果、结果全部来自演示书。最后一条是**泄漏
    闸门**：只要有任何一个书名不是演示书，说明真实语料混进了公开产物。
    """
    print("⑬ 演示数据集：公开站的三种模式能否真的跑通（frontend/data-demo）")
    demo_dir = config.HISTORY_AI_DIR / "frontend" / "data-demo"
    if not (demo_dir / "corpus.json").exists():
        print("   **缺少 frontend/data-demo/** —— 先跑 "
              "`python -m scripts.site.make_demo_data`")
        return False

    spec = demo_paths()
    tmp = config.HISTORY_AI_DIR / "data" / "_demo_paths.json"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    try:
        js = run_node("static-api", staticdir=demo_dir, paths=tmp)
    finally:
        tmp.unlink(missing_ok=True)
    res = js["results"]
    by_path = dict(zip(spec, res))

    DEMO_TITLES = {"演示樣例·甲", "演示樣例·乙"}
    fails: list[str] = []

    def need(cond: bool, what: str) -> None:
        if cond:
            print(f"   {what} OK")
        else:
            fails.append(what)
            print(f"   {what} **失败**")

    # ① 十条路由都能应答（错误契约两条是**故意**要它报错的）
    errs = [p for p, r in by_path.items()
            if isinstance(r, dict) and r.get("error") and p not in ("/api/nope",)
            and "level=bogus" not in p]
    need(not errs, f"① 10 条路由应答无错（{len(spec)} 条路径）")
    for p in errs:
        print(f"       {p} → {by_path[p]['error']}")
    need(by_path["/api/nope"].get("error") == "unknown api path",
         "   错误契约：/api/nope → unknown api path")

    # ② stats：数字对得上，且**不带本机路径**（公开产物上这条最要紧）
    st = by_path["/api/stats"]
    need(st.get("books") == 2 and st.get("files") == 4 and st.get("records") == 84,
         f"② stats 计数（2 书 / 4 文件 / 84 记录，实得 "
         f"{st.get('books')}/{st.get('files')}/{st.get('records')}）")
    need("library" not in st and "library" not in (st.get("run") or {}),
         "   泄漏闸门：/api/stats 不含本机语料库路径")

    # ③ 全文检索：三档 text_mode 都出结果
    for p in (f"/api/search?q={quote('齊桓公')}",
              f"/api/search?q={quote('齊桓公')}&text=simplified",
              f"/api/search?q={quote('齊桓公')}&text=both"):
        r = by_path[p]
        tag = p.split("text=")[-1] if "text=" in p else "orig"
        need(bool(r.get("results")) and (r.get("hit_total") or 0) > 0,
             f"③ 检索 text={tag}：{r.get('hit_total')} 命中 / "
             f"{len(r.get('results') or [])} 块")
    # 三条检索路径都要被走到（engine.run_search：>=3 字→trigram(fts)、纯 2 字→
    # bigram、其余→like）。演示数据要是撑不起某一条，公开站就等于少了一条通路。
    for q, want in (("齊", "like"), ("重耳", "bigram"), ("齊桓公", "fts")):
        got = by_path[f"/api/search?q={quote(q)}"].get("exec_mode")
        need(got == want, f"   检索路径 {q}（{len(q)} 字）→ {want}（实得 {got}）")

    # ④ Result Block：块的来源字段齐备（前端卡片要显示它们）
    blocks = by_path[f"/api/search?q={quote('齊桓公')}"].get("results") or []
    b = blocks[0] if blocks else {}
    bkeys = ("book_title", "file_name", "pb_first", "pb_last", "n_passages",
             "first_passage_id", "last_passage_id", "more_before", "more_after")
    miss = [k for k in bkeys if k not in b]
    need(not miss and (b.get("n_passages") or 0) > 0,
         f"④ Result Block 字段齐备（缺 {miss}；n_passages={b.get('n_passages')}）")

    # ⑤ 提问模式：实体与意图都要认出来，聚合出事件
    q = by_path[f"/api/search?q={quote('管仲是怎么死的？')}&mode=question"]
    ents = [e["entity"] for e in (q.get("question") or {}).get("entities") or []]
    intents = (q.get("question") or {}).get("intents") or []
    events = (q.get("aggregation") or {}).get("events") or []
    need(ents == ["管仲"] and "death" in intents,
         f"⑤ 提问：实体 {ents} / 意图 {intents}")
    need(len(events) > 0 and q.get("results"),
         f"   提问：聚合出 {len(events)} 个事件 / {len(q.get('results') or [])} 条命中")
    # 别名扩展：管仲的别名要在检索词里出现（重耳/晉文公 同理，见 entities.py 的 _CURATED）
    groups = ((q.get("expanded") or {}).get("groups") or [])
    terms = {t for g in groups for t in g.get("terms") or []}
    need({"管夷吾", "管子"} <= terms, f"   别名扩展：{[sorted(terms)]}")

    # ⑥ 原文对照：注释行标注要含「·(并入上块)」（跨行注释块是演示数据的设计目标之一）
    raw = by_path["/api/files/1/raw"]
    anns = [a for ln in raw.get("lines") or [] for a in ln.get("annotation") or []]
    need(any("并入上块" in a for a in anns),
         f"⑥ 原文对照：{len(raw.get('lines') or [])} 行，标注含「·(并入上块)」")
    need(any("文件头" in a for a in anns) and any("page/" in a for a in anns),
         "   原文对照：标注含「文件头」与 page 层")

    # ⑦ 待确认注释页：演示数据要真有 pending（SBCK 行内括注拆出来的 14 行）。
    #    `/api/books/{id}/files` 直接返回**数组**，不是 {files:[…]}（api/main.py）。
    yfiles = by_path["/api/books/DM2e0001/files"]
    pend = sum((f.get("pending") or 0) for f in yfiles)
    klc = [c for c in by_path["/api/files/3"].get("kind_layer_counts") or []
           if c.get("status") == "pending_commentary"]
    need(pend > 0 and klc,
         f"⑦ 待确认注释：演示樣例·乙 pending={pend}，"
         f"文件 3 的 kind_layer_counts 含 pending_commentary={bool(klc)}")

    # ⑨ 篇名检索：演示数据要撑得起这条路径，且返回的块要自报「篇名命中」。
    #    这条同时证明 sections.json 真的随产物下发了 —— 少了它，前端**不会**报错，
    #    只会静默地搜不到任何篇名（最容易被漏掉的那种坏法）。
    sec = by_path[f"/api/search?q={quote('齊語')}"]
    sblocks = sec.get("results") or []
    smt = [b.get("match_type") for b in sblocks]
    need(bool(sblocks) and "section" in smt or "both" in smt,
         f"⑨ 篇名检索「齊語」：{len(sblocks)} 块，match_type={smt}")
    need(any(b.get("section") for b in sblocks),
         "   篇名回填：块的 section 字段非空"
         f"（{sblocks[0].get('section') if sblocks else None}）")

    # ⑩ 泄漏闸门：产物里出现过的**每一个书名**都必须来自演示书
    seen: set[str] = set()

    def walk(x) -> None:
        if isinstance(x, dict):
            for k, v in x.items():
                if k == "book_title" and isinstance(v, str):
                    seen.add(v)
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(res)
    alien = sorted(seen - DEMO_TITLES)
    need(not alien, f"⑩ 泄漏闸门：出现的书名 {sorted(seen)} 全部是演示书")
    if alien:
        print(f"       非演示书名：{alien}")

    print(f"   {len(spec)} 条路径；{'全部通过' if not fails else str(len(fails)) + ' 项失败'}")
    return not fails


def check_boot() -> bool:
    """⑭ boot.js 冒烟：公开站那句防误判的提示，真的写进 DOM 了吗。

    boot.js 是浏览器专有的（要 document / fetch），不在 loader.mjs 的
    ENGINE_FILES 里，所以上面 13 条对拍一条都覆盖不到它。但它承载着公开站上
    唯一一句「本站不含真实史料，搜不到真实人名是正常的」—— 第六阶段开工时的
    误判（在公开站搜「楚庄王」为空 → 断定搜索引擎坏了）正是缺这句话造成的。

    这里用桩 DOM 把它真跑一遍。跑 `_site`（build_artifact 装出来的发布产物，
    `data/` 已被排除）—— 只有那个目录才会走到演示模式分支。
    """
    print("⑭ boot.js 冒烟：演示模式的横幅与页脚")
    site = config.HISTORY_AI_DIR / "_site"
    if not (site / "data-demo" / "corpus.json").exists():
        print("   _site 尚未装配，跳过 —— 先跑 "
              "python -m scripts.site.build_artifact _site")
        return True                      # 没装配不算失败
    r = run_node("boot", dir=str(site))
    banner, foot = r.get("banner", ""), r.get("footer", "")

    fails = []

    def check(label, ok):
        print(f"   {label} {'OK' if ok else 'FAIL'}")
        if not ok:
            fails.append(label)

    check(f"模式判定 {r.get('mode')}/{r.get('kind')}",
          r.get("mode") == "static" and r.get("kind") == "demo")
    check("横幅已显示", r.get("bannerHidden") is False
          and "demo" in (r.get("bannerClass") or ""))
    check("横幅说明本站不含真实史料", "不含任何真实史料" in banner)
    # 这条是关键：光说「不含真实史料」不够，必须点破那个错误推论。
    check("横幅点破误判（搜不到真实人名是正常的）",
          "搜不到" in banner and "正常的" in banner)
    check("横幅给出本地部署入口", "本地部署" in banner)
    # 曾经这里是 `**不含真实史料**`，而 banner() 用的是 innerHTML ——
    # 星号原样显示，最关键的一句既没加粗、还多了四个星号。
    check("不含字面 markdown 星号", "**" not in banner)
    check("页脚已换成演示声明", "演示" in foot and "史記" not in foot)
    return not fails


CHECKS = {"dual-text": check_dual_text, "zh": check_zh, "norm": check_norm,
          "dl": check_dl, "search": check_search, "entities": check_entities,
          "question": check_question, "ranking": check_ranking,
          "result-block": check_result_block, "aggregate": check_aggregate,
          "retrieve": check_retrieve, "static-api": check_static_api,
          "demo-site": check_demo_site, "boot": check_boot}


# 每一级判定管什么。报告的头两节直接引用它们，免得报告里的说法与检查代码走偏。
LEVEL1 = ("命中集合、passage_ids、text、n_passages、match_count、total、"
          "hit_total、exec_mode、层级与出处字段")
LEVEL2 = "score 与排序（bm25 复现的验收项）"


def write_report(path: Path, want: list[str], results: list[tuple[str, bool, str]]) -> None:
    """把本次运行的**真实输出**写成报告。

    没有手工润色，也没有「摘要」——正文就是终端里那份逐条对拍记录，重跑即覆盖。
    报告与事实之间因此不存在第二份需要维护的副本（§36 要的是可追溯，不是好看）。
    """
    import platform
    node = subprocess.run(["node", "--version"], capture_output=True, text=True).stdout.strip()
    ok_all = all(r[1] for r in results)
    n_ok = sum(1 for r in results if r[1])
    lines = [
        "# 第五阶段 · 引擎一致性验证报告",
        "",
        "> 本文件由 `python -m scripts.site.check_engine --report <本文件>` **自动生成**，",
        "> 内容就是该次运行的真实输出。重跑即整体覆盖，不存在手工维护的第二份。",
        "",
        f"- 运行时间：{datetime.datetime.now():%Y-%m-%d %H:%M:%S}",
        f"- 运行环境：{platform.platform()} / {platform.machine()}",
        f"- Python {platform.python_version()}　Node {node}",
        f"- 参照语料：`frontend/data/corpus.json`"
        f"（{CORPUS.stat().st_size / 1048576:.1f} MB；**不发**，仅本机验证用）",
        f"- 结论：**{'一致' if ok_all else '存在差异'}** —— "
        f"{len(results)} 项检查中 {n_ok} 项通过"
        + ("" if ok_all else f"，{len(results) - n_ok} 项未通过"),
        "",
        "## 这是什么",
        "",
        "第五阶段把 `search/` 的检索逻辑移植成了浏览器能跑的 JS"
        "（`frontend/engine/`），好让公开站在没有 Python 进程的情况下也能检索。",
        "**Python 原版一行未改**（§18）。因此需要一个证据，说明两份实现在同一份",
        "语料、同一组输入下给出同一个答案 —— 这就是本报告。",
        "",
        "对拍方式：两侧各自读**同一份**导出语料，同一组输入各算一遍，逐字段比。",
        "比摘要而不是比全量文本（两侧 JSON 转义规则不同，摘要按码位字节规范化）。",
        "",
        "## 判定分级",
        "",
        f"- **第一级（必须完全一致）**：{LEVEL1}。这是「同一个答案」的定义，"
        "任何一处不同都算失败。",
        f"- **第二级（须一致，否则逐条列明）**：{LEVEL2}。"
        "bm25 涉及浮点与索引建法，是移植里最可能走偏的一环；它不影响命中集合，"
        "只影响同 `match_count` 下的次级排序与输出里的 score 值。",
        "",
        "## 检查项",
        "",
        "| # | 检查 | 结果 | 内容 |",
        "|---|---|---|---|",
    ]
    for i, (name, ok, text) in enumerate(results):
        head = next((l.strip() for l in text.splitlines() if l.strip()), name)
        lines.append(f"| {i + 1} | `{name}` | {'通过' if ok else '**未通过**'} | {head} |")
    lines += ["", "## 逐项输出", ""]
    for i, (name, ok, text) in enumerate(results):
        lines += [f"### {i + 1}. `{name}`"
                  f"{'' if ok else ' —— **未通过**'}", "", "```text",
                  text.rstrip("\n"), "```", ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    print(f"报告已写入 {path}")


def main(argv: list[str]) -> int:
    argv = argv[1:]
    report: Path | None = None
    if "--report" in argv:
        i = argv.index("--report")
        if i + 1 >= len(argv):
            print("--report 后面要跟路径")
            return 2
        report = Path(argv[i + 1])
        del argv[i:i + 2]
    want = argv or list(CHECKS)
    unknown = [w for w in want if w not in CHECKS]
    if unknown:
        print(f"未知检查项：{unknown}（可用：{list(CHECKS)}）")
        return 2
    preflight()
    print(f"语料：{CORPUS}（{CORPUS.stat().st_size/1048576:.1f} MB）\n")
    results: list[tuple[str, bool, str]] = []
    for name in want:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            ok = CHECKS[name]()
        text = buf.getvalue()
        print(text, end="")
        results.append((name, ok, text))
    failed = [n for n, ok, _ in results if not ok]
    print()
    if report is not None:
        write_report(report, want, results)
    if failed:
        print(f"失败：{failed}")
        return 1
    print(f"通过：{len(want)} 项检查全部一致")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
