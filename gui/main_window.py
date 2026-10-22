"""主窗口协调 Tk 控件、状态模型与跨线程队列。"""
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk, messagebox
import queue
import threading
from gui.job_view_model import FILTERS, JobViewModel
from gui.theme import COLORS, apply_theme
from printer.controller import PrintController


class MainWindow:
    """PrintBridge 主窗口"""

    def __init__(self, root: tk.Tk, config_mgr, db, monitor, queues: dict, logger, request_exit=None, on_settings_saved=None, on_toggle_panel=None):
        self.root = root
        self.config_mgr = config_mgr
        self.db = db
        self.monitor = monitor
        self.logger = logger
        self.queues = queues
        self.controller = PrintController()
        self.request_exit = request_exit or self.root.destroy
        self.on_settings_saved = on_settings_saved
        self.on_toggle_panel = on_toggle_panel
        self.tray_available = False
        self._poll_id = None
        self.model = JobViewModel(monitor.printer_name)
        self._jobs = self.model.jobs

        # 窗口配置
        self.root.title("PrintBridge — 打印队列管理")
        self.root.geometry("1000x620")
        self.root.minsize(760, 500)
        apply_theme(self.root)

        # 窗口关闭 → 最小化到托盘(如果配置启用)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.bind('<Destroy>', self._on_root_destroy, add='+')

        # 右键菜单数据
        self._context_job_id = None

        # 构建界面
        self._build_toolbar()
        self._build_filters()
        self._build_statusbar()
        self._build_treeview()
        self._build_context_menu()
        self._update_stats()
        self.root.update_idletasks()
        chrome_height = self.root.winfo_reqheight() - self.tree.winfo_reqheight()
        self.root.minsize(760, max(500, chrome_height + 140))

        # 启动队列轮询
        self._poll_id = self.root.after(100, self._poll_queues)

    # ======================== 工具栏 ========================

    def _build_toolbar(self):
        header = ttk.Frame(self.root, padding=(20, 14, 20, 12))
        header.pack(fill=tk.X)
        ttk.Label(header, text="PrintBridge", style="Title.TLabel").pack(side=tk.LEFT)
        ttk.Label(header, text="打印队列管理", style="Muted.TLabel").pack(side=tk.LEFT, padx=12, pady=(6, 0))
        ttk.Button(header, text="设置", command=self._on_settings).pack(side=tk.RIGHT)
        panel = ttk.Button(header, text="桌面面板", command=self._on_toggle_panel)
        panel.pack(side=tk.RIGHT, padx=(0, 8))
        if self.on_toggle_panel is None:
            panel.state(['disabled'])

        device = ttk.Frame(self.root, padding=(20, 6, 20, 8))
        device.pack(fill=tk.X)
        device.columnconfigure(0, weight=1)
        ttk.Label(device, text="当前打印机", style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        self._printer_label = ttk.Label(device, text="尚未选择打印机", style="Section.TLabel", width=1)
        self._printer_label.grid(row=1, column=0, sticky="ew", pady=(3, 0))
        self._printer_label.bind('<Configure>', self._fit_printer_name)
        self._printer_status_label = ttk.Label(device, text="", style="Muted.TLabel")
        self._printer_status_label.grid(row=0, column=1, rowspan=2, padx=(12, 0), sticky="e")

        toolbar = ttk.Frame(self.root, padding=(20, 4, 20, 10))
        toolbar.pack(fill=tk.X)
        ttk.Button(toolbar, text="刷新队列", style="Primary.TButton", command=self._on_refresh).pack(side=tk.LEFT)
        self._pause_printer_button = ttk.Button(toolbar, text="暂停打印机", command=self._on_pause_printer)
        self._pause_printer_button.pack(side=tk.LEFT, padx=(8, 0))
        self._resume_printer_button = ttk.Button(toolbar, text="恢复打印机", command=self._on_resume_printer)
        self._resume_printer_button.pack(side=tk.LEFT, padx=(8, 0))
        self._purge_button = ttk.Button(toolbar, text="清空队列…", style="Quiet.TButton", command=self._on_purge_queue)
        self._purge_button.pack(side=tk.RIGHT)

    def _build_filters(self):
        summary = ttk.Frame(self.root, padding=(20, 2, 20, 8))
        summary.pack(fill=tk.X)
        self._count_labels = {}
        for name in ('进行中', '需处理', '已结束'):
            label = ttk.Label(summary, text=f"{name}  0", style="Section.TLabel")
            label.pack(side=tk.LEFT, padx=(0, 28))
            self._count_labels[name] = label
        self._clear_button = ttk.Button(summary, text="清除已结束记录", style="Quiet.TButton", command=self._on_clear_completed)
        self._clear_button.pack(side=tk.RIGHT)
        filters = ttk.Frame(self.root, padding=(20, 0, 20, 10))
        filters.pack(fill=tk.X)
        filters.columnconfigure(1, weight=1)
        ttk.Label(filters, text="搜索文档 / 提交者", style="Muted.TLabel").grid(row=0, column=0, padx=(0, 10))
        self._search_var = tk.StringVar(self.root)
        ttk.Entry(filters, textvariable=self._search_var).grid(row=0, column=1, sticky="ew")
        self._filter_var = tk.StringVar(self.root, value="全部状态")
        ttk.Combobox(filters, textvariable=self._filter_var, values=tuple(FILTERS),
                     state="readonly", width=12).grid(row=0, column=2, padx=(10, 0))
        self._search_var.trace_add('write', self._render_jobs)
        self._filter_var.trace_add('write', self._render_jobs)

    # ======================== Treeview作业列表 ========================

    def _build_treeview(self):
        frame = ttk.Frame(self.root, style="Surface.TFrame", borderwidth=1, relief="solid")
        frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=(0, 8))
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)

        columns = ("job_id", "document", "status", "pages", "owner", "submitted")
        self.tree = ttk.Treeview(frame, columns=columns, show="headings", selectmode="browse")

        # 列定义
        self.tree.heading("job_id", text="ID")
        self.tree.heading("document", text="文档名")
        self.tree.heading("status", text="状态")
        self.tree.heading("pages", text="页数")
        self.tree.heading("owner", text="提交者")
        self.tree.heading("submitted", text="提交时间")

        font = tkfont.Font(self.root, font=ttk.Style(self.root).lookup('Treeview', 'font'))
        self.tree.column("job_id", width=max(50, font.measure('0000') + 18), stretch=False, anchor=tk.CENTER)
        self.tree.column("document", width=330, minwidth=250)
        self.tree.column("status", width=max(140, font.measure(self._status_text('sent')) + 18),
                         stretch=False, anchor=tk.CENTER)
        self.tree.column("pages", width=max(50, font.measure('页数') + 18), stretch=False, anchor=tk.CENTER)
        self.tree.column("owner", width=max(120, font.measure('提交者') + 18), stretch=False)
        self.tree.column("submitted", width=max(160, font.measure('2026-10-22 10:30') + 18), stretch=False)

        # 滚动条
        scrollbar = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=self.tree.yview)
        horizontal = ttk.Scrollbar(frame, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=scrollbar.set, xscrollcommand=horizontal.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        self._empty_label = ttk.Label(frame, text="暂无打印任务\n新的任务会自动显示在这里。", justify="center", style="Surface.TLabel")
        self._empty_label.place(relx=0.5, rely=0.5, anchor="center")

        # 绑定事件
        self.tree.bind("<Double-1>", self._on_double_click)
        self.tree.bind("<Button-3>", self._on_right_click)
        self.tree.bind("<<TreeviewSelect>>", self._on_selection_changed)

        # 状态颜色标签
        for status, color in {'printing': 'accent', 'sent': 'accent', 'printed': 'ok',
                              'error': 'error', 'paperout': 'error', 'offline': 'warn',
                              'paused': 'warn', 'cancelling': 'muted',
                              'cancelled': 'muted', 'removed': 'muted'}.items():
            self.tree.tag_configure(status, foreground=COLORS[color])

    # ======================== 右键菜单 ========================

    def _build_context_menu(self):
        self.context_menu = tk.Menu(self.root, tearoff=0)
        self.context_menu.add_command(label="暂停作业", command=self._on_pause_job)
        self.context_menu.add_command(label="恢复作业", command=self._on_resume_job)
        self.context_menu.add_command(label="重新启动", command=self._on_restart_job)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="取消作业", command=self._on_cancel_job)

    # ======================== 状态栏 ========================

    def _build_statusbar(self):
        statusbar = ttk.Frame(self.root, padding=(20, 8, 20, 10))
        statusbar.pack(fill=tk.X, side=tk.BOTTOM)
        self._feedback_label = ttk.Label(statusbar, text="就绪", style="Muted.TLabel")
        self._feedback_label.pack(side=tk.LEFT)
        self._wechat_label = ttk.Label(statusbar, text="微信：未连接", style="Muted.TLabel")
        self._wechat_label.pack(side=tk.RIGHT)
        actions = ttk.Frame(self.root, padding=(20, 0, 20, 0))
        actions.pack(fill=tk.X, side=tk.BOTTOM)
        self._selection_label = ttk.Label(actions, text="选中任务后操作", style="Muted.TLabel")
        self._selection_label.pack(side=tk.LEFT)
        self._job_buttons = {}
        for action, text, command in (
                ('cancel', '取消任务', self._on_cancel_job), ('restart', '重新启动', self._on_restart_job),
                ('resume', '恢复', self._on_resume_job), ('pause', '暂停', self._on_pause_job)):
            button = ttk.Button(actions, text=text, command=command, width=0)
            button.pack(side=tk.RIGHT, padx=(6, 0))
            button.state(['disabled'])
            self._job_buttons[action] = button
        self._stats_label = ttk.Label(self.root, text="", style="Muted.TLabel", padding=(20, 0, 20, 6))
        self._stats_label.pack(fill=tk.X, side=tk.BOTTOM)

    # ======================== 队列轮询 (主线程) ========================

    def _poll_queues(self):
        """从跨线程队列取数据更新GUI (root.after循环)"""
        if self._poll_id is not None:
            self.root.after_cancel(self._poll_id)
            self._poll_id = None
        for name in ("job_queue", "status_queue"):
            for _ in range(100):
                try:
                    item = self.queues[name].get_nowait()
                except queue.Empty:
                    break
                if item is None:
                    break
                action, payload = item
                if name == "job_queue":
                    if action == "RESET":
                        self._clear_tree()
                    elif action == "NEW":
                        self._add_job_to_tree(payload)
                    elif action == "UPDATE":
                        self._update_job_in_tree(payload)
                    elif action == "REMOVE":
                        self._remove_job_from_tree(payload)
                elif action == "ui" and payload == "show":
                    self.show()
                elif action == "ui" and payload == "toggle_panel":
                    self._on_toggle_panel()
                elif action == "operation":
                    label, success, detail = payload
                    self._feedback_label.config(text=f"{label}：{'成功' if success else '失败'}",
                                                foreground=COLORS['ok' if success else 'error'])
                    if not success:
                        messagebox.showerror("打印操作失败", detail or label, parent=self.root)
                elif action == "wechat":
                    self._wechat_label.config(text=f"微信: {payload}")
                elif action == "printer":
                    self._update_stats()
        self._poll_id = self.root.after(100, self._poll_queues)

    # ======================== Treeview操作 ========================

    def _job_key(self, job):
        self.model.printer_name = self.monitor.printer_name
        return self.model.key(job)

    def _clear_tree(self):
        self.model.reset()
        self._context_job_id = None
        self._render_jobs()

    def _add_job_to_tree(self, job):
        self.model.printer_name = self.monitor.printer_name
        self.model.upsert(job)
        self._render_jobs()

    def _update_job_in_tree(self, job):
        self._add_job_to_tree(job)

    def _remove_job_from_tree(self, job):
        self.model.remove(job)
        self._render_jobs()

    @staticmethod
    def _status_text(status):
        return JobViewModel.status_text(status)

    def _render_jobs(self, *_):
        selected = self.tree.selection()
        visible = self.model.visible(self._search_var.get(), FILTERS.get(self._filter_var.get(), 'all'))
        visible_keys = {key for key, _ in visible}
        for key in self.tree.get_children():
            if key not in visible_keys:
                self.tree.delete(key)
        for index, (key, job) in enumerate(visible):
            values = (job['job_id'], job.get('document', ''), self._status_text(job.get('status')),
                      job.get('pages', 0), job.get('owner', ''), job.get('submitted_time', ''))
            if self.tree.exists(key):
                self.tree.item(key, values=values, tags=(job['status'],))
                self.tree.move(key, '', index)
            else:
                self.tree.insert('', index, iid=key, values=values, tags=(job['status'],))
        if selected and selected[0] in visible_keys:
            self.tree.selection_set(selected)
        if visible:
            self._empty_label.place_forget()
        else:
            text = "没有匹配的任务\n试试其他关键词或状态。" if self._jobs else "暂无打印任务\n新的任务会自动显示在这里。"
            self._empty_label.configure(text=text)
            self._empty_label.place(relx=0.5, rely=0.5, anchor='center')
        self._update_stats()
        self._on_selection_changed()

    def _update_stats(self):
        """更新状态栏的统计信息"""
        counts = self.model.counts()
        for label, key in (('进行中', 'active'), ('需处理', 'attention'), ('已结束', 'finished')):
            self._count_labels[label].configure(text=f"{label}  {counts[key]}")
        active = counts['active'] + counts['attention']
        self._stats_label.config(text=f"显示 {len(self.tree.get_children())} / {counts['total']} 条  ·  活跃: {active}")
        self._fit_printer_name()
        status = self.monitor.printer_status
        labels = {'ready': '就绪', 'paused': '已暂停', 'offline': '脱机', 'paperout': '缺纸', 'error': '异常'}
        self._printer_status_label.config(text=labels.get(status, status or '等待状态'))
        has_printer = bool(self.monitor.printer_name)
        for button in (self._pause_printer_button, self._resume_printer_button, self._purge_button):
            button.state(['!disabled'] if has_printer else ['disabled'])
        if status in ('paused', '已暂停'):
            self._pause_printer_button.state(['disabled'])
        elif status in ('ready', '就绪', 'printing', '打印中'):
            self._resume_printer_button.state(['disabled'])
        self._clear_button.state(['!disabled'] if counts['finished'] else ['disabled'])

    def _fit_printer_name(self, _event=None):
        text = self.monitor.printer_name or '尚未选择打印机，请在设置中选择'
        width = max(1, self._printer_label.winfo_width())
        font = tkfont.Font(self.root, font=ttk.Style(self.root).lookup('Section.TLabel', 'font'))
        if font.measure(text) > width:
            while text and font.measure(text + '…') > width:
                text = text[:-1]
            text += '…'
        self._printer_label.config(text=text)

    def _on_selection_changed(self, _event=None):
        selection = self.tree.selection()
        self._context_job_id = selection[0] if selection else None
        job = self._jobs.get(self._context_job_id)
        allowed = self.model.actions_for(job.get('status')) if job else set()
        for action, button in self._job_buttons.items():
            button.state(['!disabled'] if action in allowed else ['disabled'])
        if hasattr(self, 'context_menu'):
            for index, action in ((0, 'pause'), (1, 'resume'), (2, 'restart'), (4, 'cancel')):
                self.context_menu.entryconfigure(index, state='normal' if action in allowed else 'disabled')
        self._selection_label.config(text=f"已选任务 #{job['job_id']}" if job else "选中任务后操作")

    # ======================== 工具栏事件 ========================

    def _on_refresh(self):
        """手动刷新(重新EnumJobs一次)"""
        self.monitor.request_refresh()
        self._feedback_label.config(text="已请求刷新队列", foreground=COLORS['muted'])
        self._update_stats()

    def _on_clear_completed(self):
        """清除已完成/已取消的作业行"""
        count = self.model.clear_finished()
        self._render_jobs()
        self._feedback_label.config(text=f"已清除 {count} 条已结束记录", foreground=COLORS['muted'])

    def _run_control(self, method, label, printer, job_id=None):
        if not printer:
            messagebox.showerror("打印操作失败", "尚未选择打印机", parent=self.root)
            return
        def work():
            try:
                args = (printer,) if job_id is None else (printer, job_id)
                success = bool(getattr(self.controller, method)(*args))
                detail = "请检查打印机连接、队列状态及操作权限。"
            except Exception as exc:
                success, detail = False, str(exc)
            self.queues["status_queue"].put(("operation", (label, success, detail)))
            self.monitor.request_refresh()
        threading.Thread(target=work, name="PrintControl", daemon=True).start()

    def _on_purge_queue(self):
        printer = self.monitor.printer_name
        if printer and messagebox.askyesno("清空打印队列", f"清空打印机“{printer}”的全部待打印任务？\n此操作会取消队列中的任务。", parent=self.root, default='no'):
            self._run_control("purge_queue", "清空队列", printer)

    def _on_pause_printer(self):
        self._run_control("pause_printer", "暂停打印机", self.monitor.printer_name)

    def _on_resume_printer(self):
        self._run_control("resume_printer", "恢复打印机", self.monitor.printer_name)

    def _on_settings(self):
        from gui.settings_dialog import SettingsDialog
        SettingsDialog(self.root, self.config_mgr, self.monitor, self.logger,
                       on_saved=self.on_settings_saved)

    def _on_toggle_panel(self):
        if self.on_toggle_panel is not None:
            self.on_toggle_panel()

    # ======================== 右键菜单事件 ========================

    def _on_right_click(self, event):
        """右键弹出菜单"""
        item = self.tree.identify_row(event.y)
        if item:
            self.tree.selection_set(item)
            self._on_selection_changed()
            self.context_menu.post(event.x_root, event.y_root)

    def _on_double_click(self, event):
        """双击显示作业详情"""
        item = self.tree.selection()
        if item:
            values = self.tree.item(item[0], "values")
            messagebox.showinfo(
                "作业详情",
                f"ID: {values[0]}\n文档: {values[1]}\n状态: {values[2]}\n"
                f"页数: {values[3]}\n提交者: {values[4]}\n提交: {values[5]}", parent=self.root
            )

    def _control_context_job(self, method, label):
        job = self._jobs.get(self._context_job_id)
        action = method.removesuffix('_job')
        if job and action in self.model.actions_for(job.get('status')):
            self._run_control(method, label, job.get("printer_name", self.monitor.printer_name),
                              job["job_id"])

    def _on_pause_job(self):
        self._control_context_job("pause_job", "暂停作业")

    def _on_resume_job(self):
        self._control_context_job("resume_job", "恢复作业")

    def _on_restart_job(self):
        self._control_context_job("restart_job", "重新启动作业")

    def _on_cancel_job(self):
        self._control_context_job("cancel_job", "取消作业")

    # ======================== 窗口生命周期 ========================

    def _on_close(self):
        """关闭窗口 → 隐藏到托盘"""
        if self.tray_available and self.config_mgr.get("min_to_tray", True):
            self.root.withdraw()
        else:
            self.request_exit()

    def _on_root_destroy(self, event):
        if event.widget is self.root and self._poll_id is not None:
            self.root.after_cancel(self._poll_id)
            self._poll_id = None

    def show(self):
        """从托盘恢复显示"""
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
