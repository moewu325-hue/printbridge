"""PrintBridge CLI — 供 Claude Code / wechat-claude-code 调用的命令行接口

用法:
  python printbridge_cli.py --status          # 查看打印机状态
  python printbridge_cli.py --list            # 列出队列中的作业
  python printbridge_cli.py --print <文件名>   # 搜索并打印文件
  python printbridge_cli.py --print <完整路径> # 打印指定路径的文件
  python printbridge_cli.py --cancel <作业ID>  # 取消作业
  python printbridge_cli.py --cancel all      # 清空队列
  python printbridge_cli.py --search <关键词>  # 搜索微信接收的文件
"""
import argparse
import os
import sys
import glob
import time


def main():
    parser = argparse.ArgumentParser(description="PrintBridge CLI")
    parser.add_argument("--status", action="store_true", help="查看打印机状态")
    parser.add_argument("--list", action="store_true", help="列出队列中的作业")
    parser.add_argument("--print", type=str, metavar="FILE", help="打印文件(文件名或路径)")
    parser.add_argument("--search", type=str, metavar="KEYWORD", help="搜索微信接收的文件")
    parser.add_argument("--cancel", type=str, metavar="JOB_ID", help="取消作业 (ID 或 'all')")
    parser.add_argument("--json", action="store_true", help="JSON格式输出")

    args = parser.parse_args()

    if args.status:
        cmd_status(args.json)
    elif args.list:
        cmd_list(args.json)
    elif args.print:
        cmd_print(args.print, args.json)
    elif args.search:
        cmd_search(args.search, args.json)
    elif args.cancel:
        cmd_cancel(args.cancel, args.json)
    else:
        parser.print_help()


# ======================== 文件搜索 ========================

def find_wechat_file_storage() -> str:
    """找到微信文件存储根目录"""
    userprofile = os.environ.get("USERPROFILE", "C:\\Users\\wsn")
    base = os.path.join(userprofile, "Documents", "WeChat Files")
    if not os.path.exists(base):
        return ""
    # 遍历所有 wxid_ 账号目录
    for account in os.listdir(base):
        if not account.startswith("wxid_"):
            continue
        file_dir = os.path.join(base, account, "FileStorage", "File")
        if os.path.exists(file_dir):
            return file_dir
    return ""


def _get_search_dirs() -> list:
    """获取需要搜索的目录列表(兼容桌面重定向到D盘)"""
    dirs = []
    for env_key in ("USERPROFILE", "HOMEDRIVE", "HOMEPATH"):
        base = os.environ.get(env_key, "")
        if base:
            for sub in ("Desktop", "Downloads", "Documents"):
                d = os.path.join(base, sub)
                if os.path.isdir(d):
                    dirs.append(d)
    # 常见中文路径
    for extra in (r"D:\桌面", r"D:\下载", r"D:\文档"):
        if os.path.isdir(extra):
            dirs.append(extra)
    # 去重
    return list(dict.fromkeys(dirs))


def search_files(keyword: str) -> list:
    """在微信文件存储中搜索文件, 返回匹配的路径列表"""
    results = []
    storage = find_wechat_file_storage()
    if not storage:
        return results

    keyword_lower = keyword.lower()
    # 遍历 YYYY-MM 子目录
    for month_dir in os.listdir(storage):
        month_path = os.path.join(storage, month_dir)
        if not os.path.isdir(month_path):
            continue
        for fname in os.listdir(month_path):
            if keyword_lower in fname.lower():
                fpath = os.path.join(month_path, fname)
                results.append(fpath)

    # 按修改时间倒序
    results.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return results


def find_file(target: str) -> str:
    """根据文件名或路径找文件, 返回完整路径或空"""
    # 1. 精确路径
    if os.path.isfile(target):
        return os.path.abspath(target)

    # 2. 在当前目录搜索
    if os.path.isfile(os.path.join(os.getcwd(), target)):
        return os.path.abspath(os.path.join(os.getcwd(), target))

    # 3. 在微信文件存储中模糊搜索
    storage = find_wechat_file_storage()
    if storage:
        target_lower = target.lower()
        best_match = None
        best_time = 0
        for month_dir in os.listdir(storage):
            month_path = os.path.join(storage, month_dir)
            if not os.path.isdir(month_path):
                continue
            for fname in os.listdir(month_path):
                fname_lower = fname.lower()
                # 完全匹配文件名
                if fname_lower == target_lower:
                    return os.path.join(month_path, fname)
                # 部分匹配 (关键词在文件名中)
                if target_lower in fname_lower:
                    fpath = os.path.join(month_path, fname)
                    mtime = os.path.getmtime(fpath)
                    if mtime > best_time:
                        best_time = mtime
                        best_match = fpath
        if best_match:
            return best_match

    # 4. 全局搜索桌面和下载目录
    search_dirs = _get_search_dirs()
    for sdir in search_dirs:
        if not os.path.exists(sdir):
            continue
        for root, dirs, files in os.walk(sdir):
            depth = root.replace(sdir, "").count(os.sep)
            if depth > 2:
                continue
            for fname in files:
                if target.lower() in fname.lower():
                    return os.path.join(root, fname)

    return ""


