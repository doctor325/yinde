# 28部语料重建与来源异常审计

默认 processed 仅保留旧19部中间文件，扩容九部保存在 expansion_20261021/processed。新增 scripts.rebuild_corpus 入口显式合并两组来源输入，按旧库后追加的历史顺序重建。旧单目录 rebuild 调用仍只代表该目录，完整库使用以下入口。

```powershell
$env:PYTHONPATH='src'
python -B -X utf8 -m scripts.rebuild_corpus --output src/data/rebuild-next
```

输出目录必须尚不存在且位于仓库内。需保留两组来源JSONL与 corpus/kanripo 原件。工具先校验原件SHA256，保存输入文件摘要、每书版本、来源路径及顺序，再建独立候选库。正式库只用于比较。候选正文通过与原始文件的逐字符守恒检查。

## 来源分组

旧19部为北齊書、北史、陳書、國語、後漢書、晉書、梁書、南齊書、南史、前漢書、三國志、尚書、史記、宋書、隋書、魏書、戰國策、周書、春秋左傳。原件均位于 corpus/kanripo/<book_dir>，来源中间文件位于 src/data/processed/parsed_<book_dir>.jsonl。

新增九部为舊唐書（jiutangshu）、新唐書（xintangshu）、舊五代史（jiuwudaishi）、新五代史（xinwudaishi）、宋史（songshi）、遼史（liaoshi）、金史（jinshi）、元史（yuanshi）、明史（mingshi）。原件同属 corpus/kanripo，来源中间文件位于 src/data/expansion_20261021/processed，历史上经扩容解析后追加导入。默认入口未纳入该目录导致19与28的差异。

本轮 inputs.json 包含全部28部的来源路径、版本、原件哈希、JSONL哈希及加载顺序。没有缺失来源材料。该路径从已保留的来源中间产物复建，仍依赖这些本地文件的保留。

## 候选库实测

重建得到28部、3172文件、1618861记录。九张业务表按全部字段排序计算摘要，均与正式库一致，比较包括原文、ID、结构、版本和来源。外键检查无异常，SQLite完整性为ok。旧文件ID最大1026、旧段落ID最大760380，历史追加顺序复现后全部ID也一致。

import_runs的新运行UUID与时间不作相等判据。FTS trigram和bigram已重建，本轮未进行全量搜索排名等价验证。正式库与桌面包保持当前版本。

## 四个文件异常

| 文件 | 原始行号 | 异常标记 |
|---|---|---|
| KR2a0038_046.txt | 204 | `<pb:KR2a0038_WYG_WYG0297-0606c>` |
| KR2a0038_046.txt | 310 | `<pb:KR2a0038_WYG_WYG0297-0609b>` |
| KR2a0038_074.txt | 348 | `<pb:KR2a0038_WYG_WYG0298-0179c>` |
| KR2a0038_216.txt | 25 | `<pb:KR2a0038_WYG_WYG0299-0648b>` |
| KR2a0038_239.txt | 8 | `<pb:KR2a0038_WYG_WYG0299-0802c>` |

五处均原样存在于来源文本。重复WYG版本段和部分c面标识不符合既有严格页码语法。原件SHA256、头部metadata及正文逐字符守恒通过，归类为来源页码标记异常。尚缺独立影印页证据，原文保留原状。

校验继续计入pb_bad。validate新增pb_errors，保存记录行号、标记、原文、触发规则和来源存在性。没有扩大白名单。旧验证测试的历史例外清单仍会报告这四个文件，不能将来源异常标为已修复。

## 验证与限制

执行三条相关验证命令。一次完整候选重建及比较，一次针对性单测失败定位，修复SQLite连接显式关闭后的同组复测。最终两项单测通过，验证来源标记异常继续报错，以及ID或正文差异不会被相等比较漏过。

本地证据位于 src/data/rebuild-review-2026-11-08，包括 inputs.json、validation.json、report.json 和独立history.db。数据文件不提交。

下一轮应将完整来源清单接入统一构建入口，再按来源版本核对旧固定计数与端到端调用。本轮提供了独立完整重建入口，未宣称旧全套测试通过。
