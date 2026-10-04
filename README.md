# quant-studio

quant-studio 是只在本机回环地址运行的量化研究模板台。它让用户修改少量已声明参数，先检查生成的配置与参数列表，再选择是否运行上游工具。它不实现因子、撮合、账本或通用绘图，也不会自动安装外部研究仓库。

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

首页汇总环境、数据、最近研究、前向账户和结果入口，并列出环境阻断原因与首次使用步骤。侧栏可直接进入环境、数据、策略、回测、结果和账户。数据页展示已有目录和文件修改时间，明确它不代表行情截止；各模板的数据预检支持状态单列。策略页可修改允许的参数，A股可以勾选已实现因子。代码区展示配置与参数列表，不执行手写代码。回测页保留最近40条预览、预检、运行和失败记录；结果页展示其中带净值或报告的记录，包括存放在本次输出子目录内的报告。结果优先显示曲线、回撤、持仓成交和报告；原始配置与命令可展开查看。

A股、港股和模拟盘模板均提供独立“数据预检”，分别调用所选环境的`a_share_multifactor.preflight`、`quant-hk preflight`和`quant-paper preflight`，校验同一配置及已有输入，不执行策略。预检记录保存配置、命令、原始输出与上游证据；通过后可“使用相同参数运行”，保留参数、因子和快照选择。实际运行仍重新校验输入，预检不是数据锁定或投资适用性认证。

A股只读加载缓存、检查历史时点、因子可计算性及全收益基准，不下载缺失数据或创建快照。模拟盘检查信号、证券身份、费用及保存状态前置条件，不回放成交或改写账户；Studio仍为每次运行使用独立状态。所选上游环境需要支持对应预检命令，旧版本不支持时保留失败日志，不回退到完整回测或可写的status命令。

```powershell
python -m quant_studio check hk-equity-daily --snapshot H:/Quant/hk-snapshot
```

## 首次使用配置向导

从首页的“首次配置工作区”或侧栏的“首次配置”进入向导。填写已有工作区、此次使用的应用Python、数据或研究配置路径，以及可选的已有账户来源JSON。向导校验路径、环境和模板前置条件，展示此次选择；点击“保存这份配置”后才写入本地文件。无效路径与环境保留填写内容并说明修复方法。未配置的应用可稍后补充，合成样例无需外部配置。

默认配置保存在本次研究记录目录的`studio-settings.json`。也可以在服务启动时选择独立保存位置：

```powershell
python -m quant_studio serve --runs-root H:/Quant/studio-runs --settings H:/Quant/studio-local/settings.json
```

使用相同`--settings`路径或相同研究记录目录重新启动，即可恢复已保存选择。文件采用`quant-studio.settings/v1`，包含明确选择的工作区/输入/账户配置路径和各应用Python映射。未使用向导时继续沿用原环境变量；留空字段也沿用启动配置，非空选择优先。向导连接已有账户来源文件，不创建或修改账户配置、登记或观测。

配置检查不会回测、下载、安装依赖或认证数据质量。保存后进入模板执行原生“数据预检”，通过后再“使用相同参数运行”。合成模板保持原有预览与运行流程，没有业务数据预检。向导显式选择工作区时，未填写数据路径的模板会要求补充输入，不自动选取默认数据目录。

网页只能保存到服务启动时确定的位置，不能提交其他写入目标。保存绑定经过校验的预览与文件版本；文件被外部改动、预览过期或来源失效时拒绝保存，要求重新校验。保存位置不接受符号链接、目录联接、硬链接或非本配置格式的已有文件。新配置不改变正在处理的请求；后续请求使用新快照，进程环境变量和历史运行保持原样。网页继续要求本机Host、同源Origin和CSRF令牌。

配置文件含本机路径，请保存在源码仓库外，不提交到Git。原有十一模板和十个业务预检入口的业务契约保持不变；完整首次安装与真实业务认证仍按各应用的要求完成。

## 基金与美股模板

基金/FOF与美股/ETF模板均调用上游原生只读预检，保持各自的数据与账户规则。
加上期货、Crypto离线样例、统计套利、择时及固定反事实研究，当前共十一个模板、十个业务预检入口。

