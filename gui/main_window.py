"""主窗口: Treeview作业列表 + 工具栏 + 状态栏"""
import tkinter as tk
from tkinter import ttk, messagebox
import queue
import threading
from datetime import datetime

from printer.controller import PrintController


class MainWindow:
    """PrintBridge 主窗口"""

    def __init__(self, root: tk.Tk, config_mgr, db, monitor, queues: dict, logger):
        self.root = root
        self.config_mgr = config_mgr
        self.db = db
        self.monitor = monitor
        self.logger = logger
        self.queues = queues
        self.controller = PrintController()

        # 窗口配置
        self.root.title("PrintBridge — 打印队列管理")
        self.root.geometry("800x500")
        self.root.minsize(600, 350)

        # 窗口关闭 → 最小化到托盘(如果配置启用)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # 右键菜单数据
        self._context_job_id = None

        # 构建界面
        self._build_toolbar()
        self._build_treeview()
        self._build_statusbar()
        self._build_context_menu()

        # 启动队列轮询
        self.root.after(100, self._poll_queues)

    # ======================== 工具栏 ========================

    def _build_toolbar(self):
        toolbar = ttk.Frame(self.root, padding=5)
        toolbar.pack(fill=tk.X, side=tk.TOP)

        ttk.Button(toolbar, text="刷新", command=self._on_refresh).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="清除已完成", command=self._on_clear_completed).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="清空队列", command=self._on_purge_queue).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="暂停打印机", command=self._on_pause_printer).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="恢复打印机", command=self._on_resume_printer).pack(side=tk.LEFT, padx=2)
        ttk.Separator(toolbar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=5)
        ttk.Button(toolbar, text="设置", command=self._on_settings).pack(side=tk.LEFT, padx=2)

    # ======================== Treeview作业列表 ========================

    def _build_treeview(self):
        frame = ttk.Frame(self.root)
        frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=2)

        columns = ("job_id", "document", "status", "pages", "owner", "submitted")
        self.tree = ttk.Treeview(frame, columns=columns, show="headings", selectmode="browse")

        # 列定义
        self.tree.heading("job_id", text="ID")
        self.tree.heading("document", text="文档名")
        self.tree.heading("status", text="状态")
        self.tree.heading("pages", text="页数")
        self.tree.heading("owner", text="所有者")
        self.tree.heading("submitted", text="提交时间")

        self.tree.column("job_id", width=50, anchor=tk.CENTER)
        self.tree.column("document", width=250)
        self.tree.column("status", width=80, anchor=tk.CENTER)
        self.tree.column("pages", width=50, anchor=tk.CENTER)
        self.tree.column("owner", width=100)
        self.tree.column("submitted", width=140)

        # 滚动条
        scrollbar = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)

        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # 绑定事件
        self.tree.bind("<Double-1>", self._on_double_click)
        self.tree.bind("<Button-3>", self._on_right_click)

        # 状态颜色标签
        self.tree.tag_configure("printing", foreground="#2196F3")
        self.tree.tag_configure("printed", foreground="#4CAF50")
        self.tree.tag_configure("error", foreground="#F44336")
        self.tree.tag_configure("paused", foreground="#FF9800")
        self.tree.tag_configure("cancelled", foreground="#9E9E9E")

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
        statusbar = ttk.Frame(self.root, padding=3)
        statusbar.pack(fill=tk.X, side=tk.BOTTOM)

        self._printer_label = ttk.Label(statusbar, text="打印机: --")
        self._printer_label.pack(side=tk.LEFT, padx=10)

        self._wechat_label = ttk.Label(statusbar, text="微信: 未连接")
        self._wechat_label.pack(side=tk.LEFT, padx=10)

        self._stats_label = ttk.Label(statusbar, text="")
        self._stats_label.pack(side=tk.RIGHT, padx=10)

    # ======================== 队列轮询 (主线程) ========================

    def _poll_queues(self):
        """从跨线程队列取数据更新GUI (root.after循环)"""
        try:
            job_q = self.queues["job_queue"]
            while True:
                item = job_q.get_nowait()
                if item is None:
                    continue
                action, job = item
                if action == "NEW":
                    self._add_job_to_tree(job)
                elif action == "UPDATE":
                    self._update_job_in_tree(job)
                elif action == "REMOVE":
                    self._remove_job_from_tree(job)
        except queue.Empty:
            pass

        try:
            status_q = self.queues["status_queue"]
            while True:
                item = status_q.get_nowait()
                if item is None:
                    continue
                source, text = item
                if source == "wechat":
                    self._wechat_label.config(text=f"微信: {text}")
                elif source == "printer":
                    self._printer_label.config(text=f"打印机: {text}")
        except queue.Empty:
            pass

        # 继续轮询
        self.root.after(100, self._poll_queues)

    # ======================== Treeview操作 ========================

    def _add_job_to_tree(self, job: dict):
        jid = job["job_id"]
        # 避免重复
        for child in self.tree.get_children():
            if self.tree.item(child, "values")[0] == str(jid):
                return
        tag = job.get("status", "spooling")
        self.tree.insert("", 0, iid=f"job_{jid}", values=(
            jid, job.get("document", ""), job.get("status", ""),
            job.get("pages", 0), job.get("owner", ""),
            job.get("submitted_time", ""),
        ), tags=(tag,))
        self._update_stats()

    def _update_job_in_tree(self, job: dict):
        jid = job["job_id"]
        iid = f"job_{jid}"
        if self.tree.exists(iid):
            self.tree.item(iid, values=(
                jid, job.get("document", ""), job.get("status", ""),
                job.get("pages", 0), job.get("owner", ""),
                job.get("submitted_time", ""),
            ), tags=(job.get("status", "spooling"),))
        self._update_stats()

    def _remove_job_from_tree(self, job):
        if isinstance(job, dict):
            jid = job.get("job_id", job)
        else:
            jid = job
        iid = f"job_{jid}"
        if self.tree.exists(iid):
            self.tree.delete(iid)
        self._update_stats()

    def _update_stats(self):
        """更新状态栏的统计信息"""
        children = self.tree.get_children()
        active = sum(1 for c in children if self.tree.item(c, "tags")[0]
                     in ("spooling", "printing", "paused"))
        self._stats_label.config(text=f"活跃: {active} | 总计: {len(children)}")
        self._printer_label.config(
            text=f"打印机: {self.monitor.printer_name} ({self.monitor.printer_status})"
        )

    # ======================== 工具栏事件 ========================

    def _on_refresh(self):
        """手动刷新(重新EnumJobs一次)"""
        # monitor轮询会自动检测, 这里仅更新状态栏
        self._update_stats()

    def _on_clear_completed(self):
        """清除已完成/已取消的作业行"""
        for child in list(self.tree.get_children()):
            tag = self.tree.item(child, "tags")[0]
            if tag in ("printed", "error", "cancelled", "paperout", "offline"):
                self.tree.delete(child)
        self._update_stats()

    def _on_purge_queue(self):
        """清空整个打印队列"""
        if messagebox.askyesno("确认", "确定要清空整个打印队列吗?"):
            printer = self.config_mgr.get("selected_printer")
            if self.controller.purge_queue(printer):
                for child in self.tree.get_children():
                    self.tree.delete(child)
                self.logger.info("打印队列已清空")
            else:
                messagebox.showerror("错误", "清空队列失败")

    def _on_pause_printer(self):
        printer = self.config_mgr.get("selected_printer")
        self.controller.pause_printer(printer)

    def _on_resume_printer(self):
        printer = self.config_mgr.get("selected_printer")
        self.controller.resume_printer(printer)

    def _on_settings(self):
        from gui.settings_dialog import SettingsDialog
        SettingsDialog(self.root, self.config_mgr, self.monitor, self.logger)

    # ======================== 右键菜单事件 ========================

    def _on_right_click(self, event):
        """右键弹出菜单"""
        item = self.tree.identify_row(event.y)
        if item:
            self.tree.selection_set(item)
            self._context_job_id = int(self.tree.item(item, "values")[0])
            self.context_menu.post(event.x_root, event.y_root)

    def _on_double_click(self, event):
        """双击显示作业详情"""
        item = self.tree.selection()
        if item:
            values = self.tree.item(item[0], "values")
            messagebox.showinfo(
                "作业详情",
                f"ID: {values[0]}\n文档: {values[1]}\n状态: {values[2]}\n"
                f"页数: {values[3]}\n所有者: {values[4]}\n提交: {values[5]}"
            )

    def _on_pause_job(self):
        if self._context_job_id is not None:
            printer = self.config_mgr.get("selected_printer")
            self.controller.pause_job(printer, self._context_job_id)

    def _on_resume_job(self):
        if self._context_job_id is not None:
            printer = self.config_mgr.get("selected_printer")
            self.controller.resume_job(printer, self._context_job_id)

    def _on_restart_job(self):
        if self._context_job_id is not None:
            printer = self.config_mgr.get("selected_printer")
            self.controller.restart_job(printer, self._context_job_id)

    def _on_cancel_job(self):
        if self._context_job_id is not None:
            printer = self.config_mgr.get("selected_printer")
            self.controller.cancel_job(printer, self._context_job_id)

    # ======================== 窗口生命周期 ========================

    def _on_close(self):
        """关闭窗口 → 隐藏到托盘"""
        if self.config_mgr.get("min_to_tray", True):
            self.root.withdraw()
        else:
            self.root.destroy()

    def show(self):
        """从托盘恢复显示"""
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
