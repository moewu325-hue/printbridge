"""资源与用户数据路径分离，供GUI和CLI共用。"""
import os
from pathlib import Path
import shutil
import sqlite3
from contextlib import closing
import sys
import tempfile


def get_data_dir() -> Path:
    explicit = os.environ.get('PRINTBRIDGE_DATA_DIR', '').strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    local = os.environ.get('LOCALAPPDATA')
    base = Path(local) if local else Path.home() / 'AppData' / 'Local'
    return base.resolve() / 'PrintBridge'


def config_path() -> Path:
    return get_data_dir() / 'config.json'


def prepare_data_dir() -> Path:
    directory = get_data_dir()
    directory.mkdir(parents=True, exist_ok=True)
    # 显式数据目录用于便携和隔离测试，不自动引入旧数据。
    if os.environ.get('PRINTBRIDGE_DATA_DIR', '').strip():
        return directory
    legacy = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent
    if legacy.resolve() == directory:
        return directory
    old_config, new_config = legacy / 'config.json', directory / 'config.json'
    if old_config.is_file() and not new_config.exists():
        _migrate_file(old_config, new_config)
    old_db, new_db = legacy / 'print_history.db', directory / 'print_history.db'
    if old_db.is_file() and not new_db.exists():
        _migrate_file(old_db, new_db, database=True)
    return directory


def _migrate_file(source_path, destination, database=False):
    fd, name = tempfile.mkstemp(prefix=destination.name + '.', suffix='.tmp', dir=destination.parent)
    os.close(fd)
    temporary = Path(name)
    try:
        if database:
            with closing(sqlite3.connect(source_path.as_uri() + '?mode=ro', uri=True)) as source:
                with closing(sqlite3.connect(str(temporary))) as dest:
                    source.backup(dest)
        else:
            with source_path.open('rb') as source, temporary.open('wb') as dest:
                shutil.copyfileobj(source, dest)
                dest.flush()
                os.fsync(dest.fileno())
        # Windows rename拒绝覆盖既有目标，发布完整迁移文件。
        try:
            os.rename(temporary, destination)
        except FileExistsError:
            pass
    finally:
        temporary.unlink(missing_ok=True)


def resolve_printer(config_mgr, explicit=None) -> str:
    selected = explicit if explicit is not None else config_mgr.get('selected_printer', '')
    if selected and selected.strip():
        return selected.strip()
    from printer.controller import PrintController
    return PrintController.get_default_printer() or ''
