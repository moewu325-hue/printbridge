"""打印队列完整快照轮询与可恢复状态跟踪。"""
import threading
from datetime import datetime
from typing import Optional

import win32print

from printer.controller import enumerate_jobs


STATUS_MAP = [
    (win32print.JOB_STATUS_DELETED, 'cancelled'),
    (win32print.JOB_STATUS_DELETING, 'cancelling'),
    (win32print.JOB_STATUS_ERROR, 'error'),
    (win32print.JOB_STATUS_PAPEROUT, 'paperout'),
    (win32print.JOB_STATUS_OFFLINE, 'offline'),
    (win32print.JOB_STATUS_PRINTED, 'printed'),
    (win32print.JOB_STATUS_PAUSED, 'paused'),
    (win32print.JOB_STATUS_PRINTING, 'printing'),
    (win32print.JOB_STATUS_SPOOLING, 'spooling'),
    (win32print.JOB_STATUS_COMPLETE, 'sent'),
]
FINAL_STATUSES = {'printed', 'cancelled', 'removed'}
NOTIFY_STATUSES = FINAL_STATUSES | {'error', 'paperout', 'offline'}
PRINTER_STATUS_MAP = [
    (win32print.PRINTER_STATUS_OFFLINE, '脱机'),
    (win32print.PRINTER_STATUS_ERROR, '错误'),
    (win32print.PRINTER_STATUS_PAPER_OUT, '缺纸'),
    (win32print.PRINTER_STATUS_PAPER_JAM, '卡纸'),
    (win32print.PRINTER_STATUS_PAUSED, '暂停'),
    (win32print.PRINTER_STATUS_USER_INTERVENTION, '需要人工处理'),
    (win32print.PRINTER_STATUS_PRINTING, '打印中'),
    (win32print.PRINTER_STATUS_BUSY, '忙碌'),
]


def decode_status(status_code: int) -> str:
    if not status_code:
        return 'spooling'
    for flag, text in STATUS_MAP:
        if status_code & flag:
            return text
    return 'unknown'


def parse_job(raw_job: dict, printer_name: str) -> dict:
    submitted = raw_job.get('Submitted')
    if submitted:
        try:
            submitted = submitted.strftime('%Y-%m-%d %H:%M:%S')
        except AttributeError:
            submitted = str(submitted)
    else:
        submitted = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    return {
        'job_id': raw_job.get('JobId', 0),
        'printer_name': printer_name,
        'document': raw_job.get('pDocument') or '未知文档',
        'status': decode_status(raw_job.get('Status', 0)),
        'pages': raw_job.get('TotalPages', 0) or 0,
        'submitted_time': submitted,
        'owner': raw_job.get('pUserName') or '',
    }


