"""打印作业的界面状态；不依赖 Tk 或打印设备。"""

TERMINAL_STATUSES = frozenset({'printed', 'cancelled', 'removed'})
ATTENTION_STATUSES = frozenset({'paused', 'paperout', 'offline', 'error'})
STATUS_LABELS = {
    'spooling': '排队中', 'printing': '打印中', 'printed': '已完成',
    'paused': '已暂停', 'cancelled': '已取消', 'removed': '已移除',
    'paperout': '缺纸', 'offline': '脱机', 'error': '异常',
    'sent': '已发送至打印机', 'cancelling': '正在取消',
}
FILTERS = {'全部状态': 'all', '进行中': 'active', '需处理': 'attention', '已结束': 'finished'}


class JobViewModel:
    def __init__(self, printer_name=''):
        self.printer_name = printer_name
        self.jobs = {}

    def key(self, job):
        identity = job.get('record_id')
        if identity is None:
            identity = job.get('job_id')
        return repr((job.get('printer_name', self.printer_name),
                     identity))

    def upsert(self, job):
        key = self.key(job)
        merged = dict(self.jobs.get(key, {}))
        merged.update(job)
        merged.setdefault('printer_name', self.printer_name)
        merged.setdefault('status', 'spooling')
        self.jobs[key] = merged
        return key

    def remove(self, job):
        if isinstance(job, dict):
            self.jobs.pop(self.key(job), None)

    def reset(self):
        self.jobs.clear()

    @staticmethod
    def group(status):
        if status in TERMINAL_STATUSES:
            return 'finished'
        return 'attention' if status in ATTENTION_STATUSES else 'active'

    def counts(self):
        counts = dict(active=0, attention=0, finished=0, total=len(self.jobs))
        for job in self.jobs.values():
            counts[self.group(job.get('status'))] += 1
        return counts

    def visible(self, query='', status_filter='all'):
        query = query.strip().casefold()
        return [(key, job) for key, job in reversed(tuple(self.jobs.items()))
                if (status_filter == 'all' or self.group(job.get('status')) == status_filter)
                and (not query or query in str(job.get('document', '')).casefold()
                     or query in str(job.get('owner', '')).casefold())]

    def clear_finished(self):
        keys = [key for key, job in self.jobs.items() if job.get('status') in TERMINAL_STATUSES]
        for key in keys:
            del self.jobs[key]
        return len(keys)

    @staticmethod
    def status_text(status):
        return STATUS_LABELS.get(status, status or '未知')

    @staticmethod
    def actions_for(status):
        if status in TERMINAL_STATUSES or status == 'cancelling':
            return set()
        return {'resume' if status == 'paused' else 'pause', 'restart', 'cancel'}
