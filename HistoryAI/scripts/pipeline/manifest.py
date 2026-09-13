"""Step 5 — Corpus Manifest + Section Coverage Audit（计划书 §17–§18）。

用法：
  python -m scripts.pipeline.manifest            # 写 data/metadata/ 并打印审计表
  python -m scripts.pipeline.manifest --quiet    # 只写文件

产出两份**派生**数据（data/metadata/ 下，已被 .gitignore 忽略，也被 build_artifact
的 EXCLUDE=["data"] 挡在发布产物之外——里面全是真实书名，绝不能进 _site）：

- corpus_manifest.json    每书一行：book_id/title/family/edition/files/passages/
                          sections/juans/section_coverage/indexed/status，
                          外加 publish_gate：语料书名与发布闸门 REAL_TITLES 的对账
                          （见 publish_gate_sync）
- section_coverage.json   逐书覆盖明细 + 逐文件缺口点名

## 两个 status，别混（6.3 起）

- `coverage_status`（FAIL/WARN/OK）＝**篇名覆盖**口径，判据与 `api/db.py` 的
  list_books 逐字一致，页面与审计表必须给同一个答案，**一个字都不许改**。
- `status`（planned/imported/verified/warning/failed）＝**语料状态**五态，由
  `merge_catalog()` 把「语料目录说应该有什么」与「库和磁盘实际有什么」相乘得出，
  见该函数注释。它回答的是「这本书可不可用、为什么不可用」，与覆盖口径并存不替代。

## coverage 的口径，以及为什么它不是一个「越高越好」的装饰指标

section 用**区间模型**：一条 section 覆盖它所在文件内从 first_row 到该文件下一条
section（同文件最后一条覆盖到文件末）。Passage 行上的 section 字段不参与归属判定。
于是「正文行落在本文件第一条 section 之前」就是真缺口，成因分三类：

  ① 结构使然——卷首的撰者/注者署名行、總目、四庫叢書题。每文件一两行，清不掉也不该清。
  ② 源转录缺卷首题——國語 003 / 007 的首行直接是正文，卷名只在卷末版心题里出现，
     整卷（1129 行）无所归。只有源文件能解释，报告里点名，不硬凑一个 section 出来。
  ③ 篇题识别失败——这才是要修的 bug。本阶段修掉的三处：WYG 长题名（>8 字）与
     扩展区字形（𫝊/𤣥）、篇题后面跟着的行内注、國語卷末版心题被当成第二条 section。

所以 status 不单看阈值，而是看「这本书有没有 section」＋「缺口有没有被点名」：
  FAIL  正文行 > 0 但一条 section 都没有 —— 篇名搜索整本书用不了
  WARN  coverage < 0.90 或有文件完全没 section —— 缺口大到需要人看一眼
  OK    其余

## indexed

「这本书的正文进没进 FTS」用 passages_fts.rowid = passage_id 的连接实测，不是
拿总数相减去推。normalized_text 为 NULL 的行（heading/page/part 等结构行）本就不
入索引，所以比对的分母是「有 normalized_text 的 passage 行」。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone

from . import catalog
from . import config

# 正文层的判定：layer 为空（老数据）或 main 都算正文
BODY_LAYER_SQL = "(p.layer IS NULL OR p.layer = 'main')"

WARN_COVERAGE = 0.90

# 篇题溯源：confidence <= 这个值的算「靠猜的」（首现兜底 0.5 / 弱形态 0.6）。
# 它比覆盖率百分比更早暴露问题：覆盖率 100% 也可能是篇篇都靠首现硬记的。
LOW_CONFIDENCE = 0.6


def _connect() -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _section_first_rows(con, book_id: str) -> dict[int, int]:
    """每文件第一条 section 的行号。区间模型的左端。"""
    return {r["file_id"]: r["fr"] for r in con.execute(
        "SELECT file_id, MIN(first_row) AS fr FROM sections WHERE book_id=? "
        "GROUP BY file_id", (book_id,))}


def audit_book(con, book: sqlite3.Row) -> dict:
    """一本书的 manifest 行 + 覆盖明细。只读。"""
    bid = book["book_id"]
    first = _section_first_rows(con, bid)

    files = [r["file_id"] for r in con.execute(
        "SELECT DISTINCT p.file_id FROM passages p WHERE p.book_id=? AND p.kind='passage' "
        f"AND {BODY_LAYER_SQL}", (bid,))]
    files_with = [f for f in files if f in first]
    file_no = {r["file_id"]: r["file_no"] for r in con.execute(
        "SELECT file_id, file_no FROM files WHERE book_id=?", (bid,))}
    no_section = sorted(file_no[f] for f in files if f not in first
                        and file_no.get(f) is not None)

    total = covered = 0
    gaps: dict[int, int] = {}
    for r in con.execute(
            "SELECT p.file_id, p.row_no FROM passages p WHERE p.book_id=? AND p.kind='passage' "
            f"AND {BODY_LAYER_SQL}", (bid,)):
        total += 1
        fr = first.get(r["file_id"])
        if fr is not None and r["row_no"] >= fr:
            covered += 1
        else:
            gaps[r["file_id"]] = gaps.get(r["file_id"], 0) + 1

    nsec = con.execute("SELECT COUNT(*) FROM sections WHERE book_id=?",
                       (bid,)).fetchone()[0]
    nrow = con.execute("SELECT COUNT(*) FROM passages WHERE book_id=?",
                       (bid,)).fetchone()[0]
    npas = con.execute("SELECT COUNT(*) FROM passages WHERE book_id=? AND kind='passage'",
                       (bid,)).fetchone()[0]
    njuan = con.execute("SELECT COUNT(*) FROM juans WHERE book_id=?",
                        (bid,)).fetchone()[0]
    nfile = con.execute("SELECT COUNT(*) FROM files WHERE book_id=?",
                        (bid,)).fetchone()[0]

    # 篇题溯源直方图（6.3-C③）：这条 section 是 header/title 认出来的，还是靠
    # 「section 首现」硬记的？NULL 归 untagged —— 旧库没重跑过时整列都是 untagged，
    # 一眼就能看出「该重建了」。低置信的条数单独报：覆盖率 100% 也可能是篇篇靠猜。
    methods = {r["m"]: r["n"] for r in con.execute(
        "SELECT COALESCE(detection_method,'untagged') AS m, COUNT(*) AS n "
        "FROM sections WHERE book_id=? GROUP BY m", (bid,))}
    low_conf = con.execute(
        "SELECT COUNT(*) FROM sections WHERE book_id=? AND confidence IS NOT NULL "
        "AND confidence<=?", (bid, LOW_CONFIDENCE)).fetchone()[0]

    # indexed：拿这本书自己的正文去 FTS 里**真查一次**，不靠计数比对。
    # 计数比对在这里是假的：passages_fts 是 external-content 表
    # （content='passages', content_rowid='passage_id'），select count(*) 直接
    # 回内容表行数，_docsize 影子表又给每一行都建了条目（含 normalized_text 为
    # NULL 的结构行），两边永远相等，比了等于没比。真正要证明的是「这本书的正文
    # 通过索引查得到」，那就查。
    probe_row = con.execute(
        "SELECT normalized_text FROM passages WHERE book_id=? AND "
        "normalized_text IS NOT NULL AND LENGTH(normalized_text)>=12 "
        "ORDER BY passage_id LIMIT 1", (bid,)).fetchone()
    if probe_row:
        frag = probe_row[0][3:9]
        hit = con.execute(
            "SELECT COUNT(*) FROM passages_fts f JOIN passages p "
            "ON p.passage_id=f.rowid WHERE passages_fts MATCH ? AND p.book_id=?",
            (f'"{frag}"', bid)).fetchone()[0]
        indexed = {"ok": hit > 0, "probe": frag, "hits": hit}
    else:
        indexed = {"ok": False, "probe": None, "hits": 0,
                   "reason": "该书没有可检索正文（normalized_text 全空）"}

    # 判据必须与 api/db.py 的 list_books 逐字一致（同一套 coverage_status，页面与
    # 审计表才会给同一个答案）。那边算不出「有没有文件完全无 section」，所以这里也
    # 不把它当判据——只作为明细报出来，由人去看。
    # 6.3 起这个字段叫 coverage_status（与 api/db.py 同名），另有五态 status 由
    # merge_catalog() 按语料目录推出——两者并存，不改这一处口径。
    cov = (covered / total) if total else 1.0
    if total and nsec == 0:
        coverage_status = "FAIL"
    elif cov < WARN_COVERAGE:
        coverage_status = "WARN"
    else:
        coverage_status = "OK"

    # 缺口点名：按缺口行数排序，只列前 5 个文件，附文件号便于人工翻原文件
    gap_detail = []
    for fid, miss in sorted(gaps.items(), key=lambda kv: -kv[1])[:5]:
        f = con.execute("SELECT file_no, file_name FROM files WHERE file_id=?",
                        (fid,)).fetchone()
        gap_detail.append({
            "file_no": f["file_no"], "file_name": f["file_name"],
            "missing_rows": miss,
            "has_section": fid in first,
        })

    return {
        "book_id": bid,
        "title": book["title"],
        "book_dir": book["book_dir"],
        "family": book["family"],
        "edition": book["edition"],
        "files": nfile,
        "rows": nrow,
        "passages": npas,
        "sections": nsec,
        "juans": njuan,
        "body_rows": total,
        "covered_rows": covered,
        "uncovered_rows": total - covered,
        "section_coverage": round(cov, 4),
        "files_with_section": len(files_with),
        "files_without_section": no_section,
        "indexed": indexed,
        "coverage_status": coverage_status,
        "section_methods": methods,
        "low_confidence": low_conf,
        "untagged": methods.get("untagged", 0),
        "gap_files": gap_detail,
    }


def publish_gate_sync(titles: list[str]) -> dict:
    """语料里的书名，发布闸门 ② 的名单里都有吗（§23）。

    加一本书最容易漏的一步：书进了库，而 `scripts/site/check_publish.py` 的
    `REAL_TITLES` 没跟着加——那个闸门是「产物里出现真实书名就拒绝发布」，
    名单不跟着语料长大，对新书就是形同虚设（第六点二阶段补过一次
    前漢書/後漢書）。所以把「加书清单」变成可执行的检查，别靠记性。

    真源是 check_publish.REAL_TITLES，这里只对账、不复制一份。
    """
    from scripts.site.check_publish import REAL_TITLES      # 单一真源
    have = set(titles)
    missing = sorted(have - REAL_TITLES)     # 库里有、闸门不认识 → 闸门漏了
    stale = sorted(REAL_TITLES - have)       # 闸门认识、库里没有 → 无害，报出来
    return {"ok": not missing, "missing": missing, "stale": stale,
            "titles": sorted(have)}


def merge_catalog(rows: list[dict], cat) -> dict:
    """语料目录（应该有什么）× 库内实况（实际有什么）→ 每本书一个五态 status。

    五态判据（计划书 §4）：

      planned   目录在册，磁盘无目录、库中无记录
      imported  已入库，但证据不足以称 verified（审计未跑 / 召回尚未人工确认）
      verified  coverage_status == OK 且该书的 recall_verified 为真
      warning   coverage_status == WARN（篇名覆盖 <0.90）
      failed    coverage_status == FAIL（有正文行但 0 section）或 FTS 探针查不到自己

    `verified` 为什么要人工确认位：审计 OK 只证明「结构解析没出问题」，不证明
    「搜得到」。后者要召回用例背书（tests/search_cases/，每部 verified 书至少一条
    专属用例），那是人在 catalog 里置 `recall_verified` 的动作，机器不替人拍板。

    两处刻意的取舍（五态是计划书定死的，不为边角另立状态，改用附加字段点名）：
    - 「目录里没有、库里却有」→ status=imported + catalog_gap=true，并在报告里
      点名。目录是人工维护的，漏登记必须有人看见，而不是被静默当成正常书。
    - 「已下载、还没入库」→ status=planned + on_disk=true，报告提示跑管线。它与
      真·planned（连目录都没下载）的区别就在 on_disk 上，一眼可辨。
    """
    cat_by_id = {b.book_id: b for b in cat.books.values()} if cat else {}
    on_disk = cat.dirs_on_disk() if cat else {}
    in_db = {r["book_id"] for r in rows}

    for r in rows:
        b = cat_by_id.get(r["book_id"])
        r["catalog_gap"] = b is None
        if b is None:
            # 库里有、目录没登记：不谎称 verified，退回 imported
            r["status"] = "imported"
            r["era_group"] = r["category"] = r["dynasty"] = None
            continue
        r["era_group"], r["category"], r["dynasty"] = b.era_group, b.category, b.dynasty
        r["recall_verified"] = b.recall_verified
        if r["coverage_status"] == "FAIL" or not r["indexed"]["ok"]:
            r["status"] = "failed"
        elif r["coverage_status"] == "WARN":
            r["status"] = "warning"
        elif b.recall_verified:
            r["status"] = "verified"
        else:
            r["status"] = "imported"

    planned = [{
        "book_id": b.book_id, "title": b.title, "dir": b.dir,
        "dynasty": b.dynasty, "era_group": b.era_group, "category": b.category,
        "kanripo_id": b.book_id, "repo": b.repo, "branch": b.branch,
        "on_disk": on_disk.get(b.dir, False),
    } for b in (cat.books.values() if cat else ()) if b.book_id not in in_db]

    counts: dict[str, int] = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1

    return {
        "catalog_path": str(cat.path) if cat else None,
        "catalog_version": cat.version if cat else None,
        "note": ("catalog = 应该有什么（人工维护），books = 实际有什么（库内实况）。"
                 "status 是两者相乘的五态；coverage_status 仍是篇名覆盖的三态口径"
                 "（与 api/db.py 一致，一字未改）。"),
        "in_catalog": len(rows) - len([r for r in rows if r["catalog_gap"]]),
        "counts": dict(sorted(counts.items())),
        "planned": planned,
        "not_in_catalog": sorted(r["title"] for r in rows if r["catalog_gap"]),
        "on_disk_not_imported": sorted(p["title"] for p in planned if p["on_disk"]),
        "uncatalogued_dirs": cat.uncatalogued_dirs() if cat else [],
        "by_era_group": _era_progress(rows, planned),
    }


def _era_progress(rows: list[dict], planned: list[dict]) -> dict:
    """按时代分组的收录进度——前端 #/coverage 的时代进度条与它同源。"""
    out: dict[str, dict] = {}
    for r in rows:
        g = out.setdefault(r.get("era_group") or "未分组",
                           {"in_library": 0, "planned": 0, "verified": 0})
        g["in_library"] += 1
        if r["status"] == "verified":
            g["verified"] += 1
    for p in planned:
        g = out.setdefault(p["era_group"] or "未分组",
                           {"in_library": 0, "planned": 0, "verified": 0})
        g["planned"] += 1
    return dict(sorted(out.items()))


