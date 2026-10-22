"""微信桥接：单一UI线程、历史基线和有界发送队列。"""
import importlib
import os
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

WECHAT_CLASS = 'WeChatMainWndForPC'
WECHAT_PROCESS = 'WeChat.exe'


class DeliveryUnknown(RuntimeError):
    """已进入发送阶段，无法确认结果，禁止自动重试。"""


@dataclass
class _SendRequest:
    target: str
    text: str
    deadline: float
    done: threading.Event = field(default_factory=threading.Event)
    cancelled: threading.Event = field(default_factory=threading.Event)
    result: bool = False
    state: str = 'pending'
    error: Optional[Exception] = None
    lock: threading.Lock = field(default_factory=threading.Lock)


class WeChatBridge:
    def __init__(self, config_mgr, command_queue, file_queue, status_queue, logger):
        self.config_mgr = config_mgr
        self.command_queue = command_queue
        self.file_queue = file_queue
        self.status_queue = status_queue
        self.logger = logger
        self.running = False
        self.available = False
        self._wx_window = None
        self._thread = None
        self._stop = threading.Event()
        self._lifecycle_lock = threading.RLock()
        self._outgoing = queue.Queue(maxsize=64)
        self._snapshots = {}
        self._auto = None

    @property
    def is_available(self):
        return self.available and self.running and not self._stop.is_set()

    def _enabled(self):
        return bool(self.config_mgr.get('wechat_enabled', False))

    def _authorized(self, target):
        return self._enabled() and target in self.config_mgr.get('wechat_listen_targets', [])

    def start(self):
        with self._lifecycle_lock:
            if self._thread and self._thread.is_alive():
                return
            if not self._enabled():
                return
            self._stop = threading.Event()
            self._snapshots.clear()
            self.running = True
            self._thread = threading.Thread(target=self._run_loop, name='WeChatBridge', daemon=True)
            self._thread.start()

    def stop(self):
        with self._lifecycle_lock:
            self.running = False
            self._stop.set()
            thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=5)
        self._reject_pending()
        self.available = False
        return not thread or not thread.is_alive()

    def _reject_pending(self):
        while True:
            try:
                request = self._outgoing.get_nowait()
            except queue.Empty:
                return
            request.cancelled.set()
            with request.lock:
                request.state = 'done'
            request.done.set()

    def _set_available(self, value):
        previous = self.available
        self.available = value
        if previous != value:
            self.status_queue.put(('wechat', '已连接' if value else '未连接'))
        if not value:
            self._snapshots.clear()

    def _find_wechat(self):
        try:
            window = self._auto.WindowControl(Name='微信', ClassName=WECHAT_CLASS)
            if window.Exists(0, 0):
                self._wx_window = window
                self._set_available(True)
                return True
        except Exception as exc:
            self.logger.debug('查找微信失败: %s', exc)
        self._wx_window = None
        self._set_available(False)
        return False

    def _run_loop(self):
        try:
            self._auto = importlib.import_module('uiautomation')
            with self._auto.UIAutomationInitializerInThread():
                next_poll = 0
                while not self._stop.is_set() and self._enabled():
                    now = time.monotonic()
                    if now >= next_poll:
                        connected = self._find_wechat()
                        if connected:
                            self._check_new_messages()
                        next_poll = time.monotonic() + (2 if connected else 10)
                    try:
                        request = self._outgoing.get(timeout=0.1)
                    except queue.Empty:
                        continue
                    try:
                        if not request.cancelled.is_set() and time.monotonic() < request.deadline:
                            request.result = self._send_now(request.target, request.text, request)
                    except DeliveryUnknown as exc:
                        request.error = exc
                    except Exception as exc:
                        self.logger.warning('微信发送异常: %s', exc)
                    finally:
                        with request.lock:
                            if request.state != 'unknown':
                                request.state = 'done'
                        request.done.set()
        except Exception as exc:
            self.logger.error('微信UI工作线程失败: %s', exc)
        finally:
            self.running = False
            self._wx_window = None
            self._set_available(False)
            self._reject_pending()

    def _verify_chat(self, target):
        # 目标标题、消息区和输入框均需存在；客户端结构变化时拒绝发送。
        header = self._wx_window.TextControl(Name=target, searchDepth=2)
        messages = self._wx_window.ListControl(Name='消息')
        input_edit = self._wx_window.EditControl(Name='输入')
        return header.Exists(0, 0) and messages.Exists(0, 0) and input_edit.Exists(0, 0)

    def _open_chat(self, target):
        if not self._wx_window or self._stop.is_set():
            return False
        search = self._wx_window.EditControl(Name='搜索')
        if not search.Exists(0, 0):
            return False
        if not self._set_text(search, target):
            return False
        if self._stop.wait(0.3):
            return False
        # 精确匹配且仅有一个结果，重名联系人不猜测。
        first = self._wx_window.ListItemControl(Name=target, foundIndex=1)
        second = self._wx_window.ListItemControl(Name=target, foundIndex=2)
        if not first.Exists(0, 0) or second.Exists(0, 0):
            return False
        first.Click()
        if self._stop.wait(0.2):
            return False
        return self._verify_chat(target)

    @staticmethod
    def _set_text(edit, text):
        try:
            pattern = edit.GetValuePattern()
            return pattern.SetValue(text) is True and pattern.Value == text
        except Exception:
            return False

    def _check_new_messages(self):
        targets = self.config_mgr.get('wechat_listen_targets', []) if self._enabled() else []
        self._snapshots = {name: snapshot for name, snapshot in self._snapshots.items() if name in targets}
        for target in targets:
            if self._stop.is_set():
                break
            self._check_chat_messages(target)

    def _check_chat_messages(self, chat_name):
        if not self._authorized(chat_name):
            return
        try:
            if self._open_chat(chat_name):
                self._read_latest_message(chat_name)
            else:
                self._snapshots.pop(chat_name, None)
        except Exception as exc:
            self._snapshots.pop(chat_name, None)
            self.logger.debug('微信聊天读取失败: %s', exc)

    @staticmethod
    def _message_id(message):
        try:
            value = tuple(message.GetRuntimeId())
            return value if value else None
        except Exception:
            return None

    def _read_latest_message(self, chat_name):
        control = self._wx_window.ListControl(Name='消息')
        if not control.Exists(0, 0):
            return
        messages = control.GetChildren()
        ids = [self._message_id(message) for message in messages]
        previous = self._snapshots.get(chat_name)
        # 无稳定身份、空视图或重复身份均只重建基线。
        if not ids or None in ids or len(set(ids)) != len(ids):
            self._snapshots.pop(chat_name, None)
            return
        self._snapshots[chat_name] = ids
        if not previous or previous[-1] not in ids:
            return
        anchor = ids.index(previous[-1])
        # 视图重排或身份重用时不推断新命令。
        overlap = [item for item in ids[:anchor + 1] if item in previous]
        if overlap != [item for item in previous if item in ids]:
            return
        for message, identity in zip(messages[anchor + 1:], ids[anchor + 1:]):
            if identity in previous or not self._authorized(chat_name):
                continue
            text = message.Name or ''
            if text.startswith('/'):
                self.command_queue.put((chat_name, text))

    def send_message(self, target, text):
        if not self.is_available or not self._enabled():
            return False
        # 通知目标可独立设置；命令回复只能发往仍授权的会话。
        notify_target = self.config_mgr.get('wechat_notify_target', '')
        if target != notify_target and not self._authorized(target):
            return False
        if threading.current_thread() is self._thread:
            return self._send_now(target, text)
        request = _SendRequest(target, text, time.monotonic() + 8)
        try:
            self._outgoing.put_nowait(request)
        except queue.Full:
            return False
        if not request.done.wait(timeout=8):
            with request.lock:
                if request.state in ('sending', 'unknown'):
                    request.state = 'unknown'
                    raise DeliveryUnknown('发送已开始，等待超时；结果未知且不自动重试')
                if request.state == 'pending':
                    request.cancelled.set()
                    return False
        if request.error:
            raise request.error
        return request.result

    def _send_now(self, target, text, request=None):
        if not self.is_available or not self._enabled():
            return False
        allowed = target == self.config_mgr.get('wechat_notify_target', '') or self._authorized(target)
        if not allowed or not self._open_chat(target):
            return False
        if request and (request.cancelled.is_set() or time.monotonic() >= request.deadline):
            return False
        edit = self._wx_window.EditControl(Name='输入')
        if not self._set_text(edit, text):
            return False
        allowed = target == self.config_mgr.get('wechat_notify_target', '') or self._authorized(target)
        if self._stop.is_set() or not self._enabled() or not allowed or not self._verify_chat(target):
            return False
        if request:
            with request.lock:
                if request.cancelled.is_set() or time.monotonic() >= request.deadline or self._stop.is_set():
                    return False
                request.state = 'sending'
        try:
            edit.SendKeys('{Enter}')
        except Exception as exc:
            if isinstance(exc, DeliveryUnknown):
                raise
            raise DeliveryUnknown('发送已开始后发生异常；结果未知且不自动重试') from exc
        return True

    def receive_file(self, chat_name, save_dir) -> Optional[str]:
        self.logger.debug('未建立消息与下载文件关联，自动收件不可用')
        return None

    def find_latest_received_file(self) -> Optional[str]:
        # 不扫描其他账号或猜测最新文件的消息来源。
        return None
