"""Settings management — YAML-based configuration for PanCLI."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import yaml
from rich.style import Style
from rich.text import Text

# ── 默认配置 ────────────────────────────────────────────────────

DEFAULT_SETTINGS = """\
# ═══════════════════════════════════════════════════════════════
# PanCLI Settings 配置文件
# 路径: ./settings.yaml (与 pancli.py 同目录)
# 环境变量: PANCILI_CONFIG 可覆盖默认路径
# ═══════════════════════════════════════════════════════════════

# ── 外观 ────────────────────────────────────────────────────
# 主题模式: auto / dark / light
#   dark  — 深色背景，整体颜色浓郁醒目
#   light — 浅色背景，整体颜色柔和淡雅
#   auto  — 自动检测终端背景色
theme: auto

# ── 表格样式 ─────────────────────────────────────────────────
table:
  # 表头边框颜色 (dim / bright / <颜色>)
  border: dim

  # 是否显示斑马条纹
  zebra: false

# ── 传输设置 ─────────────────────────────────────────────────
transfer:
  # 默认并发数
  default_jobs: 4

  # 单次上传/下载块大小（字节）
  chunk_size: 65536

  # 请求超时（秒）
  timeout: 30

# ── 搜索设置 ─────────────────────────────────────────────────
search:
  # find 命令默认递归深度
  default_depth: 3

  # 最大递归深度（防止风控）
  max_depth: 10

# ── 网络设置 ─────────────────────────────────────────────────
network:
  # 最大重试次数
  max_retries: 3

  # 重试间隔（秒）
  retry_backoff: 2

  # 连接超时（秒）
  connect_timeout: 5
