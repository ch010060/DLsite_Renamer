"""Record the README screenshot (docs/gui.png) and demo GIF (docs/demo.gif) on macOS.

Run tools/record_demo.sh from a local terminal that has Screen Recording permission (not over SSH).
It creates a demo folder of empty files, runs the GUI on it (the work list filling in, then the finished
list held for a few seconds), captures only the window, then removes
the demo folder and restores your saved GUI settings. Needs ImageMagick (`magick`).

usage: python tools/record_demo.py [demo folder, default /Users/Shared/ASMR]
"""
import glob
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import tkinter
from tkinter import filedialog, messagebox

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.path.join(REPO, 'docs')
DEMO = sys.argv[1] if len(sys.argv) > 1 else '/Users/Shared/ASMR'
SETTINGS = os.path.join(REPO, '.gui_settings.json')
DEMO_FILES = ('RJ01386956.zip', 'rj01671271.zip', 'VJ013000.zip', 'BJ100000.pdf', 'RJ999999.zip',
              'ｶﾞｲﾄﾞ ﾌﾞｯｸ.pdf', 'RJ01700000.zip')

if sys.platform != 'darwin':
    sys.exit('macOS only (uses screencapture)')
if not shutil.which('magick'):
    sys.exit('ImageMagick is missing: brew install imagemagick')
if os.path.exists(DEMO):
    sys.exit(f'{DEMO} already exists — remove it yourself (or pass another demo folder), then run again')

os.makedirs(os.path.join(DEMO, 'RJ01551365'))  # a work folder (gets a cover)
for name in DEMO_FILES:
    open(os.path.join(DEMO, name), 'w').close()
frames_dir = tempfile.mkdtemp(prefix='dlsite_renamer_frames_')
settings_backup = open(SETTINGS, 'rb').read() if os.path.exists(SETTINGS) else None

sys.path.insert(0, REPO)
filedialog.askdirectory = lambda **k: DEMO
messagebox.showinfo = lambda *a, **k: None
g = {'__name__': '__main__', '__file__': os.path.join(REPO, 'dlsite_renamer.py')}
recording = threading.Event()
rect, times = {}, []


def capture(path):
    return subprocess.run(['screencapture', '-x', '-o', '-R', rect['r'], path], capture_output=True).returncode == 0


def capture_loop():
    n = 0
    while recording.is_set():
        if capture(os.path.join(frames_dir, f'{n:04d}.png')):
            times.append(time.time())
            n += 1
        time.sleep(0.12)


def at(seconds, func):
    g['root'].after(int(seconds * 1000), func)


def drive():
    r = g['root']
    r.lift()
    r.attributes('-topmost', True)
    r.update()
    rect['r'] = f"{r.winfo_rootx()},{r.winfo_rooty() - 28},{r.winfo_width()},{r.winfo_height() + 28}"
    for key, value in (('deltext', 1), ('dlcover', 1), ('recursive', 0), ('dryrun', 0)):
        g[key].set(value)  # show the defaults, whatever is saved in your settings
    recording.set()
    threading.Thread(target=capture_loop, daemon=True).start()
    at(1.0, g['choose_btn'].invoke)
    at(2.2, g['start_btn'].invoke)
    at(2.7, wait_done)


def wait_done():
    if str(g['start_btn'].cget('state')) == 'disabled':
        at(0.2, wait_done)
    else:
        at(0.8, finished_run)


def finished_run():
    capture(os.path.join(DOCS, 'gui.png'))       # the README screenshot: finished list
    at(3.0, close)                               # keep showing the finished list; nothing else


def close():
    recording.clear()
    time.sleep(0.5)
    g['root'].destroy()


original_mainloop = tkinter.Tk.mainloop


def patched_mainloop(self, n=0):
    self.after(700, drive)
    original_mainloop(self, n)


tkinter.Tk.mainloop = patched_mainloop
try:
    exec(compile(open(g['__file__'], encoding='utf-8').read(), g['__file__'], 'exec'), g)
finally:
    shutil.rmtree(DEMO, ignore_errors=True)  # created by this script above
    try:  # the demo run must not change your remembered folder/options
        if settings_backup is None:
            os.remove(SETTINGS)
        else:
            open(SETTINGS, 'wb').write(settings_backup)
    except OSError:
        pass

frames = sorted(glob.glob(os.path.join(frames_dir, '*.png')))
if len(frames) < 10:
    shutil.rmtree(frames_dir, ignore_errors=True)
    sys.exit(f'only {len(frames)} frames captured — allow Screen Recording for this terminal '
             '(System Settings → Privacy & Security), and run it locally, not over SSH')
delay = max(5, round((times[-1] - times[0]) / (len(times) - 1) * 100))  # GIF delay in 1/100 s
subprocess.run(['magick', '-delay', str(delay), '-loop', '0', *frames[:-1],
                '-delay', '300', frames[-1],  # hold the finished list 3 s before the GIF restarts
                '-resize', '900x',
                '-layers', 'Optimize', '-colors', '128', os.path.join(DOCS, 'demo.gif')], check=True)
shutil.rmtree(frames_dir, ignore_errors=True)
print(f'Saved docs/gui.png and docs/demo.gif ({len(frames)} frames, {delay / 100:.2f} s each)')
