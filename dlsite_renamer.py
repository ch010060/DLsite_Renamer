from tkinter import filedialog, messagebox, ttk
import tkinter.font as tkfont
import tkinter as tk
import threading
import queue
import os

import renamer_core as core

VERSION = '4.2'
FONT_SIZE = 16  # base font size in points (Tk's default on macOS is 13)
BASEDIR = os.path.abspath(os.path.dirname(__file__))
LAST_FOLDER_FILE = os.path.join(BASEDIR, '.last_folder')  # remembered between runs (gitignored)

# status -> (label, label in preview mode, row tag)
STATUS = {
    'waiting': ('· 等待', '· 等待', ''),
    'running': ('→ 處理中', '→ 處理中', 'running'),
    'renamed': ('✔ 已重命名', '✔ 將重命名', 'ok'),
    'repaired': ('✔ 修復', '✔ 將修復', 'ok'),
    'unchanged': ('= 未變更', '= 未變更', ''),
    'problem': ('✖ 未重命名', '✖ 未重命名', 'problem'),
}

# Tk is not thread-safe: the worker thread never touches widgets. It puts messages on this queue,
# and the main thread (pump_ui) applies them.
ui_queue = queue.Queue()
stop_event = threading.Event()
rows = {}         # path -> tree item id
order = []        # tree item ids in processing order
status_of = {}    # tree item id -> status
counts = {s: 0 for s in STATUS}  # rows per status, kept up to date by set_status (no rescans)
PUMP_BATCH = 300  # queued messages handled per pump, so a burst of events cannot freeze the window
preview = False   # the current run is a dry-run
log_open = False  # the log section is shown (tracked here: winfo_ismapped lags behind on macOS)


def log(message):
    """Thread-safe log: may be called from any thread."""
    ui_queue.put(('log', message))


def progress(*event):
    """Thread-safe progress callback for core.process."""
    ui_queue.put(('progress', event))


def call_in_ui(func, *args):
    """Run func(*args) on the Tk main thread."""
    ui_queue.put(('call', func, args))


def pump_ui():
    try:
        for _ in range(PUMP_BATCH):
            item = ui_queue.get_nowait()
            if item[0] == 'log':
                append_log(item[1])
            elif item[0] == 'progress':
                on_progress(*item[1])
            else:
                item[1](*item[2])
    except queue.Empty:
        root.after(50, pump_ui)
        return
    root.after(1, pump_ui)  # more waiting: continue right after Tk handles input and redraws


def append_log(message):
    text.insert(tk.END, message + "\n", 'warn' if message.startswith('**') else '')
    text.see(tk.END)


# -- work list ---------------------------------------------------------------------------------------
def on_progress(event, index, total, path, detail):
    if event == 'begin':
        tree.delete(*tree.get_children())
        rows.clear(), order.clear(), status_of.clear()
        counts.update({s: 0 for s in STATUS})
        counts['waiting'] = len(detail)
        bar.config(maximum=max(total, 1), value=0)
        for p in detail:
            name = os.path.basename(p)
            codes = core.find_work_codes(name)
            iid = tree.insert('', 'end', values=(STATUS['waiting'][0], codes[0] if codes else '—', name))
            rows[p], status_of[iid] = iid, 'waiting'
            order.append(iid)
        if only_problems.get():
            apply_filter()
        update_counts(0, total)
    elif event == 'start':
        iid = rows[path]
        set_status(iid, 'running')
        tree.see(iid)
        current.set(f"處理中: {os.path.basename(path)} ― {'DLsite 查詢' if detail else '修復檔名'}")
    elif event == 'result':
        status, result_text = detail
        set_status(rows[path], status, result_text)
        bar.config(value=index + 1)
        update_counts(index + 1, total)


def set_status(iid, status, result_text=None):
    label = STATUS[status][1 if preview else 0]
    values = list(tree.item(iid, 'values'))
    values[0] = label
    if result_text is not None:
        values[2] = result_text
    tree.item(iid, values=values, tags=(STATUS[status][2],))
    counts[status_of[iid]] -= 1
    counts[status] += 1
    status_of[iid] = status
    if only_problems.get():
        # results arrive in processing order, so a new ✖ row belongs at the end; others are hidden
        if status == 'problem':
            tree.move(iid, '', 'end')
        else:
            tree.detach(iid)


def apply_filter():
    """「只顯示問題」: keep only ✖ rows (re-attached in processing order)."""
    show_all = not only_problems.get()
    for iid in order:
        if show_all or status_of[iid] == 'problem':
            tree.move(iid, '', 'end')
        else:
            tree.detach(iid)


