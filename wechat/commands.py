"""命令解析与执行: /status /print /cancel /pause /resume /list /help"""
from printer.controller import PrintController


# 支持的命令白名单
COMMANDS = {
    "/status": "查看打印机状态和队列",
    "/list": "列出所有排队作业",
    "/cancel": "取消作业 /cancel <id> 或 /cancel all",
    "/pause": "暂停打印机",
    "/resume": "恢复打印机",
    "/print": "提交文件打印 /print <文件路径>",
    "/help": "显示此帮助信息",
}


class CommandExecutor:
    """命令执行器: 解析 + 执行 + 生成回复"""

    def __init__(self, controller: PrintController, monitor, config_mgr, logger):
        self.controller = controller
        self.monitor = monitor
        self.config_mgr = config_mgr
        self.logger = logger

    def execute(self, raw_text: str, sender: str) -> str:
        """执行命令并返回回复文本"""
        raw_text = raw_text.strip()
        parts = raw_text.split(maxsplit=1)
        command = parts[0].lower() if parts else ""
        args = parts[1] if len(parts) > 1 else ""

        printer = self.config_mgr.get("selected_printer")

        if command == "/status":
            return self._cmd_status(printer)
        elif command == "/list":
            return self._cmd_list(printer)
        elif command == "/cancel":
            return self._cmd_cancel(printer, args)
        elif command == "/pause":
            return self._cmd_pause(printer)
        elif command == "/resume":
            return self._cmd_resume(printer)
        elif command == "/print":
            return self._cmd_print(printer, args)
        elif command == "/help":
            return self._cmd_help()
        else:
            return f"未知命令: {command}\n发送 /help 查看可用命令"

    def _cmd_status(self, printer: str) -> str:
        summary = self.monitor.get_status_summary()
        lines = [
            f"打印机: {summary['printer']}",
            f"状态: {summary['status']}",
            f"活跃作业: {summary['active_jobs']} 个",
        ]
        for job in summary["jobs"]:
            lines.append(f"  #{job['job_id']} [{job['status']}] {job['document']}")
        return "\n".join(lines)

    def _cmd_list(self, printer: str) -> str:
        summary = self.monitor.get_status_summary()
        if not summary["jobs"]:
            return "队列为空"
        lines = [f"当前队列 ({len(summary['jobs'])} 个作业):"]
        for job in summary["jobs"]:
            lines.append(f"  #{job['job_id']} [{job['status']}] {job['document']} ({job.get('pages', 0)}页)")
        return "\n".join(lines)

    def _cmd_cancel(self, printer: str, args: str) -> str:
        args = args.strip()
        if not args:
            return "用法: /cancel <作业ID> 或 /cancel all"

        if args.lower() == "all":
            if self.controller.purge_queue(printer):
                return "已清空整个打印队列"
            return "清空队列失败"

        try:
            job_id = int(args)
            if self.controller.cancel_job(printer, job_id):
                return f"作业 #{job_id} 已取消"
            return f"取消作业 #{job_id} 失败"
        except ValueError:
            return f"无效的作业ID: {args}"

    def _cmd_pause(self, printer: str) -> str:
        if self.controller.pause_printer(printer):
            return f"打印机 {printer} 已暂停"
        return "暂停失败"

    def _cmd_resume(self, printer: str) -> str:
        if self.controller.resume_printer(printer):
            return f"打印机 {printer} 已恢复"
        return "恢复失败"

    def _cmd_print(self, printer: str, args: str) -> str:
        path = args.strip().strip('"')
        if not path:
            return "用法: /print <文件完整路径>"
        import os
        if not os.path.exists(path):
            return f"文件不存在: {path}"
        if self.controller.print_file(path, printer):
            return f"已提交打印: {os.path.basename(path)}"
        return "提交打印失败"

    def _cmd_help(self) -> str:
        lines = ["PrintBridge 命令列表:"]
        for cmd, desc in COMMANDS.items():
            lines.append(f"  {cmd}  — {desc}")
        return "\n".join(lines)
