# quant-studio

quant-studio 是只在本机回环地址运行的量化研究模板台。它让用户修改少量已声明参数，先检查生成的配置与参数列表，再选择是否运行上游工具。它不实现因子、撮合、账本或通用绘图，也不会安装三个外部研究仓库。

外部模板只会在设置 `QUANT_WORKSPACE_ROOT` 且对应上游仓库目录存在时执行。合成样例使用仓库内固定收益率，仅用于验证操作流程；合成样例不是市场收益。所有内容均为研究用途，不构成投资建议。

## 安装

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install --no-deps -r requirements.lock
.venv\Scripts\python -m pip install --no-deps --no-build-isolation -e .
.venv\Scripts\python -m pip check
```

## 预览

```powershell
.venv\Scripts\python -m quant_studio preview a-share-four-factor --set rebalance_freq=weekly --set symbols_limit=30
```

`run` 默认也只预览。只有显式添加 `--execute` 才执行：

```powershell
.venv\Scripts\python -m quant_studio run paper-sim --set initial_capital=200000 --execute
```

## 合成运行

合成模板不依赖外部仓库，会在 `runs/<id>/` 生成 `nav.csv` 和 `report.html`：

```powershell
.venv\Scripts\python -m quant_studio run synthetic-demo --set initial_capital=10000 --execute
```

## 本机页面

```powershell
.venv\Scripts\python -m quant_studio serve --host 127.0.0.1 --port 8770
```

侧栏分为数据、策略、回测和结果。数据页只展示工作区里已经存在的快照，并给出拉取命令。A 股策略页可以勾选已经实现的因子，再提交回测。结果页画出净值、回撤，并在上游交出持仓或成交文件时显示表格。没有基准净值时会明确说明。

每次执行将配置中的 `outputs_dir`、`state_dir` 定向到本次运行的 `strategy-output` 目录，只从该运行内收集结果。共享工作区里的历史输出不会被导入；多个结果集合或没有产出会标记失败。净值 CSV 保留原始 Decimal 精度，显示时才四舍五入。合成样例不依赖其他仓库。服务拒绝非回环地址；报告服务只允许读取单次运行目录内的 HTML 和 CSV 文件。

A 股模板按上游约定读取本次输出的 `latest` 集合，明确展示 Q5 组合；同一次运行的时间戳副本不会被误判为其他运行。港股模板展示留出期的净值、持仓与委托，以及整份研究报告。未声明结果文件映射的外部模板仍会拒绝歧义集合。

港股研究需要已有快照。可在网页中填写路径，或运行：

```powershell
python -m quant_studio run hk-equity-daily --snapshot F:/Quant/quant-hk-equity/data/hk-snapshot --execute
```

也可设置 `QUANT_HK_SNAPSHOT`；未指定时检查上游仓库的 `data/hk-snapshot`。缺少 `manifest.json` 时明确阻止执行；完整性、日期和业务条件仍由上游校验。Studio 不自动下载或改写快照。模拟盘信号与状态配置迁移时，输入路径继续相对于上游仓库解析。

净值按实际日期排序，拒绝重复日期、无效数值和非正初始净值。多策略 CSV 必须显式选择策略，多组合 CSV 必须指定净值列。模拟盘首次步进的一条净值显示为“单次观测”，不会伪造区间收益或回撤。命令行成功/预览返回 0，运行失败返回 1，前置条件阻断返回 2。

运行前会检查仓库、可执行命令及 `python -m` 模块。Python 命令使用启动研究台的解释器；CLI 优先从该解释器所在目录查找。请在同一兼容环境安装需要运行的策略包。启动失败或超时会保留 `result.json` 和日志；“可运行”仅表示执行环境就绪，数据和业务前置条件仍由上游验证。模拟盘模板每次使用独立状态，不会续接共享账户。

合成样例会在运行目录写出净值页。A 股、港股和模拟盘模板默认只生成配置与命令；上游命令失败，或没有把 `report.html` 写进本次运行目录时，页面不嵌入净值。

## 开发检查

```powershell
.venv\Scripts\python -m ruff check src tests
.venv\Scripts\python -m ruff format --check src tests
.venv\Scripts\python -m pytest -q
```

`Upstream integration` 工作流在 Windows/Linux 上使用固定提交的上游依赖，执行真实港股和模拟盘 CLI，并检验真实 A 股输出函数与 Studio 收集器的契约。测试行情为合成 fixture，不代表市场表现。
