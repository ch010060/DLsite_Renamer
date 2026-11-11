from tkinter import filedialog, messagebox, ttk
import tkinter.font as tkfont
import tkinter as tk
import threading
import queue
import json
import sys
import os

import renamer_core as core

VERSION = '4.3'
IS_MAC, IS_WIN = sys.platform == 'darwin', sys.platform.startswith('win')
FONT_SIZE = 15 if IS_MAC else 11  # points; Tk's default is 13 on macOS, 9 on Windows (scaled by the DPI setting)
FONT_FAMILY = 'Microsoft JhengHei UI' if IS_WIN else None  # Traditional Chinese UI font on Windows
BASEDIR = os.path.abspath(os.path.dirname(__file__))
SETTINGS_FILE = os.path.join(BASEDIR, '.gui_settings.json')  # last folder + options (gitignored)
OLD_LAST_FOLDER_FILE = os.path.join(BASEDIR, '.last_folder')  # v4.2, read once for the folder

# Dark palette (always dark)
C = {
    'bg': '#121b26', 'panel': '#172433', 'field': '#0e151e', 'border': '#2a3b4f', 'heading': '#22344a',
    'banner': '#16324a', 'text': '#d8e1ea', 'dim': '#8b9bb0', 'accent': '#2d8cf0', 'accent_hi': '#4a9ff5',
    'select': '#2b4a6b', 'ok': '#5fd38d', 'problem': '#ff6b6b', 'running': '#6cb6ff', 'warn': '#ffb454',
    'stripe': '#1b2b3d', 'divider': '#3d5470',
}

# status -> (label, label in preview mode, row tag)
STATUS = {
    'waiting': ('· 等待', '· 等待', ''),
    'running': ('→ 處理中', '→ 處理中', 'running'),
    'renamed': ('✔ 已重命名', '✔ 將重命名', 'ok'),
    'repaired': ('✔ 修復', '✔ 將修復', 'ok'),
    'unchanged': ('= 未變更', '= 未變更', ''),
    'problem': ('✖ 未重命名', '✖ 未重命名', 'problem'),
}
OPTION_DEFAULTS = {'deltext': 1, 'dlcover': 1, 'recursive': 0, 'dryrun': 0}

# Tk is not thread-safe: the worker thread never touches widgets. It puts messages on this queue,
# and the main thread (pump_ui) applies them.
ui_queue = queue.Queue()
stop_event = threading.Event()
rows = {}         # path -> tree item id
order = []        # tree item ids in processing order
status_of = {}    # tree item id -> status
shown = set()     # tree item ids currently attached (visible) in the list
stripe_of = {}    # tree item id -> row stripe tag ('' or 'stripe')
counts = {s: 0 for s in STATUS}  # rows per status, kept up to date by set_status (no rescans)
PUMP_BATCH = 300  # queued messages handled per pump, so a burst of events cannot freeze the window
preview = False   # the current run is a dry-run


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


# -- settings (last folder + options) -------------------------------------------------------------------
def load_settings():
    settings = dict(OPTION_DEFAULTS, folder='')
    try:
        with open(SETTINGS_FILE, encoding='utf-8') as f:
            settings.update(json.load(f))
    except (OSError, ValueError):
        try:  # first run of v4.3: take the folder remembered by v4.2
            with open(OLD_LAST_FOLDER_FILE, encoding='utf-8') as f:
                settings['folder'] = f.read().strip()
        except OSError:
            pass
    return settings


def save_settings():
    data = {'folder': folder.get().strip(), **{k: var.get() for k, var in option_vars.items()}}
    try:
        with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False)
    except OSError:
        pass  # remembering the settings is a convenience only


