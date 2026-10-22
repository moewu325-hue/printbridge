"""后台轮询线程、状态防抖与动作线程。"""
from __future__ import annotations

from dataclasses import replace
import logging
from queue import Empty, Full, Queue
import threading
import time
from typing import Any, Callable, Optional

from .printer_backend import JobInfo, LEVEL_ORDER, PrinterBackend, PrinterInfo, Snapshot


def _publish(events: Queue, event: tuple) -> None:
    try:
        events.put_nowait(event)
    except Full:
        try:
            events.get_nowait()
        except Empty:
            pass
        try:
            events.put_nowait(event)
        except Full:
            pass


def _join_threads(threads: tuple[threading.Thread, ...], timeout: float) -> None:
    deadline = time.monotonic() + max(0, timeout)
    for thread in threads:
        if thread is not threading.current_thread():
            thread.join(max(0, deadline - time.monotonic()))


class StatusDebouncer:
    """报警立即进入；恢复需连续两次采样，抑制驱动状态抖动。"""

    def __init__(self, confirm: int = 2) -> None:
        self._confirm = confirm
        self._applied: dict[str, tuple[str, str]] = {}
        self._pending: dict[str, tuple[str, str, int]] = {}

    def apply(self, key: str, label: str, level: str) -> tuple[str, str]:
        prev = self._applied.get(key)
        if prev is None:
            self._applied[key] = (label, level)
            return label, level
        if prev == (label, level):
            self._pending.pop(key, None)
            return prev
        if LEVEL_ORDER.get(level, 0) > LEVEL_ORDER.get(prev[1], 0):
            self._applied[key] = (label, level)
            self._pending.pop(key, None)
            return label, level
        pending = self._pending.get(key)
        count = pending[2] + 1 if pending and pending[:2] == (label, level) else 1
        if count >= self._confirm:
            self._applied[key] = (label, level)
            self._pending.pop(key, None)
            return label, level
        self._pending[key] = (label, level, count)
        return prev

    def forget(self, key: str) -> None:
        self._applied.pop(key, None)
        self._pending.pop(key, None)


class JobTracker:
    """作业消失需连续两轮确认，避免 EnumJobs 抖动导致闪没。"""

    def __init__(self) -> None:
        self._known: dict[str, dict[int, JobInfo]] = {}
        self._missing: dict[str, dict[int, int]] = {}
        self._lock = threading.Lock()

    def merge(self, printer: str, jobs: tuple[JobInfo, ...]) -> tuple[JobInfo, ...]:
        with self._lock:
            previous = self._known.setdefault(printer, {})
            missing = self._missing.setdefault(printer, {})
            current = {job.job_id: job for job in jobs}
            merged = dict(current)
            for job_id, job in previous.items():
                if job_id in current:
                    missing.pop(job_id, None)
                    continue
                count = missing.get(job_id, 0) + 1
                if count < 2:
                    missing[job_id] = count
                    merged[job_id] = job
                else:
                    missing.pop(job_id, None)
            for job_id in list(missing):
                if job_id in current:
                    missing.pop(job_id, None)
            self._known[printer] = merged
            return tuple(sorted(merged.values(), key=lambda job: job.job_id))

    def clear(self, printer: str) -> None:
        with self._lock:
            self._known[printer] = {}
            self._missing[printer] = {}


