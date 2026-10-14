# 四 ETF 工作流

部署环境由 quant-workspace 的 `profiles/etf-daily` 固定完整版本组合。
设置 `QUANT_STUDIO_ETF_DAILY` 为管理员维护的每日配置绝对路径，并在运行环境中
配置 `quant-pipeline` 的独立 Python。`/today` 是日常入口。

1. 更新到最新已收盘交易日，查看原生预检及阻断证据。
2. 点击“将此快照与冻结配方连接到研究项目”。重复点击复用已有项目。
3. 填写研究卡，登记机制、失效条件、样本用途、协议与判断标准。
4. 在项目提交预检和原生实验；提交绑定不可变项目版本及配方版本。
5. 选中一个或多个 ETF 实验进行原生候选族比较。基准、失败候选和压力测试均保留。
   区间、资金、成本、输入或协议不同时明确显示不可比。
6. 记录支持、不支持或证据不足，选择后续行动并引用实验。
7. 独立模拟候选需明确人工提名。提名要求整个研究族完成且候选存在，但不将其认定为有效策略。
   原生账户登记和启用仍遵循其正式协议；旧暂停账户不被替换。

已有项目保持可读。缺少研究卡时标为“未登记”；修改研究卡产生新项目版本。
过去区间不能改名为新登记未来区间。旧实验按历史配方关联，新实验显式关联项目，
共享配方不会跨项目混入新结果。决策保存原结果与请求的哈希，后续文件变化会阻断读取。

今日总览复用原生 QDK、前向回执和标准账本。全部持仓和费用均为模拟；无真实成交
和独立报价时不展示真实滑点。成本假设、原生费用和未成交原因分别展示。
网页不接管因子持久任务的恢复机制；ETF 中断由原生前向恢复契约处理。

本次软件交付不代表自然交易日运维验收已经完成。主表缺口、未批准候选及未来五个
交易日观察须作为真实未完成项显示，不能用历史回放或软件测试替代。

## Evening data availability

The Today page reads the native optional schedule, next acquisition attempt,
market capture time and complete-preflight timestamp. It distinguishes data
waiting/exhaustion from simulation execution failure. The deployment uses
16:30, 17:00, 18:00, 21:00 and 22:00 Shanghai slots, with a ten-minute collection
budget. Historical daily prices are not a real-time feed. New paper accounts
require previously completed order evidence before simulated opening fills;
the web UI never fabricates catch-up fills or implements a separate ledger.
