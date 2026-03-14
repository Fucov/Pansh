# AnyShare (PanCLI) 交互式 Shell

本项目是一个专为 **AnyShare 7 架构网盘**（如北航网盘）提取并高度定制的现代化、**沉浸式 REPL 命令行系统**。
它抛弃了传统 CLI 的离散调用方式，采用 `prompt_toolkit` 构建了一个带有**状态保持（当前工作目录 CWD）**的交互终端，让你在云盘中穿梭能够如本地文件系统一般自然。

## ✨ 核心亮点

- **沉浸式交互层 (REPL)**：输入 `pancli` 进入独立网盘环境，拥有类似 Bash 的光标、历史记录和连续操作体验。
- **全系 Unix 命令挂载**：支持 `cd`, `pwd`, `ls`, `tree`, `rm`, `mv`, `cp`, `mkdir`, `cat`, `touch`, `head`, `tail`, `stat` 等经典范式，无需反复输入长长的远程路径。
- **泛 AnyShare 兼容**：将配置彻底剥离硬编码。通过修改底层 `config.json`，可随时连接至全国任意的高校/政企 AnyShare 分发中心。
- **极速工程化管理**：基于 `uv` 包管理器全面接管环境，通过 `pyproject.toml` 标准分发。
- **孤岛打包策略**：完美融入 `PyInstaller`，无需 Python 运行环境也能在隔离服务器上一线到底。

---

## 🛠 安装与快速启动

### 前置要求
- Python 3.10+
- 推荐使用 [uv](https://github.com/astral-sh/uv) 管理器

### 开发级安装

**方式一：使用 uv（推荐）**
```bash
# 1. 克隆源仓库
cd PanCLI

# 2. 从本地安装到你的系统预置路径（通过 uv 极速秒装）
uv pip install -e .

# 3. 敲击命令，进入奇景！
pancli
# 亦可使用 bhpan 别名启动
```

**方式二：使用标准 Python (venv + pip)**
```bash
# 1. 克隆源仓库并进入目录
cd PanCLI

# 2. 创建并激活虚拟环境
python3 -m venv .venv
source .venv/bin/activate  # Windows 下使用: .venv\Scripts\activate

# 3. 安装依赖并注册全局命令
pip install -e .

# 4. 启动交互式系统
pancli
```
> **💡 提示：** 如果你在方式二下遇到类似 `zsh: command not found: pancli` 的报错，通常是因为当前终端窗口没有激活对应的虚拟环境。请确保你在执行前已经跑过 `source .venv/bin/activate`。

### PyInstaller 单体应用免驱打包

对于仅有 SSH 的隔离服务器，我们可以在带有运行时的本机构建独立二进制包并直接丢过去：
```bash
uv pip install pyinstaller
pyinstaller --onefile --name pancli pancli/main.py
```
打包后将其产生在 `dist/pancli`，赋予 `chmod +x` 后即可在全网服务器肆意拷贝。

---

## 💻 沉浸式指令指南

当你在终端敲下 `pancli`，经历交互式密码鉴权后，你将步入：

```text
PanCLI [/文档库/你的姓名] $ _
```
这意味着你已处在当前沙河环境中。

### 基础文件系统导航
- `pwd` - 我在哪？打印当下云盘内的绝对路径。
- `cd <dir>` - 在目录间穿梭，支持 `/` 的绝对路径与基于当前节点的相对路径。
- `ls [dir] [-h]` - 枚举当前或指定路径的一级文件。`-h` 以 K/M/G 显示超凡的 Rich 网格。
- `tree [dir]` - 穿透打印整棵目录树。

### 增删与改名
- `mkdir <dir>` - 递归创造长达千里的多级空目录。
- `touch <file>` - 抚摸出一个空白的文件碎片。
- `rm <path> [-r]` - 抹除指定目标。目录前请挂载 `-r`。
- `mv <src> <dst> [-f]` - 迁移与更名。
- `cp <src> <dst> [-f]` - 无情复制。

### 信息窥探
- `cat <file>` - 将目标文件推入你的当前 `stdout` 终端界面。
- `head <file>` / `tail <file>` - 读取头与尾的片段。
- `stat <path>` - 透视包含 `docid`、版本序列在内的 AnyShare 高级数据信息。

### 本机互传
- `upload <local_path> [remote_dir] [-r]`
  将本地文件推入云端。超大文件自动激活极尽美学的并发进度条。
- `download <remote_path> [local_dir] [-r]`
  将云端的数据拖至本地。

### 退出与整洁
- `clear` - 净化当前屏幕。
- `exit` / `quit` - 告别 AnyShare，返回你冰冷的本机终端。

---

## ⚙️ 进阶：如何漫游至其他 AnyShare 阵地

系统会在你的机器中寻找配置基地（依据操作系统的原生规范），比如 `~/.config/bhpan/config.json` 或 `~/Library/Application Support/bhpan/config.json`。

用文本编辑器打开它，你只需将其中的 `host` 替换为新的目标，并按需替换 `pubkey` 字段，你将获得一个管理任何 AnyShare 的私人利器。

---

## 📚 鸣谢与参考资料

本项目的发展经历了彻底的重构与现代化演进，在此特别鸣谢和声明以下前置开源项目与参考资料：

- **基座灵感**：本项目的初始非 REPL 终端代码逻辑分叉自并参考了开源项目 [xdedss/dist_bhpan](https://github.com/xdedss/dist_bhpan) (现已年久失修)。我们在其基础上进行了几乎彻底的底层重写，修复了诸如 CSRF 鉴权拦截、Boolean 类型序列化错误等多处核心 Bug，抛弃了老式的 argparse 单次命令调度模式，并全面演进至本仓库呈现的**状态保持交互式 REPL 生态**。
- **协议文档**：项目中新增与重构的各项网络传输层通讯代码，皆参照官方 AnyShare RESTful 开放文档进行严格校审与精编：[AnyShare 开放文档](https://developers.aishutech.com/openDoc?productId=1&versonId=30&docId=338)。