# -- work list ---------------------------------------------------------------------------------------
def on_progress(event, index, total, path, detail):
    if event == 'begin':
        tree.delete(*tree.get_children())
        rows.clear(), order.clear(), status_of.clear(), shown.clear(), stripe_of.clear()
        counts.update({s: 0 for s in STATUS})
        counts['waiting'] = len(detail)
        for p in detail:
            name = os.path.basename(p)
            codes = core.find_work_codes(name)
            stripe = 'stripe' if len(order) % 2 else ''
            iid = tree.insert('', 'end', values=(STATUS['waiting'][0], codes[0] if codes else '—', name, '', ''),
                              tags=(stripe,))
            rows[p], status_of[iid], stripe_of[iid] = iid, 'waiting', stripe
            order.append(iid)
            shown.add(iid)
        if filtering():
            apply_filter()
        update_counts(0, total)
    elif event == 'start':
        iid = rows[path]
        set_status(iid, 'running')
        if iid in shown:
            tree.see(iid)
        set_banner('執行中', f"{os.path.basename(path)} ― {'DLsite 查詢' if detail else '修復檔名'}", C['running'])
    elif event == 'result':
        status, result_text = detail
        set_status(rows[path], status, result_text)
        update_counts(index + 1, total)


def set_status(iid, status, result_text=None):
    label = STATUS[status][1 if preview else 0]
    values = list(tree.item(iid, 'values'))
    values[0] = label
    if result_text is not None:  # 新名稱 for renamed / repaired items, 原因 for ✖ items
        values[3] = result_text if status in ('renamed', 'repaired') else ''
        values[4] = result_text if status == 'problem' else ''
    tree.item(iid, values=values, tags=(STATUS[status][2], stripe_of[iid]))
    counts[status_of[iid]] -= 1
    counts[status] += 1
    status_of[iid] = status
    if not filtering():
        return
    if is_visible(iid):
        if iid not in shown:
            if only_problems.get():
                # rows only become ✖ in processing order, so a newly visible row belongs at the end
                tree.move(iid, '', 'end')
                shown.add(iid)
            else:
                apply_filter()  # rare: the new name now matches the search text
    elif iid in shown:
        tree.detach(iid)
        shown.discard(iid)


def filtering():
    return bool(only_problems.get() or search.get().strip())


def is_visible(iid):
    if only_problems.get() and status_of[iid] != 'problem':
        return False
    query = search.get().strip().lower()
    return not query or query in ' '.join(tree.item(iid, 'values')[1:]).lower()


def apply_filter(*_):
    """「只顯示問題」 and the search box: re-attach the matching rows in processing order."""
    shown.clear()
    for iid in order:
        if is_visible(iid):
            tree.move(iid, '', 'end')
            shown.add(iid)
        else:
            tree.detach(iid)
    search_hint.place_forget() if search.get() else search_hint.place(x=34, rely=0.5, anchor='w')


def update_counts(done, total):
    pct = done * 100 // total if total else 0
    draw_progress(done / total if total else 0, f"{done} / {total} ({pct}%)")
    verb = '將' if preview else '已'
    summary.set(f"{verb}重命名 {counts['renamed']} · {verb}修復 {counts['repaired']} · "
                f"未變更 {counts['unchanged']} · 未重命名 {counts['problem']}")
    errors = counts['problem']
    log_var_text.set("顯示日誌" + (f" ({errors} 個錯誤)" if errors else ''))
    cbtn_log.configure(style='Error.TCheckbutton' if errors else 'TCheckbutton')


# -- progress bar (canvas: the text sits inside the bar on every platform) -------------------------------
progress_state = [0.0, '等待開始']


