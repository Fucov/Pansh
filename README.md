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

## 登录模式与 profile

`pansh` 把非敏感连接配置与认证状态分开，并支持两种 session mode：

### 1. 持久化登录

personal 环境默认使用 `persistent`。登录成功后只更新当前 profile 的用户名、加密凭据和 token，后续进程可以复用：

```bash
pansh login
pansh --profile kaiwei
pansh login --profile kaiwei
```

### 2. Ephemeral 临时会话

`ephemeral` 不读取、写入或清除任何磁盘认证状态；账号、加密密码和 token 只存在当前 Python 进程。它仍可读取指定 profile 的 `host`、`pubkey`、`verify_tls` 等非敏感连接配置：

```bash
pansh --ephemeral
pansh --ephemeral ls .
pansh --profile work1 --ephemeral
```

共享服务器推荐显式启用 shared 环境：

```bash
PANSH_SHARED=1 pansh
```

说明：

- `PANSH_SHARED=1`、`--shared` 或 settings 中的 shared 环境默认选择 ephemeral，避免静默复用磁盘账号。
- `--once` 和 `--no-store-login` 是 `--ephemeral` 的兼容别名。
- 交互式 TTY 中，`pansh login --no-store` 会提示后直接进入同一进程的 ephemeral shell；退出后 session 自然销毁。
- 非交互环境中的 `login --no-store` 会返回非零退出码，请改用 `pansh --ephemeral <command>`。
- ephemeral shell 中多个命令复用同一个 manager；`exit`、`quit`、EOF 或 `logout` 后关闭。
- ephemeral 的 `logout` 只清理内存，不会清除任何 persistent profile。
- 如果当前是 persistent 会话，`logout` 会清除本地保存的凭据和 token。

### Profile 管理

```bash
pansh profiles list
pansh profiles create work1
pansh profiles path work1
pansh profiles delete work1
```

profile 名只允许字母、数字、`.`、`_` 和 `-`，不能使用路径。profile 用于避免误覆盖和复用其他 profile 的认证状态，但不是操作系统安全边界：共享同一个 Linux UID 的用户仍可读取该 UID 有权访问的文件。多人共用同一 UID 时应使用 ephemeral；如果需要真正隔离，请使用不同系统账号或容器权限边界。

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

诊断安装来源、运行环境和当前会话配置（不会输出 token、密码或加密凭据）：

```bash
pansh doctor
pansh doctor --json
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

- Linux 配置：`~/.config/pansh/`；认证状态：`~/.local/state/pansh/`
- macOS：`~/Library/Application Support/pansh/` 对应的配置/状态目录
- Windows：`%APPDATA%\\pansh\\` 对应的配置/状态目录

主要文件（实际根目录由 `platformdirs` 按平台解析）：

- `settings.yaml`
- `profiles/<profile>/profile.yaml`：非敏感连接配置
- 状态目录中的 `profiles/<profile>/auth.json`：当前 profile 的认证状态

`profile.yaml` 可按需填写：

```yaml
host: bhpan.buaa.edu.cn
verify_tls: true
store_password: true
```

可通过环境变量覆盖：

```bash
PANSH_CONFIG=/path/to/settings.yaml
PANSH_AUTH_DIR=/private/state/directory
```

补充说明：

- 首次运行会自动生成默认 `settings.yaml`
- `PANSH_PROFILE` 选择 profile；`PANSH_SESSION_MODE=ephemeral|persistent` 选择模式；`PANSH_SHARED=1` 开启 shared 默认值
- 优先级为命令行 > 环境变量 > `settings.yaml` > 内置默认值
- 旧 `~/.config/pansh/auth.json`（以及更早的 `bhpan/config.json`）只会在首次使用 persistent `default` profile 且新认证文件不存在时迁移；迁移前生成 `.bak` 备份，成功验证后删除旧文件
- ephemeral 和非 default profile 都不会触发旧认证文件迁移
- `PANSH_CONFIG` 及旧的 `pansh_CONFIG` 仍可用于覆盖 settings 路径

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
