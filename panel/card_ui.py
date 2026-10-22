"""桌面卡片与任务栏条的浅色紧凑绘制。"""
from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from typing import Optional

from gui.theme import COLORS as THEME_COLORS
from .printer_backend import JobInfo, PrinterInfo, Snapshot

MODE_SIZES = {"full": (320, 280), "taskbar": (184, 46)}

COLORS = dict(THEME_COLORS, card=THEME_COLORS['surface'], panel=THEME_COLORS['bg'],
              highlight=THEME_COLORS['selection'], dim=THEME_COLORS['muted'],
              info=THEME_COLORS['accent'])

M = 14              # 左右边距
JOB_ROW_H = 22
PRINTER_ROW_H = 22
JOB_VISIBLE = 3


def _round_rect(canvas: tk.Canvas, x0: int, y0: int, x1: int, y1: int,
                radius: int, **kwargs) -> int:
    points = [
        x0 + radius, y0, x1 - radius, y0, x1, y0,
        x1, y0 + radius, x1, y1 - radius, x1, y1,
        x1 - radius, y1, x0 + radius, y1, x0, y1,
        x0, y1 - radius, x0, y0 + radius, x0, y0,
    ]
    return canvas.create_polygon(points, smooth=True, **kwargs)


class PrinterCardView:
    """负责全部绘制与命中区域；业务与窗口逻辑在 app.py。"""

    def __init__(self, root: tk.Tk, mode: str = "full") -> None:
        self.canvas = tk.Canvas(root, highlightthickness=0, bd=0, bg=COLORS["bg"])
        self.canvas.pack(fill="both", expand=True)
        self.mode = mode if mode in MODE_SIZES else "full"
        self.size = MODE_SIZES[self.mode]
        self._font = {
            "title": tkfont.Font(root=root, family="Microsoft YaHei UI", size=11, weight="bold"),
            "name": tkfont.Font(root=root, family="Microsoft YaHei UI", size=10, weight="bold"),
            "body": tkfont.Font(root=root, family="Microsoft YaHei UI", size=9),
            "small": tkfont.Font(root=root, family="Microsoft YaHei UI", size=8),
            "tiny": tkfont.Font(root=root, family="Microsoft YaHei UI", size=8),
        }
        self._hit: list[tuple[int, int, int, int, str, object]] = []
        self._snapshot: Optional[Snapshot] = None
        self._selected: Optional[str] = None
        self._show_virtual = False
        self._job_offset = 0
        self._last_render: Optional[tuple[Snapshot, Optional[str], bool]] = None

    # ---- 对外接口 ----
    def set_mode(self, mode: str) -> tuple[int, int]:
        if mode in MODE_SIZES:
            self.mode = mode
            self.size = MODE_SIZES[mode]
        self._job_offset = 0
        if self._last_render:
            self.render(*self._last_render, resize=False)
        return self.size

    def render(self, snapshot: Snapshot, selected: Optional[str],
               show_virtual: bool, resize: bool = True) -> int:
        self._snapshot = snapshot
        self._selected = selected
        self._show_virtual = show_virtual
        self._last_render = (snapshot, selected, show_virtual)
        if self.mode == "taskbar":
            self._render_taskbar(snapshot)
            return self.size[1]
        return self._render_full(snapshot, selected, show_virtual)

    def hit_test(self, x: int, y: int) -> Optional[tuple[str, object]]:
        for x0, y0, x1, y1, kind, payload in reversed(self._hit):
            if x0 <= x <= x1 and y0 <= y <= y1:
                return kind, payload
        return None

    def scroll(self, delta: int) -> bool:
        if self.mode != "full" or not self._snapshot:
            return False
        total = len(self._snapshot.jobs)
        maximum = max(0, total - JOB_VISIBLE)
        new_offset = min(max(0, self._job_offset + delta), maximum)
        if new_offset == self._job_offset:
            return False
        self._job_offset = new_offset
        if self._last_render:
            self.render(*self._last_render)
        return True

    @property
    def job_offset(self) -> int:
        return self._job_offset

    # ---- 完整卡片 ----
    def _render_full(self, snapshot: Snapshot, selected: Optional[str],
                     show_virtual: bool) -> int:
        canvas = self.canvas
        canvas.delete("all")
        self._hit.clear()
        w = self.size[0]
        printers = list(snapshot.printers)
        real = [p for p in printers if not p.is_virtual]
        virtual = [p for p in printers if p.is_virtual]
        rows = len(real) + (len(virtual) if show_virtual else 0)
        h = 280 + max(0, rows - 2) * PRINTER_ROW_H
        h = min(h, 420)
        self.size = (w, h)

        _round_rect(canvas, 1, 1, w - 1, h - 1, 8,
                    fill=COLORS["card"], outline=COLORS["border"], width=1)

        current = next((p for p in printers if p.name == selected), None)
        if current is None and printers:
            current = next((p for p in printers if p.is_default), printers[0])

        # 标题行
        canvas.create_text(M, 16, text="打印机", anchor="nw",
                           font=self._font["title"], fill=COLORS["text"])
        status_text = current.status if current else "无打印机"
        status_color = COLORS.get(current.level, COLORS["muted"]) if current else COLORS["muted"]
        if not snapshot.jobs_fresh:
            status_text, status_color = '队列读取失败', COLORS['error']
        canvas.create_text(w - M, 18, text=status_text, anchor="ne",
                           font=self._font["small"], fill=status_color)
        text_w = self._font["small"].measure(status_text)
        self._dot(w - M - text_w - 10, 24, status_color)

        # 当前查看的打印机
        name = current.name if current else "未检测到打印机"
        canvas.create_text(M, 38, text=self._fit(name, self._font["name"], w - 2 * M),
                           anchor="nw", font=self._font["name"], fill=COLORS["text"])
        subtitle = f"{current.port or '未知端口'}" if current else "请检查打印服务"
        if current and current.is_default:
            subtitle += " · 默认"
        canvas.create_text(M, 57, text=self._fit(subtitle, self._font['small'], w - 2 * M), anchor="nw",
                           font=self._font["small"], fill=COLORS["dim"])

        canvas.create_line(M, 72, w - M, 72, fill=COLORS["border"])

        # 队列
        total_jobs = len(snapshot.jobs)
        canvas.create_text(M, 78, text="队列", anchor="nw",
                           font=self._font["small"], fill=COLORS["dim"])
        queue_hint = f"{total_jobs} 个任务" if total_jobs else "空"
        if not snapshot.jobs_fresh:
            queue_hint += ' · 已过期'
        if total_jobs > JOB_VISIBLE:
            queue_hint += " · 滚轮"
        canvas.create_text(w - M, 78, text=queue_hint, anchor="ne",
                           font=self._font["tiny"], fill=COLORS["dim"])

        if total_jobs == 0:
            canvas.create_text(M + 2, 98, text="队列为空", anchor="nw",
                               font=self._font["small"], fill=COLORS["muted"])
        else:
            maximum = max(0, total_jobs - JOB_VISIBLE)
            self._job_offset = min(self._job_offset, maximum)
            visible = snapshot.jobs[self._job_offset:self._job_offset + JOB_VISIBLE]
            for index, job in enumerate(visible):
                self._draw_job(job, 94 + index * JOB_ROW_H, w)

        canvas.create_line(M, 166, w - M, 166, fill=COLORS["border"])

        # 打印机列表
        canvas.create_text(M, 172, text="打印机", anchor="nw",
                           font=self._font["small"], fill=COLORS["dim"])
        canvas.create_text(w - M, 172, text=f"{len(real)} 台真实", anchor="ne",
                           font=self._font["tiny"], fill=COLORS["dim"])

        y = 188
        for printer in real:
            self._draw_printer_row(printer, y, w, selected)
            y += PRINTER_ROW_H

        if show_virtual:
            for printer in virtual:
                self._draw_printer_row(printer, y, w, selected)
                y += PRINTER_ROW_H

        if virtual:
            if show_virtual:
                fold_text = "▾ 收起虚拟打印机"
            else:
                fold_text = f"▸ 更多 {len(virtual)} 台虚拟打印机"
            canvas.create_text(M, y + 3, text=fold_text, anchor="nw",
                               font=self._font["tiny"], fill=COLORS["accent"])
            self._hit.append((M - 4, y, w - M, y + 19, "virtual_toggle", None))

        # 页脚
        footer = snapshot.error or "★ 默认 · 左键查看 · 右键操作"
        footer_color = COLORS["error"] if snapshot.error else COLORS["dim"]
        canvas.create_text(M, h - 18, text=self._fit(footer, self._font['tiny'], w - 2 * M), anchor="nw",
                           font=self._font["tiny"], fill=footer_color)
        return h

    def _draw_job(self, job: JobInfo, y: int, w: int) -> None:
        color = COLORS.get(job.level, COLORS["muted"])
        self._dot(M + 3, y + 8, color)
        status = self._fit(job.status, self._font['tiny'], 110)
        status_width = self._font['tiny'].measure(status)
        pages = f"{job.pages} 页" if job.pages else ""
        right_reserve = status_width + (self._font['tiny'].measure(pages) + 8 if pages else 0) + 12
        max_w = w - 2 * M - 14 - right_reserve
        self.canvas.create_text(
            M + 13, y + 1, text=self._fit(job.document, self._font["small"], max_w),
            anchor="nw", font=self._font["small"], fill=COLORS["text"],
        )
        if pages:
            self.canvas.create_text(w - M - status_width - 8, y + 2, text=pages, anchor="ne",
                                    font=self._font["tiny"], fill=COLORS["dim"])
        self.canvas.create_text(w - M, y + 2, text=status, anchor="ne",
                                font=self._font["tiny"], fill=color)
        self._hit.append((M - 4, y, w - M, y + JOB_ROW_H, "job", job))

    def _draw_printer_row(self, printer: PrinterInfo, y: int, w: int,
                          selected: Optional[str]) -> None:
        if printer.name == selected:
            _round_rect(self.canvas, M - 6, y, w - M + 4, y + PRINTER_ROW_H - 4, 8,
                        fill=COLORS["highlight"], outline="")
        color = COLORS.get(printer.level, COLORS["muted"])
        self._dot(M + 2, y + 8, color)
        star = "★ " if printer.is_default else ""
        right = printer.status
        if printer.job_count:
            right += f" · {printer.job_count}"
        right = self._fit(right, self._font['tiny'], 120)
        max_w = w - 2 * M - 26 - self._font['tiny'].measure(right)
        text = f"{star}{printer.name}"
        self.canvas.create_text(
            M + 12, y + 1,
            text=self._fit(text, self._font["small"], max_w),
            anchor="nw", font=self._font["small"],
            fill=COLORS["text"] if printer.is_default else COLORS["text"],
        )
        self.canvas.create_text(w - M, y + 2, text=right, anchor="ne",
                                font=self._font["tiny"], fill=color)
        self._hit.append((M - 6, y, w - M + 4, y + PRINTER_ROW_H - 4, "printer", printer))

    # ---- 任务栏条 ----
    def _render_taskbar(self, snapshot: Snapshot) -> None:
        canvas = self.canvas
        canvas.delete("all")
        self._hit.clear()
        w, h = self.size
        _round_rect(canvas, 1, 1, w - 1, h - 1, 6,
                    fill=COLORS["card"], outline=COLORS["border"], width=1)

        current = next((p for p in snapshot.printers
                        if p.name == snapshot.selected_name), None)
        if not snapshot.jobs_fresh:
            label, color = '读取失败', COLORS['error']
        elif snapshot.error and not snapshot.spooler_ok:
            label, color = "服务未运行", COLORS["error"]
        elif current:
            label, color = current.status, COLORS.get(current.level, COLORS["muted"])
        else:
            label, color = "无打印机", COLORS["muted"]

        self._draw_printer_icon(12, 15, color)
        canvas.create_text(34, 6, text="打印机", anchor="nw",
                           font=self._font["tiny"], fill=COLORS["dim"])
        canvas.create_text(34, 21, text=label, anchor="nw",
                           font=self._font["small"], fill=color)
        count = len(snapshot.jobs)
        if count:
            canvas.create_text(w - 12, 21, text=f"{count} 个", anchor="ne",
                               font=self._font["tiny"], fill=COLORS["muted"])

    def _draw_printer_icon(self, x: int, y: int, color: str) -> None:
        canvas = self.canvas
        canvas.create_rectangle(x, y + 3, x + 15, y + 11, outline=color, width=1)
        canvas.create_rectangle(x + 7, y - 1, x + 13, y + 4, outline=color, width=1)
        canvas.create_rectangle(x + 3, y + 11, x + 12, y + 15, outline=color, width=1)
        canvas.create_oval(x + 11, y + 5, x + 13, y + 7, fill=color, outline="")

    # ---- 小工具 ----
    def _fit(self, text: str, font: tkfont.Font, max_width: int) -> str:
        if font.measure(text) <= max_width:
            return text
        ellipsis = "…"
        while text and font.measure(text + ellipsis) > max_width:
            text = text[:-1]
        return (text + ellipsis) if text else ellipsis

    def _dot(self, x: float, y: float, color: str) -> None:
        self.canvas.create_oval(x - 3, y - 3, x + 3, y + 3, fill=color, outline="")
