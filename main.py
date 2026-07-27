"""PrintBridge 主入口 — 桌面打印进程显示 + 微信桥接联动"""
import sys
import os
import threading
import queue
import traceback
import tkinter as tk

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_DIR)

from config import ConfigManager
from db import PrintHistoryDB
from utils import setup_logger, singleton_lock, SafeQueue
from printer.monitor import PrintMonitor
from printer.controller import PrintController
from gui.main_window import MainWindow
from gui.tray import SystemTray
from wechat.bridge import WeChatBridge
from wechat.commands import CommandExecutor
from wechat.notifier import Notifier


def main():
    # ——— 崩溃日志 ———
    crash_log = os.path.join(PROJECT_DIR, "logs", "crash.log")
    os.makedirs(os.path.dirname(crash_log), exist_ok=True)

    try:
        _run()
    except Exception:
        with open(crash_log, "w", encoding="utf-8") as f:
            f.write(traceback.format_exc())
        # 弹出错误窗口(如果可能)
        try:
            import tkinter.messagebox as mb
            mb.showerror("PrintBridge 启动失败", traceback.format_exc()[-500:])
        except Exception:
            pass
        sys.exit(1)


def _run():
    # 单实例锁
    if not singleton_lock():
        # 已有实例在运行, 静默退出
        sys.exit(0)

    # 路径
    config_path = os.path.join(PROJECT_DIR, "config.json")
    db_path = os.path.join(PROJECT_DIR, "print_history.db")
    log_path = os.path.join(PROJECT_DIR, "logs", "printbridge.log")

    # 日志
    logger = setup_logger("PrintBridge", log_path)
    logger.info("PrintBridge 启动...")

    # 配置 & 数据库（轻量操作, 不回阻塞）
    config_mgr = ConfigManager(config_path)
    app_config = config_mgr.to_app_config()
    db = PrintHistoryDB(db_path)
    db.cleanup(keep_days=30)
    os.makedirs(app_config.temp_dir, exist_ok=True)

    # 队列
    queues = {
        "job_queue": SafeQueue(),
        "notify_queue": SafeQueue(),
        "command_queue": SafeQueue(),
        "file_queue": SafeQueue(),
        "status_queue": SafeQueue(),
    }

    shutdown_event = threading.Event()

    # 打印机控制器(轻量, 不打开打印机)
    controller = PrintController()

    # 打印机名
    selected_printer = app_config.selected_printer
    if not selected_printer:
        selected_printer = "HP LaserJet Professional P1108"

    # ============ 先创建 GUI ============
    root = tk.Tk()
    root.withdraw()  # 先隐藏, 等就绪再显示

    # 创建 monitor 和 main_window
    monitor = PrintMonitor(
        printer_name=selected_printer,
        poll_interval=app_config.poll_interval_sec,
        job_queue=queues["job_queue"],
        notify_queue=queues["notify_queue"],
        db=db,
        logger=logger,
    )
    monitor.set_auto_switch_callback(
        lambda name: config_mgr.update({"selected_printer": name})
    )

    main_window = MainWindow(root, config_mgr, db, monitor, queues, logger)

    # 微信模块 (轻量初始化, 不连接)
    wechat_bridge = None
    command_executor = CommandExecutor(controller, monitor, config_mgr, logger)
    notifier_inst = Notifier(wechat_bridge, config_mgr, queues["notify_queue"], logger)

    if app_config.wechat_enabled:
        try:
            wechat_bridge = WeChatBridge(
                config_mgr=config_mgr,
                command_queue=queues["command_queue"],
                file_queue=queues["file_queue"],
                status_queue=queues["status_queue"],
                logger=logger,
            )
            notifier_inst.bridge = wechat_bridge
        except Exception as e:
            logger.warning(f"微信初始化失败: {e}")

    # 托盘
    tray = SystemTray(main_window, config_mgr, shutdown_event, logger)
    tray.setup()

    # 退出清理
    def on_destroy():
        logger.info("退出中...")
        shutdown_event.set()
        monitor.stop()
        if wechat_bridge:
            wechat_bridge.stop()
        notifier_inst.stop()
        tray.stop()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", main_window._on_close)

    def check_shutdown():
        if shutdown_event.is_set():
            on_destroy()
        else:
            root.after(500, check_shutdown)

    # ============ 延迟启动后台服务 (GUI已就绪后) ============
    def start_services():
        """在 GUI 就绪后延迟启动后台线程, 避免启动卡死"""
        try:
            monitor.start()
            queues["status_queue"].put(("printer", f"{monitor.printer_name} (监控中)"))
        except Exception as e:
            logger.error(f"打印监控启动失败: {e}")

        if wechat_bridge:
            try:
                wechat_bridge.start()
                queues["status_queue"].put(("wechat", "连接中..."))
            except Exception as e:
                logger.error(f"微信桥接启动失败: {e}")
        else:
            queues["status_queue"].put(("wechat", "已禁用"))

        if app_config.wechat_enabled:
            try:
                notifier_inst.start()
            except Exception as e:
                logger.error(f"通知服务启动失败: {e}")

        # 启动后台工作线程
        def command_worker():
            while not shutdown_event.is_set():
                try:
                    item = queues["command_queue"].get(timeout=1.0)
                except queue.Empty:
                    continue
                if item is None:
                    continue
                chat_name, text = item
                reply = command_executor.execute(text, chat_name)
                if wechat_bridge and wechat_bridge.is_available:
                    wechat_bridge.send_message(chat_name, reply)

        def file_worker():
            from utils import is_printable_file
            import shutil as sh
            while not shutdown_event.is_set():
                try:
                    item = queues["file_queue"].get(timeout=1.0)
                except queue.Empty:
                    continue
                if item is None:
                    continue
                chat_name, filepath, filetype = item
                if not os.path.exists(filepath) or not is_printable_file(filepath):
                    continue
                dest = os.path.join(app_config.temp_dir, os.path.basename(filepath))
                try:
                    sh.copy2(filepath, dest)
                    filepath = dest
                except Exception:
                    continue
                if controller.print_file(filepath, app_config.selected_printer):
                    if wechat_bridge and wechat_bridge.is_available:
                        wechat_bridge.send_message(
                            chat_name, f"已提交打印: {os.path.basename(filepath)}"
                        )

        threading.Thread(target=command_worker, name="CmdWorker", daemon=True).start()
        threading.Thread(target=file_worker, name="FileWorker", daemon=True).start()

        logger.info("后台服务全部启动完成")

    # 延迟 500ms 启动后台, 确保 GUI 已完全绘制
    root.after(500, start_services)
    root.after(500, check_shutdown)

    # 显示窗口
    root.deiconify()
    root.lift()
    root.focus_force()

    logger.info(f"GUI 就绪 | 打印机: {selected_printer} | 微信: {'启用' if wechat_bridge else '禁用'}")

    root.mainloop()
    logger.info("PrintBridge 已退出")


if __name__ == "__main__":
    main()
