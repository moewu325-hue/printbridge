"""打印监控核心: EnumJobs轮询 + 状态解码 + 变更检测"""
import time
import threading
import win32print
import win32api
from datetime import datetime
from typing import Optional


# 状态位掩码 → 可读状态 (优先级从高到低)
STATUS_MAP = [
    (win32print.JOB_STATUS_ERROR, "error"),
    (win32print.JOB_STATUS_PAPEROUT, "paperout"),
    (win32print.JOB_STATUS_OFFLINE, "offline"),
    (win32print.JOB_STATUS_DELETING, "cancelling"),
    (win32print.JOB_STATUS_PAUSED, "paused"),
    (win32print.JOB_STATUS_PRINTING, "printing"),
    (win32print.JOB_STATUS_SPOOLING, "spooling"),
    (win32print.JOB_STATUS_PRINTED, "printed"),
]

# 终态: 作业已结束, 可触发通知
FINAL_STATUSES = {"printed", "error", "paperout", "offline", "cancelled"}


def decode_status(status_code: int) -> str:
    """将win32print Status位掩码解码为可读状态字符串"""
    if status_code == 0:
        return "spooling"
    for flag, text in STATUS_MAP:
        if status_code & flag:
            return text
    return "unknown"


def parse_job(raw_job: dict, printer_name: str) -> dict:
    """将EnumJobs原始字典转为标准PrintJob字典"""
    submitted = raw_job.get("Submitted")
    if submitted:
        try:
            submitted_str = submitted.strftime("%Y-%m-%d %H:%M:%S")
        except AttributeError:
            submitted_str = str(submitted)
    else:
        submitted_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    return {
        "job_id": raw_job.get("JobId", 0),
        "printer_name": printer_name,
        "document": raw_job.get("pDocument", "未知文档"),
        "status": decode_status(raw_job.get("Status", 0)),
        "pages": raw_job.get("TotalPages", 0) or 0,
        "submitted_time": submitted_str,
        "owner": raw_job.get("pUserName", ""),
    }