def update_counts(done, total):
    pct = done * 100 // total if total else 100
    count.set(f"{done} / {total} ({pct}%)")
    verb = '將' if preview else '已'
    summary.set(f"{verb}重命名 {counts['renamed']} · {verb}修復 {counts['repaired']} · "
                f"未變更 {counts['unchanged']} · 未重命名 {counts['problem']}")
    update_log_toggle()


# -- log section (hidden by default) ------------------------------------------------------------------
def update_log_toggle():
    errors = counts['problem']
    arrow = '▾' if log_open else '▸'
    log_toggle.config(text=f"日誌 {arrow}" + (f" ({errors} 個錯誤)" if errors else ''),
                      foreground='red' if errors else '')


def toggle_log(_event=None):
    global log_open
    log_open = not log_open
    if log_open:
        log_frame.grid()
        root.rowconfigure(5, weight=1)
    else:
        log_frame.grid_remove()
        root.rowconfigure(5, weight=0)
    update_log_toggle()


# -- run control ----------------------------------------------------------------------------------------
def choose_folder():
    path = filedialog.askdirectory()
    if path:
        folder.set(path)


def set_running(running):
    state = tk.DISABLED if running else tk.NORMAL
    for widget in (folder_entry, choose_btn, cbtn_deltext, cbtn_dlcover, cbtn_recursive, cbtn_dryrun, start_btn):
        widget.config(state=state)
    stop_btn.config(state=tk.NORMAL if running else tk.DISABLED)


def start():
    global preview
    path = folder.get().strip()
    if not os.path.isdir(path):
        messagebox.showinfo(title="錯誤", message="請選擇有效的資料夾!")
        return
    try:
        with open(LAST_FOLDER_FILE, 'w', encoding='utf-8') as f:
            f.write(path)
    except OSError:
        pass  # remembering the folder is a convenience only
    preview = bool(dryrun.get())
    options = core.Options(delete_brackets=bool(deltext.get()), download_cover=bool(dlcover.get()),
                           recursive=bool(recursive.get()), dry_run=preview)
    stop_event.clear()
    set_running(True)
    current.set("準備中…")
    threading.Thread(target=work, args=(path, options), daemon=True).start()


def stop():
    stop_event.set()
    stop_btn.config(state=tk.DISABLED)
    current.set("停止中… (完成目前項目後停止)")


def work(path, options):
    """Worker thread: no Tk calls here, only log() / progress() / call_in_ui()."""
    try:
        report = core.process(path, options, config, log, progress=progress, stop=stop_event)
        log("\n")
        if report.stopped is not None:
            message = f"已停止 (處理了 {report.stopped} 個項目)"
        elif report.problems:
            message = f"完成! {len(report.problems)} 個項目未重命名, 請查看列表"
        else:
            message = "完成!"
        call_in_ui(finish, "提示", message)
    except Exception as err:  # never leave the window stuck in the running state
        log(f"**發生錯誤: {err!r}")
        call_in_ui(finish, "錯誤", f"發生錯誤: {err}")


def finish(title, message):
    set_running(False)
    current.set(message)
    messagebox.showinfo(title=title, message=message)


# -- window ---------------------------------------------------------------------------------------------
root = tk.Tk()
root.title(f'DLsite重命名工具 v{VERSION}')
for font_name, size, weight in (('TkDefaultFont', FONT_SIZE, 'normal'), ('TkTextFont', FONT_SIZE, 'normal'),
                                ('TkHeadingFont', FONT_SIZE - 1, 'bold'), ('TkMenuFont', FONT_SIZE, 'normal'),
                                ('TkFixedFont', FONT_SIZE - 2, 'normal')):
    tkfont.nametofont(font_name).configure(size=size, weight=weight)
ttk.Style().configure('Treeview', rowheight=tkfont.nametofont('TkDefaultFont').metrics('linespace') + 8)
try:
    app_icon = tk.PhotoImage(file=os.path.join(BASEDIR, 'icon.png'))  # keep a reference: Tk does not
    root.iconphoto(True, app_icon)                                       # window + Dock icon
except tk.TclError:
    pass  # icon file missing: keep the default icon
root.geometry('900x650')
root.minsize(820, 520)  # options row needs ~770 px at FONT_SIZE 16
root.columnconfigure(0, weight=1)
root.rowconfigure(3, weight=1)

folder = tk.StringVar()
try:
    with open(LAST_FOLDER_FILE, encoding='utf-8') as f:
        last = f.read().strip()
    if os.path.isdir(last):
        folder.set(last)
except OSError:
    pass  # first run: no folder remembered yet
current = tk.StringVar(value="請選擇資料夾, 然後按「開始」")
count = tk.StringVar(value="0 / 0 (0%)")
summary = tk.StringVar()
deltext, dlcover, recursive, dryrun, only_problems = (tk.IntVar() for _ in range(5))

