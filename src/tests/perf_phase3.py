"""第三阶段 Step 9 —— Result Block 性能基准（第二阶段 vs 第三阶段同台对比）。

用法：  python tests/perf_phase3.py
前提：  history.db 已由 run_all 生成。

同一批查询词，同一台本地 HTTP 服务，分别量：
  level=passage  第二阶段逐条命中（基线）
  level=block    第三阶段史料片段（standard / long 两种显示长度）
并单独量「展开更多上下文」端点的响应，以及片段本身的规模分布。

判定标准沿用第二阶段：本地 loopback 往返，p50 <= 300ms 即视为可接受。
第三阶段多做了「取全命中 + 组装 + 合并」，会更慢；本脚本负责把差距量化出来，
而不是假装没有差距。
"""
import json
import statistics
import sys
import threading
import time
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api import main as api_main                                # noqa: E402
from scripts.pipeline import config                             # noqa: E402

# 基准期间关掉 Handler 的逐行请求日志：它往 stderr 同步写，几十次请求堆起来
# 能吃掉 80ms 量级的墙钟时间（实测「王/long」302ms → 223ms），那不是服务端成本。
# 日常跑服务时日志照常打印，只在本脚本里抑制。
api_main.Handler.log_message = lambda *a, **k: None

# 代表性查询：长词（trigram）/ 两字词（bigram）/ 限定书 / 深分页 / 超热词
QUERIES = [
    ("齐桓公", "长词·史記叙事"),
    ("管仲", "两字词·多书"),
    ("黄帝", "两字词·热词"),
    ("城濮", "两字词·少命中"),
    ("齐桓公 卒", "多词 AND"),
]
HOT = ("之", "王")          # 超热词：命中上万，最坏情况


def _time(url):
    t0 = time.perf_counter()
    with urllib.request.urlopen(url) as r:
        body = r.read()
    return (time.perf_counter() - t0) * 1000, body


def bench(base, url, warm=2, n=5):
    """返回 (min, p50, 最后一次的响应体)——响应体用于核对命中数，不额外发请求。"""
    for _ in range(warm):
        _time(url)
    times, body = [], b""
    for _ in range(n):
        t, body = _time(url)
        times.append(t)
    times.sort()
    return times[0], times[len(times) // 2], body


def main():
    if not config.DB_PATH.is_file():
        print("缺 history.db：先 python -m scripts.pipeline.run_all")
        return 1
    srv = ThreadingHTTPServer(("127.0.0.1", 0), api_main.Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"

    def url(q, **kw):
        return f"{base}/api/search?" + urllib.parse.urlencode({"q": q, **kw})

    try:
        print(f"{'查询':<12}{'说明':<16}{'P2逐条':>9}{'P3标准':>9}{'P3完整':>9}{'代价':>9}")
        print("-" * 74)
        worst = 0.0
        for q, desc in QUERIES:
            _, p2, p2_body = bench(base, url(q, level="passage"))
            _, p3, std_body = bench(base, url(q, mode="standard"))
            _, p3l, _ = bench(base, url(q, mode="long"))
            worst = max(worst, p3, p3l)
            d2 = json.loads(p2_body)
            d3 = json.loads(std_body)
            delta = f"+{(p3 - p2) / max(p2, 1) * 100:.0f}%"
            note = ""
            if d2["total"] != d3["hit_total"]:
                note = " ←命中数不一致！"
            print(f"{q:<12}{desc:<16}{p2:>8.1f}{p3:>8.1f}{p3l:>8.1f}{delta:>9}{note}")

        print("\n超热词（命中上万，最坏情况）")
        print("-" * 74)
        for q in HOT:
            _, t2, b2 = bench(base, url(q, level="passage"), warm=1, n=3)
            _, t3, b3 = bench(base, url(q, mode="standard"), warm=1, n=3)
            _, t3l, _ = bench(base, url(q, mode="long"), warm=1, n=3)
            hits = json.loads(b2)["total"]
            d3 = json.loads(b3)
            print(f"  {q}  命中 {hits:,} 条 → 片段 {d3['total']:,} 段"
                  f"  逐条 {t2:.0f}ms  标准 {t3:.0f}ms  完整 {t3l:.0f}ms"
                  f"{'   ← 超 300ms' if max(t3, t3l) > 300 else ''}")
            worst = max(worst, t3, t3l)

        print("\n片段规模分布（standard / long）")
        print("-" * 74)
        for q, _ in QUERIES[:3]:
            for mode in ("standard", "long"):
                _, body = _time(url(q, mode=mode, page_size=100))
                d = json.loads(body)
                lens = sorted(r["n_chars"] for r in d["results"])
                if not lens:
                    continue
                print(f"  {q:<8}{mode:<10} 片段 {d['total']:>4} 段"
                      f"  字数 min={lens[0]:<5}p50={statistics.median(lens):<6.0f}"
                      f"max={lens[-1]:<6}"
                      f"  可继续展开 {sum(1 for r in d['results'] if r['more_before'] or r['more_after']):>3} 段")

        # 展开端点：真实按需取数，用户点一次才走一次
        print("\n展开更多上下文 /api/blocks/<id>")
        print("-" * 74)
        _, body = _time(url("齐桓公", mode="long", page_size=100))
        cand = next((r for r in json.loads(body)["results"] if r["more_after"]), None)
        if cand is None:
            print("  本页没有可继续展开的片段（该词已读全）")
        else:
            t, body2 = _time(f"{base}/api/blocks/{cand['hit_passage_id']}"
                             f"?direction=after&count=40&after_passage_id={cand['last_passage_id']}")
            added = json.loads(body2)["added"]
            print(f"  齐桓公 段「{cand['block_id']}」再读 40 条 → 实得 {added} 条  {t:.0f}ms")

        print(f"\n第三阶段最慢 p50：{worst:.0f}ms"
              f"{'（可接受）' if worst <= 300 else '（超过 300ms，需说明）'}")
    finally:
        srv.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
