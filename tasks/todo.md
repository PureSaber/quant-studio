# Tasks

- [x] 模板加载、旋钮校验、配置深合并
  - Acceptance: 未知参数和越界值抛出明确错误；A 股合并后只改声明字段
  - Verify: `pytest -q tests/test_render.py`
- [x] 预览与执行
  - Acceptance: 预览无子进程；缺仓库为 blocked；非零退出不记录报告；路径不能逃出运行目录
  - Verify: `pytest -q tests/test_runner.py`
- [x] 合成样例
  - Acceptance: 同一本金两次运行净值一致；报告含「合成样例，不是市场收益」
  - Verify: `pytest -q tests/test_demo.py`
- [x] 本机页面与 CLI
  - Acceptance: 首页四张卡片；非回环 host 拒绝；预览页展示命令且无净值
  - Verify: `pytest -q tests/test_server.py`
- [x] 工程门禁
  - Acceptance: requirements.lock、ruff、Ubuntu/Windows CI 与 SPEC 命令一致
  - Verify: `ruff check`、`ruff format --check`、`pytest -q`
