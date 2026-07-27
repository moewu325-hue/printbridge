"""打印控制: 暂停/恢复/取消作业, 清空队列, 提交文件打印"""
import os
import win32print
import win32api
from typing import Optional


class PrintController:
    """打印控制器 — 所有操作线程安全(通过win32print句柄隔离)"""

    @staticmethod
    def pause_job(printer_name: str, job_id: int) -> bool:
        """暂停单个作业"""
        try:
            h_printer = win32print.OpenPrinter(printer_name)
            win32print.SetJob(h_printer, job_id, 0, None, win32print.JOB_CONTROL_PAUSE)
            win32print.ClosePrinter(h_printer)
            return True
        except Exception:
            return False

    @staticmethod
    def resume_job(printer_name: str, job_id: int) -> bool:
        """恢复单个作业"""
        try:
            h_printer = win32print.OpenPrinter(printer_name)
            win32print.SetJob(h_printer, job_id, 0, None, win32print.JOB_CONTROL_RESUME)
            win32print.ClosePrinter(h_printer)
            return True
        except Exception:
            return False

    @staticmethod
    def cancel_job(printer_name: str, job_id: int) -> bool:
        """取消单个作业"""
        try:
            h_printer = win32print.OpenPrinter(printer_name)
            win32print.SetJob(h_printer, job_id, 0, None, win32print.JOB_CONTROL_CANCEL)
            win32print.ClosePrinter(h_printer)
            return True
        except Exception:
            return False

    @staticmethod
    def restart_job(printer_name: str, job_id: int) -> bool:
        """重新启动作业"""
        try:
            h_printer = win32print.OpenPrinter(printer_name)
            win32print.SetJob(h_printer, job_id, 0, None, win32print.JOB_CONTROL_RESTART)
            win32print.ClosePrinter(h_printer)
            return True
        except Exception:
            return False

    @staticmethod
    def purge_queue(printer_name: str) -> bool:
        """清空整个打印队列"""
        try:
            h_printer = win32print.OpenPrinter(printer_name)
            win32print.SetPrinter(h_printer, 0, None, win32print.PRINTER_CONTROL_PURGE)
            win32print.ClosePrinter(h_printer)
            return True
        except Exception:
            return False

    @staticmethod
    def pause_printer(printer_name: str) -> bool:
        """暂停整个打印机"""
        try:
            h_printer = win32print.OpenPrinter(printer_name)
            win32print.SetPrinter(h_printer, 0, None, win32print.PRINTER_CONTROL_PAUSE)
            win32print.ClosePrinter(h_printer)
            return True
        except Exception:
            return False

    @staticmethod
    def resume_printer(printer_name: str) -> bool:
        """恢复整个打印机"""
        try:
            h_printer = win32print.OpenPrinter(printer_name)
            win32print.SetPrinter(h_printer, 0, None, win32print.PRINTER_CONTROL_RESUME)
            win32print.ClosePrinter(h_printer)
            return True
        except Exception:
            return False

    @staticmethod
    def print_file(filepath: str, printer_name: str) -> bool:
        """通过ShellExecute提交文件到打印机"""
        abs_path = os.path.abspath(filepath)
        if not os.path.exists(abs_path):
            return False
        try:
            win32api.ShellExecute(
                0,          # hwnd
                "print",    # operation
                abs_path,   # file
                f'/d:"{printer_name}"',  # parameters
                ".",        # directory
                0,          # show command (SW_HIDE)
            )
            return True
        except Exception:
            return False

    @staticmethod
    def get_default_printer() -> Optional[str]:
        """获取Windows默认打印机名称"""
        try:
            return win32print.GetDefaultPrinter()
        except Exception:
            return None

    @staticmethod
    def auto_detect_printer() -> Optional[str]:
        """自动检测最佳可用打印机: 默认打印机 > 首个实体打印机"""
        # 优先系统默认打印机
        default = PrintController.get_default_printer()
        if default:
            VIRTUAL_KEYWORDS = ["oneNote", "pdf", "xps", "fax", "wps", "kingsoft"]
            if not any(kw.lower() in default.lower() for kw in VIRTUAL_KEYWORDS):
                return default

        # 回退: 第一个非虚拟打印机
        printers = PrintController.get_all_printers()
        if printers:
            return printers[0]["name"]

        return None

    @staticmethod
    def get_printer_info(printer_name: str) -> Optional[dict]:
        """获取打印机详细信息"""
        try:
            h_printer = win32print.OpenPrinter(printer_name)
            info = win32print.GetPrinter(h_printer, 2)
            win32print.ClosePrinter(h_printer)
            return {
                "name": info.get("pPrinterName", printer_name),
                "status": info.get("Status", 0),
                "jobs_count": info.get("cJobs", 0),
                "attributes": info.get("Attributes", 0),
            }
        except Exception:
            return None

    @staticmethod
    def get_all_printers() -> list[dict]:
        """枚举所有打印机, 过滤虚拟打印机"""
        # 排除关键词
        VIRTUAL_KEYWORDS = ["oneNote", "pdf", "xps", "fax", "wps", "kingsoft"]
        flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
        printers = []
        try:
            for info in win32print.EnumPrinters(flags, None, 2):
                name = info.get("pPrinterName", "")
                # 过滤虚拟打印机
                if any(kw.lower() in name.lower() for kw in VIRTUAL_KEYWORDS):
                    continue
                printers.append({
                    "name": name,
                    "driver": info.get("pDriverName", ""),
                    "status": info.get("Status", 0),
                    "is_default": bool(info.get("Attributes", 0) & win32print.PRINTER_ATTRIBUTE_DEFAULT),
                })
        except Exception:
            pass
        return printers
