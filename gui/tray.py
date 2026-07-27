"""系统托盘: pystray集成, 最小化/还原/退出"""
import threading
import os
from PIL import Image, ImageDraw


def _create_icon_image(size: int = 64) -> Image.Image:
    """生成打印机图标 (蓝色圆形 + 白色P字母)"""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # 蓝色圆形背景
    margin = 4
    draw.ellipse(
        [margin, margin, size - margin, size - margin],
        fill=(33, 150, 243, 255),  # Material Blue
    )

    # 白色 P 字母 (用矩形模拟简单打印机图标)
    # 打印机主体
    pw, ph = size // 3, size // 4
    px = (size - pw) // 2
    py = size // 3
    draw.rectangle([px, py, px + pw, py + ph], fill=(255, 255, 255, 255))
    # 进纸口
    draw.rectangle(
        [px + pw // 4, py + ph, px + pw * 3 // 4, py + ph + ph // 3],
        fill=(255, 255, 255, 255),
    )

    return img


class SystemTray:
    """系统托盘控制器"""

    def __init__(self, main_window, config_mgr, shutdown_event, logger):
        self.main_window = main_window
        self.config_mgr = config_mgr
        self.shutdown_event = shutdown_event
        self.logger = logger
        self._tray_thread = None
        self._icon = None

    def setup(self):
        """创建托盘图标并在独立线程中运行"""
        try:
            import pystray

            icon_path = os.path.join(
                os.path.dirname(os.path.dirname(__file__)), "resources", "icon.png"
            )

            # 如果没有图标文件, 用Pillow生成
            if os.path.exists(icon_path):
                image = Image.open(icon_path)
            else:
                image = _create_icon_image()
                os.makedirs(os.path.dirname(icon_path), exist_ok=True)
                image.save(icon_path)

            menu = pystray.Menu(
                pystray.MenuItem("显示主窗口", self._on_show),
                pystray.MenuItem("暂停/恢复监听", self._on_toggle_listen, checked=lambda item: True),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("退出", self._on_exit),
            )

            self._icon = pystray.Icon(
                "printbridge", image, "PrintBridge — 打印队列管理", menu
            )

            # 在独立线程运行
            self._tray_thread = threading.Thread(
                target=self._icon.run, name="TrayIcon", daemon=True
            )
            self._tray_thread.start()
            self.logger.info("系统托盘已创建")

        except ImportError:
            self.logger.warning("pystray未安装, 托盘功能不可用")
        except Exception as e:
            self.logger.error(f"托盘创建失败: {e}")

    def _on_show(self, icon, item):
        """显示主窗口"""
        self.main_window.root.after(0, self.main_window.show)

    def _on_toggle_listen(self, icon, item):
        """切换微信监听(预留)"""
        pass

    def _on_exit(self, icon, item):
        """退出应用"""
        self.logger.info("用户通过托盘菜单退出")
        self.shutdown_event.set()
        if self._icon:
            self._icon.stop()
        self.main_window.root.after(0, self.main_window.root.destroy)

    def stop(self):
        """停止托盘"""
        if self._icon:
            try:
                self._icon.stop()
            except Exception:
                pass