择时模板`timing-research`读取已有YAML配置（页面路径或`QUANT_TIMING_CONFIG`），可修改原成本系数，
默认1保持原费率和冲击系数。原生预检不计算信号或回测；通过后可使用相同参数运行。
结果由所选上游Python核验标准产物，分别显示仓位发布状态、滚动测试折与描述性全样本曲线。
`hold_previous`明确显示未发布新系数，预热或其他发布阻断不生成成功曲线。滚动表可完整下载，
平均折超额不替代复合收益。打开页面会检查原生证据和展示文件，损坏时拒绝显示旧结果。

新版择时产物增加可核验的收益贡献：资产、现金、保证金、期货盈亏、模型基础成本及冲击成本分别显示，
合计等于描述性全区间或单个测试折的净收益。策略和匹配敞口对照按原生路径比较，贡献单位为百分点；
各折独立归因但不重新建仓、不拼接重叠日期。计算口径可展开，逐日CSV可完整下载。
页面和文件下载均复核已绑定的原生与展示文件；发布阻断不展示成功归因，旧运行缺少归因时明确提示。
模型成本不是真实成交滑点，账本贡献差不单独证明因果效应。

`timing-counterfactual`使用同一已有YAML配置，原生只读预检后执行固定候选族。
它不提供参数搜索或成本覆盖：保留原成本、预热和后续约束，比较原策略、仓位干预，
并在配置有风格模块时增加风格与联合干预。研究完成与仓位发布分别处理，所有候选禁止发布仓位。
结果页展示候选状态、描述性全区间及各测试折效应、联合交互和分量差；必需候选失败时
保留失败原因和原生报告，不生成家族效应。完整CSV、冻结协议和原生报告可直接打开。
所选上游Python从原生候选重算核验；页面和下载再次核对完整原生文件集合，新增仓位发布文件、
来源变化、文件缺失或篡改均被拒绝。各折不重新建仓、不拼接重叠日期，不增加独立前向证据。

|模板|已有输入|可修改参数|净值口径|
|---|---|---|---|
|`fund-fof`|基金数据集；网页路径或`QUANT_FUND_DATASET`|配置方法、起止日期、人民币资金、权重上限和现金缓冲|期初为1的单位净值|
|`us-equity-research`|美股bundle；网页路径或`QUANT_US_BUNDLE`|模型、股票池口径、基准ID、美元资金、训练/测试和调仓日数|以初始美元资金为基准的账户金额|

```powershell
python -m quant_studio check fund-fof --snapshot H:/Quant/fund-dataset
python -m quant_studio check us-equity-research --snapshot H:/Quant/us-bundle --set benchmark=US:YAHOO:SPY
```

基金需要`dataset.json`、`funds.json`、`nav.csv`、`calendar.csv`与`distributions.csv`；
非合成业务还需要原生应用要求的分用途日历。美股需要带文件哈希的`manifest.json`及其声明文件，
历史股票池、生命周期和质量模型分别需要对应证据。页面先检查必需文件存在，
完整数据结构、参数和业务前置条件由各自预检负责；不会自动下载、猜路径或借用港股输入。

基金预检不分配权重、不回放申赎，也不保证各决策日共同历史或优化可行。
美股预检不计算因子、不选择候选或回放账户。预检通过后可保留相同参数显式运行，
正式执行重新读取输入。基准ID必须属于美股bundle；合成`US:DEMO:SPY`需要明确填写。

在`QUANT_STUDIO_RUNTIMES`中分别配置`quant-fund`和`quant-us-equity`的Python，
可保留基金的Python3.12独立依赖。结果页根据原生产物显示合成、回顾性或历史PIT等数据性质；
缺失或未知声明会使收集失败。合成输入只验证软件。日期、币种及期初净值分别处理，保留首日损益。
美股策略与基准的观测日期必须完全一致，两者都以配置初始资金为分母；结果页显示同区间基准指标，完整比较保留在原始报告中。宽持仓和成交表在各自容器内横向滚动。

## 已有前向账户

将`QUANT_STUDIO_ACCOUNTS`指向本地JSON配置，例如：

```json
{
  "schema_version": "quant-studio.accounts/v1",
  "accounts": [
    {"id": "etf-forward", "label": "ETF前向观察", "path": "H:/Quant/paper/account"}
  ]
}
```

