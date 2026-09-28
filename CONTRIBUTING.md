# 贡献指南

请从功能分支提交改动，并保持变更聚焦。行为变更应先增加失败测试，再实现最少代码使测试通过。

提交拉取请求前，请使用 Python 3.12 按 README 安装依赖，并运行：

```powershell
.venv\Scripts\python -m ruff check src tests
.venv\Scripts\python -m ruff format --check src tests
.venv\Scripts\python -m pytest -q
```

不要提交 `runs/`、虚拟环境、密钥或外部量化仓库内容。新增运行依赖、开放非回环监听或修改上游仓库均需先讨论。
