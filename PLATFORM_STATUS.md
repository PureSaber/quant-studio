# 平台版本与验收

侧栏“版本与验收”（`/platform-status`）显示当前Studio服务进程及最近的平台观测。

服务通过`serve()`启动时固定PID、启动标识、解释器、安装元数据、Python源码树摘要、可读取的Git提交/脏状态与锁文件摘要。页面请求由同一服务进程处理，另外读取当前磁盘源码并与启动快照比较。源码或环境交付完成后，旧进程不会自动被标记为已升级。源码摘要只覆盖Python源码文件，不是已载入内存、操作系统或其他Worker的远程证明；无法读取Git信息时保持空值。

复用`QUANT_STUDIO_DELIVERY_CONFIG`中的独立Workspace解释器、source_root、profile、state_dir和命名候选。到“环境与交付”选择“核对平台版本与验收”，预览后提交已有任务队列。其固定命令为`quant-workspace platform-status`；可选择已配置候选同时核对其验收产物。网页不能提供命令或任意路径。需要Workspace包含此命令的版本，旧环境返回失败时不会显示通过。

最近检查显示生成时点及完整结果，明确是历史观测。最新任务失败、排队或尚无结果时不会自动回退到更早成功记录。结果读取核验原保存摘要；JSON按文本转义，不执行嵌入HTML。页面沿用现有Host和登录访问控制。

此入口不重启服务、安装环境、修改业务数据或启动账户。全平台状态仍区分声明、源码、安装、特定组合验收与未观测服务；Studio自身进程身份不能代表所有研究进程均已切换。

跨进程验收：设置`QUANT_TEST_PLATFORM_WORKSPACE_PYTHON`为已安装新Workspace的解释器，运行`pytest tests/test_platform_status_web.py`。真实Delivery Job在故意缺失仓库和环境的夹具上调用Workspace，保存`attention_required`报告，并确认任务保持失败、页面仍显示诊断；不会将诊断报告的成功生成当作平台验收通过。研究工作区CI使用固定Workspace提交执行此测试。
