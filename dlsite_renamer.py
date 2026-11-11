from tkinter import filedialog
from tkinter import messagebox
import tkinter as tk
import threading
import os

import renamer_core as core


def log(message):
    text.insert(tk.END, message + "\n")
    text.see(tk.END)


def set_controls(enabled):
    state = tk.NORMAL if enabled else tk.DISABLED
    for widget in (cbtn_deltext, cbtn_dlcover, cbtn_recursive, cbtn_dryrun, btn):
        widget.config(state=state)
    btn['text'] = "選擇路徑" if enabled else "等待完成"


def nameChange():
    # askdirectory()檔案對話框, 選擇目錄, 返回目錄名
    path = filedialog.askdirectory()
    if path == "":
        messagebox.showinfo(title="錯誤", message="請選擇路徑!" + "\n")
        return
    set_controls(False)
    try:
        options = core.Options(delete_brackets=bool(deltext.get()), download_cover=bool(dlcover.get()),
                               recursive=bool(recursive.get()), dry_run=bool(dryrun.get()))
        report = core.process(path, options, config, log)
        log("\n\n")
        message = "完成!" if not report.problems else f"完成! {len(report.problems)} 個項目未重命名, 請查看列表"
        messagebox.showinfo(title="提示", message=message)
    except Exception as err:  # never leave the window stuck on "等待完成"
        log(f"**發生錯誤: {err!r}")
        messagebox.showinfo(title="錯誤", message=f"發生錯誤: {err}")
    finally:
        set_controls(True)


def thread_it(func, *args):
    '''將函數打包進線程'''
    threading.Thread(target=func, args=args, daemon=True).start()


root = tk.Tk()  # 實例化object，建立視窗root
root.title('DLsite重命名工具 v3.7')  # 給視窗的標題取名字
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

btn = tk.Button(root, text='選擇路徑', command=lambda: thread_it(nameChange))

btn.pack()
cbtn_deltext.pack()
cbtn_dlcover.pack()
cbtn_recursive.pack()
cbtn_dryrun.pack()

root.mainloop()
