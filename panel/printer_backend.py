"""面板用打印机只读快照；控制动作复用 printer.controller。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import threading
from typing import Callable, Optional

import win32print

from printer.controller import (
    PrintController, enumerate_jobs, printer_handle,
)
from printer.monitor import decode_status

# ---- 状态位 ----
PRINTER_STATUS_PAUSED = 0x00000001
PRINTER_STATUS_ERROR = 0x00000002
PRINTER_STATUS_PAPER_JAM = 0x00000008
PRINTER_STATUS_PAPER_OUT = 0x00000010
PRINTER_STATUS_PAPER_PROBLEM = 0x00000040
PRINTER_STATUS_OFFLINE = 0x00000080
PRINTER_STATUS_IO_ACTIVE = 0x00000100
PRINTER_STATUS_BUSY = 0x00000200
PRINTER_STATUS_PRINTING = 0x00000400
PRINTER_STATUS_OUTPUT_BIN_FULL = 0x00000800
PRINTER_STATUS_WAITING = 0x00002000
PRINTER_STATUS_PROCESSING = 0x00004000
PRINTER_STATUS_WARMING_UP = 0x00010000
PRINTER_STATUS_TONER_LOW = 0x00020000
PRINTER_STATUS_NO_TONER = 0x00040000
PRINTER_STATUS_USER_INTERVENTION = 0x00100000
PRINTER_STATUS_OUT_OF_MEMORY = 0x00200000
PRINTER_STATUS_DOOR_OPEN = 0x00400000
PRINTER_ATTRIBUTE_WORK_OFFLINE = 0x00000400

# (位, 文案, 级别)；越靠前优先级越高
PRINTER_STATUS_RULES = [
    (PRINTER_STATUS_PAPER_JAM, "卡纸", "error"),
    (PRINTER_STATUS_PAPER_OUT, "缺纸", "error"),
    (PRINTER_STATUS_DOOR_OPEN, "门未关", "error"),
    (PRINTER_STATUS_NO_TONER, "墨粉用尽", "error"),
    (PRINTER_STATUS_TONER_LOW, "墨粉不足", "warn"),
    (PRINTER_STATUS_USER_INTERVENTION, "需要人工处理", "error"),
    (PRINTER_STATUS_PAPER_PROBLEM, "纸张问题", "error"),
    (PRINTER_STATUS_OUTPUT_BIN_FULL, "出纸盒已满", "warn"),
    (PRINTER_STATUS_OUT_OF_MEMORY, "内存不足", "error"),
    (PRINTER_STATUS_ERROR, "错误", "error"),
    (PRINTER_STATUS_PAUSED, "已暂停", "warn"),
    (PRINTER_STATUS_PRINTING, "打印中", "info"),
    (PRINTER_STATUS_PROCESSING, "处理中", "info"),
    (PRINTER_STATUS_IO_ACTIVE, "打印中", "info"),
    (PRINTER_STATUS_WARMING_UP, "预热中", "info"),
    (PRINTER_STATUS_BUSY, "忙碌", "info"),
    (PRINTER_STATUS_WAITING, "等待中", "info"),
]

JOB_LABELS = {
    'pending': ('等待中', 'info'), 'cancelled': ('已取消', 'warn'),
    'cancelling': ('正在取消', 'warn'), 'error': ('错误', 'error'),
    'paperout': ('缺纸', 'error'), 'offline': ('脱机', 'warn'),
    'printed': ('已打印', 'ok'), 'paused': ('已暂停', 'warn'),
    'printing': ('打印中', 'info'), 'spooling': ('正在后台处理', 'info'),
    'sent': ('已发送至打印机', 'info'),
}

VIRTUAL_KEYWORDS = ("onenote", "pdf", "xps", "fax", "wps", "kingsoft", "document writer")
VIRTUAL_PORTS = ("portprompt:", "nul:", "shrFAX:", "kingsoft virtual printer port")

LEVEL_ORDER = {"ok": 0, "info": 1, "warn": 2, "error": 3}


@dataclass(frozen=True)
class JobInfo:
    job_id: int
    document: str
    status: str
    level: str
    pages: int
    owner: str
    submitted: str
    printer_name: str = ''


@dataclass(frozen=True)
class PrinterInfo:
    name: str
    port: str
    driver: str
    is_default: bool
    is_virtual: bool
    status_bits: int
    attributes: int
    offline: bool
    status: str
    level: str
    job_count: int
    available: bool = True


@dataclass(frozen=True)
class Snapshot:
    default_name: Optional[str] = None
    selected_name: Optional[str] = None
    printers: tuple[PrinterInfo, ...] = field(default_factory=tuple)
    jobs: tuple[JobInfo, ...] = field(default_factory=tuple)
    spooler_ok: bool = True
    error: Optional[str] = None
    jobs_fresh: bool = True


def describe_status(status_bits: int, attributes: int) -> tuple[str, str]:
    """返回 (文案, 级别)。离线优先，因为本机离线的 Status 可能为 0。"""
    if attributes & PRINTER_ATTRIBUTE_WORK_OFFLINE or status_bits & PRINTER_STATUS_OFFLINE:
        return "脱机", "warn"
    for bit, label, level in PRINTER_STATUS_RULES:
        if status_bits & bit:
            return label, level
    return "就绪", "ok"


def describe_job(status_bits: int) -> tuple[str, str]:
    return JOB_LABELS.get(decode_status(status_bits), ('等待中', 'info'))


def _is_virtual(name: str, port: str) -> bool:
    lower_name = name.lower()
    lower_port = (port or "").lower()
    return (
        any(word in lower_name for word in VIRTUAL_KEYWORDS)
        or any(lower_port.startswith(word) for word in VIRTUAL_PORTS)
    )


def _spooler_down(exc: BaseException) -> bool:
    code = getattr(exc, "winerror", None) or (exc.args[0] if exc.args else None)
    return code in (1722, 1723, 6, 1060)


class PrinterBackend:
    """所有方法都可在工作线程调用；控制动作复用 PrintController。"""

    def __init__(self, selected: Optional[str] = None,
                 selected_provider: Optional[Callable[[], Optional[str]]] = None) -> None:
        self.selected = selected
        self._selected_provider = selected_provider
        self._job_cache: dict[str, tuple[JobInfo, ...]] = {}
        self._lock = threading.RLock()
        self._selection_revision = 0

    def set_selected(self, name: Optional[str]) -> None:
        with self._lock:
            if name != self.selected:
                self.selected = name
                self._selection_revision += 1

    # ---- 只读快照 ----
    def snapshot(self) -> Snapshot:
        if self._selected_provider is not None:
            self.set_selected(self._selected_provider())
        with self._lock:
            requested = self.selected
            revision = self._selection_revision
        try:
            default_name = win32print.GetDefaultPrinter()
        except Exception:
            default_name = None

        try:
            raw = win32print.EnumPrinters(
                win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS,
                None, 2,
            )
        except Exception as exc:
            message = "打印服务未运行" if _spooler_down(exc) else f"无法枚举打印机：{exc}"
            return Snapshot(default_name=default_name, spooler_ok=False,
                            error=message, jobs_fresh=False)

        printers: list[PrinterInfo] = []
        for item in raw:
            name = item.get("pPrinterName", "")
            port = item.get("pPortName", "") or ""
            driver = item.get("pDriverName", "") or ""
            status_bits = 0
            attributes = int(item.get("Attributes", 0) or 0)
            job_count = int(item.get("cJobs", 0) or 0)
            available = True
            try:
                with printer_handle(name) as handle:
                    info = win32print.GetPrinter(handle, 2)
                status_bits = int(info.get("Status", 0) or 0)
                attributes = int(info.get("Attributes", attributes) or 0)
                job_count = int(info.get("cJobs", job_count) or 0)
            except Exception:
                available = False
            label, level = (describe_status(status_bits, attributes)
                            if available else ("不可用", "error"))
            printers.append(PrinterInfo(
                name=name, port=port, driver=driver,
                is_default=(name == default_name),
                is_virtual=_is_virtual(name, port),
                status_bits=status_bits, attributes=attributes,
                offline=bool(attributes & PRINTER_ATTRIBUTE_WORK_OFFLINE)
                        or bool(status_bits & PRINTER_STATUS_OFFLINE),
                status=label, level=level, job_count=job_count,
                available=available,
            ))

        printers.sort(key=lambda p: (p.is_virtual, not p.is_default, p.name.lower()))

        names = [p.name for p in printers]
        selected = requested if requested in names else (
            next((p.name for p in printers if not p.is_virtual), names[0] if names else None)
        )
        with self._lock:
            if revision == self._selection_revision:
                self.selected = selected
        jobs, queue_error = self._read_jobs(selected) if selected else ((), None)

        return Snapshot(
            default_name=default_name,
            selected_name=selected,
            printers=tuple(printers),
            jobs=jobs,
            spooler_ok=queue_error is None,
            error=queue_error or (None if printers else "未检测到打印机"),
            jobs_fresh=queue_error is None,
        )

    def _read_jobs(self, printer_name: str) -> tuple[tuple[JobInfo, ...], Optional[str]]:
        try:
            with printer_handle(printer_name) as handle:
                raw_jobs = enumerate_jobs(handle)
        except Exception:
            with self._lock:
                return self._job_cache.get(printer_name, ()), "读取打印队列失败，显示上次结果"

        jobs: list[JobInfo] = []
        for raw in raw_jobs:
            bits = int(raw.get("Status", 0) or 0)
            label, level = describe_job(bits)
            submitted = raw.get("Submitted")
            if isinstance(submitted, datetime):
                submitted_text = submitted.astimezone().strftime("%m-%d %H:%M")
            else:
                submitted_text = ""
            jobs.append(JobInfo(
                job_id=int(raw.get("JobId", 0) or 0),
                document=str(raw.get("pDocument") or "未知文档"),
                status=label, level=level,
                pages=int(raw.get("TotalPages", 0) or 0),
                owner=str(raw.get("pUserName") or ""),
                submitted=submitted_text,
                printer_name=printer_name,
            ))
        result = tuple(jobs)
        with self._lock:
            self._job_cache[printer_name] = result
        return result, None

    # ---- 控制动作：统一返回 (ok, 说明) ----
    @staticmethod
    def _result(ok: bool, label: str) -> tuple[bool, str]:
        if ok:
            return True, f"{label}已提交"
        return False, "操作失败，请检查设备状态或权限"

    def set_default(self, name: str) -> tuple[bool, str]:
        return self._result(PrintController.set_default_printer(name), "设置默认打印机")

    def pause_printer(self, name: str) -> tuple[bool, str]:
        return self._result(PrintController.pause_printer(name), "暂停打印")

    def resume_printer(self, name: str) -> tuple[bool, str]:
        return self._result(PrintController.resume_printer(name), "恢复打印")

    def purge_queue(self, name: str) -> tuple[bool, str]:
        return self._result(PrintController.purge_queue(name), "清空队列")

    def pause_job(self, name: str, job_id: int) -> tuple[bool, str]:
        return self._result(PrintController.pause_job(name, job_id), "暂停任务")

    def resume_job(self, name: str, job_id: int) -> tuple[bool, str]:
        return self._result(PrintController.resume_job(name, job_id), "恢复任务")

    def cancel_job(self, name: str, job_id: int) -> tuple[bool, str]:
        return self._result(PrintController.cancel_job(name, job_id), "取消任务")

    # ---- 打开系统界面 ----
    def open_printer_settings(self) -> tuple[bool, str]:
        return self._result(PrintController.open_printer_settings(), "打开打印机设置")

    def open_queue_window(self, name: str) -> tuple[bool, str]:
        return self._result(PrintController.open_queue_window(name), "打开打印队列")

    def open_properties(self, name: str) -> tuple[bool, str]:
        return self._result(PrintController.open_printer_properties(name), "打开打印机属性")