class PrintMonitor:
    """工作线程独占Win32句柄；外部请求只更新代际和唤醒事件。"""

    def __init__(self, printer_name: str, poll_interval: float,
                 job_queue, notify_queue, db, logger):
        self.printer_name = printer_name
        self.poll_interval = poll_interval
        self.job_queue = job_queue
        self.notify_queue = notify_queue
        self.db = db
        self.logger = logger
        self.running = False
        self.printer_status = '未连接'
        self._h_printer = None
        self._handle_generation = -1
        self._known_jobs: dict[int, dict] = {}
        self._printer_jobs = {printer_name: self._known_jobs}
        self._pending_jobs = {}
        self._displayed_records = set()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._generation = 0
        self._auto_switch_callback = None

    def start(self):
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._wake_event.clear()
            self.running = True
            self._thread = threading.Thread(target=self._poll_loop,
                                            name='PrintMonitor', daemon=True)
            self._thread.start()
        self.logger.info(f'打印监控已启动: {self.printer_name}')

    def stop(self):
        with self._lock:
            self._stop_event.set()
            self._wake_event.set()
            worker = self._thread
            self.printer_status = '正在停止'
        if worker and worker is not threading.current_thread():
            worker.join(timeout=5)
            if worker.is_alive():
                self.logger.warning('打印驱动调用尚未返回，等待工作线程自行释放句柄')
        elif worker is None:
            self._close_printer()
            self.running = False
            self.printer_status = '已停止'
        return worker is None or not worker.is_alive()

    def request_refresh(self):
        self._wake_event.set()

    def set_auto_switch_callback(self, callback):
        """保留旧调用接口，连接失败不会更换用户选择。"""
        self._auto_switch_callback = callback

    def _close_printer(self):
        handle = self._h_printer
        self._h_printer = None
        self._handle_generation = -1
        if handle is not None:
            try:
                win32print.ClosePrinter(handle)
            except Exception:
                self.logger.exception('关闭打印机句柄失败')

    def _poll_loop(self):
        try:
            while not self._stop_event.is_set():
                self._wake_event.clear()
                failed = False
                try:
                    self._poll_once()
                except Exception:
                    failed = True
                    self._close_printer()
                    with self._lock:
                        self.printer_status = '连接或读取失败，等待重试'
                    self.logger.exception('打印队列轮询失败')
                if self._stop_event.is_set():
                    break
                interval = 3 if failed else max(0.1, float(self.poll_interval))
                self._wake_event.wait(interval)
        finally:
            self._close_printer()
            with self._lock:
                self.running = False
                self.printer_status = '已停止'

    def _poll_once(self):
        with self._lock:
            self._flush_pending()
            printer_name, generation = self.printer_name, self._generation
        if self._handle_generation != generation:
            self._close_printer()
        if self._h_printer is None:
            self._h_printer = win32print.OpenPrinter(printer_name)
            self._handle_generation = generation
        raw_jobs = enumerate_jobs(self._h_printer)
        info = win32print.GetPrinter(self._h_printer, 2)
        with self._lock:
            # 切换或停止后，旧设备的结果不能进入当前队列和历史。
            if generation != self._generation or self._stop_event.is_set():
                return
            status = info.get('Status', 0)
            labels = [text for flag, text in PRINTER_STATUS_MAP if status & flag]
            self.printer_status = ' / '.join(labels) if labels else ('就绪' if not status else f'状态码 {status}')
            current_ids = set()
            for raw in raw_jobs:
                job = parse_job(raw, printer_name)
                jid = job['job_id']
                current_ids.add(jid)
                previous = self._known_jobs.get(jid)
                if previous and not raw.get('Submitted'):
                    job['submitted_time'] = previous['submitted_time']
                if previous and (previous['submitted_time'], previous['document']) != (job['submitted_time'], job['document']):
                    self._remove_job(previous)
                    self._known_jobs.pop(jid, None)
                    previous = None
                if previous is None:
                    job['record_id'] = None
                    self._known_jobs[jid] = job
                    self._stage('NEW', job)
                else:
                    job['record_id'] = previous['record_id']
                    # 同一提交身份的明确终态不会因驱动清零而倒退。
                    if previous['status'] in FINAL_STATUSES:
                        job['status'] = previous['status']
                    if previous['status'] != job['status']:
                        self._known_jobs[jid] = job
                        self._stage('UPDATE', job)
                    elif previous != job or job['record_id'] not in self._displayed_records:
                        self._known_jobs[jid] = job
                        self._publish('UPDATE', job)
            self._flush_pending()
            for jid in set(self._known_jobs) - current_ids:
                self._remove_job(self._known_jobs[jid])
                self._known_jobs.pop(jid, None)

    @staticmethod
    def _identity(job):
        return (job['printer_name'], job['job_id'], job['submitted_time'], job['document'])

    def _stage(self, action, job):
        # 先保存观察证据，数据库失败时下一轮重试同一状态。
        self._pending_jobs[self._identity(job)] = (action, job)

    def _flush_pending(self):
        for identity, (action, job) in list(self._pending_jobs.items()):
            if job['record_id'] is None:
                job['record_id'] = self.db.add_job(
                    job_id=job['job_id'], printer_name=job['printer_name'],
                    document_name=job['document'], owner=job['owner'],
                    pages=job['pages'], status=job['status'],
                    submitted_time=job['submitted_time'],
                )
            else:
                self._persist(job)
            del self._pending_jobs[identity]
            self._publish(action, job)
            self._notify(job)
            if action == 'REMOVE':
                cache = self._printer_jobs[job['printer_name']]
                previous = cache.get(job['job_id'])
                if previous and self._identity(previous) == identity:
                    cache.pop(job['job_id'])

    def _publish(self, action, job):
        if job['printer_name'] != self.printer_name:
            return
        record_id = job['record_id']
        if action == 'REMOVE':
            self._displayed_records.discard(record_id)
        elif record_id not in self._displayed_records:
            action = 'NEW'
            self._displayed_records.add(record_id)
        self.job_queue.put((action, dict(job)))

    def _persist(self, job):
        self.db.update_job_status(job['job_id'], job['status'],
                                  printer_name=job['printer_name'], record_id=job['record_id'])

    def _notify(self, job):
        if job['status'] in NOTIFY_STATUSES:
            self.notify_queue.put(dict(job))

    def _remove_job(self, previous):
        job = dict(previous)
        if job['status'] not in FINAL_STATUSES:
            job['status'] = 'cancelled' if job['status'] == 'cancelling' else 'removed'
            self._stage('REMOVE', job)
            self._flush_pending()
        else:
            self._publish('REMOVE', job)

    def get_status_summary(self) -> dict:
        with self._lock:
            return {
                'printer': self.printer_name,
                'status': self.printer_status,
                'active_jobs': sum(job['status'] not in FINAL_STATUSES for job in self._known_jobs.values()),
                'jobs': [dict(job) for job in self._known_jobs.values()],
            }

    def switch_printer(self, new_printer: str):
        with self._lock:
            if new_printer == self.printer_name:
                return
            self._generation += 1
            self.printer_name = new_printer
            self._known_jobs = self._printer_jobs.setdefault(new_printer, {})
            self._displayed_records.clear()
            self.printer_status = '正在连接'
            self.job_queue.put(('RESET', None))
            self._wake_event.set()
        self.logger.info(f'切换打印机: {new_printer}')
