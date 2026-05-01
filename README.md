# Codex Auth Switcher

一个 Windows 本地 Codex 账号切换工具，用来管理多个 Codex `auth.json`，并在不同账号之间快速切换。

推荐 GitHub 仓库名：

```text
codex-auth-switcher
```

推荐 GitHub 简介：

```text
Windows 本地 Codex auth.json 账号切换器，支持账号保存、切换、更新、JSON 编辑和剩余额度预览。
```

## 功能

- 保存多个 Codex 登录账号文件。
- 一键切换当前 Codex 使用的 `auth.json`。
- 自动备份被覆盖的当前登录文件。
- 从当前 Codex 登录状态快速添加新账号。
- 用当前登录状态更新已保存账号。
- 查看 5 小时窗口和 7 天窗口的剩余额度。
- 左侧账号列表显示所有账号的剩余额度预览。
- 支持查看和编辑完整 JSON。
- 支持命令行列出账号和切换账号。
- 账号文件、缓存、备份和虚拟环境默认不会进入 Git。

## 界面说明

左侧是账号总览：

- `+ 新建空白`：创建一个空白账号草稿，并打开详情窗口填写 JSON。
- `导入 JSON 文件`：把已有 JSON 文件导入为新账号草稿。
- `添加当前登录`：把当前 Codex 登录文件保存成一个账号。
- `刷新全部用量`：依次刷新所有账号的剩余额度。
- 账号卡片：显示账号名、5 小时剩余、7 天剩余。

右侧是当前选中账号：

- 顶部账号名：点击后可编辑名称，点击旁边的保存图标完成重命名。
- `刷新用量`：刷新当前账号的剩余额度。
- `详情 / 编辑 JSON`：查看来源文件、文件大小、修改时间、last_refresh、SHA256，并编辑完整 JSON。
- `更新此账号`：用当前 Codex 登录文件覆盖选中的已保存账号。
- `切换为此账号`：把选中的账号设为当前 Codex 登录账号。

## 快速开始

1. 安装 Python 3.12。
2. 双击 `start.bat`。
3. 在左侧选择账号。
4. 点击 `切换为此账号`。

`start.bat` 会使用项目内的 `.venv`。如果 `.venv` 不存在，或不是由以下解释器创建，启动脚本会自动重建虚拟环境：

```text
%LocalAppData%\Programs\Python\Python312\python.exe
```

程序只使用 Python 标准库，不需要安装第三方依赖。

## 账号文件位置

保存的账号文件统一放在：

```text
accounts/auth -<账号名>.json
```

当前 Codex 正在使用的登录文件位于：

```text
%USERPROFILE%\.codex\auth.json
```

切换账号时，程序会把选中的账号文件复制到当前 Codex 登录文件位置。覆盖前会自动把旧文件备份到 `backups/`。

## 常见流程

添加一个新账号：

1. 先用 Codex 正常登录新账号。
2. 打开本工具。
3. 点击 `添加当前登录`。
4. 输入一个简短账号名。

更新一个已保存账号：

1. 先用 Codex 重新登录或刷新该账号。
2. 打开本工具。
3. 在左侧选择要更新的账号。
4. 点击 `更新此账号`。

手动导入账号 JSON：

1. 点击 `导入 JSON 文件`。
2. 选择一个合法的 `auth.json`。
3. 检查账号名称和 JSON 内容。
4. 在详情窗口点击 `保存账号`。

## 用量显示

`刷新用量` 和 `刷新全部用量` 会请求：

```text
https://chatgpt.com/backend-api/wham/usage
```

请求会使用账号文件中的 OAuth token，但程序不会打印 token。界面只显示剩余额度：

- 5 小时窗口
- 7 天窗口

网络请求会先尝试直连。如果失败，会尝试本地代理候选：

- `CODEX_ACC_SWITCH_PROXY` 环境变量
- Windows `ProxyServer` 注册表值

## 命令行

列出账号：

```powershell
python .\switch_codex_account.py --list
```

按编号、账号名或文件名切换：

```powershell
python .\switch_codex_account.py --use 1
python .\switch_codex_account.py --use work
python .\switch_codex_account.py --use "auth -work.json"
```

命令行模式会校验账号文件是否为合法 JSON，但不会输出凭据内容。

## 适用范围

本工具面向 Windows 本地使用场景。它不会托管账号，也不会把账号文件上传到任何服务器。所有账号文件都保存在本机。
