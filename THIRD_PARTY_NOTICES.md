# 第三方组件

项目MIT许可不替代以下组件的原许可。固定版本见requirements.txt与requirements-build.txt，原许可文本保留在resources/licenses并随EXE打包，源码可从对应上游取得；发行EXE由PyInstaller生成，用户无需另行安装这些组件。

| 组件 | 上游与许可入口 |
| --- | --- |
| Python 3.11 | [PSF License](https://docs.python.org/3.11/license.html) |
| Tcl/Tk 8.6 | [Tcl/Tk license](https://www.tcl-lang.org/software/tcltk/license.html) |
| SQLite | [public domain](https://www.sqlite.org/copyright.html) |
| pywin32 | [源码与许可](https://github.com/mhammond/pywin32) |
| uiautomation | [Apache-2.0源码与许可](https://github.com/yinkaisheng/Python-UIAutomation-for-Windows) |
| comtypes | [源码与MIT许可](https://github.com/enthought/comtypes) |
| pystray | [源码与LGPL-3.0许可](https://github.com/moses-palmer/pystray) |
| Pillow | [MIT-CMU及附带库许可](https://github.com/python-pillow/Pillow/blob/main/LICENSE) |
| six | [MIT许可](https://github.com/benjaminp/six) |
| PyInstaller bootloader | [GPL-2.0-or-later及发行例外](https://pyinstaller.org/en/stable/license.html) |

图标为本项目生成的几何打印机图形。微信和Windows名称属于各自权利人，本项目没有与其官方建立隶属关系。

本地发行环境的Tcl为8.6.12，许可原文来自[tcltk/tcl固定标签](https://github.com/tcltk/tcl/blob/core-8-6-12/license.terms)；其他原许可由scripts/collect_licenses.py从固定依赖的发行元数据取得，Python/Tk文本取构建解释器所附副本。
