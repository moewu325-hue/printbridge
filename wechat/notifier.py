"""通知推送: 打印状态变更 → 微信消息"""
import threading
import queue


# 通知模板
NOTIFY_TEMPLATES = {
    "printed": "✅ [打印完成] {document}\n打印机: {printer}\n页数: {pages}\n时间: {time}",
    "error": "❌ [打印失败] {document}\n打印机: {printer}\n请检查打印机状态",
    "paperout": "📄 [缺纸] 打印机 {printer} 纸张不足\n请添加纸张后继续",
    "offline": "🔌 [脱机] 打印机 {printer} 已断开连接\n请检查连接后重试",
    "cancelled": "🚫 [已取消] {document}\n作业已被取消",
}


class Notifier:
    """通知推送器: 消费notify_queue, 通过微信发送通知"""

    def __init__(self, bridge, config_mgr, notify_queue, logger):
        self.bridge = bridge          # WeChatBridge实例
        self.config_mgr = config_mgr
        self.notify_queue = notify_queue  # SafeQueue
        self.logger = logger
        self.running = False
        self._thread = None

    def start(self):
        """启动通知线程"""
        self.running = True
        self._thread = threading.Thread(
            target=self._run_loop, name="Notifier", daemon=True
        )
        self._thread.start()
        self.logger.info("通知推送已启动")

    def stop(self):
        """停止通知"""
        self.running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)

    def _run_loop(self):
        """主循环: 阻塞等待通知事件"""
        while self.running:
            try:
                # 阻塞等待, 超时1秒以检查running标志
                job = self.notify_queue.get(timeout=1.0)
            except queue.Empty:
                continue

            if job is None:
                continue

            status = job.get("status", "")
            # 检查是否应该通知
            if status == "printed" and not self.config_mgr.get("notify_on_complete", True):
                continue
            if status in ("error", "cancelled") and not self.config_mgr.get("notify_on_failure", True):
                continue
            if status in ("paperout", "offline") and not self.config_mgr.get("notify_on_error", True):
                continue

            # 生成通知文本
            template = NOTIFY_TEMPLATES.get(status)
            if not template:
                template = "[{status}] {document}"

            text = template.format(
                document=job.get("document", "未知"),
                printer=job.get("printer_name", ""),
                pages=job.get("pages", 0),
                status=status,
                time=job.get("submitted_time", ""),
            )

            # 发送微信通知
            target = self.config_mgr.get("wechat_notify_target", "文件传输助手")
            if self.bridge and self.bridge.is_available:
                success = self.bridge.send_message(target, text)
                if success:
                    self.logger.info(f"通知已发送: {status} — {job.get('document', '')}")
                else:
                    self.logger.warning(f"通知发送失败: {status}")
            else:
                self.logger.debug(f"微信不可用, 通知丢弃: {status} — {job.get('document', '')}")
