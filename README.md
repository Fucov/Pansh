# PanCLI v3 — 异步引擎 + 并发传输 + Typer/Shell 混合架构

> AnyShare (北航网盘) 现代化命令行工具，继承自 v2 REPL 交互模式，全面升级为异步高并发架构。

## 核心亮点

- **异步高并发引擎**：全面采用 `httpx.AsyncClient` + `asyncio`，彻底告别同步阻塞
- **Typer 混合入口**：单次命令即用即走 (`pancli ls home`)，也可进入沉浸式 Shell (`pancli` / `pancli shell`)
- **极速并发传输**：下载/上传支持 `-j/--jobs` 并发数控制（默认 4），配合 `asyncio.Semaphore` 流量控制
- **断点续传**：自动比对本地文件大小，在 `httpx` 请求中动态注入 `Range: bytes=...` Header
- **Rich 炫酷 UI**：多任务并行进度条（类似 `docker pull` 效果）、自适应浅/深色主题 + 实时速率/ETA
- **通配符 + 确认预览**：上传/下载前先预览完整文件列表，确认后再执行
- **本地 Shell 无缝执行**：`!ls -lh /tmp`、`!rm *.tmp` 直接透传参数
- **YAML 配置文件**：`settings.yaml` 存放于项目根目录，支持主题/颜色/传输参数热配置
- **Pydantic 类型安全**：所有数据模型静态类型校验，`FileMetaData`、`TransferTask` 等全面重构

---

## 安装与快速启动

### 前置要求

