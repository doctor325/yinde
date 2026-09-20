"""自制演示数据集 → frontend/data-demo/。

## 这是什么，不是什么

**是**：本项目自己撰写的一小份先秦史事演示文本，以 MIT 随仓库发布，用来让公开站
（GitHub Pages，没有 Python 进程）上的检索 / Result Block / 提问模式**真的能跑通**。

**不是**：本项目语料。Kanripo 整理本的任何一个字都不在这里（§5），也不在任何
其他产物里。文件名、书名、内容都写明「演示」，每张结果卡片都会显示演示书名，
页面上常驻「公开演示模式」横幅 —— 不会与真实史书混淆。

数据保护上的取舍（§12 的落地）：**宁可公开一个功能完整的演示前端，也不要违规
上传语料**。所以公开站挂的是这份自撰文本，而不是真实语料的删节版。

## 怎么造：走真管线，不手搓 JSON

    源文本（本文件的 DEMO_BOOKS）
      → scripts/pipeline（真管线，与真实语料同一条）
      → 独立 DB（临时目录，用完即删）
      → scripts/site/export_site.py
      → frontend/data-demo/

**为什么必须走真管线**：手写一份「差不多的」JSON 只能骗过前端一次 —— 导出的列、
字典编码、bm25 的 n_row/n_token、normalized_text 的重算规则，全都是管线的产物。
走真管线则导出格式天然一致，且顺带验证了管线在另一份输入上也能跑通。

## 覆盖到哪些形态，哪些**故意不覆盖**

覆盖：`passage` / `comment`（含跨行注释块）/ `heading`（org `**`、`*** 1.1《…》`）/
`page`；层 `main` / `commentary_candidate`（行内括注）/ `structure`；状态
`ok` / `pending_commentary`；tls 与 SBCK 两种家族；`# src:` 出处注释。
人物用**多种称谓**出现（重耳/晉文公/文公、齊桓公/桓公、秦穆公/繆公、管仲/管子），
这是实体别名与短称解析的演示素材。

不覆盖：`kind='part'`（全库 224,822 行里只有 2 行，是 SBCK 首文件里正文段落的
`#+` 块，属于极边缘形态 —— 演示数据不该是某段代码唯一被跑到的地方）；`preface`/
`backmatter`/`toc` 层（真实语料里它们来自针对具体书目的手工确认表，演示书没有
对应的书目证据，造出来就是编，违反「不猜」）。这两类在一致性自检里由真实语料
覆盖，演示数据只覆盖常见形态。

## 与真实语料的隔离

源文件与中间库写在**系统临时目录**（用完即删），产物写到 `frontend/data-demo/`（入库）。与真实导出的 `frontend/data/`（含真实正文，**永不入库**）物理并列但
互不相干 —— 导出后有一道检查：产物里出现的任何正文行都必须是本文件的原文。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.pipeline import config  # noqa: E402

HISTORY_AI = config.HISTORY_AI_DIR
OUT_DIR = HISTORY_AI / "frontend" / "data-demo"

# 演示数据的许可与出处声明（会出现在每个文件的头部注释里）
LICENSE_LINE = "#+PROPERTY: LICENSE MIT（演示数据，非本项目语料）"
NOTE_LINE = "# 演示数据：本项目自撰，非 Kanripo 语料，非真实史料原文。事实取自旧闻，文字自撰。"

# ---------------------------------------------------------------- 演示正文
#
# 甲：tls 家族。org `**` 分卷、`*** n.n《…》` 分篇、`# src:` 出处注释（含跨行注释块）。
# 每行以 ¶ 收尾（tls 系的句末符号）。
JIA_1 = """\
# -*- mode: mandoku-view -*-
{LICENSE}
#+TITLE: 演示樣例·甲
#+DATE: 2026-09-11
#+PROPERTY: ID DM1a0001
#+PROPERTY: BASEEDITION tls
#+PROPERTY: JUAN 1
#+PROPERTY: CAT 演示数据（非真实史料）
{NOTE}
<pb:DM1a0001_tls_001-1a>¶
** 1 齊語

<pb:DM1a0001_tls_001-2a>¶
齊桓公即位，任管仲為相。¶
管仲相齊，作內政而寄軍令。¶
桓公用管仲之謀，九合諸侯，一匡天下。¶
<pb:DM1a0001_tls_001-3a>¶

{SRC1}
齊桓公問於管仲曰：何以安國？¶
管仲對曰：修舊法，擇其善者而業用之。¶
<pb:DM1a0001_tls_001-4a>¶

{SRC2}
管仲病，桓公問誰可代者。¶
管仲卒，齊桓公失其輔。¶
桓公卒，五公子爭立，齊國大亂。¶
<pb:DM1a0001_tls_001-5a>¶

** 2 晉語

<pb:DM1a0001_tls_001-6a>¶
*** 2.1　《晉公子重耳》
晉公子重耳出亡，奔狄。¶
重耳出亡十九年，而後得入。¶
重耳過衛，衛不禮焉。¶
<pb:DM1a0001_tls_001-7a>¶

