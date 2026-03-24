# OpenClaw 桌面运维工具

这是一个运行在本机 MacBook Pro 上的 Python `PyQt6` 桌面应用。它通过系统 `ssh` 连接远程主机 `moon@smarthost.local`，用于诊断、修复、升级、验证以及在必要时执行 OpenClaw 的源码构建兜底。

## 为什么做成桌面应用

这个工具主要面向单人日常运维，不需要多人协作和 Web 部署链路。桌面应用的好处是：

- 直接复用本机 `ssh`、密钥和系统能力
- 不额外暴露管理端口
- 更适合实时查看日志、点击执行高风险动作
- 本地启动简单，调试和打包成本低

## 项目结构

```text
.
├── README.md
├── .env.example
├── app.py
├── config.py
├── ssh_runner.py
├── actions.py
├── ui.py
├── logging_utils.py
├── models.py
├── requirements.txt
├── tests
│   ├── test_actions.py
│   ├── test_logging_utils.py
│   ├── test_parsers.py
│   └── test_ssh_runner.py
└── logs
```

## 本地运行

1. 创建虚拟环境并安装依赖：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2. 复制配置文件：

```bash
cp .env.example .env
```

3. 启动应用：

```bash
python app.py
```

## 配置方式

程序内置了一个默认主机案例，也就是你的 `smarthost`。第一次使用时，可以直接复制 `.env.example`，然后在界面顶部通过 `新增主机` / `编辑当前主机` 来管理主机，不需要手改配置文件。

`.env` 仍然是最终落盘位置，关键项如下：

- `REMOTE_HOST=moon@smarthost.local`
- `REMOTE_PATH_PREFIX=export PATH=/opt/homebrew/bin:/usr/local/bin:$PATH;`
- `REMOTE_USER=moon`
- `SSH_IDENTITIES_ONLY=true`
- `SSH_IDENTITY_FILE=~/.ssh/id_ed25519`
- `SSH_CONFIG_PATH=~/.ssh/config`
- `OPENCLAW_REPO_URL=...`
- `REMOTE_WORKDIR=$HOME/openclaw-src`
- `NPM_GLOBAL_ROOT=/opt/homebrew/lib/node_modules`
- `COMMAND_TIMEOUT_SECONDS=300`
- `GATEWAY_PROBE_TIMEOUT_SECONDS=8`

所有远程命令都会统一通过 `ssh_runner.py` 执行，并在命令前显式注入 `REMOTE_PATH_PREFIX`，不会依赖远程 shell profile。默认会附带 `-o IdentitiesOnly=yes` 以减少 SSH agent 中多把私钥导致的 `Too many authentication failures`。

如果你想了解界面保存后的格式，额外主机会以 profile 形式写入 `.env`：

- `PROFILE_NAMES=smarthost,staging`
- `ACTIVE_PROFILE=smarthost`
- `PROFILE_SMARTHOST_DISPLAY_NAME=家庭主机`
- `PROFILE_SMARTHOST_REMOTE_HOST=moon@smarthost.local`
- `PROFILE_STAGING_DISPLAY_NAME=预发布环境`
- `PROFILE_STAGING_REMOTE_HOST=ops@staging.example.com`

界面顶部可以直接切换主机，切换后后续动作都会对当前选中的主机执行。

## SSH 前置条件

- 本机可以执行 `ssh moon@smarthost.local`
- 远程主机已安装 `node`、`npm`、`openclaw`
- 如果要用源码构建兜底，远程主机还需要 `git` 和 `pnpm`

## 如何配置免密登录

建议先在本机生成 SSH key：

```bash
ssh-keygen -t ed25519
ssh-copy-id moon@smarthost.local
```

如果系统没有 `ssh-copy-id`，可以把 `~/.ssh/id_ed25519.pub` 内容追加到远程 `~/.ssh/authorized_keys`。

## 按钮说明

- `连接检查`：连接远程主机并执行 `hostname`
- `环境诊断`：检查 PATH、Node、npm、OpenClaw、`~/.npm` 权限和全局安装残留
- `修复 npm 环境`：修复 `~/.npm` 属主、清理缓存并执行 `npm cache verify`
- `清理 OpenClaw 残留`：删除全局安装目录和 `.openclaw-*` 临时残留
- `升级 OpenClaw`：执行 `npm install -g openclaw@latest`，结束后自动验证
- `验证 OpenClaw`：检查版本并做 `openclaw gateway` 探活，识别 `Control UI assets not found`
- `源码构建兜底`：在配置的工作目录拉取或更新源码，执行 `pnpm install`、`pnpm ui:build`、`pnpm build`
- `一键修复并升级`：串行执行 npm 修复、残留清理和升级
- `打开日志目录`：在 Finder 中打开 `logs/`

## 常见故障排查

- `Permission denied` 或看到密码相关报错：说明 SSH 认证失败，或者当前环境需要交互式密码输入。建议先配置免密登录。
- 如果看到 `Too many authentication failures`：优先在 `.env` 中配置 `SSH_IDENTITY_FILE`，或者配置 `SSH_CONFIG_PATH` 指向包含 `IdentityFile` 和 `IdentitiesOnly yes` 的 SSH 配置文件。
- `node` / `npm` / `openclaw` not found：优先检查 `.env` 里的 `REMOTE_PATH_PREFIX`。
- `npm cache verify` 报 `EACCES`：先运行 `修复 npm 环境`。
- 升级后验证提示 `Control UI assets not found`：运行 `源码构建兜底`。
- `gateway probe timed out`：说明 `openclaw gateway` 没能在探活窗口内产出可用结果，需要检查远程服务依赖或进程状态。

## 日志位置

所有动作都会在 `logs/` 下生成单独日志文件，文件名格式为：

```text
YYYYMMDD_HHMMSS_<action>.log
```

日志包含：

- 动作名
- 开始时间和结束时间
- 远程命令
- 退出码
- stdout
- stderr
- 结构化摘要

## 安全注意事项

- 本工具不会硬编码密码
- 危险操作会在 UI 中标注并二次确认
- 日志可能包含远程路径和命令输出，建议仅保存在受控机器上
- `修复 npm 环境` 和 `清理 OpenClaw 残留` 会修改远程目录，请确认目标主机无误

## 测试

```bash
pytest
```

## 图标与打包

项目内置统一图标资源，生成脚本是：

```bash
python tools/generate_icon_assets.py
```

会生成：

- `assets/openclaw.png`
- `assets/openclaw.ico`
- `assets/openclaw.icns`

macOS 打包：

```bash
./build_macos.sh
```

Windows 打包：

```bat
build_windows.bat
```

两个平台共用同一套图标设计，只是分别使用 `.icns` 和 `.ico`。
