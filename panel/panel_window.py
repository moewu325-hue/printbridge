"""PrintBridge 桌面打印面板：完整卡片 / 任务栏条，随主程序运行。"""
from __future__ import annotations

import logging
from queue import Empty, Queue
import tkinter as tk
from tkinter import messagebox
from typing import Optional

from panel.card_ui import COLORS, MODE_SIZES, PrinterCardView
from panel.monitor import CommandWorker, PrinterMonitor
from panel.printer_backend import JobInfo, PrinterBackend, PrinterInfo, Snapshot
from panel.winutil import clamp_position, dock_taskbar_size, set_toolwindow


class PrinterPanel:
    """懒创建的桌面面板；隐藏时停止轮询，退出主程序时释放线程。"""

    def __init__(self, root: tk.Tk, config_mgr, logger: logging.Logger) -> None:
        self._root = root
        self._config = config_mgr
        self._logger = logger
        self._top: Optional[tk.Toplevel] = None
        self._view: Optional[PrinterCardView] = None
        self._toast_window: Optional[tk.Toplevel] = None
        self._events: Queue[tuple] = Queue(maxsize=256)
        self._after_ids: set[str] = set()
        self._pump_after: Optional[str] = None
        self._closed = False
        self._snapshot: Optional[Snapshot] = None
        self._backend = PrinterBackend(selected_provider=self._viewed_printer)
        self._monitor = PrinterMonitor(
            self._backend, self._events,
            interval=max(1.0, float(config_mgr.get("poll_interval_sec", 2) or 2)),
            logger=logger,
        )
        self._commands = CommandWorker(self._events, logger=logger)
        self._pressed: Optional[tuple[int, int]] = None
        self._origin = (0, 0)
        self._dragging = False
        self._press_hit: Optional[tuple[str, object]] = None

    # ---- 显示 / 隐藏 ----
    def is_visible(self) -> bool:
        return (self._top is not None and self._top.winfo_exists()
                and self._top.state() == "normal")

    def toggle(self) -> None:
        if self.is_visible():
            self.hide()
        else:
            self.show()

    def show(self) -> None:
        if self._closed:
            return
        if self._top is None or not self._top.winfo_exists():
            self._create()
        assert self._top is not None
        self._top.deiconify()
        self._top.lift()
        self._discard_events()
        self._commands.start()
        self._monitor.start()
        self._monitor.request_refresh()
        self._schedule_pump()
        self._set_visible_config(True)

    def hide(self) -> None:
        self._monitor.stop()
        self._commands.stop()
        self._cancel_timers()
        self._discard_events()
        self._hide_toast()
        if self._top is not None and self._top.winfo_exists():
            self._top.withdraw()
        self._set_visible_config(False)

    def restore_if_enabled(self) -> None:
        if self._config.get("panel_visible", False):
            self.show()

    def stop(self) -> None:
        """主线程关闭窗口；线程等待交给 shutdown。"""
        self._closed = True
        self._monitor.stop()
        self._commands.stop()
        self._cancel_timers()
        self._hide_toast()
        try:
            if self._top is not None and self._top.winfo_exists():
                self._top.destroy()
        except tk.TclError:
            pass
        self._top = None
        self._view = None

    def shutdown(self) -> None:
        """只停线程，不触碰 Tk；可在退出清理线程调用。"""
        self._monitor.stop()
        self._commands.stop()
        self._monitor.join()
        self._commands.join()

    def _discard_events(self) -> None:
        while True:
            try:
                self._events.get_nowait()
            except Empty:
                return

    def _after(self, delay: int, callback) -> str:
        def invoke():
            self._after_ids.discard(token)
            if not self._closed:
                callback()
        token = self._root.after(delay, invoke)
        self._after_ids.add(token)
        return token

    def _cancel_timers(self) -> None:
        for token in self._after_ids:
            try:
                self._root.after_cancel(token)
            except tk.TclError:
                pass
        self._after_ids.clear()
        self._pump_after = None

    def _schedule_pump(self) -> None:
        if self._pump_after is None:
            self._pump_after = self._after(100, self._pump)

    def _set_visible_config(self, visible: bool) -> None:
        if bool(self._config.get("panel_visible")) == visible:
            return
        self._update_config(panel_visible=visible)

    # ---- 配置读写 ----
    def _update_config(self, **values) -> None:
        try:
            self._config.update(values)
        except Exception:
            self._logger.exception("保存面板配置失败")

    def _viewed_printer(self) -> Optional[str]:
        name = self._config.get("panel_selected_printer") or self._config.get("selected_printer")
        return name or None

    def _initial_position(self, mode: str, w: int, h: int) -> tuple[int, int]:
        if mode == "taskbar":
            if not self._config.get("panel_taskbar_auto", True):
                x, y = self._config.get("panel_taskbar_x"), self._config.get("panel_taskbar_y")
                if isinstance(x, int) and isinstance(y, int):
                    return clamp_position(x, y, w, h)
            return dock_taskbar_size(w, h)
        x, y = self._config.get("panel_x"), self._config.get("panel_y")
        if isinstance(x, int) and isinstance(y, int):
            return clamp_position(x, y, w, h)
        return clamp_position(40, 80, w, h)

    # ---- 构建窗口 ----
    def _create(self) -> None:
        top = tk.Toplevel(self._root)
        top.withdraw()
        top.overrideredirect(True)
        top.configure(bg=COLORS["bg"])
        self._top = top
        mode = self._config.get("panel_mode", "full")
        if mode not in MODE_SIZES:
            mode = "full"
        self._view = PrinterCardView(top, mode=mode)
        w, h = MODE_SIZES[mode]
        x, y = self._initial_position(mode, w, h)
        top.geometry(f"{w}x{h}+{x}+{y}")
        try:
            top.attributes("-alpha", 1.0)
        except tk.TclError:
            pass
        top.attributes("-topmost", mode == "taskbar" or bool(self._config.get("panel_always_on_top")))
        set_toolwindow(top)
        self._bind_events()
        self._schedule_pump()

    def _bind_events(self) -> None:
        canvas = self._view.canvas
        canvas.bind("<ButtonPress-1>", self._on_press)
        canvas.bind("<B1-Motion>", self._on_motion)
        canvas.bind("<ButtonRelease-1>", self._on_release)
        canvas.bind("<Button-3>", self._on_context)
        canvas.bind("<Double-Button-1>", self._on_double)
        canvas.bind("<MouseWheel>", self._on_wheel)

    # ---- 拖动 / 点击 ----
    def _on_press(self, event: tk.Event) -> None:
        self._pressed = (event.x_root, event.y_root)
        self._origin = (self._top.winfo_x(), self._top.winfo_y())
        self._dragging = False
        self._press_hit = self._view.hit_test(event.x, event.y)

    def _on_motion(self, event: tk.Event) -> None:
        if self._pressed is None:
            return
        dx = event.x_root - self._pressed[0]
        dy = event.y_root - self._pressed[1]
        if not self._dragging and max(abs(dx), abs(dy)) > 3:
            self._dragging = True
        if self._dragging:
            self._top.geometry(f"+{self._origin[0] + dx}+{self._origin[1] + dy}")

    def _on_release(self, event: tk.Event) -> None:
        dragging = self._dragging
        self._pressed = None
        self._dragging = False
        if dragging:
            mode = self._config.get("panel_mode", "full")
            values = {}
            if mode == "taskbar":
                values.update(panel_taskbar_auto=False,
                              panel_taskbar_x=self._top.winfo_x(),
                              panel_taskbar_y=self._top.winfo_y())
            else:
                values.update(panel_x=self._top.winfo_x(), panel_y=self._top.winfo_y())
            self._update_config(**values)
            return
        if self._config.get("panel_mode") == "taskbar":
            self._switch_mode("full")
            return
        hit = self._press_hit
        self._press_hit = None
        if hit is None:
            return
        kind, payload = hit
        if kind == "printer" and isinstance(payload, PrinterInfo):
            self._select_printer(payload)
        elif kind == "virtual_toggle":
            self._toggle_virtual()

    def _on_double(self, event: tk.Event) -> None:
        hit = self._view.hit_test(event.x, event.y)
        if self._config.get("panel_mode") != "full" or hit is None:
            return
        kind, payload = hit
        if kind == "printer" and isinstance(payload, PrinterInfo):
            self._commands.submit("打开打印队列", self._backend.open_queue_window, payload.name)

    def _on_wheel(self, event: tk.Event) -> None:
        self._view.scroll(-1 if event.delta > 0 else 1)

    # ---- 右键菜单 ----
    def _on_context(self, event: tk.Event) -> None:
        hit = self._view.hit_test(event.x, event.y)
        menu = tk.Menu(self._top, tearoff=0)
        if hit and hit[0] == "job" and isinstance(hit[1], JobInfo):
            self._build_job_menu(menu, hit[1])
        elif hit and hit[0] == "printer" and isinstance(hit[1], PrinterInfo):
            self._build_printer_menu(menu, hit[1])
        else:
            self._build_global_menu(menu)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _build_global_menu(self, menu: tk.Menu) -> None:
        menu.add_command(label="立即刷新", command=self._monitor.request_refresh)
        mode_menu = tk.Menu(menu, tearoff=0)
        current = self._config.get("panel_mode")
        mode_menu.add_command(label="完整卡片", command=lambda: self._switch_mode("full"),
                              state="disabled" if current == "full" else "normal")
        mode_menu.add_command(label="任务栏条", command=lambda: self._switch_mode("taskbar"),
                              state="disabled" if current == "taskbar" else "normal")
        menu.add_cascade(label="显示模式", menu=mode_menu)
        show_virtual = tk.BooleanVar(value=bool(self._config.get("panel_show_virtual")))
        menu.add_checkbutton(label="显示虚拟打印机", variable=show_virtual,
                             command=self._toggle_virtual)
        if current == "taskbar":
            menu.add_command(label="重新贴靠", command=self._redock)
        else:
            top = tk.BooleanVar(value=bool(self._config.get("panel_always_on_top")))
            menu.add_checkbutton(label="置顶显示", variable=top,
                                 command=lambda: self._toggle_topmost(top))
        menu.add_separator()
        menu.add_command(label="打开打印机设置",
                         command=lambda: self._commands.submit(
                             "打开打印机设置", self._backend.open_printer_settings))
        menu.add_command(label="隐藏面板", command=self.hide)

    def _build_printer_menu(self, menu: tk.Menu, printer: PrinterInfo) -> None:
        menu.add_command(label=f"查看：{printer.name}",
                         command=lambda: self._select_printer(printer))
        if not printer.is_default:
            menu.add_command(label="设为默认打印机…",
                             command=lambda: self._set_default(printer))
        menu.add_separator()
        menu.add_command(label="打开打印队列",
                         command=lambda: self._commands.submit(
                             "打开打印队列", self._backend.open_queue_window, printer.name))
        menu.add_command(label="打开打印机属性",
                         command=lambda: self._commands.submit(
                             "打开打印机属性", self._backend.open_properties, printer.name))
        paused = bool(printer.status_bits & 0x1) or printer.status == "已暂停"
        if paused:
            menu.add_command(label="恢复打印",
                             command=lambda: self._commands.submit(
                                 "恢复打印", self._backend.resume_printer, printer.name))
        else:
            menu.add_command(label="暂停打印",
                             command=lambda: self._commands.submit(
                                 "暂停打印", self._backend.pause_printer, printer.name))
        menu.add_command(label=f"清空队列…（{printer.job_count} 个）",
                         command=lambda: self._purge_queue(printer),
                         state="normal" if printer.job_count else "disabled")
        menu.add_separator()
        self._build_global_menu(menu)

    def _build_job_menu(self, menu: tk.Menu, job: JobInfo) -> None:
        printer = job.printer_name or self._current_printer_name()
        state = 'normal' if printer and self._snapshot and self._snapshot.jobs_fresh else 'disabled'
        if job.status == "已暂停":
            menu.add_command(label="恢复任务",
                             command=lambda: self._commands.submit(
                                 "恢复任务", self._backend.resume_job, printer, job.job_id), state=state)
        else:
            menu.add_command(label="暂停任务",
                             command=lambda: self._commands.submit(
                                 "暂停任务", self._backend.pause_job, printer, job.job_id), state=state)
        menu.add_command(label="取消任务…", command=lambda: self._cancel_job(job, printer), state=state)
        menu.add_separator()
        self._build_global_menu(menu)

    # ---- 动作 ----
    def _current_printer_name(self) -> str:
        return self._snapshot.selected_name if self._snapshot else ""

    def _select_printer(self, printer: PrinterInfo) -> None:
        self._update_config(panel_selected_printer=printer.name)
        self._monitor.set_selected(printer.name)
        self._monitor.request_refresh()

    def _toggle_virtual(self) -> None:
        self._update_config(panel_show_virtual=not bool(self._config.get("panel_show_virtual")))
        self._render()

    def _toggle_topmost(self, var: tk.BooleanVar) -> None:
        self._update_config(panel_always_on_top=bool(var.get()))
        self._top.attributes("-topmost", bool(var.get()))

    def _redock(self) -> None:
        self._update_config(panel_taskbar_auto=True,
                            panel_taskbar_x=None, panel_taskbar_y=None)
        if self._config.get("panel_mode") == "taskbar" and self._top is not None:
            w, h = MODE_SIZES["taskbar"]
            x, y = dock_taskbar_size(w, h)
            self._top.geometry(f"{w}x{h}+{x}+{y}")

    def _set_default(self, printer: PrinterInfo) -> None:
        message = f"将“{printer.name}”设为系统默认打印机？"
        if printer.offline:
            message += "\n\n注意：该打印机当前处于脱机状态。"
        if not messagebox.askyesno("设为默认打印机", message, parent=self._top,
                                   default=messagebox.NO):
            return
        self._commands.submit("设为默认打印机", self._backend.set_default, printer.name)

    def _purge_queue(self, printer: PrinterInfo) -> None:
        message = (f"清空“{printer.name}”的打印队列？\n"
                   f"共 {printer.job_count} 个任务，此操作不可撤销。")
        if not messagebox.askyesno("清空打印队列", message, parent=self._top,
                                   default=messagebox.NO):
            return
        self._commands.submit(
            "清空队列", self._backend.purge_queue, printer.name,
            after=lambda: self._monitor.clear_jobs(printer.name),
        )

    def _cancel_job(self, job: JobInfo, printer: Optional[str] = None) -> None:
        printer = printer or job.printer_name or self._current_printer_name()
        if not printer:
            return
        if not messagebox.askyesno("取消打印任务", f"取消任务“{job.document}”？",
                                   parent=self._top, default=messagebox.NO):
            return
        self._commands.submit(
            "取消任务", self._backend.cancel_job,
            printer, job.job_id,
        )

    def _switch_mode(self, mode: str) -> None:
        if mode not in MODE_SIZES or mode == self._config.get("panel_mode"):
            return
        values = {"panel_mode": mode}
        if self._config.get("panel_mode") == "taskbar":
            values.update(panel_taskbar_x=self._top.winfo_x(),
                          panel_taskbar_y=self._top.winfo_y())
        else:
            values.update(panel_x=self._top.winfo_x(), panel_y=self._top.winfo_y())
        self._update_config(**values)
        self._view.set_mode(mode)
        w, h = MODE_SIZES[mode]
        x, y = self._initial_position(mode, w, h)
        self._top.attributes("-topmost",
                             mode == "taskbar" or bool(self._config.get("panel_always_on_top")))
        self._top.geometry(f"{w}x{h}+{x}+{y}")
        self._render()

    # ---- 刷新与渲染 ----
    def _pump(self) -> None:
        if self._pump_after is not None:
            self._root.after_cancel(self._pump_after)
            self._after_ids.discard(self._pump_after)
            self._pump_after = None
        if self._closed or self._top is None or not self._top.winfo_exists():
            return
        try:
            for _ in range(100):
                event = self._events.get_nowait()
                kind = event[0]
                if kind == "snapshot" and isinstance(event[1], Snapshot):
                    self._snapshot = event[1]
                    self._render()
                elif kind == "action":
                    ok, message = bool(event[1]), str(event[2])
                    self._toast(message, ok)
                    self._monitor.request_refresh()
                    self._after(400, self._monitor.request_refresh)
                    self._after(1500, self._monitor.request_refresh)
        except Empty:
            pass
        self._schedule_pump()

    def _render(self) -> None:
        if self._snapshot is None or self._view is None or self._top is None:
            return
        # 后端已回退到有效打印机，渲染以其结果为准
        height = self._view.render(
            self._snapshot,
            self._snapshot.selected_name,
            bool(self._config.get("panel_show_virtual")),
        )
        if self._config.get("panel_mode") == "full":
            w = MODE_SIZES["full"][0]
            if self._top.winfo_height() != height:
                self._top.geometry(
                    f"{w}x{height}+{self._top.winfo_x()}+{self._top.winfo_y()}")

    def _toast(self, text: str, ok: bool = True) -> None:
        self._hide_toast()
        top = tk.Toplevel(self._top)
        top.overrideredirect(True)
        top.attributes("-topmost", True)
        tk.Label(
            top, text=text, bg=COLORS["panel"],
            fg=COLORS["ok"] if ok else COLORS["error"],
            font=("Microsoft YaHei UI", 8), padx=10, pady=6,
        ).pack()
        top.update_idletasks()
        x = self._top.winfo_x() + (self._top.winfo_width() - top.winfo_width()) // 2
        y = self._top.winfo_y() + self._top.winfo_height() + 6
        x, y = clamp_position(x, y, top.winfo_width(), top.winfo_height())
        top.geometry(f"+{x}+{y}")
        self._toast_window = top
        self._after(2200, self._hide_toast)

    def _hide_toast(self) -> None:
        if self._toast_window is not None:
            try:
                self._toast_window.destroy()
            except tk.TclError:
                pass
            self._toast_window = None
