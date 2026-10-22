"""PrintBridge命令行：明确文件、打印机和可解析结果。"""
import argparse
import json
import os
import sys
import time
from pathlib import Path


class AmbiguousFileError(ValueError):
    def __init__(self, paths):
        self.paths = paths
        super().__init__('存在同名文件，请提供完整路径')


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def _config():
    from app_paths import config_path
    from config import ConfigManager
    return ConfigManager(str(config_path()), create=False)


def _printer(explicit=None):
    from app_paths import resolve_printer
    name = resolve_printer(_config(), explicit)
    if not name:
        raise ValueError('未选择打印机且系统没有默认打印机')
    return name


def _controller():
    from printer.controller import PrintController
    return PrintController


def find_wechat_file_storage() -> str:
    # 仅显式配置微信目录，避免猜测其他账号。
    return os.environ.get('PRINTBRIDGE_WECHAT_FILES', '')


def _get_search_dirs() -> list:
    value = os.environ.get('PRINTBRIDGE_SEARCH_DIRS', '')
    return [p for p in value.split(os.pathsep) if p]


def _iter_files(max_depth=2):
    roots = [find_wechat_file_storage(), *_get_search_dirs()]
    seen_roots, seen_files = set(), set()
    for raw in roots:
        if not raw:
            continue
        base = Path(raw).resolve()
        if base in seen_roots or not base.is_dir():
            continue
        seen_roots.add(base)
        for current, dirs, files in os.walk(base, followlinks=False):
            current_path = Path(current)
            depth = len(current_path.relative_to(base).parts)
            dirs[:] = sorted(d for d in dirs if not (current_path / d).is_symlink()) if depth < max_depth else []
            for name in sorted(files):
                path = current_path / name
                try:
                    if path.is_symlink() or not path.is_file():
                        continue
                    real = path.resolve()
                    if not real.is_relative_to(base) or real in seen_files:
                        continue
                    seen_files.add(real)
                    yield str(real)
                except OSError:
                    continue


def search_files(keyword: str) -> list:
    if not keyword.strip():
        raise ValueError('搜索关键词不能为空')
    results = []
    for path in _iter_files():
        if keyword.casefold() in Path(path).name.casefold():
            try:
                results.append((os.path.getmtime(path), path))
            except OSError:
                continue
    return [p for _, p in sorted(results, key=lambda item: (-item[0], item[1]))]


def find_file(target: str) -> str:
    if not target.strip():
        return ''
    path = Path(target).expanduser()
    # 仅明确路径直达；裸文件名需检查当前目录与配置根的全部重名。
    if path.is_absolute() or '/' in target or '\\' in target:
        return str(path.resolve()) if path.is_file() else ''
    matches = {p for p in _iter_files() if Path(p).name.casefold() == target.casefold()}
    for candidate in Path.cwd().iterdir():
        if candidate.name.casefold() == target.casefold() and candidate.is_file():
            matches.add(str(candidate.resolve()))
    matches = sorted(matches)
    if len(matches) > 1:
        raise AmbiguousFileError(matches)
    return matches[0] if matches else ''


def human_size(size: int) -> str:
    value = float(size)
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if value < 1024 or unit == 'TB':
            return f'{value:.0f}{unit}'
        value /= 1024


def _emit(command, json_output, action):
    try:
        data = action()
        result = {'ok': True, 'command': command, 'data': data, 'error': None}
        code = 0
    except Exception as exc:
        error = {'message': str(exc), 'type': type(exc).__name__}
        if isinstance(exc, AmbiguousFileError):
            error['candidates'] = exc.paths
        result = {'ok': False, 'command': command, 'data': None, 'error': error}
        code = 1
    if json_output:
        print(json.dumps(result, ensure_ascii=False, default=str))
    elif result['ok']:
        print(json.dumps(result['data'], ensure_ascii=False, indent=2, default=str))
    else:
        print('错误: ' + result['error']['message'])
        for path in result['error'].get('candidates', []):
            print('  ' + path)
    return code


def cmd_status(json_output=False, printer=None):
    def action():
        controller = _controller()
        if printer:
            names = [printer]
        else:
            names = [p['name'] for p in controller.get_all_printers(strict=True)]
        result = []
        for name in names:
            info = controller.get_printer_info(name)
            if info is None:
                raise RuntimeError('无法读取打印机状态: ' + name)
            result.append({'printer': name, 'info': info})
        return result
    return _emit('status', json_output, action)


def cmd_list(json_output=False, printer=None):
    def action():
        name = _printer(printer)
        return {'printer': name, 'jobs': _controller().get_jobs(name)}
    return _emit('list', json_output, action)


def cmd_print(target: str, json_output=False, printer=None):
    def action():
        filepath = find_file(target)
        if not filepath:
            raise FileNotFoundError('未找到唯一精确文件，请提供完整路径: ' + target)
        from utils import is_printable_file
        config = _config()
        allowed = {'.' + str(ext).lower().lstrip('.') for ext in config.get('allowed_file_types', [])}
        if not is_printable_file(filepath) or Path(filepath).suffix.lower() not in allowed:
            raise ValueError('配置不允许打印此文件类型')
        name = _printer(printer)
        if not _controller().print_file(filepath, name):
            raise RuntimeError('提交打印失败')
        return {'file': filepath, 'printer': name, 'status': 'submitted', 'message': '已发起打印请求，实际完成状态请查看打印队列'}
    return _emit('print', json_output, action)


def cmd_search(keyword: str, json_output=False):
    def action():
        items = []
        for path in search_files(keyword):
            try:
                stat = os.stat(path)
            except OSError:
                continue
            items.append({'path': path, 'size': stat.st_size, 'modified': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(stat.st_mtime))})
        return items
    return _emit('search', json_output, action)


def cmd_cancel(job_id: str, json_output=False, printer=None):
    def action():
        if job_id.lower() != 'all' and (not job_id.isdecimal() or int(job_id) <= 0):
            raise ValueError('作业ID必须为正整数或all')
        name = _printer(printer)
        controller = _controller()
        ok = controller.purge_queue(name) if job_id.lower() == 'all' else controller.cancel_job(name, int(job_id))
        if not ok:
            raise RuntimeError('取消作业失败')
        return {'printer': name, 'job_id': job_id, 'status': 'cancel_requested'}
    return _emit('cancel', json_output, action)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = _ArgumentParser(description='PrintBridge CLI')
    commands = parser.add_mutually_exclusive_group(required=True)
    commands.add_argument('--status', action='store_true', help='查看打印机状态')
    commands.add_argument('--list', action='store_true', help='列出所选打印机作业')
    commands.add_argument('--print', metavar='FILE', help='完整路径或唯一精确文件名')
    commands.add_argument('--search', metavar='KEYWORD', help='搜索显式配置目录')
    commands.add_argument('--cancel', metavar='JOB_ID', help='取消作业ID或all')
    parser.add_argument('--printer', help='明确指定打印机')
    parser.add_argument('--json', action='store_true', help='输出单个JSON对象')
    try:
        args = parser.parse_args(argv)
    except ValueError as exc:
        def fail(message=str(exc)):
            raise ValueError(message)
        _emit('arguments', '--json' in argv, fail)
        return 2
    if args.status:
        return cmd_status(args.json, args.printer)
    if args.list:
        return cmd_list(args.json, args.printer)
    if args.print is not None:
        return cmd_print(args.print, args.json, args.printer)
    if args.search is not None:
        return cmd_search(args.search, args.json)
    return cmd_cancel(args.cancel, args.json, args.printer)


if __name__ == '__main__':
    raise SystemExit(main())