def build(quiet: bool = False) -> dict:
    con = _connect()
    try:
        books = list(con.execute("SELECT * FROM books ORDER BY book_id"))
        rows = [audit_book(con, b) for b in books]
    finally:
        con.close()

    cat = catalog.try_load()
    cat_rep = merge_catalog(rows, cat)

    gate = publish_gate_sync([r["title"] for r in rows])

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "library": str(config.LIBRARY_DIR),
        "db": str(config.DB_PATH),
        "note": "真实语料的家底快照。含真实书名，只留在 data/ 下，绝不进发布产物。",
        "totals": {
            "books": len(rows),
            "files": sum(r["files"] for r in rows),
            "rows": sum(r["rows"] for r in rows),
            "passages": sum(r["passages"] for r in rows),
            "sections": sum(r["sections"] for r in rows),
            "juans": sum(r["juans"] for r in rows),
            "body_rows": sum(r["body_rows"] for r in rows),
            "uncovered_rows": sum(r["uncovered_rows"] for r in rows),
            "planned_books": len(cat_rep["planned"]),
        },
        "catalog": cat_rep,
        "publish_gate": gate,
        "books": rows,
    }
    coverage = {
        "generated_at": manifest["generated_at"],
        "warn_coverage": WARN_COVERAGE,
        "note": ("section 为区间模型：覆盖本文件内自 first_row 起至下一条 section。"
                 "缺口三类成因见 scripts/pipeline/manifest.py 模块注释。"),
        "books": [{
            "title": r["title"], "book_id": r["book_id"], "family": r["family"],
            "edition": r["edition"],
            "sections": r["sections"], "juans": r["juans"],
            "body_rows": r["body_rows"], "covered_rows": r["covered_rows"],
            "section_coverage": r["section_coverage"],
            "files_without_section": r["files_without_section"],
            "coverage_status": r["coverage_status"], "status": r["status"],
            "section_methods": r["section_methods"],
            "low_confidence": r["low_confidence"], "untagged": r["untagged"],
            "gap_files": r["gap_files"],
        } for r in rows],
    }

    config.ensure_data_dirs()
    (config.METADATA_DIR / "corpus_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (config.METADATA_DIR / "section_coverage.json").write_text(
        json.dumps(coverage, ensure_ascii=False, indent=2), encoding="utf-8")

    if not quiet:
        print_audit(coverage, manifest)
    return manifest


