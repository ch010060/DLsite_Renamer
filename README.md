# DLsite_Renamer
![GUI](docs/gui.png)
![DEMO](docs/demo.gif)

### Purpose
Rename the DLsite works with custom pattern and download the cover If required.

### How to use:
1. Download the binary [release pack](https://github.com/ch010060/DLsite_Renamer/releases) and run .exe or run python script by yourself.
2. There are two versions:	
	1. GUI ver. => dlsite_renamer.py(.exe)
	2. CLI ver. => dlsite_renamer-cli.py(.exe)

### Install requirements：
1. Install Python 3.10 or newer (the GUI also needs Tk: `tkinter` must be importable)
2. Install pip
```
python -m pip install -r requirements.txt
```

### Custom pattern：
We will replace the filename with these keywords.  

*Keyword List:*
1. workno: 作品番號
2. circle: サークル/會社
3. title: 標題
4. cv: 声優
5. author: 著者
6. work_age: 年齡指定
7. release_date: 販売日
8. type: 作品形式

**Default template**: "workno title"

E.g, VJ009178 英雄伝説 零の軌跡

**User defined template**: Please modify the "config.json" to customize your "type" and "to" replace rules, or use default template

The prefix code meaning of DLsite works (always 6 or 8 digits, e.g. RJ123456 / RJ01234567):  
RJ => Doujin works ("rj" template)  
RE => Old English-site works (uses the "re" template if defined, otherwise "rj")  
BJ => Books / Comics ("bj" template)  
VJ => Commercial games / software ("vj" template)  

E.g,
```
"type": "vj"        
"to": "(type)(work_age)[release_date][workno][circle] title "     
```

Before：[prefix_12345] VJ009178 零.軌跡 (postfix-56789)

After：(ゲーム)(全年齢)[150417][VJ009178][Falcom] 英雄伝説 零の軌跡  
  
*config.json example*
```json
{
	 "replace_rules":
	 [
		{
            		"type": "rj",
			"from": "",
			"to": "workno title (CV. cv) "
		},
		{
            		"type": "bj",
			"from": "",
			"to": "[workno][circle (author)] title "
		},
		{
            		"type": "vj",
			"from": "",
			"to": "[workno][circle] title "
		}
	]
}
```

*Optional config.json keys*
```json
{
	"replace_rules": [ ... ],
	"max_name_bytes": 255,
	"request_delay": 1.0,
	"asmr_one_fallback": true
}
```
- `max_name_bytes`: longest file/folder name in UTF-8 bytes (default 255, safe for Windows and Linux NAS shares).
  If a new name is longer, only the **title** is shortened; the work code, `(CV. xxx)`, other fields and the
  extension are kept. Lower it for special shares (e.g. 143 for Synology encrypted folders).
- `request_delay`: seconds between DLsite requests (default 1.0, plus a small random delay).
- `asmr_one_fallback`: use ASMR.ONE as the second fallback for RJ/VJ works (default `true`; `false` to turn it off).

### Notice：
1. Please modify the config.json under **UTF-8**.
![Notepad3](https://i.imgur.com/L73BXEZ.png)
2. You can delete the unnecessary string between【】in filename if required.
3. Skip download the "cover.jpg" if it does exist. A cover is only saved when DLsite has a real image.
4. Special character processing: Convert the "Windows invalid character" to fullwidth form, multilple spaces to single space.
   Double quotes (`" “ ” ＂`) become `'`.
5. Name cleanup runs on **every** file and folder that is processed (also files without a work code), so names
   are the same on Mac, Windows and NAS shares:
   Unicode NFC, half-width katakana → full width (ｶﾞｲﾄﾞ → ガイド), characters a Mac writes to SMB shares
   (e.g. U+F022) → fullwidth form, no trailing space or dot. Hidden files (`.xxx`, `._xxx`) are not touched.
6. Preview (dry-run): tick「預覽（不重命名）」in the GUI or use `-n` in the CLI to see every new name without
   renaming anything or downloading covers.
   GUI: choose the folder (or type the path), then press「開始」. The work list shows every item with its status
   (等待 / 處理中 / 已重命名 / 修復 / 未變更 / ✖ 未重命名 with the reason), plus a progress bar and percentage.
   「只顯示問題」shows only the works that were not renamed;「停止」ends the run after the current item.
   The full log is hidden; click「日誌」to open it (the label shows the error count).
7. An existing file is never overwritten; the conflict is listed instead.
8. Works that are not renamed are listed at the end with the reason (missing fields, not found, blocked,
   name too long, conflict, error). The CLI exits with code 1 when the list is not empty.
9. Work data comes from DLsite's product data (one request per work, every DLsite section). This is not an
   official DLsite API, so if it fails in any way (blocked 403/429/503/529 after waiting and retrying, removed,
   server error, maintenance page, changed format, network error), the renamer uses DLsite's basic info
   (title, date, age, cover) plus the work's own web page (circle, CV, author, type). Works without a web page
   (e.g. comics sold on comipo, AI works, old English-site works) and translations whose template needs the
   circle are listed, not renamed. After 3 product-data failures in a row the rest of the run uses the fallback
   directly. After 3 works in a row without full data from any source it pauses 5 minutes; if still failing,
   it stops asking DLsite and lists the remaining works.
10. Second fallback — ASMR.ONE (RJ/VJ voice works, `asmr_one_fallback`): used whenever DLsite cannot give the
    full work info for any reason (not found, no longer sold, only basic info, outage). DLsite data always wins;
    ASMR.ONE fills the gaps (circle, CV, type, date, age, cover). If DLsite has no title at all, the ASMR.ONE title
    is used and may be a Chinese translation (a warning is logged). It is also asked for the CV when DLsite lists
    none, and for the cover when DLsite has none. Translations keep the circle unknown; there is no author.
    ASMR.ONE may be region-blocked outside East Asia; after 3 failures in a row it is skipped for the run.
11. In recursive mode, files inside a work folder keep their own names unless they contain a work code themselves.

### (Optional) CLI version without GUI and loop：
```
usage: dlsite_renamer-cli.py [-h] [-d] [-c] [-r] [-n] -i PATH

Renamer for DLsite works v4.2

options:
  -h, --help            show this help message and exit
  -d, --DEL             delete string in 【】
  -c, --COVER           download cover
  -r, --RECUR           recursively processing
  -n, --dry-run         preview the new names without renaming or downloading
                        anything
  -i PATH, --PATH PATH  path for processing
```

### Tests
```
python -m unittest discover -s tests
```
