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

侧栏分为数据、策略、回测和结果。数据页只展示工作区里已经存在的快照，标明最近更新时间，并给出拉取命令。策略页把每个模板排成数据、因子、交易、回测几块积木；A 股可以勾选已经实现的因子。旁边的代码区只展示将要生成的配置和参数列表，并随表单更新，不执行手写代码。回测页列出全部任务；结果页只保留已经交出净值或报告的运行，打开后画出净值、回撤，并在上游交出持仓或成交文件时显示表格。没有基准净值时会明确说明。运行页会写出这次提交的参数和生成的配置。

运行成功后，工作室在上游输出目录里寻找 `report.html` 和净值序列，并在本页画出期末净值、区间涨跌、最大回撤和日期轴。合成样例不依赖其他仓库。服务拒绝非回环地址；报告服务只允许读取单次运行目录内的 HTML 和 CSV 文件。

合成样例会在运行目录写出净值页。A 股、港股和模拟盘模板默认只生成配置与命令；上游命令失败，或没有把 `report.html` 写进本次运行目录时，页面不嵌入净值。

## 开发检查

```powershell
.venv\Scripts\python -m ruff check src tests
.venv\Scripts\python -m ruff format --check src tests
.venv\Scripts\python -m pytest -q
```