def print_section_methods(manifest: dict) -> None:
    """篇题溯源：这批 section 是认出来的还是猜出来的（6.3-C③）。"""
    print("\n=== 篇题溯源（detection_method：这条 section 是怎么来的）")
    tot: dict[str, int] = {}
    for b in manifest["books"]:
        for k, v in (b.get("section_methods") or {}).items():
            tot[k] = tot.get(k, 0) + v
    print("  合计：" + ("、".join(f"{k} {v}" for k, v in sorted(tot.items()))
                       or "（无 section）"))
    for b in manifest["books"]:
        if not b.get("sections"):
            continue
        m = b.get("section_methods") or {}
        flags = []
        if b.get("low_confidence"):
            flags.append(f"低置信 {b['low_confidence']}")
        if b.get("untagged"):
            flags.append(f"未标注 {b['untagged']}")
        print(f'  {b["title"]:<9}{b["sections"]:>5} 条  '
              + "、".join(f"{k} {v}" for k, v in sorted(m.items()))
              + ("   ← " + "；".join(flags) if flags else ""))
    if tot.get("untagged"):
        print("  ** 有未标注的 section —— 旧库没重跑过；跑一次 run_all 即可补齐")
    if tot.get("first-occurrence"):
        print(f'  ** 首现兜底 {tot["first-occurrence"]} 条：没有标题证据、靠 section '
              f'首次出现记下的，篇名可信度低，audit_sections 里逐条可查')


