"""第二阶段 — 先秦史料全文检索。

search.engine.run_search  全文检索主入口（FTS5 trigram + 短词 LIKE 补充路径）
search.context.get_context 相邻上下文（同文件内真实相邻 passage，非 AI 生成）
search.zh               简体→繁体（Windows LCMapStringEx；不可用则恒等回退）

原则：索引只吃 passages.normalized_text（检索派生文本）；text_orig 永不改动。
出处字段一律来自数据库真实值，缺失返回 None（前端显示「暂无」），
绝不根据 KRxxxx_NNN.txt 文件名猜测语义章节。
"""