"""


# ═══════════════════════════════════════════════════════════════════════════════
# 颜色系统 — 两套调色板 + Rich Style 工厂
# ═══════════════════════════════════════════════════════════════════════════════

# 调色板定义：每种语义色对应 [dark_mode_style, light_mode_style]
# 所有颜色均通过 Rich Style 组合实现优雅视觉效果

_PALETTE: dict[str, list[str]] = {
    # ── 语义色 ────────────────────────────────────────────────────
    "folder":    ["bold cyan",          "cyan"],
    "file":      ["bold white",         "blue"],
    "symlink":   ["bold magenta",       "magenta"],
    "hidden":    ["dim",                "dim"],
    "exec":      ["bold green",         "green"],
    # ── 状态色 ────────────────────────────────────────────────────
    "success":   ["bold green",         "green"],
    "warning":   ["bold yellow",        "yellow"],
    "error":     ["bold red",           "bold red"],
    "info":      ["bold cyan",          "blue"],
    # ── UI 色 ─────────────────────────────────────────────────────
    "title":     ["bold bright_white",  "bold"],
    "dim":       ["dim",                "dim"],
    "border":    ["dim",               "dim"],
    "muted":     ["bright_black",       "bright_black"],
    # ── 表格专用 ─────────────────────────────────────────────────
    "tbl_hdr":   ["bold bright_white", "bold blue"],
    "tbl_row_a": ["",                  ""],
    "tbl_row_b": ["dim",               ""],
    "tbl_idx":   ["cyan",              "cyan"],
}


def _make_styles(dark: bool) -> dict[str, Style]:
    """根据主题生成完整 Style 字典。"""
    idx = 0 if dark else 1
    styles: dict[str, Style] = {}
    for key, variants in _PALETTE.items():
        try:
            styles[key] = Style.parse(variants[idx])
        except Exception:
            styles[key] = Style()
    return styles


# 全局样式缓存（运行时按需生成，不在模块加载时生成）
_styles_cache: dict[bool, dict[str, Style]] = {}


def get_styles(dark: bool) -> dict[str, Style]:
    """获取指定主题的完整样式字典（带缓存）。"""
    if dark not in _styles_cache:
        _styles_cache[dark] = _make_styles(dark)
    return _styles_cache[dark]


# ── 路径解析 ────────────────────────────────────────────────────


def _find_config_file() -> Path:
    env_path = os.environ.get("PANCILI_CONFIG")
    if env_path:
        return Path(env_path)

    cwd_path = Path.cwd() / "settings.yaml"
    if cwd_path.exists():
        return cwd_path

    script_dir = Path(sys.argv[0] if sys.argv else __file__).parent
    script_path = script_dir / "settings.yaml"
    if script_path.exists():
        return script_path

    return cwd_path


def get_config_dir() -> Path:
    cfg_path = _find_config_file()
    if cfg_path.exists():
        return cfg_path.parent
    return Path.cwd()


# ── 配置读写 ────────────────────────────────────────────────────


class Settings:
    _instance: "Settings | None" = None

    def __init__(self) -> None:
        self._path: Path = _find_config_file()
        self._raw: dict = {}
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            try:
                self._raw = yaml.safe_load(self._path.read_text(encoding="utf-8")) or {}
            except Exception:
                self._raw = {}
        else:
            self._raw = {}
        self._migrate()
        self._apply_env_overrides()

    def _migrate(self) -> None:
        pass  # 后续扩展用

    def _apply_env_overrides(self) -> None:
        if v := os.environ.get("PANCILI_THEME"):
            self._raw["theme"] = v
        if v := os.environ.get("PANCILI_JOBS"):
            self._raw.setdefault("transfer", {})["default_jobs"] = int(v)

    def save(self) -> None:
        if self._path.exists():
            backup = self._path.with_suffix(".yaml.bak")
            shutil.copy2(self._path, backup)
        self._path.write_text(
            yaml.dump(self._raw, allow_unicode=True, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )

    def get(self, key: str, default=None):
        keys = key.split(".")
        val: dict = self._raw
        for k in keys:
            if isinstance(val, dict):
                val = val.get(k)  # type: ignore
            else:
                return default
            if val is None:
                return default
        return val

    def set(self, key: str, value) -> None:
        keys = key.split(".")
        d: dict = self._raw
        for k in keys[:-1]:
            d = d.setdefault(k, {})  # type: ignore
        d[keys[-1]] = value

    # ── 属性 ─────────────────────────────────────────────────────
    @property
    def theme(self) -> str:
        return self.get("theme", "auto")

    @property
    def default_jobs(self) -> int:
        return int(self.get("transfer.default_jobs", 4))

    @property
    def chunk_size(self) -> int:
        return int(self.get("transfer.chunk_size", 65536))

    @property
    def search_depth(self) -> int:
        return int(self.get("search.default_depth", 3))

    @property
    def max_depth(self) -> int:
        return int(self.get("search.max_depth", 10))

    @property
    def max_retries(self) -> int:
        return int(self.get("network.max_retries", 3))

    @property
    def retry_backoff(self) -> float:
        return float(self.get("network.retry_backoff", 2.0))

    @property
    def connect_timeout(self) -> float:
        return float(self.get("network.connect_timeout", 5.0))

    @property
    def request_timeout(self) -> float:
        return float(self.get("transfer.timeout", 30.0))

    @property
    def table_border(self) -> str:
        return str(self.get("table.border", "dim"))

    @property
    def table_zebra(self) -> bool:
        return bool(self.get("table.zebra", False))

    @property
    def is_dark(self) -> bool:
        t = self.theme
        if t == "dark":
            return True
        if t == "light":
            return False
        return _detect_terminal_bg()

    @property
    def styles(self) -> dict[str, Style]:
        """当前主题对应的完整样式字典。"""
        return get_styles(self.is_dark)

    @classmethod
    def get_instance(cls) -> "Settings":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reload(cls) -> "Settings":
        """重新加载配置（用于 config 命令）。"""
        cls._instance = cls()
        _styles_cache.clear()  # 清除样式缓存
        return cls._instance


def load_settings() -> Settings:
    return Settings.get_instance()


def init_settings() -> Settings:
    path = _find_config_file()
    if not path.exists():
        path.write_text(DEFAULT_SETTINGS, encoding="utf-8")
    return load_settings()


# ── 终端背景色检测 ────────────────────────────────────────────────


def _detect_terminal_bg() -> bool:
    if os.name == "nt":
        try:
            import ctypes
            try:
                ctypes.windll.shcore.GetSystemThemeBrush.restype = ctypes.c_void_p
                ref = ctypes.c_int()
                ctypes.windll.shcore.GetSystemThemeBrush(ctypes.byref(ref), 0, 0)
                if ref.value:
                    return False
            except Exception:
                pass
        except Exception:
            pass
    return True
