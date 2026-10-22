"""打印队列控制与指定打印机的文件关联请求。"""
import os
from contextlib import contextmanager
import subprocess
from typing import Optional

import win32api
import win32print
import pythoncom


@contextmanager
def printer_handle(printer_name):
    """操作失败也释放本次打开的句柄。"""
    handle = win32print.OpenPrinter(printer_name)
    try:
        yield handle
    finally:
        win32print.ClosePrinter(handle)


def enumerate_jobs(handle):
    """一次读取完整队列，避免作业移位使分页偏移跳过活跃作业。"""
    # pywin32按实际pcbNeeded分配缓冲区，不按请求数量预分配。
    return list(win32print.EnumJobs(handle, 0, 0x7FFFFFFF, 2))


class PrintController:
    """每次控制操作独立打开句柄。"""

    @staticmethod
    def _control(printer_name, command, job_id=None):
        try:
            with printer_handle(printer_name) as handle:
                if job_id is None:
                    win32print.SetPrinter(handle, 0, None, command)
                else:
                    win32print.SetJob(handle, job_id, 0, None, command)
            return True
        except Exception:
            return False

    @staticmethod
    def pause_job(printer_name: str, job_id: int) -> bool:
        return PrintController._control(printer_name, win32print.JOB_CONTROL_PAUSE, job_id)

    @staticmethod
    def resume_job(printer_name: str, job_id: int) -> bool:
        return PrintController._control(printer_name, win32print.JOB_CONTROL_RESUME, job_id)

    @staticmethod
    def cancel_job(printer_name: str, job_id: int) -> bool:
        return PrintController._control(printer_name, win32print.JOB_CONTROL_CANCEL, job_id)

    @staticmethod
    def restart_job(printer_name: str, job_id: int) -> bool:
        return PrintController._control(printer_name, win32print.JOB_CONTROL_RESTART, job_id)

    @staticmethod
    def purge_queue(printer_name: str) -> bool:
        return PrintController._control(printer_name, win32print.PRINTER_CONTROL_PURGE)

    @staticmethod
    def pause_printer(printer_name: str) -> bool:
        return PrintController._control(printer_name, win32print.PRINTER_CONTROL_PAUSE)

    @staticmethod
    def resume_printer(printer_name: str) -> bool:
        return PrintController._control(printer_name, win32print.PRINTER_CONTROL_RESUME)

    @staticmethod
    def get_jobs(printer_name: str) -> list[dict]:
        with printer_handle(printer_name) as handle:
            return enumerate_jobs(handle)

    @staticmethod
    def print_file(filepath: str, printer_name: str) -> bool:
        """True仅代表关联程序请求已发起，不代表入队或纸张输出。"""
        if not isinstance(printer_name, str) or not printer_name.strip():
            return False
        if any(c in printer_name for c in ('"', '\r', '\n', '\0')):
            return False
        try:
            abs_path = os.path.abspath(filepath)
            if not os.path.isfile(abs_path):
                return False
            # printto的第二参数是设备名称；不回退默认打印机。
            pythoncom.CoInitialize()
            try:
                result = win32api.ShellExecute(
                    0, 'printto', abs_path, f'"{printer_name}"',
                    os.path.dirname(abs_path), 0,
                )
            finally:
                pythoncom.CoUninitialize()
            return result > 32
        except Exception:
            return False

    @staticmethod
    def set_default_printer(printer_name: str) -> bool:
        try:
            win32print.SetDefaultPrinter(printer_name)
            return True
        except Exception:
            return False

    @staticmethod
    def _spawn(args: list[str]) -> bool:
        try:
            subprocess.Popen(
                args, close_fds=True,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            )
            return True
        except Exception:
            return False

    @staticmethod
    def open_printer_settings() -> bool:
        return PrintController._spawn(['control.exe', 'printers'])

    @staticmethod
    def open_queue_window(printer_name: str) -> bool:
        return PrintController._spawn(
            ['rundll32.exe', 'printui.dll,PrintUIEntry', '/o', '/n', printer_name])

    @staticmethod
    def open_printer_properties(printer_name: str) -> bool:
        return PrintController._spawn(
            ['rundll32.exe', 'printui.dll,PrintUIEntry', '/p', '/n', printer_name])

    @staticmethod
    def get_default_printer() -> Optional[str]:
        try:
            return win32print.GetDefaultPrinter()
        except Exception:
            return None

    @staticmethod
    def auto_detect_printer() -> Optional[str]:
        """仅采用用户的系统默认设备，不静默选择其他设备。"""
        return PrintController.get_default_printer()

    @staticmethod
    def get_printer_info(printer_name: str) -> Optional[dict]:
        try:
            with printer_handle(printer_name) as handle:
                info = win32print.GetPrinter(handle, 2)
            return {
                'name': info.get('pPrinterName', printer_name),
                'status': info.get('Status', 0),
                'jobs_count': info.get('cJobs', 0),
                'attributes': info.get('Attributes', 0),
            }
        except Exception:
            return None

    @staticmethod
    def get_all_printers(include_virtual=False, *, strict=False) -> list[dict]:
        virtual_keywords = ('onenote', 'pdf', 'xps', 'fax', 'wps', 'kingsoft')
        flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
        printers = []
        try:
            for info in win32print.EnumPrinters(flags, None, 2):
                name = info.get('pPrinterName', '')
                if not include_virtual and any(word in name.lower() for word in virtual_keywords):
                    continue
                printers.append({
                    'name': name,
                    'driver': info.get('pDriverName', ''),
                    'status': info.get('Status', 0),
                    'is_default': bool(info.get('Attributes', 0) & win32print.PRINTER_ATTRIBUTE_DEFAULT),
                })
        except Exception:
            if strict:
                raise
        return printers
