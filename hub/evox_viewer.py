"""Small local-only desktop window for conversations routed through SUMMON to EvoX."""
import argparse
import json
from pathlib import Path
import tkinter as tk
from tkinter import ttk


class ConversationWindow:
    def __init__(self, path):
        self.path = Path(path)
        self.offset = 0
        self.root = tk.Tk()
        self.root.title('SUMMON · EvoX 云端会话')
        self.root.geometry('760x620')
        self.root.minsize(520, 400)
        self.root.lift()
        self.root.focus_force()
        self.root.after(1200, lambda: self.root.attributes('-topmost', False))
        self.root.configure(bg='#10151d')
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('TFrame', background='#10151d')
        style.configure('Header.TLabel', background='#10151d', foreground='#f1f5f9', font=('Segoe UI', 15, 'bold'))
        style.configure('Hint.TLabel', background='#10151d', foreground='#9aa9bb', font=('Segoe UI', 9))
        outer = ttk.Frame(self.root, padding=18)
        outer.pack(fill='both', expand=True)
        ttk.Label(outer, text='SUMMON · EvoX 云端会话', style='Header.TLabel').pack(anchor='w')
        ttk.Label(outer, text='Passport / 云端 Agent 的消息与本地 EvoX 回复 · 仅保存在本机', style='Hint.TLabel').pack(anchor='w', pady=(4, 12))
        body = ttk.Frame(outer)
        body.pack(fill='both', expand=True)
        self.text = tk.Text(body, wrap='word', state='disabled', padx=16, pady=14,
                            bg='#171e29', fg='#dce6f2', insertbackground='white',
                            relief='flat', font=('Segoe UI', 10), spacing1=3, spacing3=9)
        scroll = ttk.Scrollbar(body, orient='vertical', command=self.text.yview)
        self.text.configure(yscrollcommand=scroll.set)
        self.text.pack(side='left', fill='both', expand=True)
        scroll.pack(side='right', fill='y')
        self.text.tag_configure('time', foreground='#718096', font=('Segoe UI', 8))
        self.text.tag_configure('user', foreground='#76d6b0', font=('Segoe UI', 10, 'bold'))
        self.text.tag_configure('agent', foreground='#94baff', font=('Segoe UI', 10, 'bold'))
        self.text.tag_configure('system', foreground='#ffc978', font=('Segoe UI', 9))
        ttk.Label(outer, text='保持此窗口打开，即可查看后续云端对话。电脑命令及输出不会显示在这里。', style='Hint.TLabel').pack(anchor='w', pady=(10, 0))
        self.root.after(250, self.refresh)

    def refresh(self):
        try:
            if not self.path.exists():
                self.root.after(500, self.refresh)
                return
            size = self.path.stat().st_size
            if size < self.offset:
                self.offset = 0
            with self.path.open('r', encoding='utf-8') as stream:
                stream.seek(self.offset)
                lines = stream.readlines()
                self.offset = stream.tell()
            for line in lines:
                try:
                    item = json.loads(line)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                self.add(item)
        except OSError:
            pass
        self.root.after(500, self.refresh)

    def add(self, item):
        role = item.get('role', 'system')
        labels = {'user': '云端消息', 'agent': 'EvoX', 'system': '状态'}
        tag = role if role in ('user', 'agent') else 'system'
        self.text.configure(state='normal')
        self.text.insert('end', f"{labels.get(role, '状态')}  {item.get('at', '')}\n", ('time', tag))
        self.text.insert('end', str(item.get('text', ''))[:1200] + '\n\n', tag)
        self.text.configure(state='disabled')
        self.text.see('end')

    def run(self):
        self.root.mainloop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--log-file', required=True)
    ConversationWindow(parser.parse_args().log_file).run()


if __name__ == '__main__':
    main()
