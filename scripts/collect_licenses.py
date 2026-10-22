"""从固定构建环境保留原许可文本，随源码及EXE分发。"""
from importlib.metadata import distribution
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ('uiautomation', 'comtypes', 'pystray', 'Pillow', 'pywin32', 'six', 'pyinstaller')


def collect():
    destination = ROOT / 'resources' / 'licenses'
    destination.mkdir(parents=True, exist_ok=True)
    count = 0
    for package in PACKAGES:
        dist = distribution(package)
        copied = 0
        for item in dist.files or ():
            parts = item.parts
            if not any(part.endswith('.dist-info') for part in parts):
                continue
            filename = item.name.lower()
            if 'license' not in filename and not filename.startswith('copying'):
                continue
            target = destination / package.lower() / Path(*parts[1:])
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(dist.locate_file(item), target)
            count += 1
            copied += 1
        if not copied:
            raise RuntimeError('缺少许可文本: ' + package)
    for name, original in (
            ('python/LICENSE.txt', Path(sys.base_prefix) / 'LICENSE.txt'),
            ('tk/license.terms', Path(sys.base_prefix) / 'tcl/tk8.6/license.terms')):
        if original.is_file():
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(original, target)
            count += 1
    # Tcl原许可随仓库固定保留；部分Python安装仅附带Tk副本。
    if not (destination / 'tcl' / 'license.terms').is_file():
        raise RuntimeError('缺少Tcl许可原文')
    return count


if __name__ == '__main__':
    print('Collected license files:', collect())