只列出明确配置的账户，打开详情时调用`quant_pipeline.research_paper inspect`。核验环境由下文`QUANT_STUDIO_RUNTIMES`中的`quant-pipeline`项指定；不指定时使用Studio环境。该环境需要支持只读inspect命令，可使用独立维护环境，不更新账户自己的冻结环境。子进程使用隔离模式，不继承PYTHONPATH。

账户页显示登记窗口、已完成观测、失败尝试和保存证据的核验结果。无观测时绩效显示不可用；读取失败时显示原因，不回退到未核验缓存。此入口不登记、追加观测、封存、生成原账户报告或提供任意本地文件下载。

每次执行将配置中的 `outputs_dir`、`state_dir` 定向到本次运行的 `strategy-output` 目录，只从该运行内收集结果。共享工作区里的历史输出不会被导入；多个结果集合或没有产出会标记失败。净值 CSV 保留原始 Decimal 精度，显示时才四舍五入。合成样例不依赖其他仓库。服务拒绝非回环地址；报告服务只允许读取单次运行目录内的 HTML、CSV，以及 `strategy-output` 包内的 JSON 证据。外部报告保留原目录，`result.json` 的 `report` 字段指向其真实位置，确保配方、账本等相对链接可用。

A 股模板按上游约定读取本次输出的 `latest` 集合，明确展示 Q5 组合；同一次运行的时间戳副本不会被误判为其他运行。港股模板展示留出期的净值、持仓与委托，以及整份研究报告。未声明结果文件映射的外部模板仍会拒绝歧义集合。

港股研究需要已有快照。可在网页中填写路径，或运行：

```powershell
python -m quant_studio run hk-equity-daily --snapshot F:/Quant/quant-hk-equity/data/hk-snapshot --execute
```

也可设置 `QUANT_HK_SNAPSHOT`；未指定时检查上游仓库的 `data/hk-snapshot`。缺少 `manifest.json` 时明确阻止执行；完整性、日期和业务条件仍由上游校验。Studio 不自动下载或改写快照。模拟盘信号与状态配置迁移时，输入路径继续相对于上游仓库解析。

净值按实际日期排序，拒绝重复日期、无效数值和非正初始净值。多策略 CSV 必须显式选择策略，多组合 CSV 必须指定净值列。模拟盘首次步进的一条净值显示为“单次观测”，不会伪造区间收益或回撤。命令行成功/预览返回 0，运行失败返回 1，前置条件阻断返回 2。

港股留出期以冻结配置的 `initial_cash` 为期初本金，首日盈亏和费用均进入收益、最大回撤。导出的 `nav.csv` 用 `initial_nav` 列保存该口径；曲线将本金标为“期初”，不编造交易日期。有明确本金的一次收盘观测可以计算首日收益；只有快照、没有本金时仍不计算区间表现。
旧港股运行的结果页也会从已保存配置读取期初本金，并优先展示仍在原目录中的报告；读取不会改写旧净值或证据文件。

运行前会检查仓库、可执行命令及`python -m`模块。默认使用启动研究台的解释器，CLI优先从该解释器目录查找。不同应用已有独立环境时，将`QUANT_STUDIO_RUNTIMES`设置为本地JSON配置文件的绝对路径：

```json
{
  "schema_version": "quant-studio.runtimes/v1",
  "python_by_repo": {
    "a-share-multifactor": "H:/Quant/research/.venv/Scripts/python.exe",
    "quant-hk-equity": "H:/Quant/hk/.venv/Scripts/python.exe",
    "quant-paper-sim": "H:/Quant/paper/.venv/Scripts/python.exe"
  }
}
```

路径应换成已安装并验证过的环境；配置中不需要的仓库可以省略。环境页展示实际命令路径，预览保存相同命令。已指定环境的CLI只能从该Python目录取得，缺失时明确阻止运行，不借用PATH上的其他版本。模块预检也在所选Python中执行。配置不可读、格式错误、路径非绝对或指定Python缺失时均明确提示；Studio不会自动安装依赖或更新冻结环境。

启动失败或超时会保留`result.json`和日志；“可运行”仅表示执行环境就绪，数据和业务前置条件仍由上游验证。模拟盘模板每次使用独立状态，不会续接共享账户。

合成样例会在运行目录写出净值页。外部模板默认只生成配置与命令；执行成功后展示模板声明的净值和报告。上游命令失败或缺少本次结果时，保留日志并显示失败原因。Python 子进程统一使用 UTF-8 日志，避免 Windows 英文区域设置导致中文输出失败。