{SRC3}
*** 2.2　《晉文公》
晉文公即位，賞從亡者。¶
文公修政，施惠百姓。¶
文公入國，秦穆公之力也。¶
<pb:DM1a0001_tls_001-8a>¶

{SRC4}
晉文公與楚人戰於城濮。¶
城濮之戰，晉侯敗楚師。¶
晉文公卒，子襄公立。¶
"""

JIA_2 = """\
# -*- mode: mandoku-view -*-
{LICENSE}
#+TITLE: 演示樣例·甲
#+DATE: 2026-09-11
#+PROPERTY: ID DM1a0001
#+PROPERTY: BASEEDITION tls
#+PROPERTY: JUAN 2
#+PROPERTY: CAT 演示数据（非真实史料）
{NOTE}
<pb:DM1a0001_tls_002-1a>¶
** 3 秦語

<pb:DM1a0001_tls_002-2a>¶
秦穆公聞百里奚之賢，以五羖羊皮贖之。¶
穆公相百里奚，秦國大治。¶
<pb:DM1a0001_tls_002-3a>¶

{SRC5}
百里奚讓曰：臣不如蹇叔。¶
繆公用百里奚之謀，遂霸西戎。¶
秦穆公卒，葬於雍。¶
<pb:DM1a0001_tls_002-4a>¶

