# 研究工作区：数据→项目→Notebook→实验→建议

本版关注研究链路：把一个研究问题及其输入、方案版本、代码、实验和结论放在同一个项目中。
策略收益、异地连接、手机操作和服务重启验收不属于本次变更的验收结论。

## 日常使用

1. 打开侧栏“数据目录”，搜索已登记数据。点选CSV、TSV或Parquet文件，查看字段、样本、空值和重复统计；需要采集时进入原有“数据集”页。
2. 在“研究项目”创建问题，连接数据集及固定版本的研究方案，保存项目。
3. 点击“创建草稿或刷新数据上下文”，再打开JupyterLab。首次访问需要独立的Jupyter凭据。
4. 在Notebook交互探索、检验或绘图，保存文件。回到项目页，点击“冻结已保存草稿并提交实验”。
5. 任务进入现有FIFO队列。打开Notebook实验，查看图表、文本或失败诊断，下载已执行Notebook、源码和环境记录。
6. 将判断记在项目笔记中，保存新版本。方案实验沿用原有实验比较；Notebook实验可分别查看和下载，首版不自动推断或比较自定义指标。
7. 在“研究助手”输入问题，预览将使用的证据。离线整理无需模型；配置模型后，另有明确的发送按钮。历史建议保存在项目内。

“预检并运行”会串行执行原生预检及原生运行；预检失败不继续运行。合成模板用于测试流程，无外部预检。
项目历史保留不可变版本，并保留历次连接过的方案实验入口；同一方案被多个项目连接时，相关实验可同时出现在这些项目中。
项目版本页可回看旧问题和笔记，保存使用乐观并发检查，旧页面不会覆盖新版本。

## 独立Notebook环境

Studio核心依赖未增加Jupyter、Pandas、PyArrow或绘图库。创建新的Windows/Python3.12环境，使用本次独立锁文件。
不要将这个环境安装到已有账户或冻结研究环境中。

```powershell
python -m venv .venv-notebook
.venv-notebook\Scripts\python -m pip install --no-deps -r optional/notebook-windows-py312.lock
.venv-notebook\Scripts\python -m pip check
```

下例路径需替换为本机实际路径。将Notebook配置保存到工作目录之外的私有配置位置：

```json
{
  "python": "C:\\quant\\quant-studio\\.venv-notebook\\Scripts\\python.exe",
  "lab_url": "http://127.0.0.1:8890",
  "timeout_seconds": 600,
  "max_input_bytes": 1073741824
}
```

启动Studio的进程设置`QUANT_STUDIO_NOTEBOOK_CONFIG`为此JSON的绝对路径。
`lab_url`是浏览器实际访问的地址，支持本机HTTP或HTTPS独立域名根路径，不接受URL内凭据或令牌。
同一设备上的Studio与JupyterLab必须使用同一个`runs-root`。需要通过远端访问时，由部署侧提供已有认证和HTTPS接入；本版启动器始终监听回环地址。

先生成并私下保存一个Jupyter令牌文件，然后在另一个终端启动Lab：

```powershell
$env:QUANT_STUDIO_NOTEBOOK_CONFIG = 'C:\quant\private\notebooks.json'
.venv\Scripts\python -c "import secrets; from pathlib import Path; Path(r'C:\quant\private\jupyter-token.txt').write_text(secrets.token_urlsafe(32), encoding='utf-8')"
.venv\Scripts\python -m quant_studio.notebook_lab --runs-root C:\quant\state\runs --token-file C:\quant\private\jupyter-token.txt --port 8890
```

令牌文件只生成一次；不要将其或Jupyter启动日志提交到Git。Lab根目录为`runs/.notebooks`，项目草稿为`<project-id>/research.ipynb`。
启动器使用所选环境的JupyterLab；批量实验显式构造所选Python的内核，不采用Notebook携带的内核路径。
Lab本身具有独立登录，当前没有单点登录，也没有自动服务安装。部署侧需分别监护Studio与Lab。

Notebook在登录用户的操作系统权限下运行，仅适合单用户可信代码。这是依赖环境分离，不是针对不可信代码的安全沙箱。
数据目录预览Parquet沿用应用已配置的Python环境，要求该环境已有PyArrow；不自动修改其依赖。
可选Notebook环境自带PyArrow供自由探索，不能替代其他原生应用的独立依赖环境。

## 数据目录与字段定义

