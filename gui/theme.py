"""主窗口、设置和桌面面板共用的浅色工具主题。"""
from tkinter import ttk

COLORS = {
    'bg': '#F4F6F8', 'surface': '#FFFFFF', 'text': '#182330',
    'muted': '#637183', 'border': '#DCE2E9', 'accent': '#2563A6',
    'hover': '#EAF1F8', 'ok': '#28724F', 'warn': '#94621A',
    'error': '#B33D3D', 'selection': '#E2EDF8',
}
FONT = 'Microsoft YaHei UI'


def apply_theme(root):
    root.configure(bg=COLORS['bg'])
    root.option_add('*Font', (FONT, 9))
    style = ttk.Style(root)
    style.theme_use('clam')
    style.configure('.', font=(FONT, 9), background=COLORS['bg'], foreground=COLORS['text'])
    style.configure('TFrame', background=COLORS['bg'])
    style.configure('Surface.TFrame', background=COLORS['surface'])
    style.configure('TLabel', background=COLORS['bg'], foreground=COLORS['text'])
    style.configure('Surface.TLabel', background=COLORS['surface'])
    style.configure('Muted.TLabel', foreground=COLORS['muted'])
    style.configure('Title.TLabel', font=(FONT, 16, 'bold'))
    style.configure('Section.TLabel', font=(FONT, 10, 'bold'))
    style.configure('TButton', padding=(10, 5), relief='flat', borderwidth=1,
                    background=COLORS['surface'], bordercolor=COLORS['border'])
    style.map('TButton', background=[('active', COLORS['hover'])],
              foreground=[('disabled', '#9AA5B1')])
    style.configure('Primary.TButton', background=COLORS['accent'], foreground='white')
    style.map('Primary.TButton', background=[('active', '#1E548E'), ('disabled', '#A9BCD1')],
              foreground=[('disabled', 'white')])
    style.configure('Quiet.TButton', background=COLORS['bg'], foreground=COLORS['muted'])
    style.configure('Treeview', background=COLORS['surface'], fieldbackground=COLORS['surface'],
                    foreground=COLORS['text'], rowheight=32, borderwidth=0)
    style.configure('Treeview.Heading', background='#EDF1F5', foreground=COLORS['muted'],
                    font=(FONT, 9), padding=(8, 8), relief='flat')
    style.map('Treeview', background=[('selected', COLORS['selection'])],
              foreground=[('selected', COLORS['text'])])
    style.configure('TEntry', padding=5, fieldbackground=COLORS['surface'], bordercolor=COLORS['border'])
    style.configure('TCombobox', padding=5, fieldbackground=COLORS['surface'], bordercolor=COLORS['border'])
    style.configure('TNotebook', background=COLORS['bg'], borderwidth=0)
    style.configure('TNotebook.Tab', padding=(18, 8), background=COLORS['bg'])
    style.map('TNotebook.Tab', background=[('selected', COLORS['surface'])],
              foreground=[('selected', COLORS['accent'])])
    style.configure('TLabelframe', background=COLORS['bg'], bordercolor=COLORS['border'])
    style.configure('TLabelframe.Label', font=(FONT, 9, 'bold'))
    return style