{SRC6}
管子曰：倉廩實而知禮節。¶
管仲之書，後人述之。¶
"""

# 乙：SBCK 家族。行内括注 → commentary_candidate + pending_commentary
# （真实语料里这条通路有 17,347 行，是「待确认注释」页的全部内容）。
YI_1 = """\
# -*- mode: mandoku-view; -*-
{LICENSE}
#+TITLE: 演示樣例·乙
#+DATE: 2026-09-11
#+PROPERTY: ID DM2e0001
#+PROPERTY: BASEEDITION SBCK
#+PROPERTY: WITNESS SBCK
#+PROPERTY: JUAN 1
{NOTE}
<pb:DM2e0001_SBCK_001-1a>¶
*** 1.1　《晉語》
公將伐虢(虢國名也/公晉獻公)¶
荀息請以屈產之乘假道於虞(息晉大夫也/屈產良馬所出)¶
<pb:DM2e0001_SBCK_001-1b>¶
虞公許之(虞公貪馬/許許其假道)¶
宮之奇諫曰脣亡則齒寒(奇虞大夫也/諫止也)¶
虞公不聽遂假之道¶
<pb:DM2e0001_SBCK_001-2a>¶
晉滅虢還而襲虞(還反也/襲掩其不備)¶
遂虜虞公(虜獲也/虞公不悟)¶
"""

YI_2 = """\
# -*- mode: mandoku-view; -*-
{LICENSE}
#+TITLE: 演示樣例·乙
#+DATE: 2026-09-11
#+PROPERTY: ID DM2e0001
#+PROPERTY: BASEEDITION SBCK
#+PROPERTY: WITNESS SBCK
#+PROPERTY: JUAN 2
{NOTE}
<pb:DM2e0001_SBCK_002-1a>¶
*** 2.1　《齊語》
桓公自莒反於齊(反還也/桓公小白)¶
使鮑叔牙為宰(鮑叔齊大夫/宰官名也)¶
鮑叔辭曰臣不如管夷吾(夷吾管仲名也/辭讓也)¶
<pb:DM2e0001_SBCK_002-1b>¶
桓公曰管夷吾射寡人中鉤(鉤帶鉤也/射射桓公)¶
鮑叔曰夫為其君動也(各為其主/動謂盡力)¶
君若宥而反之(宥赦也/反還也)¶
<pb:DM2e0001_SBCK_002-2a>¶
桓公乃召管仲於魯(魯國名也/召召還之)¶
管仲相齊四十餘年(相輔也/四十餘年久任)¶
"""

# `# src:` 出处注释块。两个键相邻出现就是**跨行注释块**（一条 comment 记录占两行，
# 原文对照页里第二行会标「·(并入上块)」），这是真实语料里最常见的注释形态。
SRC = {
    "SRC1": "# src: DEMO JIA 1.1; 演示資料（自撰）\n# dating: demo-1",
    "SRC2": "# src: DEMO JIA 1.2; 演示資料（自撰）",
    "SRC3": "# src: DEMO JIA 2.1; 演示資料（自撰）\n# dating: demo-2",
    "SRC4": "# src: DEMO JIA 2.2; 演示資料（自撰）",
    "SRC5": "# src: DEMO JIA 3.1; 演示資料（自撰）",
    "SRC6": "# src: DEMO JIA 3.2; 演示資料（自撰）\n# dating: demo-3",
}


def _fill(text: str) -> str:
    return text.format(LICENSE=LICENSE_LINE, NOTE=NOTE_LINE, **SRC)


DEMO_BOOKS = {
    # 目录名 = book_dir（管线按目录名发现书，不硬编码书名）
    "demoji": [("DM1a0001_001.txt", _fill(JIA_1)), ("DM1a0001_002.txt", _fill(JIA_2))],
    "demoyi": [("DM2e0001_001.txt", _fill(YI_1)), ("DM2e0001_002.txt", _fill(YI_2))],
}


# ---------------------------------------------------------------- 执行

def run(args: list[str], library: Path, data: Path) -> None:
    """跑管线/导出脚本（子进程 + 环境变量覆盖路径）。

    必须用环境变量而不是改 config：config 在 import 时读 env，子进程里设好就
    自然指向演示数据，**不动真实语料的任何路径**（§32 的纪律同样适用于 data/）。
    """
    env = dict(os.environ)
    env["HISTORY_LIBRARY"] = str(library)
    env["HISTORY_DATA"] = str(data)
    env["PYTHONPATH"] = str(HISTORY_AI)
    env["PYTHONIOENCODING"] = "utf-8"
    # 产物里的 source 标签：这是一份要公开的自撰数据，不能自称来自 Kanripo。
    env["HISTORY_SITE_SOURCE"] = "demo-authored"
    p = subprocess.run([sys.executable, "-m", *args], cwd=str(HISTORY_AI), env=env,
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    if p.returncode != 0:
        raise SystemExit(f"`python -m {' '.join(args)}` 失败：\n{p.stdout}\n{p.stderr}")


def write_sources(library: Path) -> None:
    for book_dir, files in DEMO_BOOKS.items():
        d = library / book_dir
        d.mkdir(parents=True, exist_ok=True)
        for name, text in files:
            (d / name).write_text(text, encoding="utf-8", newline="\n")
        print(f"  源文件 {book_dir}/  {len(files)} 个")


def demo_lines() -> list[str]:
    """本文件自撰的全部文本行（去空）。**这是「什么算演示数据」的唯一判据。**

    造数据时用它做隔离检查，发布闸门（scripts/site/check_publish.py）也用同一份 ——
    两边各写一份的话，改了一边忘了另一边，闸门就会在某个方向上失效。
    """
    out = []
    for files in DEMO_BOOKS.values():
        for _, text in files:
            for line in text.splitlines():
                t = line.strip()
                if t:
                    out.append(t)
    return out


def check_isolated(out_dir: Path) -> None:
    """产物里出现的正文片段必须**全部**来自本文件的自撰文本。

    这是「演示数据与真实语料物理隔离」的最后一道确认：如果哪天有人把真实语料的
    导出误写进 data-demo/，产物里的句子不会在本文件里找到 —— 检查当场失败。

    判据是「**逐字子串**」而不是「整行相等」：管线会把 SBCK 的行内括注拆成两条记录
    （`公將伐虢(虢國名也/公晉獻公)¶` → 正文 `公將伐虢` + 注释 `(虢國名也/公晉獻公)¶`），
    拆出来的两半都不是完整的源文件行。子串判据对「泄露」同样敏感 —— 真实语料的
    任何一个字都不可能是这几十行自撰文本的子串。
    """
    lines = demo_lines()
    corpus = json.loads((out_dir / "corpus.json").read_text(encoding="utf-8"))
    cols = corpus["columns"]
    i_text = cols.index("text_orig")
    bad = []
    for row in corpus["rows"]:
        for piece in str(row[i_text]).split("\n"):
            t = piece.strip()
            if t and not any(t in line for line in lines):
                bad.append(piece[:60])
    if bad:
        raise SystemExit(
            f"演示产物里出现了 {len(bad)} 行**不来自** make_demo_data.py 的文本 —— "
            f"拒绝产出（前几行）：\n  " + "\n  ".join(bad[:5]))
    print(f"  隔离检查：{len(corpus['rows']):,} 行的正文全部来自本文件的自撰文本 OK")


def main(argv: list[str]) -> int:
    print(f"演示数据 → {OUT_DIR}")
    # 源文件写在**临时目录**：它们是本文件的派生物，留在磁盘上只会多一处需要
    # 判断「这算不算语料」的地方。真正的产物是 data-demo/。
    tmp = Path(tempfile.mkdtemp(prefix="histdemo-"))
    library = tmp / "library"
    data = tmp / "data"
    library.mkdir(parents=True, exist_ok=True)

    print("\n[1/3] 写演示源文件（临时目录）…")
    write_sources(library)

    print("\n[2/3] 跑真管线（独立 library/data，与真实语料完全隔离）…")
    run(["scripts.pipeline.run_all"], library, data)

    print("\n[3/3] 导出静态数据…")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    run(["scripts.site.export_site", str(OUT_DIR)], library, data)

    print("\n[检查] 演示产物不含真实语料：")
    check_isolated(OUT_DIR)

    # 临时目录必须清理：里面是演示源文件与中间库，留着没有意义。
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n完成。产物在 {OUT_DIR}（入库；演示数据为 MIT）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