def human_size(size: int) -> str:
    for unit in ("B", "KB", "MB"):
        if size < 1024:
            return f"{size:.0f}{unit}"
        size /= 1024
    return f"{size:.0f}MB"


# ======================== 命令实现 ========================

def cmd_status(json_output=False):
    """查看打印机状态"""
    try:
        from printer.controller import PrintController
        printers = PrintController.get_all_printers()
        if not printers:
            print("未检测到实体打印机")
            return

        for p in printers:
            info = PrintController.get_printer_info(p["name"])
            status_text = "就绪" if (info and info.get("status", 0) == 0) else "异常"
            jobs = info.get("jobs_count", 0) if info else 0
            print(f"🖨 {p['name']}")
            print(f"   状态: {status_text} | 队列: {jobs} 个作业")
    except Exception as e:
        print(f"错误: {e}")


def cmd_list(json_output=False):
    """列出队列中的作业"""
    try:
        import win32print
        from printer.monitor import decode_status
        from printer.controller import PrintController

        printers = PrintController.get_all_printers()
        if not printers:
            print("未检测到打印机")
            return

        for p in printers:
            try:
                h = win32print.OpenPrinter(p["name"])
                jobs = win32print.EnumJobs(h, 0, 99, 2)
                win32print.ClosePrinter(h)

                print(f"🖨 {p['name']} ({len(jobs)} 个作业)")
                for j in jobs:
                    jid = j.get("JobId", 0)
                    doc = j.get("pDocument", "?")
                    status = decode_status(j.get("Status", 0))
                    pages = j.get("TotalPages", 0) or 0
                    print(f"   #{jid} [{status}] {doc} ({pages}页)")
                if not jobs:
                    print("   (空)")
            except Exception as e:
                print(f"  无法读取: {e}")
    except Exception as e:
        print(f"错误: {e}")


def cmd_print(target: str, json_output=False):
    """搜索并打印文件"""
    filepath = find_file(target)
    if not filepath:
        print(f"❌ 未找到文件: {target}")
        print(f"   提示: 请确保文件已在微信中下载, 或提供完整路径")
        sys.exit(1)

    fname = os.path.basename(filepath)
    fsize = human_size(os.path.getsize(filepath))
    print(f"📄 找到: {fname} ({fsize})")

    # 提交打印
    try:
        from printer.controller import PrintController
        printers = PrintController.get_all_printers()
        if not printers:
            print("❌ 未检测到打印机")
            sys.exit(1)

        printer = printers[0]["name"]
        ok = PrintController.print_file(filepath, printer)
        if ok:
            print(f"✅ 已提交打印: {fname} → {printer}")
        else:
            print(f"❌ 提交打印失败")
            sys.exit(1)
    except Exception as e:
        print(f"❌ 打印失败: {e}")
        sys.exit(1)


def cmd_search(keyword: str, json_output=False):
    """搜索微信接收的文件"""
    files = search_files(keyword)
    if not files:
        print(f"未找到包含 '{keyword}' 的文件")
        return

    print(f"找到 {len(files)} 个匹配文件:")
    for f in files[:10]:  # 最多显示10个
        fname = os.path.basename(f)
        mtime = time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(f)))
        size = human_size(os.path.getsize(f))
        print(f"  📄 {fname}")
        print(f"     {size} | {mtime}")
        print(f"     {f}")
    if len(files) > 10:
        print(f"  ... 还有 {len(files) - 10} 个结果")


def cmd_cancel(job_id: str, json_output=False):
    """取消作业"""
    try:
        from printer.controller import PrintController
        printers = PrintController.get_all_printers()
        if not printers:
            print("未检测到打印机")
            return
        printer = printers[0]["name"]

        if job_id.lower() == "all":
            ok = PrintController.purge_queue(printer)
            print("✅ 打印队列已清空" if ok else "❌ 清空失败")
        else:
            jid = int(job_id)
            ok = PrintController.cancel_job(printer, jid)
            print(f"✅ 作业 #{jid} 已取消" if ok else f"❌ 取消失败")
    except ValueError:
        print(f"无效的作业ID: {job_id}")
    except Exception as e:
        print(f"❌ 错误: {e}")


if __name__ == "__main__":
    main()
