# 外汇、利率与信用研究入口

| 模板 | 功能 | 运行环境键 |
|---|---|---|
| macro-fx-research | 单对现货／远期、利差、展期 | quant-macro |
| macro-rates-research | 国债现金流、零息曲线、久期、期货风险 | quant-macro |
| macro-credit-research | 公司债估值、利差、ETF 篮子 | quant-macro |
| macro-fx-portfolio | 多对、多币种共享账本、T+n 结算 | quant-macro |
| macro-bond-replay | 单券交易、票息、兑付及再投资 | quant-macro |
| macro-etf-relative-value | ETF 与成分债券二级市场联动、借券和分红 | quant-macro |
| macro-reference-history | ECB、Fed GSW、发行人持仓参考数据 | quant-macro |
| fx-futures-research | 6E 单位规则检查、换月回放 | quant-macro-futures |
| rates-futures-research | ZN、SR3、TS、TF、T、TL 规则检查和回放 | quant-macro-futures |

核心应用位于 quant-futures-spread/macro。期货运行环境还需
international/requirements.lock 与 quant-futures-spread/international；
期货账户事实由已有 QExec 研究回放引擎生成。所有模板冻结上游精确提交。
这是研究环境，不修改根目录 fixture-certified 依赖及原实盘交易开关。

## 数据和设置

设置页自动发现上述环境键和各模板的独立数据入口。先在隔离环境安装应用，
再把环境键指向解释器；QUANT_WORKSPACE_ROOT 指向兄弟仓根目录。
quant_macro.cli demo --output <新目录> 提供所有宏观合成数据。
期货请使用 quant_macro.futures_demo.create(path, product) 生成对应合成单位样例。
通用 DEMO 期货包无法通过产品规则检查。

真实账户输入按上游 macro/DATA_CONTRACT.md 组织，用 seal 校验与封包。
必须具备报价、可知时间、结算日历、历史条款／保证金；做空还需借券容量和成本。
公开参考历史可以 download-public 或 import-public；参考数据禁止进入交易账户。
ECB/Fed 下载历史只有当前版本，不能解释为原始历史 PIT。
iShares 若网站拒绝下载，提供合法取得的 CSV 后即可使用已实现的导入入口。

账户平仓日期须在窗口内，并为最终 T+n 和分红付款留下足够数据；
报告保留模型边界和所有结算尾项校验。ETF 联动为二级市场组合，无一级市场 AP 申赎。

## 端到端验收

```powershell
python scripts/integration_macro.py --workspace <兄弟仓根目录> --python <解释器绝对路径> --output <新目录> --public-root <含ecb-bundle和fed-bundle的目录> --futures
```

16 条流程执行真实 Studio 预检、调度、表格／报告收集，再调用上游校验器。
不提供 public-root 时跳过 2 条真实参考数据流程；不提供 futures 时跳过 7 条期货流程。
回执写入 receipts.json。宏观校验包含确定性重新计算，QExec 校验包含哈希和分录平衡。
脚本不会重启或改写已部署服务；上线应在组合发布中统一处理。
