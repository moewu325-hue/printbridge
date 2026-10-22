"""桌面组合与生命周期；Tk 操作集中在事件线程。"""
from __future__ import annotations

import json
from pathlib import Path
from queue import Full
import threading
import time
import tkinter as tk
from tkinter import messagebox

from app_paths import prepare_data_dir, resolve_printer
from app_runtime import AppRuntime
from config import ConfigManager
from db import PrintHistoryDB
from gui.main_window import MainWindow
from gui.tray import SystemTray
from panel.panel_window import PrinterPanel
from printer.controller import PrintController
from printer.monitor import PrintMonitor
from utils import SafeQueue, setup_logger


def create_application(version: str) -> 'DesktopApplication':
    directory = prepare_data_dir()
    logger = setup_logger('PrintBridge', str(directory / 'logs' / 'printbridge.log'))
    config = ConfigManager(str(directory / 'config.json'))
    history = PrintHistoryDB(str(directory / 'print_history.db'))
    history.cleanup(keep_days=30)
    queues = {name: SafeQueue(maxsize=1000 if name == 'job_queue' else 128)
              for name in ('job_queue', 'notify_queue', 'command_queue', 'file_queue', 'status_queue')}
    controller = PrintController()
    monitor = PrintMonitor(resolve_printer(config), config.get('poll_interval_sec', 2),
        queues['job_queue'], queues['notify_queue'], history, logger)
    runtime = AppRuntime(config, monitor, controller, queues, logger)
    return DesktopApplication(directory, config, history, monitor, runtime, queues, logger, version)


