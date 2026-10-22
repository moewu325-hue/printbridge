"""设置面板: 打印机选择 + 微信白名单 + 通知开关"""
import tkinter as tk
from tkinter import ttk, messagebox

from printer.controller import PrintController
from gui.theme import COLORS, FONT, apply_theme


class SettingsDialog(tk.Toplevel):
    """设置弹窗"""

    def __init__(self, parent, config_mgr, monitor, logger, on_saved=None):
        super().__init__(parent)
        self.config_mgr = config_mgr
        self.monitor = monitor
        self.logger = logger
        self.on_saved = on_saved
        self._data = dict(config_mgr.data)  # 编辑副本

        self.title("PrintBridge 设置")
        self.geometry("600x590")
        self.minsize(560, 540)
        apply_theme(self)
        self.transient(parent)
        self.grab_set()

        self._build_ui()
        self.wait_window()

    def _build_ui(self):
        heading = ttk.Frame(self, padding=(20, 16, 20, 8))
        heading.pack(fill=tk.X)
        ttk.Label(heading, text="设置", style="Title.TLabel").pack(anchor=tk.W)
        ttk.Label(heading, text="管理打印设备、微信授权与通知方式", style="Muted.TLabel").pack(anchor=tk.W, pady=(4, 0))
        notebook = ttk.Notebook(self)
        notebook.pack(fill=tk.BOTH, expand=True, padx=20, pady=(4, 0))

        # 标签页
        tab_printer = ttk.Frame(notebook)
        tab_wechat = ttk.Frame(notebook)
        tab_notify = ttk.Frame(notebook)
        notebook.add(tab_printer, text="打印机")
        notebook.add(tab_wechat, text="微信")
        notebook.add(tab_notify, text="通知与文件")

        self._build_printer_tab(tab_printer)
        self._build_wechat_tab(tab_wechat)
        self._build_notify_tab(tab_notify)

        # 底部按钮
        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill=tk.X, padx=20, pady=16)
        ttk.Button(btn_frame, text="保存设置", style="Primary.TButton", command=self._on_save).pack(side=tk.RIGHT)
        ttk.Button(btn_frame, text="取消", command=self.destroy).pack(side=tk.RIGHT, padx=8)
        self.bind('<Escape>', lambda event: self.destroy())

    # ======================== 打印机标签页 ========================

    def _build_printer_tab(self, parent):
        frame = ttk.LabelFrame(parent, text="打印设备", padding=14)
        frame.pack(fill=tk.X, padx=4, pady=14)

        printers = PrintController.get_all_printers()
        printer_names = [p["name"] for p in printers]
        current = self._data.get("selected_printer", "")

        ttk.Label(frame, text="当前设备", style="Muted.TLabel").pack(anchor=tk.W)
        self._printer_var = tk.StringVar(value=current)
        combo = ttk.Combobox(frame, textvariable=self._printer_var, values=printer_names, state="readonly")
        combo.pack(fill=tk.X, pady=5)
        ttk.Label(frame, text="未指定时使用系统默认打印机；更换设备后会刷新队列。", style="Muted.TLabel").pack(anchor=tk.W, pady=(0, 12))

        # 轮询间隔
        int_frame = ttk.Frame(frame)
        int_frame.pack(fill=tk.X, pady=5)
        ttk.Label(int_frame, text="队列刷新间隔").pack(side=tk.LEFT)
        self._poll_var = tk.StringVar(value=str(self._data.get("poll_interval_sec", 2)))
        ttk.Spinbox(int_frame, from_=1, to=10, textvariable=self._poll_var, width=5).pack(side=tk.LEFT, padx=5)
        ttk.Label(int_frame, text="秒（1–10）", style="Muted.TLabel").pack(side=tk.LEFT)

        # 最小化到托盘
        self._tray_var = tk.BooleanVar(value=self._data.get("min_to_tray", True))
        ttk.Checkbutton(frame, text="关闭窗口时最小化到系统托盘", variable=self._tray_var).pack(anchor=tk.W, pady=5)

        # 按钮
        ttk.Button(frame, text="刷新打印机列表", command=lambda: self._refresh_printers(combo)).pack(anchor=tk.W, pady=(12, 0))

    def _refresh_printers(self, combo):
        printers = PrintController.get_all_printers()
        combo["values"] = [p["name"] for p in printers]

    # ======================== 微信标签页 ========================

    def _build_wechat_tab(self, parent):
        # 启用开关
        toggle_frame = ttk.Frame(parent, padding=(4, 12, 4, 0))
        toggle_frame.pack(fill=tk.X)
        self._wechat_var = tk.BooleanVar(value=self._data.get("wechat_enabled", False))
        ttk.Checkbutton(toggle_frame, text="启用微信命令与通知", variable=self._wechat_var).pack(anchor=tk.W)
        ttk.Label(toggle_frame, text="需登录桌面微信，仅处理下方明确授权的会话。", style="Muted.TLabel").pack(anchor=tk.W, pady=(4, 0))

        # 监听目标
        listen_frame = ttk.LabelFrame(parent, text="授权会话", padding=10)
        listen_frame.pack(fill=tk.X, padx=4, pady=10)

        self._listen_listbox = tk.Listbox(listen_frame, height=3, font=(FONT, 9),
                                        bg=COLORS['surface'], fg=COLORS['text'],
                                        highlightbackground=COLORS['border'], relief='flat',
                                        selectbackground=COLORS['selection'], selectforeground=COLORS['text'])
        self._listen_listbox.pack(fill=tk.X, pady=5)
        for target in self._data.get("wechat_listen_targets", ["文件传输助手"]):
            self._listen_listbox.insert(tk.END, target)

        btn_frame = ttk.Frame(listen_frame)
        btn_frame.pack(fill=tk.X)
        ttk.Button(btn_frame, text="添加", command=self._add_listen_target).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="删除", command=self._del_listen_target).pack(side=tk.LEFT, padx=2)

        # 通知目标
        notify_frame = ttk.LabelFrame(parent, text="通知发送目标", padding=10)
        notify_frame.pack(fill=tk.X, padx=4, pady=(0, 10))
        self._notify_target_var = tk.StringVar(value=self._data.get("wechat_notify_target", "文件传输助手"))
        ttk.Entry(notify_frame, textvariable=self._notify_target_var).pack(fill=tk.X, pady=5)
        ttk.Label(notify_frame, text="建议使用文件传输助手，避免发送到其他会话。", style="Muted.TLabel").pack(anchor=tk.W)

        # 自动打印
        self._auto_print_var = tk.BooleanVar(value=self._data.get("auto_print_wechat_files", False))
        self._auto_print_check = ttk.Checkbutton(parent, text="自动接收并打印微信附件（暂不可用）", variable=self._auto_print_var)
        self._auto_print_check.pack(anchor=tk.W, padx=8)
        self._auto_print_check.state(['disabled'])
        ttk.Label(parent, text="附件收件流程尚未开放；保留已有配置，不自动接收附件。", style="Muted.TLabel").pack(anchor=tk.W, padx=8, pady=(4, 0))

    def _add_listen_target(self):
        dialog = tk.Toplevel(self)
        dialog.title("添加监听目标")
        dialog.geometry("300x120")
        dialog.transient(self)
        dialog.grab_set()

        ttk.Label(dialog, text="微信好友备注名或群名:").pack(padx=10, pady=10)
        entry = ttk.Entry(dialog, width=30)
        entry.pack(padx=10)
        entry.focus()

        def confirm():
            name = entry.get().strip()
            if name and name not in self._listen_listbox.get(0, tk.END):
                self._listen_listbox.insert(tk.END, name)
            dialog.destroy()

        ttk.Button(dialog, text="确定", command=confirm).pack(pady=10)
        entry.bind("<Return>", lambda e: confirm())

    def _del_listen_target(self):
        sel = self._listen_listbox.curselection()
        if sel:
            self._listen_listbox.delete(sel[0])

    # ======================== 通知标签页 ========================

    def _build_notify_tab(self, parent):
        frame = ttk.LabelFrame(parent, text="微信通知触发条件", padding=10)
        frame.pack(fill=tk.X, padx=4, pady=14)

        self._notify_complete_var = tk.BooleanVar(value=self._data.get("notify_on_complete", True))
        self._notify_failure_var = tk.BooleanVar(value=self._data.get("notify_on_failure", True))
        self._notify_error_var = tk.BooleanVar(value=self._data.get("notify_on_error", True))

        ttk.Checkbutton(frame, text="打印完成时通知", variable=self._notify_complete_var).pack(anchor=tk.W, pady=3)
        ttk.Checkbutton(frame, text="打印失败时通知", variable=self._notify_failure_var).pack(anchor=tk.W, pady=3)
        ttk.Checkbutton(frame, text="打印机异常时通知(缺纸/脱机)", variable=self._notify_error_var).pack(anchor=tk.W, pady=3)

        # 文件类型白名单
        type_frame = ttk.LabelFrame(parent, text="打印文件扩展名白名单", padding=10)
        type_frame.pack(fill=tk.X, padx=4, pady=(0, 8))

        allowed = self._data.get("allowed_file_types", [])
        types = list(dict.fromkeys([
            "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "txt", "csv",
            "png", "jpg", "jpeg", *allowed,
        ]))
        self._type_vars = {}
        for i, extension in enumerate(types):
            var = tk.BooleanVar(value=extension in allowed)
            self._type_vars[extension] = var
            ttk.Checkbutton(type_frame, text=f".{extension}", variable=var).grid(
                row=i // 4, column=i % 4, sticky=tk.W, padx=10, pady=2
            )
        ttk.Label(parent, text="每种扩展名单独授权；取消勾选会移除该类型权限。", style="Muted.TLabel").pack(anchor=tk.W, padx=8)

    # ======================== 保存 ========================

    def _on_save(self):
        # 收集允许的文件类型
        try:
            interval = int(self._poll_var.get())
            if not 1 <= interval <= 10:
                raise ValueError
        except ValueError:
            messagebox.showerror("设置无效", "轮询间隔请输入1至10之间的整数。", parent=self)
            return
        allowed_types = [extension for extension, var in self._type_vars.items()
                         if var.get()]

        # 收集监听目标
        listen_targets = list(self._listen_listbox.get(0, tk.END))

        updates = {
            "selected_printer": self._printer_var.get(),
            "poll_interval_sec": interval,
            "min_to_tray": self._tray_var.get(),
            "wechat_enabled": self._wechat_var.get(),
            "wechat_listen_targets": listen_targets,
            "wechat_notify_target": self._notify_target_var.get().strip() or "文件传输助手",
            "notify_on_complete": self._notify_complete_var.get(),
            "notify_on_failure": self._notify_failure_var.get(),
            "notify_on_error": self._notify_error_var.get(),
            "auto_print_wechat_files": self._auto_print_var.get(),
            "allowed_file_types": allowed_types,
        }

        try:
            self.config_mgr.update(updates)
        except Exception as exc:
            messagebox.showerror("保存失败", str(exc), parent=self)
            return

        if self.on_saved:
            self.on_saved()
        else:
            new_printer = self._printer_var.get()
            if new_printer != self.monitor.printer_name:
                self.monitor.switch_printer(new_printer)
            self.monitor.poll_interval = interval
            self.monitor.request_refresh()

        self.logger.info("设置已保存")
        messagebox.showinfo("设置", "设置已保存", parent=self)
        self.destroy()