def draw_progress(fraction=None, label=None):
    if fraction is not None:
        progress_state[:] = [fraction, label]
    fraction, label = progress_state
    w, h = bar.winfo_width(), bar.winfo_height()
    bar.delete('all')
    bar.create_rectangle(0, 0, w - 1, h - 1, fill=C['field'], outline=C['border'])
    if fraction > 0:
        bar.create_rectangle(1, 1, max(2, int((w - 2) * fraction)), h - 2, fill=C['accent'], width=0)
    bar.create_text(w // 2, h // 2, text=label, fill=C['text'], font='TkDefaultFont')


# -- banner / log -----------------------------------------------------------------------------------------
def set_banner(state, message, color):
    banner_state.configure(text=state, foreground=color)
    current.set(message)


def toggle_log():
    if log_open.get():
        log_frame.grid()
        root.rowconfigure(LOG_ROW, weight=1)
    else:
        log_frame.grid_remove()
        root.rowconfigure(LOG_ROW, weight=0)


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
    save_settings()
    preview = bool(dryrun.get())
    options = core.Options(delete_brackets=bool(deltext.get()), download_cover=bool(dlcover.get()),
                           recursive=bool(recursive.get()), dry_run=preview)
    stop_event.clear()
    set_running(True)
    set_banner('執行中', "準備中…", C['running'])
    threading.Thread(target=work, args=(path, options), daemon=True).start()


def stop():
    stop_event.set()
    stop_btn.config(state=tk.DISABLED)
    set_banner('停止中', "完成目前項目後停止…", C['warn'])


def work(path, options):
    """Worker thread: no Tk calls here, only log() / progress() / call_in_ui()."""
    try:
        report = core.process(path, options, config, log, progress=progress, stop=stop_event)
        log("\n")
        if report.stopped is not None:
            call_in_ui(finish, "提示", '已停止', f"處理了 {report.stopped} 個項目", C['warn'])
        elif report.problems:
            call_in_ui(finish, "提示", '完成', f"{len(report.problems)} 個項目未重命名, 請查看列表", C['problem'])
        else:
            call_in_ui(finish, "提示", '完成', "全部處理完成", C['ok'])
    except Exception as err:  # never leave the window stuck in the running state
        log(f"**發生錯誤: {err!r}")
        call_in_ui(finish, "錯誤", '錯誤', f"發生錯誤: {err}", C['problem'])


def finish(title, state, message, color):
    set_running(False)
    set_banner(state, message, color)
    messagebox.showinfo(title=title, message=f"{state}: {message}")


# -- window: fonts, dark theme, icon ------------------------------------------------------------------------
root = tk.Tk()
root.title(f'DLsite重命名工具 v{VERSION}')
root.configure(background=C['bg'])
for font_name, delta, weight in (('TkDefaultFont', 0, 'normal'), ('TkTextFont', 0, 'normal'),
                                 ('TkHeadingFont', 0, 'bold'), ('TkMenuFont', 0, 'normal'),
                                 ('TkFixedFont', -1, 'normal')):
    font = tkfont.nametofont(font_name)
    font.configure(size=FONT_SIZE + delta, weight=weight)
    if FONT_FAMILY and font_name != 'TkFixedFont':
        font.configure(family=FONT_FAMILY)
default_font = tkfont.nametofont('TkDefaultFont')
bold_font = default_font.copy()
bold_font.configure(weight='bold', size=FONT_SIZE + 2)

style = ttk.Style()
style.theme_use('clam')  # the native macOS/Windows themes cannot be recolored
style.configure('.', background=C['bg'], foreground=C['text'], fieldbackground=C['field'],
                bordercolor=C['border'], lightcolor=C['border'], darkcolor=C['border'], troughcolor=C['bg'],
                selectbackground=C['select'], selectforeground=C['text'], insertcolor=C['text'],
                focuscolor=C['accent'])
style.map('.', foreground=[('disabled', C['dim'])])
style.configure('TButton', background=C['panel'], padding=(12, 5))
style.map('TButton', background=[('disabled', C['bg']), ('pressed', C['border']), ('active', C['heading'])])
style.configure('Accent.TButton', background=C['accent'], foreground='white')
style.map('Accent.TButton', background=[('disabled', C['panel']), ('pressed', C['accent']),
                                        ('active', C['accent_hi'])], foreground=[('disabled', C['dim'])])
style.configure('TCheckbutton', indicatorbackground=C['field'], indicatorforeground='white')
style.map('TCheckbutton', background=[('active', C['bg'])],
          indicatorbackground=[('selected', C['accent']), ('disabled', C['bg'])])
style.configure('Error.TCheckbutton', foreground=C['problem'])
style.map('TEntry', fieldbackground=[('disabled', C['bg'])])
style.configure('Card.TFrame', background=C['panel'], relief='solid', borderwidth=1)
style.configure('Card.TLabel', background=C['panel'])
style.configure('Banner.TFrame', background=C['banner'], relief='solid', borderwidth=1)
style.configure('Banner.TLabel', background=C['banner'])
style.configure('Hint.TLabel', background=C['field'], foreground=C['dim'])
style.configure('Footer.TLabel', foreground=C['ok'])
style.configure('Treeview', background=C['panel'], fieldbackground=C['panel'], foreground=C['text'],
                rowheight=default_font.metrics('linespace') + 10, borderwidth=0)
style.map('Treeview', background=[('selected', C['select'])])
style.configure('Treeview.Heading', background=C['heading'], foreground=C['text'], relief='raised',
                borderwidth=1, bordercolor=C['divider'], lightcolor=C['divider'], darkcolor=C['divider'],
                font='TkHeadingFont', padding=(6, 6))  # visible lines between the column headings
style.map('Treeview.Heading', background=[('active', C['border'])])
style.configure('Vertical.TScrollbar', background=C['border'], troughcolor=C['bg'], arrowcolor=C['dim'],
                bordercolor=C['bg'], lightcolor=C['border'], darkcolor=C['border'])
style.map('Vertical.TScrollbar', background=[('pressed', C['divider']), ('active', C['divider'])],
          lightcolor=[('active', C['divider'])], darkcolor=[('active', C['divider'])])  # stays dark on hover


def checkbox_images(size):
    """Dark checkbox indicator: an empty box, and a blue box with a white ✓ (clam would draw ☒)."""
    def box(fill, edge, check=None):
        img = tk.PhotoImage(width=size, height=size)
        img.put(edge, to=(0, 0, size, size))
        img.put(fill, to=(1, 1, size - 1, size - 1))
        for x, y in ((0, 0), (size - 1, 0), (0, size - 1), (size - 1, size - 1)):
            img.transparency_set(x, y, True)  # slightly rounded corners
        if check:
            pen = max(2, size // 7)
            points = ((0.22, 0.52), (0.42, 0.72), (0.78, 0.30))
            for (x0, y0), (x1, y1) in zip(points, points[1:]):
                for i in range(41):
                    x = int((x0 + (x1 - x0) * i / 40) * size)
                    y = int((y0 + (y1 - y0) * i / 40) * size)
                    img.put(check, to=(x, y, min(size, x + pen), min(size, y + pen)))
        return img
    return (box(C['field'], C['divider']), box(C['accent'], C['accent'], 'white'),
            box(C['bg'], C['border']), box(C['border'], C['border'], C['dim']))


check_size = max(13, int(default_font.metrics('linespace') * 0.8))
check_images = checkbox_images(check_size)  # keep references: Tk does not
style.element_create('Dark.Checkbutton.indicator', 'image', check_images[0],
                     ('disabled', 'selected', check_images[3]), ('disabled', check_images[2]),
                     ('selected', check_images[1]), width=check_size + 8, sticky='w')
style.layout('TCheckbutton', [('Checkbutton.padding', {'sticky': 'nswe', 'children': [
    ('Dark.Checkbutton.indicator', {'side': 'left', 'sticky': ''}),
    ('Checkbutton.focus', {'side': 'left', 'sticky': 'w', 'children': [('Checkbutton.label', {'sticky': 'nswe'})]})]})])

try:
    app_icon = tk.PhotoImage(file=os.path.join(BASEDIR, 'icon.png'))  # keep a reference: Tk does not
    root.iconphoto(True, app_icon)                                       # window + Dock / taskbar icon
except tk.TclError:
    pass  # icon file missing: keep the default icon

# -- variables ----------------------------------------------------------------------------------------------
settings = load_settings()
folder = tk.StringVar(value=settings['folder'] if os.path.isdir(settings['folder']) else '')
option_vars = {key: tk.IntVar(value=int(settings.get(key, default))) for key, default in OPTION_DEFAULTS.items()}
deltext, dlcover, recursive, dryrun = (option_vars[k] for k in ('deltext', 'dlcover', 'recursive', 'dryrun'))
only_problems = tk.IntVar()
log_open = tk.IntVar()
search = tk.StringVar()
current = tk.StringVar(value="請選擇資料夾, 然後按「開始」")
summary = tk.StringVar(value="已重命名 0 · 未重命名 0")
log_var_text = tk.StringVar(value="顯示日誌")

PAD = 10
root.columnconfigure(0, weight=1)

# 0: folder card
card = ttk.Frame(root, style='Card.TFrame', padding=10)
card.grid(row=0, column=0, sticky='ew', padx=PAD, pady=(PAD, 6))
card.columnconfigure(1, weight=1)
ttk.Label(card, text="資料夾", style='Card.TLabel').grid(row=0, column=0, padx=(0, 8))
folder_entry = ttk.Entry(card, textvariable=folder, width=10)
folder_entry.grid(row=0, column=1, sticky='ew', ipady=3)
choose_btn = ttk.Button(card, text="📁 選擇資料夾", command=choose_folder)
choose_btn.grid(row=0, column=2, padx=(8, 0))

# 1: toolbar — buttons first, so they are never squeezed
toolbar = ttk.Frame(root)
toolbar.grid(row=1, column=0, sticky='ew', padx=PAD, pady=6)
start_btn = ttk.Button(toolbar, text="▶ 開始", style='Accent.TButton', command=start)
start_btn.pack(side=tk.LEFT)
stop_btn = ttk.Button(toolbar, text="■ 停止", command=stop, state=tk.DISABLED)
stop_btn.pack(side=tk.LEFT, padx=(6, 16))
cbtn_deltext = ttk.Checkbutton(toolbar, text='去除【】', variable=deltext)
cbtn_dlcover = ttk.Checkbutton(toolbar, text='下載封面', variable=dlcover)
cbtn_recursive = ttk.Checkbutton(toolbar, text='遞迴檢索', variable=recursive)
cbtn_dryrun = ttk.Checkbutton(toolbar, text='預覽（不重命名）', variable=dryrun)
for widget in (cbtn_deltext, cbtn_dlcover, cbtn_recursive, cbtn_dryrun):
    widget.pack(side=tk.LEFT, padx=(0, 12))
cbtn_problems = ttk.Checkbutton(toolbar, text='只顯示問題', variable=only_problems, command=apply_filter)
cbtn_problems.pack(side=tk.RIGHT)

# 2: status banner — state word, current item, counts
banner = ttk.Frame(root, style='Banner.TFrame')
banner.grid(row=2, column=0, sticky='ew', padx=PAD, pady=6)
banner.columnconfigure(2, weight=1)
tk.Frame(banner, width=4, background=C['accent']).grid(row=0, column=0, sticky='ns')
banner_state = ttk.Label(banner, text='READY', style='Banner.TLabel', font=bold_font)
banner_state.grid(row=0, column=1, padx=(12, 12), pady=10)
ttk.Label(banner, textvariable=current, style='Banner.TLabel', foreground=C['dim'], width=1).grid(
    row=0, column=2, sticky='ew')
ttk.Label(banner, textvariable=summary, style='Banner.TLabel').grid(row=0, column=3, padx=(12, 14))

# 3: search box with a placeholder
search_frame = ttk.Frame(root, style='Card.TFrame')
search_frame.grid(row=3, column=0, sticky='ew', padx=PAD, pady=6)
search_frame.columnconfigure(1, weight=1)
ttk.Label(search_frame, text="🔍", style='Card.TLabel').grid(row=0, column=0, padx=(8, 4))
search_entry = ttk.Entry(search_frame, textvariable=search, width=10)
search_entry.grid(row=0, column=1, sticky='ew', ipady=3, padx=(0, 4), pady=4)
search_hint = ttk.Label(search_frame, text="搜尋作品編號或名稱…", style='Hint.TLabel')
search_hint.bind('<Button-1>', lambda _e: search_entry.focus_set())
search.trace_add('write', apply_filter)

# 4: work list (grows with the window)
LIST_ROW, LOG_ROW = 4, 6
list_frame = ttk.Frame(root)
list_frame.grid(row=LIST_ROW, column=0, sticky='nsew', padx=PAD, pady=6)
root.rowconfigure(LIST_ROW, weight=3)
list_frame.columnconfigure(0, weight=1)
list_frame.rowconfigure(0, weight=1)
tree = ttk.Treeview(list_frame, columns=('status', 'code', 'name', 'new', 'reason'), show='headings', height=8)
for column, heading, sample, stretch in (('status', '狀態', '✔ 將重命名', False),
                                         ('code', '作品編號', 'RJ01234567', False),
                                         ('name', '名稱', 'x' * 20, True),
                                         ('new', '新名稱', 'x' * 20, True),
                                         ('reason', '原因', 'x' * 16, True)):
    tree.heading(column, text=heading, anchor='w')
    tree.column(column, width=default_font.measure(sample) + 30, minwidth=60, stretch=stretch, anchor='w')
tree.tag_configure('stripe', background=C['stripe'])  # alternating rows: each row reads clearly across columns
tree.tag_configure('problem', foreground=C['problem'])
tree.tag_configure('running', foreground=C['running'])
tree.tag_configure('ok', foreground=C['ok'])
tree_scroll = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=tree.yview)
tree.configure(yscrollcommand=tree_scroll.set)
tree.grid(row=0, column=0, sticky='nsew')
tree_scroll.grid(row=0, column=1, sticky='ns')

# 5: log toggle, 6: log (hidden by default)
cbtn_log = ttk.Checkbutton(root, textvariable=log_var_text, variable=log_open, command=toggle_log)
cbtn_log.grid(row=5, column=0, sticky='w', padx=PAD)
log_frame = ttk.Frame(root)
log_frame.grid(row=LOG_ROW, column=0, sticky='nsew', padx=PAD, pady=(4, 0))
log_frame.columnconfigure(0, weight=1)
log_frame.rowconfigure(0, weight=1)
text = tk.Text(log_frame, height=8, wrap='none', background=C['field'], foreground=C['text'],
               insertbackground=C['text'], selectbackground=C['select'], relief='flat', borderwidth=0,
               highlightthickness=1, highlightbackground=C['border'], highlightcolor=C['border'], padx=6, pady=4)
text.tag_configure('warn', foreground=C['problem'])
log_scroll = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=text.yview)
text.configure(yscrollcommand=log_scroll.set)
text.grid(row=0, column=0, sticky='nsew')
log_scroll.grid(row=0, column=1, sticky='ns')
log_frame.grid_remove()

