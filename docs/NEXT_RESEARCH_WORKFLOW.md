# 研究工作台五项推进指南

本轮将同项目研究迭代、数据维护、研究接口、历史助手和环境交付接入Studio。
复用独立安装的quant-data-kit、quant-agent和quant-workspace，不增加Studio核心运行依赖。

## 1. 同项目调参与后台批次

1. 创建研究项目并连接已登记数据，在JupyterLab保存草稿；代码通过`parameters['window']`读取参数。
2. 将Notebook保存为个人模板，给`window`声明整数类型、默认值和上下限。参数定义方法见[个人模板指南](PERSONAL_NOTEBOOK_TEMPLATES.md)。
3. 在项目的“Notebook调参与批量实验”选择参数定义，填`[5,10,20,60]`。其他参数填单值数组即可保持固定。
4. 点击“冻结输入并预览候选”，检查候选列表，再提交排队。代码来自当前已保存草稿，模板只提供参数定义。
5. Studio后台FIFO运行任务；关闭浏览器不会停止服务。选择2至4次实验比较指标，将判断保存在项目笔记中。

单批最多32项，页面默认4项。候选共享同一份源码、原始数据和环境身份；每项保留独立物理副本及参数哈希。
单次输入复制上限由Notebook配置的`max_input_bytes`控制，批次磁盘量约为单次输入量乘候选数，重试另占一份。
重复提交不会重复执行；部分提交可补全排队。失败、中断或取消项须显式重试，使用原冻结输入，创建新实验并保留原记录。
服务重启仍沿用任务中心的中断语义，不会自动重放正在执行的研究。修改草稿不会改变已冻结候选。

## 2. 数据更新与质量中心

侧栏“数据更新与质量中心”以用户指定的研究截止日期对照数据登记日期，展示更新任务及失败日志。
CSV/TSV覆盖检查需指定日期列、标的列和预期标的，保存全文件实际起止日期、缺失标的、空值及重复标的日期。
检查支持64MB、100万行以内文件；文件变更后报告显示失效。Parquet继续使用数据目录预览和原生准入报告。
覆盖检查不推断交易日历、停牌原因或PIT历史可得性，登记截止也不代表每个标的完整覆盖。

ETF来源首次创建独立维护目录，提交更新调用QDK原生`research_dataset build/update`。
后续采用其重叠窗口更新，保存原生质量回执，并登记新的不可变快照；旧研究仍绑定旧版本。
运行环境复用`QUANT_STUDIO_INTAKE_CONFIG`，其中`python`为安装了QDK的独立解释器，`source`为对应源码仓库。
该环境须具备所选行情供应商依赖。任务队列显示采集失败，不用空数据冒充完成。
港股来源复用原有增量入口，其他本地数据通过“导入与质量”接入，不暗示任意行情源都已有自动更新器。

## 3. 统一研究接口

新草稿和批量执行可直接使用`research`。旧草稿在刷新上下文后可加入以下初始化代码：

```python
from research import Research
research = Research()
parameters = research.parameters
research_metrics = research.metrics

research.datasets()  # 查已连接文件名和登记范围
frame = research.load(0, file='bars.csv', numeric=['close'])
prices = research.align(frame, value='close', join='outer')
returns = research.returns(prices)
summary = research.statistics(returns.iloc[:, 0])
research.plot(prices, title='收盘价', ylabel='来源声明的价格单位')
research.metric('有效样本数', summary['count'], unit='条', definition='第一标的非空日收益数')
```

单一表格可省略`file`；多文件必须明确选择。准入数据不指定底层文件，`research.load(0)`复用冻结的QDK读取器、用途和范围。
普通登记文件读取前后核对哈希，代码列默认保持文本，缺失不自动补值，重复标的日期要求研究者先明确聚合方式。
`metric`要求单位和口径，缺失必须传`None`及`reason`；`NaN`和无穷值被拒绝。
`research.factors(frame, ['momentum_20d'])`直接调用quant-factors的现有接口，因子名与输入要求以该环境支持的注册表为准。
需要Pandas、Matplotlib或quant-factors时应在独立Notebook环境中按受审锁文件配置；本接口不自动安装依赖。
`research.py`随实验一起复制并记录哈希，后续接口升级不会改写历史实验。

