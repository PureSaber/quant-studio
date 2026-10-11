# 外汇、利率与信用研究入口

新增五个模板：外汇现货与远期、国债与利率风险、公司债与债券 ETF、
外汇期货与换月、国债及短端利率期货回放。

前三项使用 `quant-futures-spread/macro` 中的独立 `quant-macro` 应用；
后两项复用既有 `quant-futures-global` 运行环境。设置页通过模板自动发现新的环境键，
不需要覆盖原认证期货环境。

1. 在对应源码目录安装应用，创建独立 Python 3.12 环境。
2. Studio 设置中将 `quant-macro` 指向该解释器；源码工作区仍按兄弟仓布局设置。
3. 使用 `python -m quant_macro.cli demo --output <新目录>` 生成合成演示包，
   或按上游 `macro/DATA_CONTRACT.md` 准备带来源、哈希和发布时间的历史数据。
4. 选择模板、数据包和参数，先预检，再执行。时间点、结算日期必须匹配真实输入。
5. 报告展示数据性质、模型限制和价格质量，结果表保留净价／全价、久期、现金流和 ETF 成分依据。

外汇只处理一个货币对的两个现金币种；固定收益提供估值和风险研究。
新入口不声明已有真实外汇／国债／信用市场数据认证、交易所适配或实盘接入。
合成数据不代表真实行情，国内金融期货原交易开关不因此开启。

期货模板复用既有 `qdk.derivatives/v1` 合约包。必须明确报价方向、每手乘数、
结算币种、保证金、结算价和交割日期；默认 `DEMO` 仅为合成通用期货样例。

新增的 `valuations.csv,cashflows.csv,rate_futures.csv,etfs.csv,components.csv`
已加入明确的结果收集白名单，页面使用中文表名。原有结果文件校验保持生效。

安装完两侧应用后可在新目录执行四条端到端流程：

```powershell
python scripts/integration_macro.py --workspace <兄弟仓根目录> --python <quant-macro解释器绝对路径> --output <新验收目录>
```

该脚本调用真实 Studio 预检、执行与结果收集，然后调用上游 verify 复算，
结果写入 receipts.json。它不会修改已部署服务配置。
