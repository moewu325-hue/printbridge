"""打印事件通知：异常隔离、有限重试和实时开关。"""
import queue
import threading
from wechat.bridge import DeliveryUnknown

NOTIFY_TEMPLATES = {
    'printed': '✅ [打印完成] {document}\n打印机: {printer}\n页数: {pages}\n时间: {time}',
    'error': '❌ [打印异常] {document}\n打印机: {printer}\n请检查打印队列与设备状态',
    'paperout': '📄 [缺纸] 打印机 {printer} 纸张不足\n请添加纸张后继续',
    'offline': '🔌 [脱机] 打印机 {printer} 已断开连接\n请检查连接后重试',
    'cancelled': '🚫 [已取消] {document}\n作业已被取消',
    'removed': '⚠ [结果未知] {document}\n作业已离开打印队列，无法确认实际打印结果',
}


class Notifier:
    def __init__(self, bridge, config_mgr, notify_queue, logger):
        self.bridge = bridge
        self.config_mgr = config_mgr
        self.notify_queue = notify_queue
        self.logger = logger
        self.running = False
        self._thread = None
        self._stop = threading.Event()
        self._lock = threading.RLock()

    def start(self):
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self.running = True
            self._stop = threading.Event()
            self._thread = threading.Thread(target=self._run_loop, name='Notifier', daemon=True)
            self._thread.start()

    def stop(self):
        with self._lock:
            self.running = False
            self._stop.set()
            thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=10)
        return not thread or not thread.is_alive()

    def _should_notify(self, status):
        if not self.config_mgr.get('wechat_enabled', False):
            return False
        setting = {'printed': 'notify_on_complete', 'error': 'notify_on_failure',
                   'cancelled': 'notify_on_failure', 'removed': 'notify_on_error',
                   'paperout': 'notify_on_error', 'offline': 'notify_on_error'}.get(status)
        return bool(setting and self.config_mgr.get(setting, True))

    def _deliver(self, job):
        status = job.get('status', '')
        if not self._should_notify(status):
            return
        text = NOTIFY_TEMPLATES[status].format(
            document=job.get('document', '未知'), printer=job.get('printer_name', ''),
            pages=job.get('pages', 0), time=job.get('submitted_time', ''))
        for attempt in range(3):
            if not self.running or self._stop.is_set() or not self._should_notify(status):
                return
            target = self.config_mgr.get('wechat_notify_target', '')
            try:
                if target and self.bridge and self.bridge.is_available and self.bridge.send_message(target, text):
                    self.logger.info('通知发送操作已完成，未确认服务器投递: %s', status)
                    return
            except DeliveryUnknown as exc:
                self.logger.warning('通知投递结果未知，停止重试: %s', exc)
                return
            except Exception as exc:
                self.logger.warning('通知发送异常: %s', exc)
            if attempt < 2 and self._stop.wait(0.5 * (attempt + 1)):
                return
        self.logger.warning('通知重试已耗尽: %s', status)

    def _run_loop(self):
        try:
            while self.running and not self._stop.is_set():
                try:
                    job = self.notify_queue.get(timeout=0.2)
                    if job is not None:
                        self._deliver(job)
                except queue.Empty:
                    continue
                except Exception as exc:
                    self.logger.warning('忽略无效通知事件: %s', exc)
        finally:
            self.running = False
