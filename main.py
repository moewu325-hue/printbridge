"""PrintBridge桌面入口，后台服务和退出共用一个生命周期。"""
import argparse
import os
from pathlib import Path
import sys
import traceback

from app_paths import get_data_dir
from desktop_app import create_application
from utils import singleton_lock, release_singleton_lock

from version import __version__ as VERSION


def main(argv=None):
    parser = argparse.ArgumentParser(description='PrintBridge 打印队列管理')
    parser.add_argument('--data-dir', help='指定持久数据目录')
    parser.add_argument('--smoke-test', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.data_dir:
        os.environ['PRINTBRIDGE_DATA_DIR'] = str(Path(args.data_dir).resolve())
    if args.smoke_test and not os.environ.get('PRINTBRIDGE_DATA_DIR'):
        parser.error('烟测必须显式指定隔离数据目录')
    try:
        return _run(smoke_test=args.smoke_test)
    except Exception:
        detail = traceback.format_exc()
        try:
            log = get_data_dir() / 'logs' / 'crash.log'
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text(detail, encoding='utf-8')
        except OSError:
            pass
        if not args.smoke_test:
            from tkinter import messagebox
            messagebox.showerror('PrintBridge 启动失败', str(sys.exc_info()[1]))
        return 1
    finally:
        release_singleton_lock()


def _run(smoke_test=False):
    if not singleton_lock():
        return 0
    return create_application(VERSION).run(smoke_test=smoke_test)


if __name__ == '__main__':
    raise SystemExit(main())
