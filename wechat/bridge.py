"""微信桥接核心: 基于uiautomation操控微信PC客户端"""
import time
import threading
import os
import shutil
from typing import Optional

import uiautomation as auto


# 微信窗口类名
WECHAT_CLASS = "WeChatMainWndForPC"
# 微信进程名
WECHAT_PROCESS = "WeChat.exe"


class WeChatBridge:
    """微信桥接: 监听消息 + 发送消息 + 文件下载"""

    def __init__(self, config_mgr, command_queue, file_queue, status_queue, logger):
        self.config_mgr = config_mgr
        self.command_queue = command_queue
        self.file_queue = file_queue
        self.status_queue = status_queue
        self.logger = logger
        self.running = False
        self.available = False
        self._wx_window: Optional[auto.WindowControl] = None
        self._thread: Optional[threading.Thread] = None
        self._last_msg_count = 0  # 跟踪上次消息数量, 检测新消息

    @property
    def is_available(self) -> bool:
        return self.available

    def start(self):
        """启动微信监听线程"""
        self.running = True
        self._thread = threading.Thread(
            target=self._run_loop, name="WeChatBridge", daemon=True
        )
        self._thread.start()
        self.logger.info("微信桥接线程已启动")

    def stop(self):
        """停止监听"""
        self.running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        self.logger.info("微信桥接已停止")

    def _find_wechat(self) -> bool:
        """查找微信主窗口"""
        try:
            self._wx_window = auto.WindowControl(
                Name='微信', ClassName=WECHAT_CLASS
            )
            if self._wx_window.Exists(1, 1):
                self.available = True
                return True
        except Exception:
            pass
        self.available = False
        self._wx_window = None
        return False

    def _run_loop(self):
        """主循环: 定期检测微信状态 + 读取消息"""
        reconnect_delay = 10  # 重连间隔(秒)

        while self.running:
            if not self._find_wechat():
                # 微信未运行或窗口找不到
                if self.available:
                    self.status_queue.put(("wechat", "未连接"))
                    self.available = False
                time.sleep(reconnect_delay)
                continue

            # 微信已找到
            if not self.available:
                self.available = True
                self.status_queue.put(("wechat", "已连接"))
                self.logger.info("微信已连接")

            # 检查是否有新消息
            try:
                self._check_new_messages()
            except Exception as e:
                self.logger.debug(f"检查消息异常: {e}")
                self.available = False

            time.sleep(2)

    def _check_new_messages(self):
        """检查当前聊天窗口的新消息"""
        if not self._wx_window:
            return

        # 获取当前会话的聊天列表
        try:
            # 会话列表在微信窗口左侧
            session_list = self._wx_window.ListControl(Name='会话')
            if not session_list.Exists(0, 0):
                return

            # 获取列表项
            items = session_list.GetChildren()
            if not items:
                return

            # 遍历监听的会话 (简化版: 通过搜索和选择目标聊天)
            targets = self.config_mgr.get("wechat_listen_targets", ["文件传输助手"])
            for target in targets:
                self._check_chat_messages(target)

        except Exception as e:
            self.logger.debug(f"消息检查失败: {e}")

    def _check_chat_messages(self, chat_name: str):
        """检查指定聊天的未读消息"""
        try:
            # 搜索并点击目标聊天
            search_edit = self._wx_window.EditControl(Name='搜索')
            if not search_edit.Exists(0, 0):
                return

            # 点击搜索框, 输入聊天名称
            search_edit.Click()
            auto.SendKeys('{Ctrl}a{Delete}')
            auto.SendKeys(chat_name)
            time.sleep(0.3)

            # 选择第一个搜索结果
            result = self._wx_window.ListItemControl(Name=chat_name)
            if result.Exists(0, 0.5):
                result.Click()
                time.sleep(0.2)

                # 读取最新消息
                self._read_latest_message(chat_name)
        except Exception as e:
            self.logger.debug(f"聊天 {chat_name} 检查异常: {e}")

    def _read_latest_message(self, chat_name: str):
        """读取当前聊天窗口的最新消息"""
        try:
            # 消息列表区域
            msg_list = self._wx_window.ListControl(Name='消息')
            if not msg_list.Exists(0, 0):
                return

            msgs = msg_list.GetChildren()
            if not msgs:
                return

            # 检查是否有新消息 (比较数量)
            current_count = len(msgs)
            if current_count > self._last_msg_count:
                # 有新消息, 读取最后一条
                last_msg = msgs[-1]
                msg_text = last_msg.Name or ""

                self.logger.info(f"[微信] {chat_name}: {msg_text[:50]}")

                # 判断是否为命令
                if msg_text.startswith("/"):
                    self.command_queue.put((chat_name, msg_text))

                self._last_msg_count = current_count

        except Exception as e:
            self.logger.debug(f"读取消息异常: {e}")

    # ======================== 发送消息 ========================

    def send_message(self, target: str, text: str) -> bool:
        """向指定联系人/群发送消息"""
        if not self.available or not self._wx_window:
            return False

        try:
            # 搜索目标
            search_edit = self._wx_window.EditControl(Name='搜索')
            if not search_edit.Exists(0, 0):
                return False

            search_edit.Click()
            auto.SendKeys('{Ctrl}a{Delete}')
            auto.SendKeys(target)
            time.sleep(0.3)

            result = self._wx_window.ListItemControl(Name=target)
            if result.Exists(0, 0.5):
                result.Click()
                time.sleep(0.2)

                # 找到输入框并发送
                input_edit = self._wx_window.EditControl(Name='输入')
                if not input_edit.Exists(0, 0):
                    # 尝试通过类名找
                    input_edit = self._wx_window.EditControl()
                if input_edit.Exists(0, 0):
                    input_edit.Click()
                    auto.SendKeys(text)
                    auto.SendKeys('{Enter}')
                    self.logger.info(f"[微信] 发送 → {target}: {text[:50]}")
                    return True

        except Exception as e:
            self.logger.error(f"发送消息失败: {e}")

        return False

    # ======================== 文件操作 ========================

    def receive_file(self, chat_name: str, save_dir: str) -> Optional[str]:
        """尝试下载当前聊天窗口的最新文件 (实验性)"""
        # uiautomation 无法直接通过API下载微信文件
        # 此方法作为扩展接口预留
        # 微信文件通常保存在: Documents\WeChat Files\wxid_xxx\FileStorage\
        self.logger.debug(f"文件接收功能需微信客户端手动下载, 暂不支持自动下载")
        return None

    def find_latest_received_file(self) -> Optional[str]:
        """查找微信最近接收的文件"""
        # 微信默认文件存储路径
        wechat_files = os.path.join(
            os.environ.get("USERPROFILE", "C:\\Users\\wsn"),
            "Documents", "WeChat Files"
        )
        if not os.path.exists(wechat_files):
            return None

        latest = None
        latest_time = 0

        # 遍历所有微信账号目录
        for account_dir in os.listdir(wechat_files):
            if not account_dir.startswith("wxid_"):
                continue
            file_storage = os.path.join(wechat_files, account_dir, "FileStorage", "File")
            if not os.path.exists(file_storage):
                continue
            # 按月组织的目录
            for month_dir in os.listdir(file_storage):
                month_path = os.path.join(file_storage, month_dir)
                if not os.path.isdir(month_path):
                    continue
                for fname in os.listdir(month_path):
                    fpath = os.path.join(month_path, fname)
                    try:
                        mtime = os.path.getmtime(fpath)
                        if mtime > latest_time:
                            latest_time = mtime
                            latest = fpath
                    except OSError:
                        continue

        return latest
