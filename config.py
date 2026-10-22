"""配置快照与原子持久化。"""
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import threading
from models import AppConfig


def _auto_detect_default_printer() -> str:
    """保留旧调用入口，设备检测不在配置读写期间执行。"""
    from printer.controller import PrintController
    return PrintController.get_default_printer()


DEFAULT_CONFIG = {
    'selected_printer': '', 'poll_interval_sec': 2,
    'wechat_enabled': False, 'wechat_listen_targets': ['文件传输助手'],
    'wechat_notify_target': '文件传输助手', 'notify_on_complete': True,
    'notify_on_failure': True, 'notify_on_error': True,
    'auto_print_wechat_files': False,
    'allowed_file_types': ['pdf', 'doc', 'docx', 'xlsx', 'pptx', 'txt', 'png', 'jpg', 'jpeg'],
    'temp_dir': '', 'min_to_tray': True,
    # 桌面打印面板
    'panel_visible': False, 'panel_mode': 'full',
    'panel_x': None, 'panel_y': None,
    'panel_taskbar_x': None, 'panel_taskbar_y': None, 'panel_taskbar_auto': True,
    'panel_show_virtual': False, 'panel_always_on_top': False,
    'panel_selected_printer': '',
}


class ConfigManager:
    """读返回副本；写入成功后才发布新的内存快照。"""

    def __init__(self, config_path: str, create: bool = True):
        self._path = Path(config_path).resolve()
        self._lock = threading.RLock()
        self._data = {}
        self._create = create
        self.load()

    def _validated(self, data):
        if not isinstance(data, dict):
            raise ValueError('配置必须为JSON对象')
        result = deepcopy(DEFAULT_CONFIG)
        result.update(deepcopy(data))
        interval = result['poll_interval_sec']
        if isinstance(interval, bool) or not isinstance(interval, (int, float)) or not 1 <= interval <= 10:
            raise ValueError('轮询间隔必须在1到10秒之间')
        for key in ('wechat_enabled', 'notify_on_complete', 'notify_on_failure',
                    'notify_on_error', 'auto_print_wechat_files', 'min_to_tray'):
            if not isinstance(result[key], bool):
                raise ValueError(f'{key}必须为布尔值')
        for key in ('selected_printer', 'wechat_notify_target', 'temp_dir'):
            if not isinstance(result[key], str) or '\0' in result[key]:
                raise ValueError(f'{key}必须为有效文本')
        for key in ('wechat_listen_targets', 'allowed_file_types'):
            values = result[key]
            if not isinstance(values, list) or any(not isinstance(x, str) or not x.strip() or '\0' in x for x in values):
                raise ValueError(f'{key}必须为非空文本的列表')
            result[key] = list(dict.fromkeys(x.strip() for x in values))
        result['allowed_file_types'] = list(dict.fromkeys(x.lower().lstrip('.') for x in result['allowed_file_types']))
        if any(not x.isalnum() for x in result['allowed_file_types']):
            raise ValueError('文件扩展名只能包含字母与数字')
        if not result['temp_dir']:
            result['temp_dir'] = str(self._path.parent / 'temp')
        # 面板配置：非法值回落默认，不阻断主配置保存
        if result.get('panel_mode') not in ('full', 'taskbar'):
            result['panel_mode'] = 'full'
        for key in ('panel_visible', 'panel_show_virtual',
                    'panel_always_on_top', 'panel_taskbar_auto'):
            if not isinstance(result.get(key), bool):
                result[key] = DEFAULT_CONFIG[key]
        for key in ('panel_x', 'panel_y', 'panel_taskbar_x', 'panel_taskbar_y'):
            value = result.get(key)
            if isinstance(value, bool) or not isinstance(value, int):
                result[key] = None
        if not isinstance(result.get('panel_selected_printer'), str) \
                or '\0' in result['panel_selected_printer']:
            result['panel_selected_printer'] = ''
        return result

    def load(self):
        with self._lock:
            if self._path.exists():
                try:
                    loaded = json.loads(self._path.read_text(encoding='utf-8-sig'))
                except (json.JSONDecodeError, UnicodeError) as exc:
                    raise ValueError(f'配置损坏，原文件已保留：{self._path}') from exc
                self._data = self._validated(loaded)
            else:
                candidate = self._validated({})
                if self._create:
                    self._write(candidate)
                self._data = candidate

    def _write(self, data):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8',
                    dir=self._path.parent, prefix=self._path.name + '.', suffix='.tmp', delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(data, stream, ensure_ascii=False, indent=2)
                stream.write('\n')
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def save(self):
        with self._lock:
            self._write(self._data)

    def get(self, key, default=None):
        with self._lock:
            return deepcopy(self._data.get(key, default))

    def set(self, key, value):
        self.update({key: value})

    def update(self, updates: dict):
        with self._lock:
            candidate = deepcopy(self._data)
            candidate.update(deepcopy(updates))
            candidate = self._validated(candidate)
            self._write(candidate)
            self._data = candidate

    def to_app_config(self) -> AppConfig:
        snapshot = self.data
        return AppConfig(**{key: snapshot[key] for key in AppConfig.__dataclass_fields__})

    @property
    def data(self) -> dict:
        with self._lock:
            return deepcopy(self._data)
