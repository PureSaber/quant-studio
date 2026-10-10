# 批量实验与资源预算集成

本模块把批量实验建在现有`RecipeStore`和`JobManager`之上。批次固定保存方案的精确`revision`、完整方案副本、参数网格、候选和每次attempt；它不会更新方案HEAD，也不会从结果中自动挑选候选。

## 批量接口

`BatchStore(runs_root)`公开以下接口：

- `prepare(recipe_id, revision, grid, max_candidates=..., budget=...)`先用各维长度计算候选数，超过显式上限时不展开网格。每个值都通过冻结方案对应模板的knob契约校验，包括模板已声明的日期和随机种子。
- `submit(batch_id, submission_key=..., enqueue=..., owner_id=..., expected=...)`是唯一首次调度入口。相同`submission_key`幂等返回；不同键不能重复提交。`expected`提供记录revision的CAS检查。
- `status(batch_id, lookup=..., enqueue=..., owner_id=...)`核对已有Job终态。只有预检状态为`checked`时才提交同一候选的`execute`，随后才处理下一候选。
- `cancel(batch_id, cancel=..., expected=...)`取消当前Job并把尚未派发的候选标记为取消，不删除已完成、失败或中断的attempt。
- `retry(batch_id, candidate_ids, retry_key=..., enqueue=..., owner_id=..., expected=...)`只接受预检失败、运行失败或中断的候选，并追加新attempt。相同`retry_key`幂等返回。

`.batches`记录使用临时文件、`fsync`、原子替换，以及Windows`msvcrt`/Linux`flock`单机跨进程锁。记录revision覆盖完整可变状态。外部Job调度使用由批次、候选、attempt和阶段推导的确定性`dispatch_key`。

服务必须创建`BatchSupervisor(manager, store=BatchStore(root), profile=...)`。协调器扫描持久化活动批次，因此浏览器关闭后仍会推进“预检→运行→下一候选”。关闭服务时先`supervisor.close()`，再`manager.close()`。新服务的`owner_id`不同；它会读取被现有JobManager标为`interrupted`的旧Job并终结批次，不会自动派发剩余候选。用户需要显式选择失败项创建新attempt。

## JobManager最小集成

`job_manager_enqueue`兼容当前`JobManager.submit`，也会在签名支持时传递下面两个参数：

```python
def submit(..., dispatch_key=None, batch=None):
    ...
```

父层应在`JobManager`锁内按`dispatch_key`查找已有记录：已存在且请求身份一致时返回原记录；同键身份不同则拒绝。Job持久化记录和内存request均保存`dispatch_key`与`batch`。`batch`只含`batch_id`、`candidate_id`、`attempt_no`、`phase`和预算，不含命令行或环境。这个检查补齐“外部Job已落盘、批次尚未写回Job ID”崩溃窗口的幂等性。

服务Handler集成步骤：

1. 让主Handler继承`BatchHandler`，设置`Handler.batch_store`和`Handler.batch_supervisor`。
2. GET把`/batches`及`/batches/<id>`交给`_batch_get`；POST把`/batches/prepare`及`/batches/<id>/{submit,cancel,retry}`交给`_batch_post`。
3. 将这些POST路径加入现有同源、Content-Type、请求体上限和CSRF验证之后的白名单。`BatchHandler`假定CSRF字段已经由主Handler校验并移除。
4. 服务和测试清理路径都先关闭`BatchSupervisor`，再关闭`JobManager`。

`batch_web.py`提供带label的筛选表单、窄屏横向滚动表、失败候选重试、CSV导出和统一HTML转义。CSV按candidate/attempt逐行保留Job和run关联；以`= + - @`开头的文本值会加前导单引号，避免电子表格把参数当成公式。

## 资源监控接口

`ResourceBudget`要求显式正数`wall_seconds`，并可选正整数`memory_bytes`和`output_bytes`。`ResourceMonitor`与`ResourceStore`记录：

- 从Job创建到executor开始的排队等待时间；
- executor墙钟耗时；
- 当前原生进程及后代的RSS观测峰值；
- 已绑定运行目录内普通文件的输出体积；
- 触发终止的限额和各采样器状态。

executor按Job的`batch.budget`包装现有control：

```python
monitor = ResourceMonitor(
    job["job_id"],
    ResourceBudget(**job["batch"]["budget"]),
    store=ResourceStore(runs_root),
    queued_at=job["created_at"],
)
control = MonitoredControl(control, monitor)
try:
    return existing_execute(job, control)
finally:
    control.close()
```

runner在每次`_create_run_dir`后增加一个窄接口，不改变运行语义：

```python
if control is not None and hasattr(control, "attach_output"):
    control.attach_output(run_dir)
```

`MonitoredControl.bind_process`沿用现有JobControl持有的真实`Popen`句柄。超限时调用原control的`request_cancel()`，不读取持久化PID，不读取进程命令行或环境。Windows使用Toolhelp进程父子关系和`GetProcessMemoryInfo`；Linux使用`/proc/.../children`和`VmRSS`。其他平台或权限错误会把RSS状态写成`unavailable`或`incomplete`，`peak_rss_bytes`保持`null`，不会写成0或声称限额合格。

这些预算是轮询软限额。采样间隔内可能短暂超过阈值，且不提供容器、cgroup、Job Object或其他系统强隔离保证。输出上限只有在runner调用`attach_output`后才能实时执行；未绑定时记录为不可用。
