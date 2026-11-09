# 验证链路后续修复

修复FTS实际统计量读取（取代按历史建库次数猜测）、docsize的SQLite varint解码，以及Python/JS高频词IDF的1e-6下限。逐条SQLite BM25校验保留。相对时间在同段有明确纪年且无竞争纪年时记录原文锚点，不换算公历；霍光指定真实材料源码审计已从insufficient变为clues_present，relation_verified仍为false。

实际同源导出242.7MB，1618861行、3172文件、28书。normalized_text全表摘要通过；首次dl失败后修复解码，仅复查dl，全表摘要通过：trigram20484727、bigram21623155。日志分别在src/data/parity-repair-20261109.log和parity-repair-20261109-dl.log，保留失败记录。3项修复测试最终通过；证据模块17项在首次组合检查中通过。因最小测试失败定位而额外定向复查，没有全量suite、模型评测或桌面冒烟。

尚未完成：高频结果块/重建超时的隔离定位、variant驱动资源清理、祖逖跨人物指代、带族属修饰的动作对象和因果漏召回。本轮未验证JS完整排序或实际模型排名改善。正式库、原文、gold、旧静态数据和桌面包均未替换。check_engine.py只暂存本轮解码修复，既有无关改动保留。