## 期货与Crypto离线样例

`futures-spread-fixture`和`crypto-basis-fixture`提供两类离线样例。
期货使用上游固定四段开平仓、换月及结算计划；Crypto可选择Binance/OKX离线来源、
maker/taker、随机种子和USDT初始资金。两个模板都支持最多8位小数的本金，
仅消费随上游交付的合成/脱敏fixture，不接受外部行情目录，也不访问交易所。

```powershell
python -m quant_studio check crypto-basis-fixture --set source=okx --set liquidity=taker --set initial_cash=250000.12345678
python -m quant_studio run futures-spread-fixture --set initial_cash=250000.12345678 --execute
```

在`QUANT_STUDIO_RUNTIMES`中配置两个仓各自的已安装Python。期货需要原生只读预检版本，
Crypto还需要支持`--initial-cash`及`--liquidity`的版本；CI固定的上游提交见
`.github/workflows/upstream-integration.yml`，模板也记录对应提交。不自动升级原生环境。

正式运行成功后，Studio在同一上游Python中调用冻结QLab核验`standard/v2`，
再把原生账户快照、持仓、委托、成交、保证金、费用及现金账本投影为展示CSV。
Studio基础环境仍只依赖PyYAML。展示文件位于本次`strategy-output/studio-view`，
与不可变标准产物分离；源文件内容、时间戳及清单在转换前后核对。

净值直接使用账本的整数及精度，从已核验配置读取实际初始资金和币种，
不累乘语义不同的收益列、不猜本金，也不建立第二套现金或保证金状态。
UTC事件时点和Crypto毫秒精度完整保留，横轴按事件等距展示。
结果页说明样例性质、观测次数和期初资金；表格预览前12行并提供完整CSV。
非零微小收益按需增加显示精度，缺少基准时明确标示。该流程不提供真实市场或策略有效性认证。

跨仓检查覆盖期货及Crypto四组来源/成交组合的实际CLI、相同参数预检与运行、
精确本金和逐值账本投影、来源不变、重复只读投影及篡改拒绝。

## 统计套利研究

`stat-arb-research`选择已有YAML研究配置文件，可在网页填写路径或设置
`QUANT_STAT_ARB_CONFIG`；`--snapshot`在这个模板中表示配置文件，不是数据目录。
其价格、全收益、行业和ADV相对路径始终以原配置所在目录解析。

```powershell
python -m quant_studio check stat-arb-research --snapshot H:/Quant/research/study.yaml
python -m quant_studio run stat-arb-research --snapshot H:/Quant/research/study.yaml --set gross_budget_scale=0.5 --set cost_multiplier=2 --execute
```

在运行环境配置中为`quant-stat-arb`指定已安装原生预检版本的Python。
两个系数默认1，保持原参数；Studio只保存独立覆盖文件，不复制或修改研究配方。
预检共用原生对齐和滚动窗口校验，不选对、不拟合、不模拟、不执行泄漏审计。
通过后按相同参数运行，运行重新读取并校验输入。

结果显示从1开始的单位净值、研究持仓权重、目标变动和收益比例费用。
不把原生权重列当成股数或市值，不把零基准占位列当成市场基准。
来源性质由原配置显式声明；缺省显示未声明，不猜测为真实或合成。
没有选出配对可正常完成研究；原生`blocked`或损坏输入仍显示失败并保留日志。

统计套利为私有上游，其CI检出固定公共Studio提交运行
`integration/test_stat_arb_upstream.py`；公共CI不持有私有仓凭据。

## 开发检查

```powershell
.venv\Scripts\python -m ruff check src tests
.venv\Scripts\python -m ruff format --check src tests
.venv\Scripts\python -m pytest -q
```

`Upstream integration`工作流在Windows/Linux上使用固定提交的上游依赖，执行港股、模拟盘和基金原生CLI，并检验A股输出、预检及账户检查契约。美股应用为私有仓，其自身CI检出固定提交的公共Studio运行同一集成测试，不向公共工作流增加私有仓访问凭据。基金/美股集成检查输入内容及修改时间、相同配置、原生净值口径、报告和损坏输入拒绝；测试行情为合成fixture，不代表市场表现。