pad = {'padx': 8, 'pady': 4}
row0 = ttk.Frame(root)
row0.grid(row=0, column=0, sticky='ew', **pad)
row0.columnconfigure(1, weight=1)
ttk.Label(row0, text="資料夾").grid(row=0, column=0, padx=(0, 6))
folder_entry = ttk.Entry(row0, textvariable=folder)
folder_entry.grid(row=0, column=1, sticky='ew')
choose_btn = ttk.Button(row0, text="選擇…", command=choose_folder)
choose_btn.grid(row=0, column=2, padx=(6, 0))

row1 = ttk.Frame(root)
row1.grid(row=1, column=0, sticky='ew', **pad)
cbtn_deltext = ttk.Checkbutton(row1, text='去除【】', variable=deltext)
cbtn_dlcover = ttk.Checkbutton(row1, text='下載封面', variable=dlcover)
cbtn_recursive = ttk.Checkbutton(row1, text='遞迴檢索', variable=recursive)
cbtn_dryrun = ttk.Checkbutton(row1, text='預覽（不重命名）', variable=dryrun)
cbtn_problems = ttk.Checkbutton(row1, text='只顯示問題', variable=only_problems, command=apply_filter)
for widget in (cbtn_deltext, cbtn_dlcover, cbtn_recursive, cbtn_dryrun, cbtn_problems):
    widget.pack(side=tk.LEFT, padx=(0, 12))
stop_btn = ttk.Button(row1, text="停止", command=stop, state=tk.DISABLED)
stop_btn.pack(side=tk.RIGHT)
start_btn = ttk.Button(row1, text="開始", command=start)
start_btn.pack(side=tk.RIGHT, padx=(0, 6))

row2 = ttk.Frame(root)
row2.grid(row=2, column=0, sticky='ew', **pad)
row2.columnconfigure(0, weight=1)
bar = ttk.Progressbar(row2, mode='determinate')
bar.grid(row=0, column=0, sticky='ew')
ttk.Label(row2, textvariable=count, width=18).grid(row=0, column=1, padx=(8, 0))
ttk.Label(row2, textvariable=current).grid(row=1, column=0, columnspan=2, sticky='w', pady=(4, 0))

list_frame = ttk.Frame(root)
list_frame.grid(row=3, column=0, sticky='nsew', **pad)
list_frame.columnconfigure(0, weight=1)
list_frame.rowconfigure(0, weight=1)
tree = ttk.Treeview(list_frame, columns=('status', 'code', 'name'), show='headings')
for column, heading, width, stretch in (('status', '狀態', 150, False), ('code', '作品編號', 140, False),
                                       ('name', '名稱 / 新名稱 / 原因', 450, True)):
    tree.heading(column, text=heading, anchor='w')
    tree.column(column, width=width, minwidth=60, stretch=stretch, anchor='w')
tree.tag_configure('problem', foreground='red')
tree.tag_configure('running', foreground='blue')
tree.tag_configure('ok', foreground='green4')
tree_scroll = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=tree.yview)
tree.configure(yscrollcommand=tree_scroll.set)
tree.grid(row=0, column=0, sticky='nsew')
tree_scroll.grid(row=0, column=1, sticky='ns')

log_toggle = ttk.Label(root, text="日誌 ▸", cursor='hand2')
log_toggle.grid(row=4, column=0, sticky='w', padx=8)
log_toggle.bind('<Button-1>', toggle_log)

log_frame = ttk.Frame(root)
log_frame.grid(row=5, column=0, sticky='nsew', **pad)
log_frame.columnconfigure(0, weight=1)
log_frame.rowconfigure(0, weight=1)
text = tk.Text(log_frame, height=10, wrap='none')
text.tag_configure('warn', foreground='red')
log_scroll = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=text.yview)
text.configure(yscrollcommand=log_scroll.set)
text.grid(row=0, column=0, sticky='nsew')
log_scroll.grid(row=0, column=1, sticky='ns')
log_frame.grid_remove()  # hidden by default; the toggle shows the error count

ttk.Label(root, textvariable=summary).grid(row=6, column=0, sticky='w', **pad)

# 讀取配置文件 (當前腳本所在路徑); the active templates stay visible under the folder row
config = core.load_config(BASEDIR, log)
ttk.Label(row0, text="命名模板").grid(row=1, column=0, padx=(0, 6), pady=(4, 0), sticky='w')
template_label = ttk.Label(row0, foreground='gray', text="   ·   ".join(
    f"{kind.upper()}: {template.strip()}" for kind, template in config.templates.items()))
template_label.grid(row=1, column=1, columnspan=2, pady=(4, 0), sticky='w')

pump_ui()
root.mainloop()
