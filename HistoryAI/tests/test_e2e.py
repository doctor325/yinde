"""Step 10 — 端到端：JSONL→SQLite(临时库)、validate 全量对账、API 只读抽查。

前提：data/processed/parsed_*.jsonl 与 data/database/history.db 已由 run_all 生成
（缺则跳过本文件；重建命令见 README）。
"""
import json
import sqlite3
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

from scripts.pipeline import config
from scripts.pipeline.validate import run_validation

def db_count(sql: str) -> int:
    """从**正式库**数一个计数，当作基准。

    写死数字在扩容时必然过期：第六点二阶段加了 前漢書/後漢書，原来写死的
    `== 5`（书数）让整个 test_e2e.py 静默跳过，`== 118`（文件数）则直接判错
    ——§21 表里登记的正是这两处。书数/文件数由语料决定，让库当基准。
    """
    if not config.DB_PATH.is_file():
        return 0
    conn = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    try:
        return conn.execute(sql).fetchone()[0]
    finally:
        conn.close()


# 判据是「第六点一阶段那 5 部书都还在」，不是「正好几部」——加了书就不跑测试，
# 等于把验收悄悄关掉。
HAS_OUTPUTS = (config.DB_PATH.is_file()
               and len(list(config.PROCESSED_DIR.glob("parsed_*.jsonl"))) >= 5)
N_BOOKS = db_count("SELECT COUNT(*) FROM books")
N_FILES = db_count("SELECT COUNT(*) FROM files")


@unittest.skipUnless(HAS_OUTPUTS, "先运行 python -m scripts.pipeline.run_all 生成产物")
class TestDbLoader(unittest.TestCase):
    def test_rebuild_into_throwaway_db(self):
        from scripts.pipeline.sqlite_store import rebuild
        with tempfile.TemporaryDirectory() as td:
            stats = rebuild(Path(td) / "t.db")
            self.assertEqual(stats["books"], N_BOOKS)
            self.assertEqual(stats["files"], N_FILES)
            self.assertGreater(stats["records"], 220_000)
            self.assertGreater(stats["passages"], 200_000)
            conn = sqlite3.connect(Path(td) / "t.db")
            # 与正式库同 schema、同重建逻辑
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM kr_chars").fetchone()[0], stats["kr_codes"])
            conn.close()

    def test_db_counts_match_metadata(self):
        """库内计数与 `data/metadata/*.json` 导出一致。

        两边由不同代码路径写出（库走 sqlite_store，metadata 走 inventory），
        对不上说明有一边过期了。数字本身不写死（见 db_count 的说明）。
        """
        books = json.loads((config.METADATA_DIR / "books.json")
                           .read_text(encoding="utf-8"))
        files = json.loads((config.METADATA_DIR / "files.json")
                           .read_text(encoding="utf-8"))
        self.assertEqual(N_BOOKS, len(books))
        self.assertEqual(N_FILES, sum(1 for f in files if f.get("kind") == "txt"),
                         "metadata 里的 txt 文件数应与库内 files 表一致"
                         "（readme 是附带文件、不入库）")
        import sqlite3
        conn = sqlite3.connect(str(config.DB_PATH))
        # 正式库导出的 import_runs 记录应与库内记录数一致
        run = conn.execute("SELECT n_records, status FROM import_runs "
                           "ORDER BY ran_at DESC LIMIT 1").fetchone()
        self.assertEqual(run[1], "ok")
        self.assertEqual(run[0], conn.execute("SELECT COUNT(*) FROM passages").fetchone()[0])
        conn.close()


