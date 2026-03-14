"""BHPAN CLI Entry Point."""

import argparse
import sys

from .core import __version__
from .shell import PanShell


def cli() -> None:
    parser = argparse.ArgumentParser(
        prog="pancli",
        description="AnyShare (PanCLI) 沉浸式交互 Shell 客户端。",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog="说明:\n  直接运行 `pancli` 将进入沉浸式文件系统。\n  在 REPL 系统内部，可以键入 `help` 查看支持的文件管理子命令。",
    )
    parser.add_argument(
        "-v",
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "--whoami",
        action="store_true",
        help="查看当前本地环境缓存的鉴权信息与 Host 设置并退出",
    )
    parser.add_argument(
        "--logout",
        action="store_true",
        help="立刻清除本地保存的所有账号凭据缓存并退出",
    )

    args = parser.parse_args()

    if args.logout:
        from .config import load_config, save_config
        cfg = load_config()
        cfg.username = None
        cfg.encrypted = None
        cfg.cached_token.token = ""
        save_config(cfg)
        print("✓ 已清除本地登录凭据，下次启动将重新要求登录。")
        sys.exit(0)
        
    if args.whoami:
        from .config import load_config
        cfg = load_config()
        print(f"当前配置 Host: {cfg.host}")
        print(f"当前记住账号: {cfg.username if cfg.username else '无'}")
        if cfg.encrypted:
            print("密码状态: 已加密保存在本地")
        else:
            print("密码状态: 未保存")
        sys.exit(0)

    try:
        PanShell().run()
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    cli()
