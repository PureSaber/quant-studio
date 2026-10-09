# 本机作为研究服务端

这是单人研究工作台。研究代码、数据与任务在服务端运行，浏览器设备只编辑配置和查看结果。不接入真实订单。当前部署支持指定私网 IPv4 上的 HTTPS；异地设备需要先通过你的受控网络连接到这台电脑。没有配置公网端口映射或公共隧道。

## 安装与启动

使用 Python 3.12，在新目录安装锁定环境：

```powershell
./tools/Install-Locked.ps1 -Python C:/path/to/python.exe -Environment D:/workbench/env
D:/workbench/env/Scripts/python.exe tools/provision_access.py --output D:/workbench/private --host 192.168.3.73 --openssl 'C:/Program Files/Git/usr/bin/openssl.exe'
./tools/Start-Workbench.ps1 -Python D:/workbench/env/Scripts/python.exe -State D:/workbench -BindAddress 192.168.3.73
```

将地址替换为这台电脑实际的局域网地址。`studio-settings.json` 可通过首次配置页面生成，包含各研究仓库与其独立 Python 环境路径。各环境使用各自锁文件，不能把不同版本的研究栈强行安装到同一个环境。

`private/` 在 Windows 上限制为当前用户和 SYSTEM 可访问。访问密码在 `login-password.txt`，服务只加载 `access.json` 中的加盐密码哈希。不要把私钥或密码加入仓库或共享目录。服务器证书有效期 90 天；请在到期前在新目录生成凭据并更新启动路径。地址改变时也要重发包含新地址的证书。

服务要求证书、私钥和访问配置同时存在；缺少任意一项时不能对非回环地址启动。允许的 Host 固定在访问配置中，POST 必须具有匹配的 HTTPS Origin 与会话 CSRF 令牌。Cookie 为 Secure / HttpOnly / SameSite=Strict，会话最长 8 小时，退出或服务重启会撤销会话。登录失败限流为每来源 5 次 / 5 分钟，同时设置全局限制。

## 第二台设备连接

1. 先将第二台设备连到同一受控网络。
2. 只把 `private/ca.crt` 复制到自己的客户端，并通过操作系统的证书管理界面确认信任；不要复制 `ca.key`、`server.key`。Windows 可导入“当前用户 / 受信任的根证书颁发机构”。不要通过忽略浏览器证书警告连接。
3. Windows 防火墙若阻止连接，由管理员对所选 Python、端口和 `LocalSubnet` 开放入站。下面命令限制本机 IP、程序和本地子网；必须替换示例路径及地址：

```powershell
New-NetFirewallRule -DisplayName 'Quant Studio controlled LAN' -Direction Inbound -Action Allow -Protocol TCP -LocalAddress 192.168.3.73 -LocalPort 8782 -RemoteAddress LocalSubnet -Program D:/workbench/env/Scripts/python.exe -Profile Any
```

4. 打开 `https://192.168.3.73:8782/research`，输入服务端保存的访问密码。
5. 新建或复制合成研究，提交任务，关闭浏览器，再从另一设备登录确认任务与结果仍存在。

本机经局域网地址连接自身不等同于第二台设备验收；防火墙、无线 AP 隔离和客户端信任必须通过真实客户端确认。电脑关机、睡眠或脱离网络时不可访问。运行需要用户会话中的服务进程；当前没有自动安装系统服务或更改电源策略。

## 研究方案与自己的代码

“研究方案”支持保存、复制、JSON 方案导入导出及原生配置导入。数字、日期和开关使用表单，标的和规则名单使用 JSON 数组。每次保存产生不可变版本，跨设备旧页面保存会被冲突检查拒绝；任务与运行记录保留提交版本。运行按钮明确使用“已保存版本”，请先保存参数修改。

统计套利、择时和择时反事实可加载原生 YAML，保存其中的完整研究配置，按原生解析规则将输入路径转为绝对路径。任务创建自己的 `source-config.yaml`，不会因之后修改原 YAML 而改变。行情文件本身仍由原生预检和运行重新读取校验，并未被复制进方案。

可以把 `examples/templates/custom-example` 复制到自己的模块目录，使用 `custom-` 前缀。调整 `template.json` 指向已经安装到指定 Python 环境的 `python -m package.module`，并提供配置、只读预检和独立输出目录契约。启动时通过 `--templates` 或 PowerShell 的 `-Templates` 注册目录。网页不能上传或登记可执行命令。`examples` 中的样例是固定合成收益，只用来验证模块接入。

新增模块应输出 `report.html` 或可识别净值 CSV。预检必须输出声明的 JSON 契约，且不运行策略。不要把预检通过等同于策略盈利或真实成交认证。

## 停止、备份与恢复

运行 `tools/Stop-Workbench.ps1 -State D:/workbench` 后等待服务进程退出。停止会取消当前实例持有的运行任务，并将未开始任务标为中断；重启不会自动重放任务。确认已退出后删除 `private/stop.request` 再启动。

停止后备份 `runs/`（包括 `.recipes` 与 `.jobs`）、输入数据、配置文件、源代码提交及环境锁。凭据单独保存在受控位置；公开工程证据归档必须排除 `private/`。恢复到新目录后先核对归档文件哈希；路径改变时更新首次配置，并为需要迁移输入路径的研究方案保存新版本。不要改写旧运行记录或旧方案版本。

原生 A 股标准输出要求干净且可追溯的 Git 源码。开发时先完成检查、提交，再进行标准输出验收；预检主要检查数据、因子、基准和成交规则，运行时仍会核对环境和源码要求。