## 4. 历史检索与可审阅代码建议

侧栏“研究历史检索”支持项目版本、笔记、Notebook参数、指标和失败日志尾部的中英文关键词查询。
来源链接回到具体项目版本或实验。项目改名后，旧实验仍使用执行时的名称及研究问题。
Notebook差异比较沿用固定参数、代码、输入身份和指标口径；检索结果不自动判断策略优劣。

项目助手预览会加入最多5条相关历史证据。若需要代码建议，勾选包含Notebook源码，再审阅预览内容。
配置quant-agent解释器后可离线整理；在线建议仍需服务端显式模型配置、授权开关与用户点击发送。
源码上限60KB，每个补丁绑定原单元格序号、SHA-256及证据编号。结果展示diff，不自动写入草稿或运行代码。
代码注释与历史文本被当作待分析数据，模型没有工具执行权限；引用核验不代表建议已通过研究验证。
当前检索是关键词匹配，不是向量语义检索；日志仅检索末尾6000字符。

## 5. 环境与交付

侧栏展示当前Studio进程的解释器、安装版本和源码位置。设置服务端`QUANT_STUDIO_DELIVERY_CONFIG`后连接Workspace：

```json
{
  "python": "C:/quant/quant-workspace/.venv/Scripts/python.exe",
  "workspace_config": "C:/quant/workspace.yaml",
  "source_root": "C:/quant/repos",
  "profile": "C:/quant/releases/runtime-profile.json",
  "state_dir": "C:/quant/releases/state",
  "candidates": {
    "next": {
      "candidate": "C:/quant/releases/next.json",
      "prepared_root": "C:/quant/candidates/next",
      "evidence": "C:/quant/releases/next-acceptance.json"
    }
  },
  "transfers": {
    "new-host": {
      "archive": "C:/quant/transfers/research.zip",
      "destination": "C:/quant/restored-research"
    }
  }
}
```

以上为示例路径，须全部替换为实际绝对路径。配置文件由主机管理员管理，网页仅选择命名目标。
先用Workspace原生`runtime-profile`、`release-candidate`、`transfer-package`生成需要的声明及迁移包。
页面支持环境缺口检查、候选安装预览、安装到新目录、原生验收、登记激活/回滚、迁移包校验及恢复到新目录。
每次先预览再执行，配置和声明哈希必须一致；激活使用预览时的当前指针做并发检查。
原生JSON回执及任务日志保留，失败不会显示为通过。迁移回执会保留`needs_configuration`等后续要求。
Workspace需包含[候选环境路径修复#56](https://github.com/PureSaber/quant-workspace/pull/56)：虚拟环境在最终新目录创建，不在创建后搬移；安装失败保留新目录和日志，使用另一新目录重建。
登记发布指针不会自动重启服务，不代表当前进程已经切换；服务切换仍按现有部署流程完成。

## 验证与证据边界

- `tests/test_notebook_batches.py`与`test_workbench_next.py`覆盖固定参数批次、幂等、重试、历史版本及网页鉴权；独立Notebook环境真实运行四组窗口、绘图、比较指标并保存结论。
- `test_data_maintenance.py`覆盖缺标的、重复数据与检查失效；`test_next_native_integration.py`通过独立QDK进程验证合成ETF构建、重叠更新及旧快照保留。
- 独立Agent进程验证离线源码预览，Agent仓库用可控模型响应验证补丁引用和源码身份。
- 独立Workspace进程验证原生环境缺口、候选安装预览、验收、激活并发冲突、回滚以及迁移包校验和恢复；所有写入限定在临时测试目录。环境样本离线构造，不从网络安装依赖。
- `integration/next_workbench_browser.cjs`在隔离状态目录中用Edge/Playwright检查4个新页面的1440px和390px布局、批次表单及浏览器错误，输出截图和JSON回执。

跨仓CI固定上游提交，使用独立Notebook、Agent、QDK、Workspace环境。
本轮合成数据与可控模型响应不代表真实在线ETF行情或供应商模型已验收；也不改变此前已完成的异地设备、Android和Windows重启验收结论。
