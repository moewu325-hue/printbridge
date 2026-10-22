"""系统托盘: pystray集成, 最小化/还原/退出"""
from PIL import Image, ImageDraw
import threading


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

    def __init__(self, main_window, config_mgr, shutdown_event, logger, on_toggle_panel=None):
        self.main_window = main_window
        self.config_mgr = config_mgr
        self.shutdown_event = shutdown_event
        self.logger = logger
        self.on_toggle_panel = on_toggle_panel
        self._icon = None
        self._thread = None
        self._ready = threading.Event()
        self._stopping = threading.Event()

    @property
    def is_ready(self):
        return (self._ready.is_set() and not self._stopping.is_set()
                and self._thread is not None and self._thread.is_alive())

    def setup(self) -> bool:
        """创建托盘图标并在独立线程中运行"""
        if self._thread and self._thread.is_alive():
            return self.is_ready
        self._ready.clear()
        self._stopping.clear()
        try:
            import pystray

            image = _create_icon_image()

            menu = pystray.Menu(
                pystray.MenuItem("显示主窗口", self._on_show),
                pystray.MenuItem("显示/隐藏打印面板", self._on_toggle_panel),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("退出", self._on_exit),
            )

            self._icon = pystray.Icon(
                "printbridge", image, "PrintBridge — 打印队列管理", menu
            )

            # Windows事件循环与其setup线程均继承daemon，失败不会阻止进程退出。
            self._thread = threading.Thread(target=self._run_icon, name='TrayIcon', daemon=True)
            self._thread.start()
            return self.is_ready

        except ImportError:
            self.logger.warning("pystray未安装, 托盘功能不可用")
        except Exception as e:
            self.logger.error(f"托盘创建失败: {e}")
        self.stop()
        return False

    def _run_icon(self):
        try:
            self._icon.run(setup=self._on_ready)
        except Exception:
            self.logger.exception('托盘事件循环创建失败')
        finally:
            self._ready.clear()

    def _on_ready(self, icon):
        try:
            if self._stopping.is_set():
                icon.stop()
                return
            icon.visible = True
            if self._stopping.is_set():
                icon.stop()
                return
            self._ready.set()
            self.logger.info('系统托盘已就绪')
        except Exception:
            self._ready.clear()
            self.logger.exception('托盘图标显示失败')
            icon.stop()

    def _on_show(self, icon, item):
        """显示主窗口"""
        self.main_window.queues["status_queue"].put(("ui", "show"))

    def _on_toggle_panel(self, icon, item):
        """托盘线程只投递请求，实际切换在主线程完成。"""
        self.main_window.queues["status_queue"].put(("ui", "toggle_panel"))

    def _on_exit(self, icon, item):
        """退出应用"""
        self.logger.info("用户通过托盘菜单退出")
        self.shutdown_event.set()

    def stop(self):
        """停止托盘"""
        self._stopping.set()
        self._ready.clear()
        if self._icon:
            try:
                self._icon.stop()
            except Exception:
                pass
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=3)
