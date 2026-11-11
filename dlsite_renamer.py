from tkinter import filedialog
from tkinter import messagebox
import tkinter as tk
import threading
import queue
import os

import renamer_core as core

VERSION = '4.0'

# Tk is not thread-safe: the worker thread never touches widgets. It puts messages on this queue,
# and the main thread (pump_ui) applies them.
ui_queue = queue.Queue()


def log(message):
    """Thread-safe log: may be called from any thread."""
    ui_queue.put(('log', message))


def call_in_ui(func, *args):
    """Run func(*args) on the Tk main thread."""
    ui_queue.put(('call', func, args))


def pump_ui():
    try:
        while True:
            item = ui_queue.get_nowait()
            if item[0] == 'log':
                text.insert(tk.END, item[1] + "\n")
                text.see(tk.END)
            else:
                item[1](*item[2])
    except queue.Empty:
        pass
    root.after(50, pump_ui)


def set_controls(enabled):
    state = tk.NORMAL if enabled else tk.DISABLED
    for widget in (cbtn_deltext, cbtn_dlcover, cbtn_recursive, cbtn_dryrun, btn):
        widget.config(state=state)
    btn['text'] = "選擇路徑" if enabled else "等待完成"


def start():
    """Button handler (main thread): choose the folder, then process it in a worker thread."""
    # askdirectory()檔案對話框, 選擇目錄, 返回目錄名
    path = filedialog.askdirectory()
    if not path:
        messagebox.showinfo(title="錯誤", message="請選擇路徑!" + "\n")
        return
    options = core.Options(delete_brackets=bool(deltext.get()), download_cover=bool(dlcover.get()),
                           recursive=bool(recursive.get()), dry_run=bool(dryrun.get()))
    set_controls(False)
    threading.Thread(target=work, args=(path, options), daemon=True).start()


def work(path, options):
    """Worker thread: no Tk calls here, only log() / call_in_ui()."""
    try:
        report = core.process(path, options, config, log)
        log("\n\n")
        if report.problems:
            message = f"完成! {len(report.problems)} 個項目未重命名, 請查看列表"
        else:
            message = "完成!"
        call_in_ui(finish, "提示", message)
    except Exception as err:  # never leave the window stuck on "等待完成"
        log(f"**發生錯誤: {err!r}")
        call_in_ui(finish, "錯誤", f"發生錯誤: {err}")


def finish(title, message):
    set_controls(True)
    messagebox.showinfo(title=title, message=message)


root = tk.Tk()  # 實例化object，建立視窗root
root.title(f'DLsite重命名工具 v{VERSION}')  # 給視窗的標題取名字
root.eval('tk::PlaceWindow . center')
root.geometry('350x470')  # 設定視窗的大小(橫向 * 縱向)

text = tk.Text(root)
text.pack()

# 讀取配置文件 (當前腳本所在路徑)
config = core.load_config(os.path.abspath(os.path.dirname(__file__)), log)

deltext = tk.IntVar()  # 定義整數變數用來存放選擇行為返回值
dlcover = tk.IntVar()
recursive = tk.IntVar()
dryrun = tk.IntVar()
cbtn_deltext = tk.Checkbutton(root, text='去除title中【】之間的內容', variable=deltext, onvalue=1, offvalue=0)
cbtn_dlcover = tk.Checkbutton(root, text='下載封面', variable=dlcover, onvalue=1, offvalue=0)
cbtn_recursive = tk.Checkbutton(root, text='遞迴檢索', variable=recursive, onvalue=1, offvalue=0)
cbtn_dryrun = tk.Checkbutton(root, text='預覽（不重命名）', variable=dryrun, onvalue=1, offvalue=0)

btn = tk.Button(root, text='選擇路徑', command=start)

btn.pack()
cbtn_deltext.pack()
cbtn_dlcover.pack()
cbtn_recursive.pack()
cbtn_dryrun.pack()

pump_ui()
root.mainloop()
