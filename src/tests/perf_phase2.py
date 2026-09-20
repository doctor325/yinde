"""Step 5 — 第二阶段性能基准（HTTP 实测，本地 loopback，128KB 上下的往返开销）

用法：  python tests/perf_phase2.py
前提：  history.db 已由 run_all 生成。每个操作先跑 2 次热身（缓存/连接池就位），
再取 5 次的最小值——代表浏览器真实可感的最快路径。目标：除个别大文件外 <=300ms。
"""
import json
import sys
import threading
import time
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api import main as api_main
from scripts.pipeline import config

# 基准期间关掉 Handler 的逐行请求日志（同步写 stderr，会混进墙钟时间）；
# 服务日常运行时照常打印。详见 tests/perf_phase3.py 的同一处说明。
api_main.Handler.log_message = lambda *a, **k: None

OPS = []


def op(name, path, warm=2, n=5):
    OPS.append((name, path, warm, n))


op("首页数据 /api/stats", "/api/stats")
op("书列表 /api/books", "/api/books")
op("文件列表(國語22) /api/books/KR2e0001/files", "/api/books/KR2e0001/files")
op("解析记录 第21页×50 /api/files/*/passages",
   "/api/files/2/passages?offset=1000&limit=50")
op("原文对照 200行(缓存行) /api/files/*/raw", "/api/files/2/raw?start=300&end=499")
# 第三阶段起 /api/search 默认返回 Result Block（片段）。本文件量的是**逐条命中**
# 那条老路径的基准，所以显式带 level=passage；片段的性能见 tests/perf_phase3.py。
op("检索FTS 齐桓公 /api/search", "/api/search?q=%E9%BD%90%E6%A1%93%E5%85%AC&level=passage")
op("检索bigram 管仲 /api/search", "/api/search?q=%E7%AE%A1%E4%BB%B2&level=passage")
op("检索FTS+短词AND 齊桓公 卒 /api/search",
   "/api/search?q=%E9%BD%90%E6%A1%93%E5%85%AC%20%E5%8D%92&level=passage")
op("检索限定 桓公@国语SBCK /api/search",
   "/api/search?q=%E6%A1%93%E5%85%AC&book=KR2e0001&edition=sbck&level=passage")
op("检索深分页 齐桓公 p5×50 /api/search",
   "/api/search?q=%E9%BD%90%E6%A1%93%E5%85%AC&page=5&page_size=50&level=passage")
op("记录详情 /api/passages/2149", "/api/passages/2149")
op("上下文 /api/passages/2149/context", "/api/passages/2149/context?before=3&after=3")


def main():
    if not config.DB_PATH.is_file():
        print("缺 history.db：先 python -m scripts.pipeline.run_all")
        return 1
    srv = ThreadingHTTPServer(("127.0.0.1", 0), api_main.Handler)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{port}"
    try:
        rows = []
        for name, path, warm, n in OPS:
            for _ in range(warm):
                _time(base + path)
            times = sorted(_time(base + path) for _ in range(n))
            rows.append((name, times[0], times[len(times) // 2], sum(times) / n))
        print(f"{'操作':<42}{'min':>8}{'median':>9}{'avg':>9}")
        for name, lo, med, avg in rows:
            flag = "" if med <= 300 else "  ← 超 300ms"
            print(f"{name:<42}{lo:>7.1f}ms{med:>8.1f}ms{avg:>8.1f}ms{flag}")
        # 原文对照冷路径（清缓存后首次读 library 文件）
        api_main._raw_cache.clear()
        times = sorted(_time(f"{base}/api/files/2/raw?start=300&end=499")
                       for _ in range(5))
        print(f"{'原文对照 200行(冷·读原件)':<42}{times[0]:>7.1f}ms{times[2]:>8.1f}ms"
              f"{sum(times) / 5:>8.1f}ms")
    finally:
        srv.shutdown()


def _time(url):
    t0 = time.perf_counter()
    with urllib.request.urlopen(url) as r:
        r.read()
    return (time.perf_counter() - t0) * 1000


if __name__ == "__main__":
    sys.exit(main())