# 7: progress bar with the text inside
bar = tk.Canvas(root, height=default_font.metrics('linespace') + 10, background=C['bg'], highlightthickness=0)
bar.grid(row=7, column=0, sticky='ew', padx=PAD, pady=(8, 4))
bar.bind('<Configure>', lambda _e: draw_progress())

# 8: footer — active naming templates
config = core.load_config(BASEDIR, log)
ttk.Separator(root).grid(row=8, column=0, sticky='ew', padx=PAD)
template_label = ttk.Label(root, style='Footer.TLabel', width=1, text="命名模板  " + "  ·  ".join(
    f"{kind.upper()}: {template.strip()}" for kind, template in config.templates.items()))
template_label.grid(row=9, column=0, sticky='ew', padx=PAD + 4, pady=(4, PAD))

apply_filter()  # shows the search placeholder


# -- size: from the measured content (works at any Windows display scaling), centred ------------------------
def fit_window():
    root.update_idletasks()
    need_w, need_h = root.winfo_reqwidth(), root.winfo_reqheight()
    screen_w, screen_h = root.winfo_screenwidth(), root.winfo_screenheight()
    width = min(max(need_w, int(screen_w * 0.6)), int(screen_w * 0.95))
    height = min(max(need_h + 150, int(screen_h * 0.7)), int(screen_h * 0.9))
    root.minsize(min(need_w, width), min(need_h, height))
    root.geometry(f"{width}x{height}+{(screen_w - width) // 2}+{max(0, (screen_h - height) // 3)}")


def dark_title_bar():
    try:
        if IS_MAC:
            root.tk.call('::tk::unsupported::MacWindowStyle', 'appearance', root, 'darkaqua')
        elif IS_WIN:
            import ctypes
            hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
            on = ctypes.c_int(1)
            for attribute in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (Windows 10 20H1+, older builds)
                if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(on),
                                                              ctypes.sizeof(on)) == 0:
                    break
    except Exception:
        pass  # older systems keep the light title bar


fit_window()
dark_title_bar()
pump_ui()
root.mainloop()
