# 可选 Notebook 隔离执行

后台 Notebook 实验可以显式选择 quant-pipeline 的 Linux Docker 后端。
原有独立 Python 环境仍属于可信本地执行，不提供安全隔离。
JupyterLab 交互编辑和手动执行仍在其原环境中；本配置只作用于通过
Studio“冻结已保存草稿并提交实验”提交的后台任务。

支持 Linux、本机 `/var/run/docker.sock` Docker 服务、cgroup v2 与内置 seccomp。
Windows 宿主、不支持资源控制、不可用 daemon、可变镜像标签都会明确拒绝。
不会自动安装/启动 Docker，也不会回退到宿主 Python 执行。

先在 Studio 环境安装锁定的可选实现：

```bash
python -m pip install --no-deps -r optional/isolation.lock
```

该文件与 pyproject 的可选依赖锁定同一个 quant-pipeline 提交。
后端核心只使用标准库，Studio 已提供其现有命令入口所需 PyYAML。
运行完整 quant-pipeline 功能时仍须安装该仓正常锁定依赖。

由操作者准备含 `/usr/local/bin/python`、nbclient、ipykernel 及研究所需包的
可信 Linux 镜像。运行 `docker image inspect --format '{{.Id}}' IMAGE` 获取
`sha256:` 开头的不可变镜像 ID。后端不拉取镜像。测试用
`optional/isolation.Dockerfile` 仅供 CI；正式研究应使用由操作者锁定并保留的镜像。

在 `QUANT_STUDIO_NOTEBOOK_CONFIG` 指向的 JSON 中添加 `isolation`：

```json
{
  "python": "/absolute/path/to/local/jupyter/python",
  "lab_url": "http://127.0.0.1:8890",
  "timeout_seconds": 60,
  "isolation": {
    "schema": "quant.research-isolation/v1",
    "backend": "docker-linux",
    "image": "sha256:REPLACE_WITH_64_HEX_IMAGE_ID",
    "memory_mb": 512,
    "cpus": 1,
    "pids": 128,
    "work_mb": 32,
    "tmp_mb": 32,
    "output_bytes": 16777216,
    "log_bytes": 1048576,
    "timeout_seconds": 150,
    "seed": 42
  }
}
```

外层 timeout 是单元格超时；隔离策略 timeout 是整个 Notebook 工作进程的墙钟
上限，应留出内核启动和报告写入时间。网页展示当前后台模式及每次实验保存的模式。
宿主不支持时，提交失败原因与 blocked 记录可见。

原有 HTTP 提交路径和 API 保持不变：

```python
from quant_studio.notebooks import NotebookStore
store = NotebookStore("/absolute/runs-root")
record = store.prepare(project_id, project_revision)
result = store.execute({"project_id": project_id, "id": record["id"]}, control=None)
```

准备时冻结源码、登记输入、研究 SDK、镜像身份和执行策略。只读内容挂载于
`/inputs`；内核工作目录也是 `/inputs`，现有 `Research()` 和 `inputs.json`
读取方式可用。用户文件输出请写入 `/work`，内核临时文件使用受限 `/tmp`。
不可修改输入、宿主文件、网络或镜像根文件系统。自动报告、执行 Notebook、
指标和包清单写入 `/work`，经有总大小限制、拒绝链接的归档接口取回。
NumPy（若存在）和 Python random 使用记录的种子；其他随机库由 Notebook 显式设置。

`isolation.json` 可从实验页下载，包含真实执行/未执行状态、输入输出哈希、
镜像/daemon/kernel 身份、种子、资源策略及容器清理核验。`environment.json`
保存容器内实际 Python 和包清单。失败实验保留日志与已取得的报告；取消仅在
整容器及内核/后代清理核验通过后记录为 cancelled。输出超限、清理失败或
依赖缺失都不能变成成功。所有记录仍为原有不可覆盖的新实验身份。

有限支持范围：普通已登记数据文件和镜像内 Python 依赖。当前绑定宿主专用
QDK 解释器的 intake 数据明确拒绝隔离提交；不偷偷调用宿主读取器。
Docker daemon、内核、镜像及操作者属于可信边界；这不是对宿主内核漏洞或
恶意 Docker 管理员的防护。宿主断电/SIGKILL 后需要操作者检查带
`quant.research-isolation=v1` 标签的遗留容器。其他后端、实时交易与服务部署均不在范围。

`isolated Notebook execution` CI 在 Linux 实际构建并记录镜像 ID/包清单，执行
Notebook 成功/失败、只读输入、网络拒绝、种子、实际 JobManager 取消及清理测试。
后端更完整的文件、代理凭据、FIFO/链接竞态、CPU/内存/PID/空间、日志与输出攻击测试
在锁定版本 quant-pipeline 的 `real research isolation` CI 中执行。
Windows 本地测试验证明确拒绝及可信本地模式回归，不能替代 Linux 实测。