class PrinterMonitor:
    """单工作线程轮询整份快照，结果经队列回 Tk 主线程。"""

    def __init__(self, backend: PrinterBackend, events: Queue,
                 interval: float = 2.0, logger: Optional[logging.Logger] = None) -> None:
        self._backend = backend
        self._events = events
        self._interval = max(1.0, float(interval))
        self._logger = logger or logging.getLogger("PrinterPanel")
        self._debouncer = StatusDebouncer()
        self._jobs = JobTracker()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last: Optional[Snapshot] = None
        self._lock = threading.RLock()
        self._generation = 0
        self._selection_revision = 0
        self._threads: list[threading.Thread] = []
        self._desired = False

    def start(self) -> None:
        with self._lock:
            if self._desired:
                return
            self._desired = True
            self._generation += 1
            self._stop, self._wake = threading.Event(), threading.Event()
            if self._thread is None or not self._thread.is_alive():
                self._launch()

    def _launch(self) -> None:
        self._thread = threading.Thread(target=self._loop,
            args=(self._generation, self._stop, self._wake), name="PanelMonitor", daemon=True)
        self._threads = [thread for thread in self._threads if thread.is_alive()]
        self._threads.append(self._thread)
        self._thread.start()

    def stop(self) -> None:
        with self._lock:
            self._desired = False
            self._stop.set()
            self._wake.set()

    def join(self, timeout: float = 3) -> None:
        with self._lock:
            threads = tuple(self._threads)
        _join_threads(threads, timeout)

    def request_refresh(self) -> None:
        self._wake.set()

    def set_selected(self, name: Optional[str]) -> None:
        with self._lock:
            self._selection_revision += 1
            self._backend.set_selected(name)
            self.request_refresh()

    def clear_jobs(self, printer: str) -> None:
        self._jobs.clear(printer)

    def _loop(self, generation: int, stop: threading.Event, wake: threading.Event) -> None:
        try:
            self._poll(generation, stop, wake)
        finally:
            with self._lock:
                self._thread = None
                if self._desired and generation != self._generation:
                    self._launch()

    def _poll(self, generation: int, stop: threading.Event, wake: threading.Event) -> None:
        while not stop.is_set():
            wake.clear()
            with self._lock:
                revision = self._selection_revision
            failed = False
            try:
                snapshot = self._backend.snapshot()
            except Exception:
                failed = True
                self._logger.exception("打印机轮询失败")
                snapshot = None
            with self._lock:
                if stop.is_set() or generation != self._generation:
                    return
                if revision != self._selection_revision:
                    continue
                snapshot = self._failure_snapshot() if failed else self._process(snapshot)
                self._last = snapshot
                _publish(self._events, ("snapshot", snapshot))
            wake.wait(3.0 if failed else self._interval)

    def _failure_snapshot(self) -> Snapshot:
        last = self._last
        if last is None:
            return Snapshot(spooler_ok=False, jobs_fresh=False, error="读取打印机状态失败")
        return replace(last, spooler_ok=False, jobs_fresh=False, error="读取打印机状态失败，正在重试")

    def _process(self, snapshot: Snapshot) -> Snapshot:
        printers: list[PrinterInfo] = []
        for printer in snapshot.printers:
            label, level = self._debouncer.apply(printer.name, printer.status, printer.level)
            printers.append(replace(printer, status=label, level=level))

        selected = snapshot.selected_name
        jobs = snapshot.jobs
        if selected and snapshot.jobs_fresh:
            jobs = self._jobs.merge(selected, jobs)
        return replace(snapshot, printers=tuple(printers), jobs=jobs)


class CommandWorker:
    """控制动作串行执行，避免 spooler 阻塞 Tk 主线程。"""

    def __init__(self, events: Queue, logger: Optional[logging.Logger] = None) -> None:
        self._events = events
        self._logger = logger or logging.getLogger("PrinterPanel")
        self._tasks: Queue[tuple[str, Callable[..., Any], tuple, Optional[Callable[[], None]]]] = Queue()
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self._thread: Optional[threading.Thread] = None
        self._threads: list[threading.Thread] = []
        self._generation = 0
        self._desired = False
        self.start()

    def start(self) -> None:
        with self._lock:
            if self._desired:
                return
            self._desired = True
            self._generation += 1
            self._stop = threading.Event()
            self._tasks = Queue(maxsize=128)
            if self._thread is None or not self._thread.is_alive():
                self._launch()

    def _launch(self) -> None:
        self._thread = threading.Thread(target=self._loop,
            args=(self._generation, self._stop, self._tasks), name="PanelCommand", daemon=True)
        self._threads = [thread for thread in self._threads if thread.is_alive()]
        self._threads.append(self._thread)
        self._thread.start()

    def submit(self, label: str, func: Callable[..., Any], *args: Any,
               after: Optional[Callable[[], None]] = None) -> bool:
        with self._lock:
            if self._stop.is_set():
                return False
            try:
                self._tasks.put_nowait((label, func, args, after))
            except Full:
                _publish(self._events, ('action', False, '操作队列已满，请稍后重试'))
                return False
            return True

    def stop(self) -> None:
        with self._lock:
            self._desired = False
            self._stop.set()
            while True:
                try:
                    self._tasks.get_nowait()
                except Empty:
                    break
            self._tasks.put_nowait(("", lambda: (True, ""), (), None))

    def join(self, timeout: float = 2) -> None:
        with self._lock:
            threads = tuple(self._threads)
        _join_threads(threads, timeout)

    def _loop(self, generation: int, stop: threading.Event, tasks: Queue) -> None:
        try:
            self._consume(generation, stop, tasks)
        finally:
            with self._lock:
                self._thread = None
                if self._desired and generation != self._generation:
                    self._launch()

    def _consume(self, generation: int, stop: threading.Event, tasks: Queue) -> None:
        while not stop.is_set():
            try:
                label, func, args, after = tasks.get(timeout=0.5)
            except Empty:
                continue
            with self._lock:
                if not label or stop.is_set() or generation != self._generation:
                    return
            try:
                result = func(*args)
                if isinstance(result, tuple) and len(result) == 2:
                    ok, message = bool(result[0]), str(result[1])
                else:
                    ok, message = bool(result), ""
                with self._lock:
                    if stop.is_set() or generation != self._generation:
                        return
                    if ok and after is not None:
                        after()
                    _publish(self._events, ("action", ok, f"{label}：{message}" if message else label))
            except Exception as exc:
                self._logger.exception("动作执行失败: %s", label)
                with self._lock:
                    if not stop.is_set() and generation == self._generation:
                        _publish(self._events, ("action", False, f"{label}失败：{exc}"))