def print_catalog_status(manifest: dict) -> None:
    """五态总表（catalog × 库）+ 未入库名单 + 三处必须有人看的告警。"""
    rep = manifest.get("catalog") or {}
    if not rep.get("catalog_path"):
        print("\n=== 语料状态：没有语料目录（corpus_catalog.json 缺失）"
              "—— 五态退化为三态，未入库的书无从得知")
        return
    print(f"\n=== 语料状态（五态 = 语料目录 × 库内实况；目录 {rep['catalog_path']}）")
    print(f'{"书名":<9}{"时代":<8}{"类目":<11}{"覆盖":<7}{"书籍状态":<10}召回确认')
    for b in manifest["books"]:
        print(f'{b["title"]:<9}{str(b.get("era_group") or "-"):<8}'
              f'{str(b.get("category") or "-"):<11}'
              f'{b["coverage_status"]:<7}{b["status"]:<10}'
              f'{"是" if b.get("recall_verified") else "否"}')
    print("  已入库 " + str(manifest["totals"]["books"]) + " 部："
          + "、".join(f"{k} {v}" for k, v in rep["counts"].items()))
    print(f"  未入库（目录在册）{len(rep['planned'])} 部"
          + ("：" + "、".join(p["title"] for p in rep["planned"])
             if rep["planned"] else ""))
    print("  按时代：" + "  ".join(
        f"{g} {v['in_library']}/{v['in_library'] + v['planned']}"
        for g, v in rep["by_era_group"].items()))
    if rep["on_disk_not_imported"]:
        print(f"  ** 已下载未入库：{rep['on_disk_not_imported']}"
              f" —— 跑一次 python -m scripts.pipeline.run_all")
    if rep["not_in_catalog"]:
        print(f"  ** 库里有、目录未登记：{rep['not_in_catalog']}"
              f" —— 补进 corpus_catalog.json（否则状态永远停在 imported）")
    if rep["uncatalogued_dirs"]:
        print(f"  ** 磁盘上有未登记的书目录：{rep['uncatalogued_dirs']}")


