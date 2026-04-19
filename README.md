# pansh

`pansh` 是一个面向 AnyShare / 网盘的命令行工具，支持交互式 shell 和单行命令两种使用方式，提供登录、目录浏览、查找、上传、下载等稳定能力。

## Python 版本

已明确兼容并优先测试：

- Python 3.10
- Python 3.11
- Python 3.12
- Python 3.13

推荐优先使用 Python 3.11 或 3.12。更老版本未支持；更新版本如果没有在 release note 中说明，视为“未专门验证”。

## 安装

PyPI 安装：

```bash
pip install pansh
```

更推荐用 `pipx` 隔离安装，能减少和现有科研/工程环境的依赖冲突：

```bash
pipx install pansh
```

安装后可验证：

```bash
pansh --help
python -m pansh --help
pansh --version
```

开发安装：

```bash
pip install -e .
```

## 快速开始

进入交互式 shell：

```bash
pansh
```

或显式进入 shell：

```bash
pansh shell
```

单行命令示例：

```bash
pansh ls .
pansh find 报告 --path .
pansh upload README.md .
pansh download /home/file.pdf ./downloads
```

## 登录模式

`pansh` 支持两种登录模式：

### 1. 持久化登录

这是默认模式。登录成功后会写入本地凭据和 token cache，后续命令可复用：

```bash
pansh login
```

### 2. 一次性登录 / session-only

只在当前进程或当前 shell 会话有效，不写入本地 token：

```bash
pansh --once
```

或：

```bash
pansh --once ls .
pansh --no-store-login
pansh login --no-store
```

说明：

- `--once` 是更短的入口，`--no-store-login` 也可继续使用。
- `--once` / `--no-store-login` 适合直接进入 shell，或执行单条命令时临时登录。
- `login --no-store` 只验证并建立当前进程会话，不更新本地缓存。
- `whoami` 在 session-only 登录后同样可用。
- 在交互式 shell 里，临时 token 会在整个会话期间保留；`exit` / `quit` / EOF / 异常退出后自然失效，不会落盘。
- 如果当前是 session-only 会话，`logout` 只结束当前临时会话。
- 如果当前是 persistent 会话，`logout` 会清除本地保存的凭据和 token。

## 常用命令

### 浏览与查询

```bash
pansh whoami
pansh ls .
pansh tree . --depth 2
pansh stat /home/file.pdf
pansh find 报告 --path /home/docs
```

### 上传

```bash
pansh upload a.txt b.txt .
pansh upload --glob "*.pdf" .
pansh upload --regex ".*\\.(pdf|docx)$" ./docs . --recursive
```

规则说明：

- `upload` 不写远端目标时，默认使用当前远端工作目录。
- 多文件上传时，最后一个参数只有在明显是远端目标时才会被当作目标目录。

### 下载

```bash
pansh download /home/a.pdf
pansh download /home/a.pdf /home/b.pdf
pansh download --glob "*.zip" .
pansh download --regex ".*2026.*\\.pdf$" /home/docs ./downloads --recursive
```

规则说明：

- `download` 不写本地目标时，默认使用当前本地工作目录。
- `--glob` 适合 `*.pdf` 这类通配符。
- `--regex` 适合更复杂的正则匹配。

### 输出模式

```bash
pansh ls . --json
pansh stat /home/file.pdf --json
pansh find 报告 --json
```

## 交互式 shell

进入后可使用：

- `help`
- `clear`
- `logout`
- `pwd`, `cd`
- `lpwd`, `lcd`, `lls`
- `!<command>`
- 以及稳定 CLI 命令：`whoami`、`ls`、`tree`、`stat`、`find`、`mkdir`、`touch`、`rm`、`mv`、`cp`、`cat`、`upload`、`download`

在 shell 内查看某个命令参数：

```bash
help upload
find --help
download -h
```

补充说明：

- shell 启动时会自动登录
- `logout` 会注销当前会话并退出 shell
- `exit` / `quit` 只退出 shell，不清理已保存的登录信息

## 配置路径

默认配置采用用户目录，不依赖仓库根目录。

典型路径：

- Linux/macOS: `~/.config/pansh/`
- Windows: `%APPDATA%\\pansh\\`

主要文件：

- `settings.yaml`
- `auth.json`

可通过环境变量覆盖：

```bash
PANSH_CONFIG=/path/to/settings.yaml
```

补充说明：

- 首次运行会自动生成默认 `settings.yaml`
- session-only 登录不会写入本地 token
- `auth.json` 用于持久化凭据与 token cache

## 稳定性说明

当前正式对外支持并在帮助中显示的命令以“稳定可用”为优先。以下内部实现暂不作为公开能力展示：

- quota
- link
- revisions
- restore-revision

这些能力即使在代码中保留了内部实现，也不属于当前 README 承诺范围。

## 发布前本地自检

```bash
pip install .
python -m pansh --help
pansh --help
pansh login --no-store
pansh upload README.md .
pansh download /home/file.pdf
```

## 其他学校

`pansh` 目前默认按北航的 AnyShare 配置工作。如果你所在学校也在使用爱数 AnyShare / 爱数云盘，通常也可以复用这套 CLI，但可能需要自行适配 `host`、登录入口或认证细节。

我目前确认到的公开案例包括：

- 中山大学：https://pan.sysu.edu.cn/
- 天津大学：https://pan.tju.edu.cn/
- 北京大学：https://disk.pku.edu.cn/
- 中国人民大学：https://pan.ruc.edu.cn/

如果你来自其他学校并完成了适配，欢迎提 PR 合并配置说明或兼容补丁。