class PrintMonitor:
    """打印队列监控器: daemon线程轮询EnumJobs"""

    def __init__(self, printer_name: str, poll_interval: float,
                 job_queue, notify_queue, db, logger):
        self.printer_name = printer_name
        self.poll_interval = poll_interval
        self.job_queue = job_queue       # SafeQueue → GUI
        self.notify_queue = notify_queue  # SafeQueue → Notifier
        self.db = db
        self.logger = logger
        self.running = False
        self._h_printer = None
        self._known_jobs: dict[int, dict] = {}  # job_id → job dict
        self._thread: Optional[threading.Thread] = None
        self.printer_status = "就绪"
        self._fail_count = 0
        self._auto_switch_callback = None  # 自动切换回调, 通知config_mgr更新

    def start(self):
        """启动监控线程"""
        self.running = True
        self._thread = threading.Thread(
            target=self._poll_loop, name="PrintMonitor", daemon=True
        )
        self._thread.start()
        self.logger.info(f"打印监控已启动: {self.printer_name} (间隔{self.poll_interval}s)")

    def stop(self):
        """停止监控"""
        self.running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        self._close_printer()
        self.logger.info("打印监控已停止")

    def _open_printer(self) -> bool:
        """打开打印机句柄, 连续失败3次尝试自动切换"""
        if self._h_printer:
            return True
        try:
            self._h_printer = win32print.OpenPrinter(self.printer_name)
            self._fail_count = 0  # 成功, 重置计数
            return True
        except Exception as e:
            self._fail_count += 1
            self.logger.warning(f"无法打开打印机 {self.printer_name}: {e} (失败{self._fail_count}次)")
            self.printer_status = f"离线: {e}"

            # 连续失败3次 → 尝试自动切换
            if self._fail_count >= 3:
                self._try_auto_switch()
            return False

    def _try_auto_switch(self):
        """尝试自动切换到其他可用打印机"""
        try:
            from printer.controller import PrintController
            printers = PrintController.get_all_printers()
            # 找一个不是当前打印机且能打开的
            for p in printers:
                if p["name"] == self.printer_name:
                    continue
                try:
                    h = win32print.OpenPrinter(p["name"])
                    win32print.ClosePrinter(h)
                    # 可用! 切换
                    old_name = self.printer_name
                    self.switch_printer(p["name"])
                    self.logger.info(f"自动切换打印机: {old_name} → {p['name']}")
                    if self._auto_switch_callback:
                        self._auto_switch_callback(p["name"])
                    return
                except Exception:
                    continue
            self.logger.warning("未找到可用的替代打印机")
        except Exception as e:
            self.logger.error(f"自动切换失败: {e}")

    def set_auto_switch_callback(self, callback):
        """设置自动切换回调, 用于通知config_mgr更新配置"""
        self._auto_switch_callback = callback

    def _close_printer(self):
        """关闭打印机句柄"""
        if self._h_printer:
            try:
                win32print.ClosePrinter(self._h_printer)
            except Exception:
                pass
            self._h_printer = None

    def _poll_loop(self):
        """主轮询循环(运行在daemon线程)"""
        while self.running:
            if not self._open_printer():
                time.sleep(5)  # 重试间隔
                continue

            try:
                # EnumJobs: Level 2 = 详细信息 (JOB_INFO_2)
                raw_jobs = win32print.EnumJobs(self._h_printer, 0, 99, 2)
            except win32print.error as e:
                self.logger.error(f"EnumJobs失败: {e}, 尝试重连...")
                self._close_printer()
                time.sleep(3)
                continue

            current_ids = set()

            for raw in raw_jobs:
                job = parse_job(raw, self.printer_name)
                jid = job["job_id"]
                current_ids.add(jid)

                if jid not in self._known_jobs:
                    # 新作业
                    self._known_jobs[jid] = job
                    self.job_queue.put(("NEW", job))
                    self.db.add_job(
                        job_id=jid, printer_name=self.printer_name,
                        document_name=job["document"], owner=job.get("owner", ""),
                        pages=job.get("pages", 0), status=job["status"],
                        submitted_time=job["submitted_time"],
                    )
                    self.logger.info(f"新作业 #{jid}: {job['document']} [{job['status']}]")

                elif self._known_jobs[jid]["status"] != job["status"]:
                    # 状态变更
                    old_status = self._known_jobs[jid]["status"]
                    self._known_jobs[jid] = job
                    self.job_queue.put(("UPDATE", job))
                    self.db.update_job_status(jid, job["status"])
                    self.logger.info(f"作业 #{jid}: {old_status} → {job['status']}")

                    # 终态 → 通知
                    if job["status"] in FINAL_STATUSES:
                        self.notify_queue.put(job)

            # 检测消失的作业(已从队列移除)
            gone = set(self._known_jobs.keys()) - current_ids
            for jid in gone:
                job = self._known_jobs.pop(jid)
                # 标记为已完成(如果没有明确终态)
                if job["status"] not in FINAL_STATUSES:
                    job["status"] = "printed"
                self.job_queue.put(("REMOVE", job))
                self.db.update_job_status(jid, "printed")
                self.notify_queue.put(job)
                self.logger.info(f"作业 #{jid} 已完成: {job['document']}")

            # 更新打印机状态
            self.printer_status = "就绪" if self._h_printer else "离线"
            time.sleep(self.poll_interval)

    def get_status_summary(self) -> dict:
        """获取打印机状态摘要"""
        return {
            "printer": self.printer_name,
            "status": self.printer_status,
            "active_jobs": len(self._known_jobs),
            "jobs": list(self._known_jobs.values()),
        }

    def switch_printer(self, new_printer: str):
        """切换监控的打印机"""
        self._close_printer()
        self._known_jobs.clear()
        self.printer_name = new_printer
        self.logger.info(f"切换打印机: {new_printer}")
