"""只打包Git跟踪的公开源码，固定ZIP元数据。"""
import argparse
import hashlib
from pathlib import Path, PurePosixPath
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def build(output):
    names = subprocess.check_output(['git', '-C', str(ROOT), 'ls-files', '-z']).decode('utf-8').split('\0')
    names = sorted(name for name in names if name)
    output = Path(output).resolve()
    inputs = {}
    for name in names:
        path = ROOT / name
        parts = PurePosixPath(name).parts
        if (PurePosixPath(name).is_absolute() or '..' in parts or ':' in name
                or any(x in parts for x in ('.git', 'build', 'dist', 'logs', 'temp', '.venv', '__pycache__'))
                or path.name in ('config.json', 'print_history.db')
                or path.suffix.lower() in ('.exe', '.key', '.pem', '.pyc')
                or path.name.startswith('.env') or path.is_symlink()
                or not path.resolve().is_relative_to(ROOT)):
            raise ValueError('禁止打包的输入: ' + name)
        if path.resolve() == output:
            raise ValueError('发行输出不可覆盖源码')
        inputs['PrintBridge/' + name] = path.read_bytes()
    sums = ''.join(f'{hashlib.sha256(data).hexdigest()}  {name}\n' for name, data in inputs.items())
    inputs['PrintBridge/SHA256SUMS'] = sums.encode('utf-8')
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in sorted(inputs.items()):
            info = zipfile.ZipInfo(name, (2026, 10, 21, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data, compresslevel=9)
    return hashlib.sha256(output.read_bytes()).hexdigest()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    print(build(args.output), args.output)
