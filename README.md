# OpenClawOps

一个基于 Python `PyQt6` 的桌面运维工具，用来通过本机 `ssh` 连接远程主机，执行 OpenClaw 相关的连接检查、环境诊断、版本检查、修复升级和源码构建兜底。

## 当前能力

- 多主机配置：支持在界面中新增、复制、编辑、删除主机配置
- 连接测试：主机编辑弹窗里可直接测试 SSH 连通性
- 顺序化操作流：
  - `连接检查`
  - `环境诊断`
  - `修复并升级`
  - `验证 OpenClaw`
  - `源码构建兜底`
- 状态指示：
  - 绿灯：成功
  - 黄灯：告警
  - 红灯：失败
  - 蓝灯：运行中
  - 灰灯：空闲
- 版本检查：
  - 独立的 `最新版检查`
  - `环境诊断` 内会自动比较当前版本和 npm 最新版本
  - 如果发现版本落后，会弹窗提示并把最近一次操作结果标记为 `需升级`

## 本地运行

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python app.py
```

## 配置方式

第一次使用时，可以直接复制 [`.env.example`](/Users/moon/openclawops/.env.example)。程序内置的是通用示例主机，推荐后续直接在界面顶部通过 `新增主机` / `编辑当前主机` 来维护连接信息，而不是手改配置文件。

`.env` 中的核心字段包括：

- `DISPLAY_NAME`
- `REMOTE_HOST`
- `REMOTE_USER`
- `REMOTE_PATH_PREFIX`
- `SSH_IDENTITIES_ONLY`
- `SSH_IDENTITY_FILE`
- `SSH_CONFIG_PATH`
- `OPENCLAW_REPO_URL`
- `REMOTE_WORKDIR`
- `NPM_GLOBAL_ROOT`
- `COMMAND_TIMEOUT_SECONDS`
- `GATEWAY_PROBE_TIMEOUT_SECONDS`

额外主机会以 profile 形式写入 `.env`，例如：

```env
PROFILE_NAMES=staging
ACTIVE_PROFILE=default
PROFILE_STAGING_DISPLAY_NAME=预发布环境
PROFILE_STAGING_REMOTE_HOST=ops@staging.example.com
PROFILE_STAGING_REMOTE_USER=ops
```

## SSH 前置条件

- 本机可以直接执行 `ssh <user>@<host>`
- 远程主机已安装 `node`、`npm`、`openclaw`
- 如果要使用源码构建兜底，远程主机还需要 `git` 和 `pnpm`

工具默认会附带 `-o IdentitiesOnly=yes`，降低 SSH agent 中多把私钥导致 `Too many authentication failures` 的概率。

## 日志与配置落盘位置

源码运行时：

- 配置文件默认使用项目根目录下的 `.env`
- 日志默认写到项目根目录下的 `logs/`

打包运行时：

- macOS：`~/Library/Application Support/OpenClawOps/`
- Windows：`%APPDATA%\OpenClawOps\`

这可以避免打包后的应用向只读安装目录写日志或配置。

## 测试

```bash
pytest
```

## 图标与打包

统一图标资源生成：

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

注意：`PyInstaller` 不能在 macOS 上直接产出 Windows `.exe`，Windows 版本需要在 Windows 机器上执行打包脚本。
