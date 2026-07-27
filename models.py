"""PrintBridge 数据模型"""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class PrintJob:
    """打印作业"""
    job_id: int
    printer_name: str
    document: str
    status: str          # spooling/printing/printed/error/paused/cancelled/paperout/offline
    pages: int = 0
    submitted_time: str = ""
    owner: str = ""
    source: str = "local"  # local / wechat
    wechat_sender: str = ""  # 微信发送者(仅source=wechat时)


@dataclass
class WeChatMessage:
    """微信消息"""
    msg_type: str        # text / file / image
    content: str         # 文本内容 或 文件路径
    sender: str          # 发送者备注名
    timestamp: str = ""


@dataclass
class AppConfig:
    """应用配置(运行时从JSON加载)"""
    selected_printer: str = "HP LaserJet Professional P1108"
    poll_interval_sec: int = 2
    wechat_enabled: bool = True
    wechat_listen_targets: list = field(default_factory=lambda: ["文件传输助手"])
    wechat_notify_target: str = "文件传输助手"
    notify_on_complete: bool = True
    notify_on_failure: bool = True
    notify_on_error: bool = True
    auto_print_wechat_files: bool = True
    allowed_file_types: list = field(default_factory=lambda: [
        "pdf", "doc", "docx", "xlsx", "pptx", "txt", "png", "jpg", "jpeg"
    ])
    temp_dir: str = ""
    min_to_tray: bool = True
