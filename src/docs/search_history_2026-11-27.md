# 搜索历史：任务汇报书

日期：2026-11-27

## 1. 任务概述

用户要求增加“查看搜索历史”功能，并说明此前的需求没有要求保留搜索记录。经确认，范围为**主搜索框**（首页搜索、标点版检索、原底本检索），显示在**搜索框下方折叠列表**，且桌面版需要**跨重启保留**。问答与单独语义入口不记录。

历史只是本机便利功能，不参与检索排序，不上传，不改变任何原文、索引或语料。

## 2. 执行结果

- 首页搜索、标点版检索表单、原底本“检索原文”在用户主动提交时写入历史。
- 初始 URL 加载、翻页、改变显示长度、点击历史条目重新检索时不写入、不改变排序。
- 搜索框下方显示“最近搜索”折叠面板；最多 100 条，重复的“查询＋版本＋书籍＋底本”去重并移到最前。
- 点击条目重新执行原检索，保留检索策略、书籍、底本与标点/原底本类型。
- 提供“清空历史”，二次确认后清空；面板注明“只保存在本机，不上传”。
- 读取或保存失败时检索仍照常工作，面板给出安静提示，不弹出阻断式错误。

## 3. 存储与接口

### 桌面版

桌面版每次启动使用随机回环端口，`localStorage` 的 origin 会随端口变化，因此历史不能只放在浏览器存储。新增本机接口：

| 方法 | 路径 | 行为 |
|---|---|---|
| GET | `/api/desktop/search-history` | 返回 `{"history":[...]}`；文件不存在或损坏时返回空列表 |
| POST | `/api/desktop/search-history` | 校验后原子写入 `src/data/desktop/search-history.json` |

POST 沿用桌面会话的安全边界：必须通过 `yinde_desktop` HttpOnly Cookie 与 Host 校验，`Origin` 必须是当前回环端口；请求体上限 256 KiB；写入使用临时文件加 `Path.replace`，并由进程内锁串行化。历史文件位于被 `src/.gitignore` 的 `data/` 下，不进入公开产物。

### 网页/静态模式

使用 `localStorage` 键 `historyai.searchHistory`，按站点 origin 保存。

### 条目与校验

条目形状：

```json
{"q":"查询","kind":"punctuated|original","book":"书籍ID","bookLabel":"显示名",
 "edition":"底本","strategy":"检索策略","at":1700000000000}
```

服务端与前端都做边界检查：查询非空且不超过 300 字；书籍不超过 64 字；显示名不超过 100 字；底本不超过 32 字；策略限定在既有枚举内；`kind` 只允许标点/原底本；最多 100 条。服务端如发现无效或重复条目，会拒绝整次保存，而不是悄悄丢掉其中一条。

## 4. 改动清单

| 文件 | 本轮性质 |
|---|---|
| `src/desktop/runtime.py` | 新增历史读取/保存接口、条目归一化、原子写入与锁 |
| `src/frontend/app.js` | 新增历史存储、渲染、去重、重新检索与首页/原底本记录 |
| `src/frontend/punctuated_search.js` | 标点版检索表单记录与折叠面板接入 |
| `src/frontend/style.css` | 历史折叠列表、滚动区、移动端元信息隐藏 |
| `src/tests/test_desktop_runtime.py` | 新增 4 项历史接口测试；超大体积测试改用只发请求头的原始连接，避开 Windows 在未读入请求体时的 RST 竞态 |
| `src/tests/test_search_history.mjs` | 新增前端历史归一化、网页存储、桌面接口、并发合并、链接与面板渲染测试 |
| `src/docs/search_history_2026-11-27.md` | 本报告 |

仓库中另有大量历史未提交文件，本报告只描述本轮增量，不把相对 HEAD 的累计差异当成本轮改动。

## 5. 验证结果

- `python -B -X utf8 -m unittest tests.test_desktop_runtime -v`：**32 项全部通过**，包含新增的历史往返、非法条目、Origin/体积和损坏文件回退测试。
- `node src/tests/test_search_history.mjs`：**6 组全部通过**（归一化、网页 localStorage、链接、桌面 GET/POST、并发记录、清空）。
- `python -B -X utf8 -m scripts.site.build_artifact data/site-check-search-history`：装配 30 个文件，发布闸门三项全部通过；真实语料 `data/` 未进入产物。
- `python -B -X utf8 src/scripts/build_desktop.py`：桌面包构建完成，输出 `desktop-dist/Yinde/Yinde.exe`。
- `python -B -X utf8 src/scripts/verify_desktop_backend.py desktop-dist/Yinde/Yinde.exe --receipt src/data/desktop/backend-receipt.json`：**29 个嵌入模块与底本目录校验通过**，嵌入运行时与源码一致。

## 6. 边界与待用户验收

- 已有桌面进程不会加载新运行时。验收前请关闭旧窗口，从 `desktop-dist/Yinde/Yinde.exe` 重新启动。
- 本轮没有启动真实窗口做点击验收。后端单元测试证明文件可跨进程读取，前端测试证明记录与重放逻辑，但“关闭桌面再打开，历史仍在折叠列表中”这一条仍建议用户实际重启一次确认。
- 历史仅保留查询与筛选信息，不保存检索结果正文；最多 100 条，只支持整体清空，暂不支持单条删除、分类筛选或跨设备同步。
- 历史是可选功能：存储失败不会影响检索，也不会把失败伪装成成功。
