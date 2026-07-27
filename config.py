"""配置管理: JSON读写 + 默认值 + 运行时热更新"""
import json
import os
from models import AppConfig


def _auto_detect_default_printer() -> str:
    """启动时自动检测默认打印机, 失败则返回空字符串"""
    try:
        from printer.controller import PrintController
        detected = PrintController.auto_detect_printer()
        if detected:
            return detected
    except Exception:
        pass
    return ""


DEFAULT_CONFIG = {
    "selected_printer": "",  # 空=启动时自动检测
    "poll_interval_sec": 2,
    "wechat_enabled": True,
    "wechat_listen_targets": ["文件传输助手"],
    "wechat_notify_target": "文件传输助手",
    "notify_on_complete": True,
    "notify_on_failure": True,
    "notify_on_error": True,
    "auto_print_wechat_files": True,
    "allowed_file_types": ["pdf", "doc", "docx", "xlsx", "pptx", "txt", "png", "jpg", "jpeg"],
    "temp_dir": "",
    "min_to_tray": True,
}


class ConfigManager:
    """配置管理器: 单例, 线程安全读, 写需加锁"""

    def __init__(self, config_path: str):
        self._path = config_path
        self._data = {}
        self.load()

    def load(self):
        """从JSON文件加载配置, 文件不存在则用默认值创建"""
        if os.path.exists(self._path):
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
            except (json.JSONDecodeError, IOError):
                self._data = {}
        # 合并默认值: 已存在的键保留, 缺失的补默认
        for key, value in DEFAULT_CONFIG.items():
            if key not in self._data:
                self._data[key] = value
        # 确保temp_dir默认值
        if not self._data.get("temp_dir"):
            self._data["temp_dir"] = os.path.join(os.path.dirname(self._path), "temp")
        # 自动检测打印机(首次运行或配置为空时)
        if not self._data.get("selected_printer"):
            detected = _auto_detect_default_printer()
            if detected:
                self._data["selected_printer"] = detected
        self.save()

    def save(self):
        """保存配置到JSON文件"""
        dirname = os.path.dirname(os.path.abspath(self._path))
        os.makedirs(dirname, exist_ok=True)
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)

    def get(self, key: str, default=None):
        return self._data.get(key, default)

    def set(self, key: str, value):
        self._data[key] = value

    def update(self, updates: dict):
        """批量更新并持久化"""
        self._data.update(updates)
        self.save()

    def to_app_config(self) -> AppConfig:
        """转换为AppConfig数据类"""
        return AppConfig(
            selected_printer=self._data.get("selected_printer", DEFAULT_CONFIG["selected_printer"]),
            poll_interval_sec=self._data.get("poll_interval_sec", 2),
            wechat_enabled=self._data.get("wechat_enabled", True),
            wechat_listen_targets=self._data.get("wechat_listen_targets", ["文件传输助手"]),
            wechat_notify_target=self._data.get("wechat_notify_target", "文件传输助手"),
            notify_on_complete=self._data.get("notify_on_complete", True),
            notify_on_failure=self._data.get("notify_on_failure", True),
            notify_on_error=self._data.get("notify_on_error", True),
            auto_print_wechat_files=self._data.get("auto_print_wechat_files", True),
            allowed_file_types=self._data.get("allowed_file_types", DEFAULT_CONFIG["allowed_file_types"]),
            temp_dir=self._data["temp_dir"],
            min_to_tray=self._data.get("min_to_tray", True),
        )

    @property
    def data(self) -> dict:
        return self._data