- Python 3.10+
- 推荐使用 [uv](https://github.com/astral-sh/uv) 管理器

### 开发级安装

```bash
# 克隆源仓库
cd PanCLI

# uv 安装（推荐）
uv pip install -e .

# 或标准 pip 安装
pip install -e .

# 启动！
pancli
```

### 更新（Git Pull）

```bash
git pull origin main
# 代码立即生效（可编辑模式 -e）
```

---

## 使用方式

### 模式一：单次命令（Typer 路由）

```bash
pancli ls home -h                    # 列出目录，-h 人类可读大小
pancli tree home --depth 5           # 显示目录树，最大深度 5
pancli find homework --path home -d 5 # 搜索 "homework" 文件
pancli stat home/file.pdf             # 查看文件元信息
pancli mkdir home/newdir/subdir      # 递归创建目录
pancli rm home/old.txt               # 删除文件
pancli mv home/a.txt home/b.txt      # 移动/重命名
pancli cp home/a.txt home/b.txt      # 复制文件

# 上传/下载（支持并发 + 确认预览）
pancli upload ./local_file home/dir -j 8        # 8 并发上传
pancli upload "*.pdf" home/doc -j 4             # 通配符上传，交互确认
pancli download home/bigdir ./local -j 8 -r     # 递归下载整个目录
pancli download home/*.zip . -j 4              # 通配符下载
```

### 模式二：交互式 Shell

```bash
pancli              # 直接进入 REPL
pancli shell        # 等效，显式进入 Shell
```

进入 Shell 后，享受类似 Bash 的状态保持体验：

```bash
PanCLI [/home/你的名字] $ ls
PanCLI [/home/你的名字] $ cd documents
PanCLI [/home/你的名字/documents] $ find report
PanCLI [/home/你的名字/documents] $ upload ./local.pdf . -j 4
PanCLI [/home/你的名字/documents] $ download remote.pdf . -j 4
PanCLI [/home/你的名字/documents] $ exit
```

### 全局选项

```bash
pancli --whoami          # 查看本地缓存的账号信息
pancli --logout          # 清除登录凭据
pancli --version         # 输出版本号
pancli -h                # 显示帮助
```

---

## 命令参考

### 云端文件操作

| 命令 | 描述 | 示例 |
|------|------|------|
| `ls [path] [-h]` | 列出目录（空目录显示提示） | `ls home -h` |
| `tree [path] [--depth N]` | 显示目录树 | `tree home -d 3` |
| `find <keyword> [--depth d]` | **新增** 全局搜索 | `find homework -d 5` |
| `stat <path>` | 查看元信息 | `stat home/file.pdf` |
| `mkdir <path>` | 创建目录 | `mkdir home/a/b/c` |
| `touch <path>` | 创建空文件 | `touch home/empty.txt` |
| `rm <path> [-r]` | 删除 | `rm home/trash -r` |
| `mv <src> <dst> [-f]` | 移动/重命名 | `mv a.txt b.txt -f` |
| `cp <src> <dst> [-f]` | 复制 | `cp a.txt b.txt -f` |
| `cat <file> [--head N] [--tail N]` | 查看文件内容 | `cat home/readme.txt --head 20` |
| `link <path> [-c/-d]` | 外链管理 | `link home/file.pdf -c` |

### 传输管理

| 命令 | 描述 | 示例 |
|------|------|------|
| `upload <本地> [远程] [-j N] [-r] [-y]` | 上传（通配符预览） | `upload "*.pdf" . -j 4` |
| `download <远程> [本地] [-j N] [-r] [-y]` | 下载（通配符预览） | `download "*.zip" . -r -j 4` |

> **通配符说明**：支持 `*`、`?`、`[abc]` 等 glob 模式。执行前会预览完整文件列表，输入 `y` 确认，参数 `-y` 跳过确认。
>
> **注意**：下载通配符 (`download *.zip`) 只在**当前云端目录**中匹配一级文件，不递归子目录。递归下载请用 `-r` 选项。

### 本地文件系统

| 命令 | 描述 | 示例 |
|------|------|------|
| `lls [path] [-l] [-h]` | 列出本地目录 | `lls /tmp -lh` |
| `lcd <path>` | 切换本地工作目录 | `lcd ~/Downloads` |
| `lpwd` | 显示本地工作目录 | `lpwd` |

### 本地 Shell 执行（`!` 前缀）

| 命令 | 描述 | 示例 |
|------|------|------|
| `!<cmd> [args...]` | 执行本地 Shell 命令，参数完整透传 | `!ls -lh /tmp` |
| | | `!rm *.tmp` |
| | | `!grep -r pattern .` |
| | | `!split -n 10 bigfile part_` |

> 所有参数完整透传给 subprocess，工作目录继承 `lpwd`/`lcd` 设置的本地路径。

### 环境与配置

| 命令 | 描述 | 示例 |
|------|------|------|
| `whoami` | 查看当前账号 | `whoami` |
| `su [user]` | 切换账号 | `su` |
| `logout` | 清除登录凭据 | `logout` |
| `config [show/get/set/reload]` | 查看/修改配置（theme 切换实时生效） | `config show` / `config set theme dark` |
| `clear` | 清屏 | `clear` |
| `exit / quit` | 退出 Shell | `exit` |

---

## YAML 配置文件

配置文件路径：**项目根目录 `./settings.yaml`**（可通过环境变量 `PANCILI_CONFIG` 覆盖）。

首次运行自动在当前目录创建默认配置。

```yaml
# ── 外观 ────────────────────────────────────────────────────
# 主题模式: auto / dark / light
#   dark  — 深色背景，整体颜色浓郁醒目
#   light — 浅色背景，整体颜色柔和淡雅
#   auto  — 自动检测终端背景色
theme: auto

# ── 表格样式 ─────────────────────────────────────────────────
table:
  border: dim           # 表头边框颜色
  zebra: false          # 斑马条纹

# ── 传输设置 ─────────────────────────────────────────────────
transfer:
  default_jobs: 4        # 默认并发数
  chunk_size: 65536      # 上传块大小（字节）
  timeout: 30            # 请求超时（秒）

# ── 搜索设置 ─────────────────────────────────────────────────
search:
  default_depth: 3      # find 默认递归深度
  max_depth: 10         # 最大递归深度（防风控）

# ── 网络设置 ─────────────────────────────────────────────────
network:
  max_retries: 3         # 最大重试次数
  retry_backoff: 2       # 重试间隔（秒）
  connect_timeout: 5     # 连接超时（秒）
```

### config 命令

```bash
config show              # 显示当前配置
config get theme         # 查看单个配置
config set theme dark    # 修改配置（下次命令生效）
config reload            # 重新加载配置（修改文件后生效）
```

> **主题切换**：执行 `config set theme dark` 或 `config set theme light` 后，下一条命令即按新主题渲染。若需立即生效可在 Shell 中 `config reload` 后再执行命令。

---

## 架构设计

```
pancli/
  ├─ main.py          Typer app 入口 + 各命令路由
  ├─ shell.py        prompt-toolkit 交互 Shell（全异步架构）
  ├─ transfer.py      并发传输引擎（Semaphore + Rich Progress + 断点续传）
  ├─ api.py          AsyncApiManager（全异步业务 API）
  ├─ network.py      httpx.AsyncClient + 同步 Client（供 auth）
  ├─ auth.py         OAuth2 登录（保持同步）
  ├─ config.py       platformdirs 配置管理（密码/token 持久化）
  ├─ settings.py      YAML 配置文件管理（主题/颜色/传输参数）
  └─ models.py       Pydantic 数据模型
```

### 模块说明

| 模块 | 职责 |
|------|------|
| `models.py` | Pydantic 数据模型（`FileMetaData`, `TransferTask`, `AppConfig`, `SearchResult` 等） |
| `config.py` | `platformdirs` 配置管理，存储账号密码 token（`~/.config/bhpan/`） |
| `settings.py` | YAML 配置文件，主题/颜色/传输参数（`./settings.yaml`） |
| `network.py` | HTTP 传输层，保留同步接口供 `auth.py` 使用，新增全量异步接口 |
| `auth.py` | OAuth2 登录 + RSA 加密（无变更，逻辑保持） |
| `api.py` | `AsyncApiManager` 全异步业务 API，支持 `search_recursive` 搜索 |
| `transfer.py` | 并发传输引擎，`batch_download` / `batch_upload` 带 Rich 多行进度条 |
| `main.py` | Typer 入口，无子命令默认进入 Shell，支持 `--whoami` / `--logout` 全局回调 |
| `shell.py` | prompt-toolkit REPL，全异步架构，本地 Shell 无缝集成 |

---

## 断点续传原理

```python
# 下载时自动检测本地已有文件大小
local_size = Path(dest).stat().st_size if Path(dest).exists() else 0
if local_size < remote_size:
    headers["Range"] = f"bytes={local_size}-"
    # 从断点处继续下载
```

---

## 并发传输原理

```python
# transfer.py
semaphore = asyncio.Semaphore(jobs)  # 控制最大并发数

async def worker(task):
    async with semaphore:
        # 执行下载/上传
        ...

# 并发执行所有任务
await asyncio.gather(*[worker(t) for t in tasks])
```

---

## 登录凭据存储

账号密码和 token 存储在 `platformdirs` 标准目录下：

- **Linux/macOS**: `~/.config/bhpan/config.json`
- **Windows**: `C:\Users\<用户名>\AppData\Local\bhpan\config.json`

示例配置（加密存储）：

```json
{
  "revision": 4,
  "host": "bhpan.buaa.edu.cn",
  "username": "你的学号",
  "encrypted": "RSA加密后的密码",
  "store_password": true,
  "theme": "auto",
  "cached_token": {
    "token": "...",
    "expires": 1234567890.0
  }
}
```

---

## 鸣谢

- 项目初始逻辑参考 [xdedss/dist_bhpan](https://github.com/xdedss/dist_bhpan)
- API 文档参考 [AnyShare 开放文档](https://developers.aishutech.com/openDoc?productId=1&versonId=30&docId=338)
