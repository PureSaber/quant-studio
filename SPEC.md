# Spec: quant-studio

## Objective

本地模板回测台。代码不熟的人打开本机页面，选择已经存在的研究模板，只改允许的少数参数，预览将要写给上游仓的配置和命令。确认后才执行。成功的上游运行嵌入该仓自己的报告；失败不画净值。另有一个明确标注的合成样例，不依赖其余量化仓库，用来走通「改参数 → 运行 → 净值页」。

本仓库是应用层，不属于 M8 的 14 仓不可变清单。不实现因子、撮合、账本或通用绘图库。

## Assumptions

1. 仓库公开在 `https://github.com/PureSaber/quant-studio`，开发走功能分支和 PR。
2. 只监听 `127.0.0.1`。
3. A 股、港股、模拟盘模板只覆盖上游已经读取的字段，并记录 M8 时期的上游 tag 作为说明，不在本仓库安装那些包。
4. 合成样例的曲线来自仓库内固定收益率序列，页面标明「合成样例，不是市场收益」。
5. 执行外部模板时使用参数列表调用进程，工作目录必须落在 `QUANT_WORKSPACE_ROOT` 下的指定仓库内。

## Tech Stack

- Python 3.12
- PyYAML
- pytest、ruff
- 标准库 `http.server` 提供页面，无前端框架

## Commands

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install --no-deps -r requirements.lock
.venv\Scripts\python -m pip install --no-deps --no-build-isolation -e .
.venv\Scripts\python -m pip check
.venv\Scripts\python -m ruff check src tests
.venv\Scripts\python -m ruff format --check src tests
.venv\Scripts\python -m pytest -q
.venv\Scripts\python -m quant_studio serve --host 127.0.0.1 --port 8770
```

## Project Structure

```text
SPEC.md
src/quant_studio/            目录、渲染、执行、页面、合成样例
src/quant_studio/templates/  模板说明与上游配置基线
tests/                       pytest
.github/workflows/tests.yml  Ubuntu 与 Windows，Python 3.12
```

## Code Style

标识符使用英文。页面文案使用中文。配置深合并只允许模板声明的路径。进程参数是列表，禁止 `shell=True`。

```python
def overlay_config(base: dict, knobs: dict, fields: list[Knob]) -> dict:
    result = copy.deepcopy(base)
    for field in fields:
        if field.target != "config":
            continue
        _assign_path(result, field.path, knobs[field.name])
    return result
```

## Templates

| id | 上游 | 允许旋钮 | 命令 |
|---|---|---|---|
| `a-share-four-factor` | `a-share-multifactor` tag `v0.4.2` | `rebalance_freq`=`daily\|weekly\|monthly`；`costs.initial_capital`；CLI `--symbols-limit` 1–300 | `python -m a_share_multifactor.backtest --config {config} --symbols-limit {symbols_limit}` |
| `hk-equity-daily` | `quant-hk-equity`，配置形态 `quant-hk-study/v1` | `initial_cash` 十进制字符串；`rebalance_sessions` 只允许 1 或 5 | `quant-hk run --config {config} --snapshot {snapshot} --output {output}` |
| `paper-sim` | `quant-paper-sim` tag `v0.2.2` | `initial_capital` | `quant-paper step --config {config}` |
| `synthetic-demo` | 本仓库固定日收益 | `initial_capital` | 进程内生成 `nav.csv` 与 `report.html` |

基线配置随模板存放。渲染结果写入 `runs/<run_id>/`，不修改模板文件，不修改上游仓库。

港股模板的 `snapshot` 与 `output` 使用本次运行目录下的新子目录。上游要求这两个目录事先不存在。

## Runner

- `preview` 只写配置、请求记录和将要执行的参数列表。
- `execute` 默认关闭。外部模板在仓库目录不存在时状态为 `blocked`，不启动子进程。
- 子进程非零退出时状态为 `failed`，不标记报告可嵌入。
- 子进程退出码为 0 且模板声明的 `report.html` 出现在本次运行目录内时，页面才嵌入该文件。
- 合成样例成功时嵌入本仓库生成的报告，标题包含「合成样例，不是市场收益」。
- 拒绝模板未声明的参数名、越界数值、非回环地址，以及逃出运行目录的路径。

## Testing Strategy

pytest 放在 `tests/`。覆盖参数拒绝、配置深合并、预览不启动子进程、缺仓库时 blocked、失败退出码不嵌入报告、合成样例净值可复现、页面含三张研究卡片和一张合成卡片、非回环 host 被拒绝。外部命令用测试里的假可执行文件或 `subprocess` 替身，不调用真实策略仓。

## Boundaries

- Always: 测试通过后再提交；参数列表调用；只监听回环地址；合成结果与上游研究文案分开。
- Ask first: 增加 PyPI 依赖、改动 14 个已认证仓库、把服务绑定到非回环地址。
- Never: 提交密钥或运行产物；用装饰性硬编码曲线冒充回测；`shell=True`；覆盖已有运行目录。

## Success Criteria

1. `pytest -q`、`ruff check`、`ruff format --check` 通过。
2. 预览 A 股模板时，配置里的调仓频率和 `costs.initial_capital` 等于请求值，因子列表仍来自基线。
3. 未知参数、港股 `rebalance_sessions=3`、`--host 0.0.0.0` 均失败。
4. 合成样例在无其他量化仓库时生成净值页，且页面含合成声明。
5. CI 在 Ubuntu 与 Windows 的 Python 3.12 上执行与本机相同的安装和测试命令。
