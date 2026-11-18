# Harmony安装与适配进度（2026-11-18，未完成）

本轮新增固定gpt-oss-20b MXFP4模型登记、独立Harmony兼容性入口和两个离线测试。默认模型、搜索、桌面及语料不变。

## 已完成

- 固定制品沿用gpt_oss_local_comparison_plan.json中的revision、12,109,566,624字节和SHA256。
- 使用既有安装器：续传文件与成品分离，大小及完整SHA通过后才重命名并生成独立回执。旧运行器已校验，没有重装。
- 独立tests.context_reader.probe_harmony继承已验证CPU配置；原生模板、medium强度、512推理预算，不人为添加<think>。运行时记录实际模板与渲染hash。
- 入口只设计一条非历史算术兼容性请求，验证analysis/final分离及完整结束，不载入历史测试题，不评价古文能力。需显式--run，默认不启动模型。
- python -B -X utf8 -m unittest tests.test_harmony_probe：2项通过，验证固定制品对应、独立参数、拒绝错误模板结构。只执行这一个测试命令。

## 尚未完成与下载记录

仓库所在卷可用空间约385.5GiB。尝试直连及仅本进程代理续传，网络传输持续缓慢；本轮停止下载并保留859,832,320字节（约7.1%）于src/data/evidence-reader/gpt-oss-20b-MXFP4.download.partial。文件未完成完整哈希校验，未产生receipt-gpt-oss-20b.json，不可加载。没有把缓慢下载称为服务器错误。

没有启动新模型，没有真实生成请求，历史题/留出题均0次。模板兼容、通道分离、计算正确性、预算实际激活/截断、峰值内存和时延均待实测。离线测试不代表运行兼容通过。

## 下一次续接

使用已有安装入口python -u -B -X utf8 -m scripts.setup_evidence_reader --model gpt-oss-20b（工作目录src），自动从片段续传；如使用代理只限本进程，不改系统设置。下载完成并校验后，显式运行python -u -B -X utf8 -m tests.context_reader.probe_harmony --run。

真实探针完成后需审阅日志确认预算支持。简单算术可能不耗尽512推理预算，不能由正常收尾宣称已验证强制截断；若不能从固定运行器机制和日志证实，保留未验证状态，不进入历史题。原正反题仍须全部完整通过才可运行留出题。

参考：[固定运行器的Harmony专用处理](https://github.com/ggml-org/llama.cpp/blob/b12283/common/chat.cpp)。注册和代码支持不代替本机测试。

本轮仅提交安装登记、实验入口、两个小测试及此记录，不提交模型片段、日志或本机信息。未下载新运行器、未改GPU、未上传史料、未调用云端。

## 续传与探针验收更新（2026-11-18）

原859,832,320字节完整保留，剩余字节从同一固定发布版本按区间并行续传，逐段核对HTTP 206及Content-Range。区间片段保留在忽略的数据目录，合并后仍需经过安装器的完整大小及SHA256校验。续传已实测超过2.4GB；本节记录时尚未完成，无安装通过结论。续传进行期间不要同时启动另一个安装进程写入同一片段。

自动等待任务最多90分钟，只在成品及安装回执均存在时启动一次非历史算术探针；探针会再核验权重及现有运行器。进度日志为 `src/data/evidence-reader/harmony-compatibility-queued.log`，实际运行报告路径会由探针写入该日志。等待超时或初始化失败都不会启动历史题。

探针现在分别记录原生输出通道、算术正确性、512推理预算激活和强制截断。完整停止、推理与最终回答分离、算术正确、日志确认预算激活才标兼容探针完成。若未耗尽预算，记录 `budget_forced_end=false`，只能确认预算被启用，不能声称已实测其截断。

固定版本的 [GPT-OSS 解析器](https://raw.githubusercontent.com/ggml-org/llama.cpp/b12283/common/parsers/gpt-oss.cpp) 提供 Harmony 推理开始与结束标记；[服务端代码](https://github.com/ggml-org/llama.cpp/blob/b12283/tools/server/server-common.cpp) 表明只有存在结束标记才把预算参数传给采样层。实际激活、通道输出、耗时和峰值内存仍以本机报告为准。`python -B -X utf8 -m unittest tests.test_harmony_probe` 三项通过；未运行历史题或留出题。
