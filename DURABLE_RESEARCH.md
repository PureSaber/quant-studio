# 后台因子研究

网页入口为`/durable-research`，支持选择管理员配置的不可变快照、修改观察窗口、提交、查询结果及请求取消。任务状态和尝试记录来自Pipeline持久任务库；网页服务与计算Worker使用独立进程。原生模板、Notebook及冻结账户继续使用原入口。

## 安装和配置

完整六仓固定安装、跨仓测试及服务机交接步骤见[quant-workspace安装配置](https://github.com/PureSaber/quant-workspace/tree/main/profiles/durable-research)。安装到新目录，再配置网页进程的`QUANT_STUDIO_DURABLE_CONFIG`为以下JSON文件的绝对路径：

```json
{
  "schema_version": "quant-studio.durable-research/v1",
  "python": "D:/quant-durable-v1/env/Scripts/python.exe",
  "database": "D:/quant-durable-v1/state/jobs.db",
  "artifact_root": "D:/quant-durable-v1/state/artifacts",
  "cache_database": "D:/quant-durable-v1/state/cache.db",
  "requests_root": "D:/quant-durable-v1/state/requests",
  "snapshots": {
    "example": {
      "title": "管理员已登记的快照",
      "request": {}
    }
  }
}
```

`request`需要完整的`quant.job-request/v1`滚动研究请求，包含单个因子、频率、证券池、诊断参数和真实快照引用，不能使用示例中的空对象。Lab的`examples/rolling_research_fixture.py`可生成明确标为合成的完整请求，供安装测试使用。所有路径来自管理员配置，浏览器只能选择快照与1–4096个周期的窗口。

Worker使用相同任务库、CAS和因子缓存路径，由服务机独立管理：

```powershell
$env:QUANT_RESEARCH_STORE_ROOT='D:/quant-durable-v1/state/artifacts'
$env:QUANT_RESEARCH_CACHE_DB='D:/quant-durable-v1/state/cache.db'
D:/quant-durable-v1/env/Scripts/python.exe -I -m quant_pipeline.durable_worker --database D:/quant-durable-v1/state/jobs.db --workspace D:/quant-durable-v1/state/attempts --handler rolling_research=quant_lab.rolling_research:worker_handler --artifact-guard quant_pipeline.durable_research:retain_artifacts
```

## 日常使用

### 多窗口批量研究

同一页面的“比较多个观察窗口”接受逗号分隔的 1–64 个不同窗口（每个 1–4096）。
提交到 Pipeline `quant.job-batch/v1`，所有候选共享同一不可变快照，各自使用原有
Job/Attempt、日志、取消及租约恢复机制。管理员研究请求的 `project_id` 保留。
批次页面显示逐候选状态与结果／尝试历史，可整批取消未完成候选；失败及取消候选
可新建批次重试，旧批次、失败记录与完成结果全部保留。重复表单提交保持幂等。

此入口需要含 `durable_batches` 的 Pipeline 版本；原先固定六仓配置需由发布流程
升级并完成跨仓验收后才开放批次功能。测试 `test_durable_batch_integration.py`
使用真实隔离解释器 CLI、CAS、Lab 处理器与 Worker，不以 mock 结果代替集成通过。
没有扩展因子缓存支持范围；批次共享输入并不等于端到端流式计算。

### 单次研究与恢复

1. 打开“后台因子研究”，选择快照并修改观察窗口。
2. 提交后查看任务状态、结果、逐次尝试及事件。相同表单重复提交返回同一任务；改变参数后重新打开提交页面。
3. 关闭浏览器或停止网页服务后，Worker继续执行。重启网页服务使用相同任务库即可查看结果。
4. 取消请求由Worker处理；已经完成的结果保留。Worker异常退出的任务在租约到期后由另一Worker恢复，旧尝试不会覆盖新结果。

管理员修改快照配置后，旧表单会拒绝提交并提示刷新。登录、同源和CSRF检查沿用工作台已有访问控制；网页不能提交处理器模块、命令或任意本地路径。研究结果是因子计算及诊断，不代表策略收益。

部署验收由服务机执行；本次代码交付不代表已升级服务机、自启服务或异地访问。
