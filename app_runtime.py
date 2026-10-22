"""后台服务与最终消费边界；GUI线程只发起生命周期请求。"""
import os
from pathlib import Path
import shutil
import tempfile
import threading

from app_paths import resolve_printer
from utils import is_printable_file, safe_filename
from wechat.bridge import WeChatBridge
from wechat.commands import CommandExecutor
from wechat.notifier import Notifier


class AppRuntime:
    def __init__(self, config_mgr, monitor, controller, queues, logger):
        self.config = config_mgr
        self.monitor = monitor
        self.controller = controller
        self.queues = queues
        self.logger = logger
        self.bridge = WeChatBridge(config_mgr, queues['command_queue'],
            queues['file_queue'], queues['status_queue'], logger)
        self.executor = CommandExecutor(controller, monitor, config_mgr, logger)
        self.notifier = Notifier(self.bridge, config_mgr, queues['notify_queue'], logger)
        self.stopping = threading.Event()
        self._lock = threading.RLock()
        self._threads = []
        self._started = False
        self._allow_wechat = True

    def start(self, allow_wechat=True):
        with self._lock:
            if self._started or self.stopping.is_set():
                return
            self._started = True
            self._allow_wechat = allow_wechat
            self.monitor.start()
            self.notifier.start()
            if allow_wechat and self.config.get('wechat_enabled', False):
                self.bridge.start()
            else:
                self.queues['status_queue'].put(('wechat', '已禁用'))
            for name, queue_name, callback in (
                    ('Commands', 'command_queue', self.dispatch_command),
                    ('Files', 'file_queue', self.dispatch_file)):
                thread = threading.Thread(target=self._consume,
                    args=(queue_name, callback), name=name, daemon=True)
                self._threads.append(thread)
                thread.start()

    def apply_settings(self):
        with self._lock:
            if self.stopping.is_set():
                return
            self.monitor.poll_interval = self.config.get('poll_interval_sec', 2)
            name = resolve_printer(self.config)
            if name != self.monitor.printer_name:
                self.monitor.switch_printer(name)
            self.monitor.request_refresh()
            if self._allow_wechat and self.config.get('wechat_enabled', False):
                self.bridge.start()
            else:
                self.bridge.stop()
                self.queues['status_queue'].put(('wechat', '已禁用'))

    def _consume(self, queue_name, callback):
        while not self.stopping.is_set():
            item = self.queues[queue_name].get(timeout=0.2)
            if item is None:
                continue
            try:
                callback(item)
            except Exception:
                self.logger.exception('后台事件处理失败：%s', queue_name)

    def _authorized(self, sender, snapshot):
        return (not self.stopping.is_set() and self._allow_wechat
                and snapshot.get('wechat_enabled', False)
                and sender in snapshot.get('wechat_listen_targets', []))

    def dispatch_command(self, item):
        sender, text = item
        if not self._authorized(sender, self.config.data):
            return
        reply = self.executor.execute(text, sender)
        if reply and self._authorized(sender, self.config.data):
            self.bridge.send_message(sender, reply)

    def dispatch_file(self, item):
        sender, filepath, _filetype = item
        snapshot = self.config.data
        source = Path(filepath)
        if (not self._authorized(sender, snapshot)
                or not snapshot.get('auto_print_wechat_files', False)
                or not source.is_file() or not is_printable_file(str(source))
                or source.suffix.lower().lstrip('.') not in snapshot.get('allowed_file_types', [])):
            return
        directory = Path(snapshot['temp_dir'])
        directory.mkdir(parents=True, exist_ok=True)
        fd, destination = tempfile.mkstemp(prefix=safe_filename(source.stem)[:48] + '-',
            suffix=source.suffix.lower(), dir=directory)
        requested = False
        try:
            with os.fdopen(fd, 'wb') as dest, source.open('rb') as original:
                shutil.copyfileobj(original, dest)
            latest = self.config.data
            if (not self._authorized(sender, latest)
                    or not latest.get('auto_print_wechat_files', False)
                    or source.suffix.lower().lstrip('.') not in latest.get('allowed_file_types', [])):
                Path(destination).unlink(missing_ok=True)
                return
            printer = resolve_printer(self.config)
            if not printer or not self.controller.print_file(destination, printer):
                Path(destination).unlink(missing_ok=True)
                self.logger.warning('文件打印请求未能发起')
                return
            requested = True
            if self._authorized(sender, self.config.data):
                self.bridge.send_message(sender, '已发起打印请求：' + source.name)
        except Exception:
            # 文件关联程序可能仍在读取；回复失败不撤销已发起的请求。
            if not requested:
                Path(destination).unlink(missing_ok=True)
            raise

    def stop(self):
        self.stopping.set()
        with self._lock:
            self.bridge.stop()
            self.notifier.stop()
            self.monitor.stop()
        for thread in self._threads:
            if thread is not threading.current_thread():
                thread.join(timeout=3)
                if thread.is_alive():
                    self.logger.warning('后台线程仍在等待系统调用：%s', thread.name)
