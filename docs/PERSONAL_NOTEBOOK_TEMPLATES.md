# 保存研究模板与比较Notebook实验

这条流程适合数据探索、统计检验和参数研究，不要求先编写交易策略。

## 日常使用

1. 在研究项目中打开JupyterLab，保存写好的Notebook。
2. 在项目页展开“把当前Notebook保存为研究模板”，填写名称并保存。
3. 打开“研究模板库”，选择模板、已登记数据和参数，创建新项目。
4. 直接“冻结已保存草稿并提交实验”，或先进入JupyterLab继续编辑。
5. 在“Notebook实验比较”中选择2至4个实验，查看条件和指标，下载CSV。
6. 回到项目的研究笔记记录结论，再复用模板开展下一次研究。

每次保存生成独立的不可变模板快照；同名模板不会覆盖旧版本。复用创建新项目和新草稿，
不会覆盖源项目。模板只是代码和参数定义，用户以前在Notebook中手工填写的常量不会自动变为参数。
模板库与Notebook比较页可从“研究项目”进入，支持跨项目比较。

## 模板作者：让别人只需填表

参数定义只需填写一次。例如：

```json
{
  "window": {"label": "观察窗口", "type": "integer", "default": 20, "min": 1, "max": 120},
  "start_date": {"label": "起始日期", "type": "date", "default": "2026-01-01"},
  "method": {"label": "统计方式", "type": "choice", "default": "mean", "choices": ["mean", "median"]}
}
```

支持integer、number、string、date、boolean、choice，最多30项。number支持有限小数；
日期须为YYYY-MM-DD；boolean在页面显示“是/否”；choice显示下拉框。
未传的参数采用声明的默认值；未知参数、越界数值、不合法日期与选项会拒绝。

代码使用`parameters['window']`读取表单值，使用`context['datasets']`读取本项目的数据。
复用时，首个单元格从`inputs.json`加载参数；后台实验也会在全新内核中加载同一份冻结输入。
参数值按JSON处理，不拼接进Python源码。日期、方法等参数须在研究代码中实际使用；
页面不会代替代码执行筛选。JupyterLab中可查看`inputs.json`，
再次使用不同参数时从模板库创建新项目，保留上一组参数与实验。

模板保存会清除代码单元格的历史输出与执行计数。新内置模板按用途和字段从当前数据上下文
选择匹配数据；指标描述首个匹配数据集。旧草稿若把原数据集编号写在代码中，
保存模板时会提示先改用上下文读取，不自动替换代码字符串。
自定义Notebook使用的绝对路径、额外模块、网络请求和副作用仍由模板作者负责。
保存模板不代表任意Notebook已自动完成数据绑定或业务审核。

## 声明可比较指标

在Notebook中定义`research_metrics`字典。例如：

```python
research_metrics = {
    "样本数": {
        "value": int(len(data)), "unit": "行",
        "definition": "完成日期筛选后实际参与统计的记录数"
    },
    "相关系数": {
        "value": None, "unit": "无量纲",
        "definition": "成对非空样本的Pearson相关系数",
        "reason": "有效样本不足"
    }
}
```

每项需要数值`value`、单位`unit`和计算口径`definition`。最多100项，指标文件上限200KB。
NumPy/pandas标量请显式转为Python的int或float；无效或缺失统计用None并给出reason。
NaN、Infinity、布尔值和数字字符串不视为有效指标；真实的0保留为0。
如用于回测，应在口径中明确收益期间、年化约定、成本、基准和复权方式。

执行器在所有研究单元格成功运行后导出`metrics.json`，Studio验证格式并记录文件哈希。
格式不合格时实验标记为失败，保留执行日志和Notebook输出；失败或取消实验的指标不采用。
没有声明指标的普通Notebook仍可运行，比较时显示未提供。旧实验不会被自动补算。

## 怎样解读比较表

比较页显示参数、模板版本和登记数据范围；代码、数据身份和环境的完整证据折叠展示。
数据、代码、环境或参数不同会提示；同名但单位或定义不同的指标分行显示。
未成功、未提供此口径以及有原因的缺失分别展示，不补0、不自动排名或判断显著性。

登记范围不是Notebook内部二次筛选结果；实际筛选需要通过参数与指标定义说明。
CSV包含完整条件、指标、口径和比较提示，文本公式前缀会被保护，负数指标仍为数值。
相关文件受原有下载白名单和哈希校验保护；保存、复用沿用Studio的CSRF与Origin校验。

## 验证

基础测试：`python -m pytest tests/test_notebook_templates.py tests/test_notebook_metrics.py tests/test_notebook_template_workflow.py -q`。
设置`QUANT_TEST_NOTEBOOK_PYTHON`为独立Notebook解释器，可运行两个不同窗口的真实内核实验；
合成数列[2,4,8,16,32]的前2项和前4项均值分别为3和7.5，用于验证链路，不代表市场结果。

浏览器验收：使用已有Playwright，设置`QUANT_TEST_STUDIO_PYTHON`、
`QUANT_TEST_NOTEBOOK_PYTHON`和绝对目录`QUANT_BROWSER_ARTIFACTS`，执行
`node integration/notebook_templates_browser.cjs`。
可用`QUANT_BROWSER_EXECUTABLE`指定已有浏览器可执行文件。
脚本在独立目录登记合成CSV，从页面保存模板、填写参数、运行两次真实实验、比较并下载，
验证桌面与390px手机页面并保存截图；结束时关闭临时本机服务。

本功能不新增运行依赖；Notebook环境与Studio环境仍按研究工作区指南分别维护。
