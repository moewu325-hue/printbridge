"""工具函数: 日志、线程安全队列、文件类型判断、单实例锁"""
import logging
import os
import socket
import sys
import queue
from pathlib import Path


def setup_logger(name: str = "PrintBridge", log_file: str = None) -> logging.Logger:
    """配置日志: 控制台输出 + 可选文件输出"""
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)

    if logger.handlers:
        return logger

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(threadName)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # 控制台
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    # 文件
    if log_file:
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(fmt)
        logger.addHandler(fh)

    return logger


class SafeQueue:
    """线程安全队列的薄封装, 带超时get和put"""

    def __init__(self, maxsize: int = 0):
        self._q = queue.Queue(maxsize=maxsize)

    def put(self, item, timeout: float = 2.0) -> bool:
        try:
            self._q.put(item, timeout=timeout)
            return True
        except queue.Full:
            return False

    def get(self, timeout: float = None):
        try:
            return self._q.get(timeout=timeout)
        except queue.Empty:
            return None

    def get_nowait(self):
        try:
            return self._q.get_nowait()
        except queue.Empty:
            return None

    def empty(self) -> bool:
        return self._q.empty()

    @property
    def raw(self) -> queue.Queue:
        return self._q


PRINTABLE_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".txt", ".csv", ".rtf",
    ".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tiff",
}


def is_printable_file(filepath: str) -> bool:
    """检查文件是否为可打印类型"""
    ext = Path(filepath).suffix.lower()
    return ext in PRINTABLE_EXTENSIONS


def safe_filename(filename: str) -> str:
    """去除文件名中的非法字符"""
    illegal = '<>:"/\\|?*'
    for c in illegal:
        filename = filename.replace(c, "_")
    return filename


def human_readable_size(size_bytes: int) -> str:
    """字节数 → 人类可读大小"""
    for unit in ("B", "KB", "MB", "GB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


def singleton_lock(port: int = 21345) -> bool:
    """通过端口绑定实现单实例锁, 返回True表示首次启动"""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", port))
        sock.listen(1)
        # 不关闭, 进程结束时自动释放
        return True
    except OSError:
        return False
