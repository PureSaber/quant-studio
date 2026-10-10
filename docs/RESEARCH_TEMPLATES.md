# 固定数据版本Notebook与研究模板

Studio可把已登记为`kind=intake`的QDK固定数据版本交给Notebook。读取过程始终使用登记时固定的`purpose`和`scope`，Notebook不能自行扩大列、标的或时间范围。

## 初始化

`NotebookStore.initialize(identifier,revision=None,template=None)`新增可选`template`参数。省略参数时仍生成原有通用Notebook；已有`research.ipynb`时只刷新输入上下文和固定读取器，不覆盖用户草稿。

稳定模板ID如下：

- `data-quality-exploration`：检查字段类型、缺失、重复和QDK问题账本，回答数据能否支撑当前问题。
- `price-return-missingness`：仅接受`daily_bars_research`且固定列含`symbol`、`date`、`close`的版本，检查收益、价格缺失、重复日期和日期间隔。
- `historical-financial-availability`：仅接受`historical_financial_factor_backtest`且已通过PIT准入、固定列含`available_at`的版本。缺少真实可用时点或准入被阻断时拒绝创建模板。

模板先写研究问题和可证伪假设，再读取固定数据，随后给出数据检查和解释边界。模板不会用占位文字要求用户自行补齐核心检查。

网页没有选择列、标的或日期筛选时，登记层会删除对应的空值并把固定`scope`保存为`{}`，含义是使用准入回执核验过的完整快照范围。冻结读取命令会省略这些可选参数。显式空数组或空字符串不会进入Notebook读取契约，避免同一范围出现两种表示。

```python
from quant_studio.notebooks import NotebookStore

draft = NotebookStore(root).initialize(
    project_id,
    revision=project_revision,
    template="price-return-missingness",
)
```

## 固定读取链

存在`intake`数据时，Studio从`IntakeWorkspace.runtime()`取得独立QDK解释器和源码仓库，将当前`research_intake.py`和无Studio依赖的`notebook_data_reader.py`复制到Notebook工作目录，并记录：

- QDK脚本与桥接读取器的SHA-256；
- QDK解释器绝对路径、Python实现和版本；
- `numpy`、`pandas`、`pyarrow`版本；
- QDK源码仓库身份；
- 每个固定版本的逐文件哈希、原始`purpose`、原始`scope`和完整准入回执。

准备执行时，上述文件与`source.ipynb`、`inputs.json`、复制后的输入一起进入运行哈希清单。读取器以`python -I -X utf8`执行冻结脚本的`read-snapshot`，强制`--limit 0`，并从登记记录逐项生成`--purpose`、`--columns`、`--symbols`、`--start`和`--end`。读取前后都会重新核对数据文件集合、逐文件哈希、脚本哈希和解释器环境身份；QDK响应还必须匹配`qdk.research-intake-response/v1`、版本ID、完整准入回执及完整行数。

Notebook只得到白名单环境变量，配置或凭据不会写入冻结上下文。没有`intake`数据时不会读取QDK配置，也不会改变原有Notebook路径。

## 研究质量提示

上下文中的`research_quality`明确分为两类：

自动检查包括固定版本完整性、读取器和环境身份、登记范围一致性，以及QDK对重复键、类型、缺失和可用时点的规则结果。这些检查说明实际读取与登记证据一致。

人工判断包括特征在决策时点是否真实可得、业务语义是否含未来信息、样本和缺失处理是否造成选择偏差、尝试过多少假设与参数、多重检验是否需要校正，以及统计差异是否有经济含义。哈希一致和代码可重复执行不能代替这些判断，也不能把格式完整的`available_at`自动解释为真实PIT证据。

## 运行边界

Notebook内核环境和QDK读取环境彼此独立：内核负责分析单元格，QDK解释器只负责固定数据读取。更换解释器、包版本、冻结脚本或输入字节后，既有提交会在执行前或读取时失败，需要从明确的新环境重新准备实验。固定读取器不下载数据、不访问网络、不修改快照，也不为缺失的PIT证据提供兜底。
