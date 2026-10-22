import tkinter as tk
import unittest

from panel.card_ui import COLORS, MODE_SIZES, PrinterCardView
from panel.printer_backend import JobInfo, PrinterInfo, Snapshot


class PanelCardTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
            self.root.withdraw()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        self.view = PrinterCardView(self.root)

    def tearDown(self):
        if hasattr(self, 'root'):
            self.root.destroy()

    def snapshot(self, jobs=5):
        real = PrinterInfo('测试办公室打印机', 'USB001', '测试驱动', True, False,
                           0, 0, False, '就绪', 'ok', jobs)
        virtual = PrinterInfo('测试 PDF', 'PORTPROMPT:', '测试驱动', False, True,
                              0, 0, False, '就绪', 'ok', 0)
        queue = tuple(JobInfo(index, '这是测试长文档名称' * 8 + '.pdf', '打印中',
                              'info', 3, '测试提交者', '10:30') for index in range(jobs))
        return Snapshot(selected_name=real.name, printers=(real, virtual), jobs=queue)

    def text_items(self):
        return [self.view.canvas.itemcget(item, 'text') for item in self.view.canvas.find_all()
                if self.view.canvas.type(item) == 'text']

    def test_light_palette_and_existing_dimensions(self):
        self.assertEqual(COLORS['bg'], '#F4F6F8')
        self.assertEqual(COLORS['card'], '#FFFFFF')
        self.assertEqual(self.view.render(self.snapshot(), '测试办公室打印机', False), 280)
        self.assertEqual(self.view.set_mode('taskbar'), MODE_SIZES['taskbar'])
        self.assertEqual(self.view.render(self.snapshot(), None, False), 46)

    def test_hit_regions_and_scroll_remain_available(self):
        self.view.render(self.snapshot(), '测试办公室打印机', False)
        self.assertEqual(self.view.hit_test(30, 100)[0], 'job')
        self.assertEqual(self.view.hit_test(30, 196)[0], 'printer')
        self.assertEqual(self.view.hit_test(30, 215)[0], 'virtual_toggle')
        self.assertTrue(self.view.scroll(99))
        self.assertEqual(self.view.job_offset, 2)
        self.assertFalse(self.view.scroll(1))
        self.view.set_mode('taskbar')
        self.assertFalse(self.view.scroll(-1))

    def test_empty_and_long_names_render_without_changing_source_data(self):
        self.view.render(Snapshot(), None, False)
        self.assertIn('队列为空', self.text_items())
        snapshot = self.snapshot(1)
        original = snapshot.jobs[0].document
        self.view.render(snapshot, snapshot.selected_name, False)
        self.assertEqual(snapshot.jobs[0].document, original)
        self.assertTrue(any(text.endswith('…') for text in self.text_items()))

    def test_stale_snapshot_is_explicit_in_both_modes(self):
        from dataclasses import replace
        snapshot = replace(self.snapshot(1), jobs_fresh=False, spooler_ok=False, error='测试：队列读取失败')
        self.view.render(snapshot, snapshot.selected_name, False)
        self.assertIn('队列读取失败', self.text_items())
        self.assertTrue(any('已过期' in text for text in self.text_items()))
        self.view.set_mode('taskbar')
        self.assertIn('读取失败', self.text_items())

    def test_long_status_and_page_count_do_not_overlap(self):
        from dataclasses import replace
        snapshot = self.snapshot(1)
        job = replace(snapshot.jobs[0], pages=999, status='正在后台处理')
        self.view.render(replace(snapshot, jobs=(job,)), snapshot.selected_name, False)
        labels = [self.view.canvas.bbox(item) for item in self.view.canvas.find_all()
                  if self.view.canvas.type(item) == 'text'
                  and 94 <= self.view.canvas.coords(item)[1] <= 100]
        labels.sort()
        self.assertEqual(len(labels), 3)
        for left, right in zip(labels, labels[1:]):
            self.assertLessEqual(left[2], right[0])


if __name__ == '__main__':
    unittest.main()