预览先核对已登记文件哈希，读取后再次核对。CSV/TSV上限4MB，质量扫描最多10000行，页面最多展示20行；Parquet按批读取并显示扫描范围。
证券代码保留文本。CSV空值按空白单元格，Parquet空值按Arrow null；重复统计按整行。空值统计不等于停牌判断、交易日完整性或历史可得性认证。
数据来源、起止日期和证券集合来自原始登记清单，未声明的内容不会根据文件修改时间或列名猜测。

字段解释可在登记前写入`manifest.json`，与数据一起绑定哈希：

```json
{
  "provider": "your-declared-source",
  "column_descriptions": {
    "bars.parquet": {
      "symbol": "证券代码，保留前导零",
      "volume": "成交量；单位和复权规则由数据提供者明确声明"
    }
  }
}
```

首版无网页数据字典编辑器。修改清单后需登记新数据版本，避免用新解释追认旧实验。

## 实验记录与可复现范围

- 项目：`runs/.projects/<id>/<revision>.json`及HEAD，包含问题、数据ID、方案精确版本和笔记。
- 草稿：`runs/.notebooks/<id>/research.ipynb`及`inputs.json`。刷新上下文不覆盖代码；刷新后需重跑Notebook中读取上下文的单元格。
- 实验：`runs/.projects/<id>/executions/<execution-id>/`，保存清除旧输出的源码、执行器副本、登记输入实体副本、项目版本、提交与执行时环境清单、输出及哈希。
- 建议：`runs/.projects/<id>/advice/<request-id>/`，保存审阅过的证据摘要、来源链接、发送方式和建议结果。

提交时保存解释器与包版本清单，执行时若身份不同则拒绝运行。输入和源码执行前后均校验，输出查看/下载也核对哈希。
同一实验不会自动重放；重跑创建新实验。重复提交同一助手请求被拒绝。排队取消、服务停止和重启后的中断状态同步到项目记录。
进程强制中断时可能没有完整Notebook输出，任务中心保留已收集日志，不把中断视为成功。

这些措施固定已登记输入及执行证据，不冻结整个操作系统。代码自行联网、读取未登记文件、随机数、GPU非确定性和同版本包内容变化仍需研究者自行控制。
如登记的是配置文件，只复制配置本身；其中引用的其他文件必须分别登记才能进入本次输入快照。
普通方案运行仍沿用原生数据验证，只有Notebook提交额外复制已登记文件。大量数据会增加复制耗时和磁盘占用，默认总量上限1GiB。
备份前停止JupyterLab和正在运行的内核，再使用已有Studio备份流程；Jupyter活动不在Studio任务队列内。

## 连接研究助手

先在quant-agent的独立环境中按其`requirements.lock`安装对应工作台助手提交，遵循该仓库安装说明。
在Studio已有运行环境配置`python_by_repo`中配置`quant-agent`解释器。
默认只离线整理证据。在线建议另需服务端设置`QUANT_AGENT_LLM_OK=1`、`QUANT_STUDIO_ASSISTANT_MODEL`和模型供应商凭据。
网页只在用户点击“同意发送这些内容并生成建议”后发送上方问题与摘要。

摘要目前包含本项目问题、前10个数据登记、前8个方案、最近5个相关方案实验和最近5个Notebook实验，每条有字节上限。
不读取账户账本、价格表或Notebook源码来发送。路径及常见令牌赋值会隐藏，但自由文本仍需在发送前审阅。
输出引用必须存在，引用片段必须能在证据中找到；这不能自动证明模型推理或因果判断正确。模型不运行工具或修改研究代码。
首版未接跨项目语义检索，也未实现自动修复或自动生成策略。

## 验证

基础回归：`python -m pytest -q`及Ruff。可选集成测试需要显式配置：

```powershell
$env:QUANT_TEST_NOTEBOOK_PYTHON = 'C:\quant\quant-studio\.venv-notebook\Scripts\python.exe'
$env:QUANT_TEST_AGENT_SOURCE = 'C:\quant\quant-agent'
$env:QUANT_TEST_AGENT_PYTHON = 'C:\quant\quant-agent\.venv\Scripts\python.exe'
python -m pytest tests/test_research_workspace_integration.py tests/test_notebook_research.py tests/test_project_lifecycle.py -q
```

集成测试真实启动临时Lab，检查鉴权、启动内核、保存Notebook、读取Parquet、排队执行、图表和下载，并在结束后关闭测试服务。
另有真实独立Agent进程的离线测试；模型接口使用可控替身测试引用和错误处理，不代表真实在线模型已经验收。
本版借鉴BigQuant的研究入口与数据目录思路，Notebook交互采用[JupyterLab](https://jupyterlab.readthedocs.io/en/stable/user/)，批量执行采用[nbclient](https://nbclient.readthedocs.io/en/latest/client.html)。