@unittest.skipUnless(HAS_OUTPUTS, "需先运行管线")
class TestValidate(unittest.TestCase):
    # 已知的上游语料例外：这三处的 <pb:> 页标记少了「文件号-」一段
    # （源文件写的是 <pb:KR2a0012_WYG_1a>，规范形是 <pb:KR2a0012_WYG_000-1a>）。
    # 第六点三阶段第二批入库后暴露，**不是处理坏了**：这三条记录 kind=page、
    # layer=structure，字符守恒（body_ok）通过，前端 rich() 用宽容正则隐藏
    # <pb:…> 也不会漏字。其余任何一处 pb 写法不合规都必须让本用例失败，
    # 所以这里点名到「文件 + 字面值」，不是把 pb_bad 的阈值放宽。
    KNOWN_BAD_PB = {
        # 值 = 逐次出现（晉書那处同一字面值出现两次，故列两项）
        "KR2a0012_000.txt": ["<pb:KR2a0012_WYG_1a>"],
        "KR2a0015_000.txt": ["<pb:KR2a0015_WYG_1a>", "<pb:KR2a0015_WYG_1a>"],
    }

    def test_full_validation_passes(self):
        v = run_validation(quiet=True)
        self.assertEqual(v["files"], N_FILES)
        self.assertEqual(v["sha256_ok"], N_FILES)
        self.assertEqual(v["body_ok"], N_FILES)
        self.assertEqual(v["meta_ok"], N_FILES)
        self.assertEqual(v["kr_bad"], 0)

        # files_ok / pb_bad 的判据：除上述例外外，一部文件都不能掉队
        bad = {r["file"]: r for r in v["bad_files"]}
        self.assertEqual(sorted(bad), sorted(self.KNOWN_BAD_PB), "坏的必须是且只是这些已知例外")
        for fn, markers in self.KNOWN_BAD_PB.items():
            r = bad[fn]
            self.assertEqual(r["pb_bad"], len(markers), f"{fn} 的 pb 异常条数变了")
            self.assertEqual(r["kr_bad"], 0)
            self.assertTrue(r["body_ok"], f"{fn} 字符守恒失败（这不是上游例外，是处理坏了）")
            self.assertTrue(r["sha256_ok"] and r["meta_ok"], fn)
        self.assertEqual(v["files_ok"], N_FILES - len(self.KNOWN_BAD_PB))
        self.assertEqual(v["pb_bad"], sum(len(m) for m in self.KNOWN_BAD_PB.values()))
        self.assertEqual(v["kr_bad"], 0)


@unittest.skipUnless(HAS_OUTPUTS, "需先运行管线")
class TestApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from api import main as api_main
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), api_main.Handler)
        cls.port = cls.srv.server_address[1]
        cls.t = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.t.start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def get(self, path):
        import urllib.request
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}") as r:
            return json.load(r)

    def test_books(self):
        books = self.get("/api/books")
        # /api/books 的口径必须与库一致（不写死 5：第六点二阶段加了
        # 前漢書/後漢書，这个数字就成了 7）
        self.assertEqual(len(books), N_BOOKS)
        self.assertIn("KR2a0001", {b["book_id"] for b in books})

    def test_files_and_passages(self):
        files = self.get("/api/books/KR2e0001/files")     # 國語
        self.assertEqual(len(files), 22)
        fid = files[1]["file_id"]                          # 國語卷一（正文）
        d = self.get(f"/api/files/{fid}/passages?limit=5")
        self.assertGreater(d["total"], 1000)
        pc = self.get(f"/api/files/{fid}/passages?status=pending_commentary&limit=1")
        self.assertGreater(pc["total"], 0)                 # 韦昭注候选
        r = pc["rows"][0]
        self.assertEqual(r["layer"], "commentary_candidate")
        self.assertIn("(", r["text_orig"])

    def test_raw_compare(self):
        # 原文行与解析覆盖标注：guoyu_000 首行应是文件头
        d = self.get("/api/files/1/raw?start=1&end=3")
        self.assertIn("文件头", d["lines"][0]["annotation"])

    def test_q_search(self):
        import urllib.parse
        q = urllib.parse.quote("五帝本紀")
        files = self.get("/api/books/KR2a0001/files")
        d = self.get(f"/api/files/{files[0]['file_id']}/passages?q={q}&limit=2")
        self.assertGreater(d["total"], 0)


if __name__ == "__main__":
    unittest.main()