class DesktopApplication:
    def __init__(self, directory, config, history, monitor, runtime, queues, logger, version,
                 *, root=None, panel_factory=PrinterPanel, window_factory=MainWindow,
                 tray_factory=SystemTray, clock=time.monotonic,
                 thread_factory=threading.Thread):
        self.directory = Path(directory)
        self.config, self.history = config, history
        self.monitor, self.runtime, self.queues = monitor, runtime, queues
        self.logger, self.version = logger, version
        self.root = root if root is not None else tk.Tk()
        self._clock, self._thread_factory = clock, thread_factory
        self.shutdown_event = threading.Event()
        self._stopped = threading.Event()
        self._closing = False
        self._destroyed = False
        self._cleanup_ok = True
        self._cleanup_thread = None
        self._after_ids: set[str] = set()
        self._next_status = 0
        self._smoke_result = None
        self._startup_failed = False
        self.root.withdraw()
        self._configure_root()
        self.panel = panel_factory(self.root, config, logger)
        try:
            self.window = window_factory(self.root, config, history, monitor, queues, logger,
                request_exit=self.request_exit, on_settings_saved=self.apply_settings,
                on_toggle_panel=self.panel.toggle)
            self.root.title(f'PrintBridge {self.version} — 打印队列管理')
            self.tray = tray_factory(self.window, config, self.shutdown_event, logger,
                on_toggle_panel=self.panel.toggle)
            self.window.tray_available = self.tray.setup()
        except Exception:
            self.panel.stop()
            self._thread_factory(target=self.panel.shutdown, name='PanelCleanup', daemon=True).start()
            self.root.destroy()
            raise

    def _configure_root(self):
        try:
            self.root.tk.call('tk', 'scaling', self.root.winfo_fpixels('1i') / 72)
            self.root.iconphoto(True, tk.PhotoImage(file=str(Path(__file__).parent / 'resources' / 'icon.png')))
        except (tk.TclError, OSError):
            pass

    def _after(self, delay, callback):
        def invoke():
            self._after_ids.discard(token)
            callback()
        token = self.root.after(delay, invoke)
        self._after_ids.add(token)

    def _cancel_timers(self):
        for token in self._after_ids:
            try:
                self.root.after_cancel(token)
            except tk.TclError:
                pass
        self._after_ids.clear()

    def request_exit(self):
        if self._closing:
            return
        self._closing = True
        self.shutdown_event.set()
        self._cancel_timers()
        try:
            self.root.withdraw()
        except tk.TclError:
            pass
        self.panel.stop()
        self._cleanup_thread = self._thread_factory(
            target=self._stop_services, name='Shutdown', daemon=True)
        self._cleanup_thread.start()
        if not self._destroyed:
            try:
                self._after(50, self._check_lifecycle)
            except tk.TclError:
                pass

    def _stop_services(self):
        try:
            for name, stop in (('面板', self.panel.shutdown),
                               ('后台服务', self.runtime.stop), ('托盘', self.tray.stop)):
                try:
                    stop()
                except Exception:
                    self._cleanup_ok = False
                    self.logger.exception('%s退出清理失败', name)
        finally:
            self._stopped.set()

    def apply_settings(self):
        if self._closing:
            return
        def apply():
            try:
                self.runtime.apply_settings()
            except Exception:
                self.logger.exception('应用设置失败')
                self._status(('operation', ('应用设置', False, '设置已保存，后台应用失败，请检查日志')))
        self._thread_factory(target=apply, name='Settings', daemon=True).start()

    def _status(self, event):
        try:
            self.queues['status_queue'].put(event, timeout=0)
        except Full:
            pass

    def _check_lifecycle(self):
        if self.shutdown_event.is_set() and not self._closing:
            self.request_exit()
            return
        if self._stopped.is_set():
            self._destroy_root()
            return
        if not self._closing and hasattr(self.tray, 'is_ready'):
            ready = self.tray.is_ready
            if self.window.tray_available and not ready and self.root.state() == 'withdrawn':
                self.window.show()
            self.window.tray_available = ready
        now = self._clock()
        if not self._closing and now >= self._next_status:
            self._status(('printer', f'{self.monitor.printer_name or "未选择"} ({self.monitor.printer_status})'))
            self._next_status = now + 1
        self._after(50, self._check_lifecycle)

    def _destroy_root(self):
        if self._destroyed:
            return
        self._destroyed = True
        self._cancel_timers()
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    def _smoke_check(self):
        self._smoke_result = {
            'ok': bool(self.root.winfo_exists()), 'version': self.version,
            'geometry': self.root.geometry(), 'data_dir': str(self.directory),
            'wechat_started': self.runtime.bridge.running,
            'monitor_running': self.monitor.running,
            'rows': len(self.window.tree.get_children()),
        }
        self.request_exit()

    def _start_services(self, smoke_test):
        try:
            self.runtime.start(allow_wechat=not smoke_test)
        except Exception as exc:
            self._startup_failed = True
            self.logger.exception('后台服务启动失败')
            if not smoke_test:
                messagebox.showerror('PrintBridge 启动失败',
                    f'后台服务未能启动：{exc}\n请检查日志后重试。', parent=self.root)
            self.request_exit()

    def run(self, smoke_test=False, smoke_delay_ms=1800):
        if not self._closing:
            self._after(50, self._check_lifecycle)
            self._after(0, lambda: self._start_services(smoke_test))
            if smoke_test:
                self._after(smoke_delay_ms, self._smoke_check)
            else:
                self._after(300, self.panel.restore_if_enabled)
            self.root.deiconify()
            self.logger.info('PrintBridge %s 已启动', self.version)
        try:
            self.root.mainloop()
        finally:
            self.request_exit()
            self._destroy_root()
            if self._cleanup_thread is not None:
                self._cleanup_thread.join(timeout=15)
        if smoke_test:
            return self._write_smoke_result()
        self.logger.info('PrintBridge 已退出')
        return 1 if self._startup_failed or not self._cleanup_ok else 0

    def _write_smoke_result(self):
        if self._smoke_result is None:
            return 1
        self._smoke_result['stopped'] = (self._stopped.is_set() and self._cleanup_ok
            and not self.runtime.bridge.running and not self.monitor.running)
        (self.directory / 'smoke.json').write_text(
            json.dumps(self._smoke_result, ensure_ascii=False, indent=2), encoding='utf-8')
        return 0 if (not self._startup_failed and self._smoke_result['ok']
                     and self._smoke_result['monitor_running'] and self._smoke_result['stopped']
                     and not self._smoke_result['wechat_started']) else 1