def print_audit(coverage: dict, manifest: dict) -> None:
    print("\n=== Corpus Manifest（§17）：" + str(manifest["totals"]))
    print("\n=== Section Coverage Audit（§15 / §18）")
    print(f'{"书名":<9}{"家族":<6}{"版本":<6}{"无篇文件":>8}{"juans":>7}{"sections":>9}'
          f'{"正文行":>8}{"已归篇":>8}{"覆盖":>8}  状态')
    for b in coverage["books"]:
        print(f'{b["title"]:<9}{b["family"]:<6}{str(b["edition"]):<6}'
              f'{len(b["files_without_section"]):>8}{b["juans"]:>7}{b["sections"]:>9}'
              f'{b["body_rows"]:>8}{b["covered_rows"]:>8}'
              f'{b["section_coverage"] * 100:>7.1f}%  {b["coverage_status"]}')
    for b in coverage["books"]:
        if b["coverage_status"] == "OK":
            continue
        print(f'\n  [{b["coverage_status"]}] {b["title"]}：{len(b["files_without_section"])} '
              f'个文件无 section，缺口 {b["body_rows"] - b["covered_rows"]} 行')
        if b["files_without_section"]:
            print(f'        无 section 的文件号：{b["files_without_section"]}')
        for g in b["gap_files"]:
            print(f'        {g["file_name"]}  缺 {g["missing_rows"]} 行'
                  f'{"（该文件无 section）" if not g["has_section"] else "（section 之前）"}')
    bad = [b["title"] for b in coverage["books"] if b["coverage_status"] == "FAIL"]
    warn = [b["title"] for b in coverage["books"] if b["coverage_status"] == "WARN"]
    print(f"\n  合计 {len(coverage['books'])} 书："
          f"{len(coverage['books']) - len(bad) - len(warn)} OK / {len(warn)} WARN / "
          f"{len(bad)} FAIL" + (f"  FAIL={bad}" if bad else ""))
    idx_bad = [b["title"] for b in manifest["books"] if not b["indexed"]["ok"]]
    print(f"  FTS 索引完整：{'全部入索引' if not idx_bad else '缺口 ' + str(idx_bad)}")
    print_section_methods(manifest)
    print_catalog_status(manifest)
    g = manifest["publish_gate"]
    print(f"  发布闸门书名清单（check_publish.REAL_TITLES）："
          + ("与语料一致" if g["ok"] else
             f"**漏了 {g['missing']}** —— 闸门对这"
             f"{len(g['missing'])}部书形同虚设，补进 REAL_TITLES"))
    if g["stale"]:
        print(f"    （名单里多出：{g['stale']} —— 库里暂时没有，无害）")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Corpus Manifest + Section Coverage Audit")
    ap.add_argument("--quiet", action="store_true", help="只写文件，不打表")
    args = ap.parse_args(argv)
    build(quiet=args.quiet)


if __name__ == "__main__":
    main()
