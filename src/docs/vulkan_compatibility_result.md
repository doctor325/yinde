# GPU兼容性排查：Flash Attention与FP16（2026-11-16）

**已缩小故障范围，但没有得到可交付的GPU配置。** 相同史料输入在自动启用Flash Attention时重复G字母；显式关闭后不再重复，但输出是泛泛的任务分析，未回答历史问题。单独设置禁用FP16计算开关仍重复G。不能将“乱码消失”当成“模型判读正确”，也不能据单题确定具体驱动或计算内核缺陷。

后续[组合设置及完整题结果](vulkan_combined_pilot_result.md)：同时关闭Flash Attention、禁用FP16计算后，本次输出可读且完整，但正反题仍因事件错配和引用错误得到0/2。尚缺CPU同输入的完整回答对照，未认定GPU计算已完全可靠。

## 固定条件和依据

沿用上轮冻结的64-token请求、同一8B模型、同一b12283可执行文件及DLL、同一实际聊天模板。请求SHA256仍为`6de4d67fd0fdf86dcf75cc6b1a42b520c13ed4f48d51d43e689b797e47d65ade`，完整输入756 tokens、缓存命中0。未修改问题、编号原文、gold、模板或采样参数；每次启动新进程，每请求120秒上限，不重试、不调用留出题。

上轮报告字节SHA256固定为`f726953b77ac9808da57fe292ef44f3e7d2836044f05d3c25ed4c733509c0c89`。先验证此报告及原始输入来源，再将当前模型/程序/模板/请求身份与上轮逐项比较；漂移时不生成。本轮不再运行已取得可读开头的CPU对照。

参考的是实际使用版本的官方说明，而非猜测启动参数：

- [b12283服务器参数](https://github.com/ggml-org/llama.cpp/blob/b12283/tools/server/README.md)支持`--flash-attn off`，默认auto；`-lv 4`增加运行日志。本轮三组都使用相同日志级别，新采集基线用于核对实际生效的计算配置。
- [b12283 Vulkan实现](https://github.com/ggml-org/llama.cpp/blob/b12283/ggml/src/ggml-vulkan/ggml-vulkan.cpp)读取`GGML_VK_DISABLE_F16`控制后端fp16能力选择。该开关不等价于“所有计算与缓存都切成FP32”。
- 既有上游重复字母/Intel乱码问题只作线索，不认定与本机同因。本次没有下载新运行时、修改驱动或查询新的硬件/系统配置。

## 实测

每组均确认37/37层加载到GPU，模型缓冲约4455.34 MiB，KV缓冲1152 MiB；不存在静默退回CPU。身份哈希与上轮一致。

| 配置 | 相对于本轮GPU基线的唯一计算设置改动 | 启动 / 请求耗时 | 实际输出 | 判断 |
|---|---|---|---|---|
| baseline | 无，Flash Attention为auto | 8.27 / 12.06秒 | 一个汉字后连续63个G | 重复故障仍在 |
| flash-off | `--flash-attn off` | 7.52 / 13.71秒 | 泛泛说明如何分析用户文本，未进入具体史料事件 | 本次无连续G，仍未得到有效答案 |
| no-f16 | 临时子进程`GGML_VK_DISABLE_F16=1`，Flash Attention仍auto | 4.37 / 17.75秒 | “原文”后连续63个G | 单独该开关不足以消除故障 |

三组均产生64输出tokens，停止原因为`length`，`completed=false`、`semantic_acceptance=false`。这是受控的输出开头排查，不进行完整答案评分，也不报告有效答案加速倍率。

配置日志提供的进一步证据：

- baseline：Flash Attention实际enabled，GPU计算缓冲112.01 MiB。
- flash-off：`flash_attn=disabled`，GPU计算缓冲564.01 MiB，说明关闭参数被实际执行。
- no-f16：Flash Attention仍实际enabled，KV仍为K(f16)/V(f16)，计算缓冲112.01 MiB。因此不能由这次失败宣称“已排除所有半精度问题”；日志没有逐内核精度跟踪。

本地原始结果（不提交Git）：

- `src/data/evidence-reader/vulkan-compatibility/aeed9e607c94/report.json`：基线与关闭Flash Attention的成对运行，旁边保存各自日志。
- `src/data/evidence-reader/vulkan-compatibility/18e9c842b0dc/report.json`：单独禁用FP16计算的运行与日志。

## 能确定和不能确定的事

当前自动启用Flash Attention的GPU配置不可用于历史判读验收。改变Flash Attention路径会改变重复故障表现，值得优先追踪；但关闭后仍偏离任务，不能直接接入默认搜索。关闭前后的差异还不足以区分内核、精度、缓存路径或驱动兼容性，更不能证明显卡硬件损坏。

单独禁用FP16的结果说明这一开关不是现成修复；没有验证它与关闭Flash Attention组合后的行为。没有改变KV类型、批大小、卸载层数、模型或驱动，也没有进行组合搜索。每组仅一次，执行顺序和驱动缓存未随机化，不能宣称稳定率或覆盖任意输入。

## 验证预算与交付

实际只执行3条直接相关命令，预算用完即停止：

1. `python -B -X utf8 -m unittest tests.test_vulkan_compatibility`：2项通过，0.227秒。检查各配置只改指定选项、仍固定GPU、未知配置拒绝、父进程环境不变，以及模拟启动失败后临时补丁被还原。
2. `python -B -X utf8 -m tests.context_reader.probe_vulkan_compatibility --step flash`：基线与关闭Flash Attention各一次，退出0仅表示采集完成。
3. `python -B -X utf8 -m tests.context_reader.probe_vulkan_compatibility --step f16`：单独禁用FP16一次，退出0仅表示采集完成。

新增内容只在tests与docs。环境开关通过独立诊断中的临时函数替换，仅加入模型子进程环境字典，不设置`os.environ`、不写用户或系统环境、不改变生产启动器；模型关闭沿用原有自有进程清理。测试还覆盖补丁在失败后恢复。

未改默认搜索、正式库、原文、gold、桌面包、系统驱动或用户配置；没有云端模型调用或外发史料。原始请求、输出、日志、硬件信息及模型文件均不进入提交。

下一步建议先做一个受控组合对照：以已经关闭Flash Attention的配置为参照，仅再禁用FP16计算，检查偏题是否仍在；输入、模板、64-token上限都不变。这是补上两条计算路径可能相互影响的缺口，不是继续调提示词。若仍无法恢复与CPU一致的可靠输出，就暂停当前GPU后端试错，使用CPU完成原正反题的完整判读；再决定是否值得评估另一本地后端。无论哪条路线，都不能跳过完整语义验收直接更新桌面版。
