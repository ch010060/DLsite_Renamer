"""Shared logic for the DLsite renamer GUI (dlsite_renamer.py) and CLI (dlsite_renamer-cli.py).

The design notes are in scratchpad.md (sections 1.1 - 1.11).
"""
import errno
import json
import os
import re
import time
import unicodedata
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Work code (scratchpad 1.1, verified against DLsite 2026-11-11)
# Prefix RJ / RE / BJ / VJ, followed by exactly 6 or exactly 8 digits.
# ---------------------------------------------------------------------------
WORK_CODE = re.compile(r'(?<![A-Z0-9])(RJ|RE|BJ|VJ)(\d{8}|\d{6})(?!\d)')


def find_work_codes(name):
    """Return every distinct work code in a file/folder base name, in order."""
    codes = []
    for m in WORK_CODE.finditer(unicodedata.normalize('NFC', name).upper()):
        code = m.group(1) + m.group(2)
        if code not in codes:
            codes.append(code)
    return codes


# ---------------------------------------------------------------------------
# Name normalization (scratchpad 1.10, 1.11)
# ---------------------------------------------------------------------------
def _fullwidth(ch):
    return chr(ord(ch) + 0xFEE0)


# macOS SMB clients send NTFS-illegal characters as private-use code points
# (Samba source3/lib/string_replace.c).
_PUA_TABLE = {cp: None for cp in range(0xF001, 0xF020)}           # control chars 0x01-0x1F
_PUA_TABLE[0xF020] = '"'                                            # -> handled by the quote table
_PUA_TABLE.update({0xF021 + i: _fullwidth(c) for i, c in enumerate('*:<>?\\|')})
_PUA_TABLE[0xF028] = None                                           # trailing space
_PUA_TABLE[0xF029] = None                                           # trailing dot

# Every double-quote variant becomes an ASCII apostrophe; single quotes are kept.
_QUOTE_TABLE = {ord(c): "'" for c in '"“”„‟＂'}

# Remaining characters that are illegal on Windows/SMB -> full width; control chars removed.
_ILLEGAL_TABLE = {ord(c): _fullwidth(c) for c in '\\/:*?<>|'}
_ILLEGAL_TABLE.update({cp: None for cp in range(0x00, 0x20)})
_ILLEGAL_TABLE[0x7F] = None

_HALFWIDTH_KANA = re.compile(r'[｡-ﾟ]+')


def _widen_kana(match):
    s = unicodedata.normalize('NFC', unicodedata.normalize('NFKC', match.group()))
    # A voiced mark with nothing to attach to stays combining after NFKC: use the spacing form.
    return s.replace('゙', '゛').replace('゚', '゜')


def normalize_name(name, collapse_spaces=False):
    """Clean one file/folder name so it is identical everywhere (Mac, Windows, Linux NAS).

    collapse_spaces: also turn runs of whitespace into one space (used for template names,
    as the renamer always did; repair of existing names leaves inner spaces alone).
    Returns '' only if nothing usable is left.
    """
    s = unicodedata.normalize('NFC', name)
    s = s.translate(_PUA_TABLE)
    s = s.translate(_QUOTE_TABLE)
    s = _HALFWIDTH_KANA.sub(_widen_kana, s)
    s = s.translate(_ILLEGAL_TABLE)
    if collapse_spaces:
        s = ' '.join(s.split())
    s = s.strip(' ').rstrip(' .')
    return unicodedata.normalize('NFC', s)


def repair_name(name):
    """Normalized version of an existing name, or the name itself if nothing usable would be left."""
    return normalize_name(name) or name


def name_bytes(name):
    """Bytes the name needs on a share: the larger of its NFC and NFD UTF-8 lengths (scratchpad 1.8)."""
    return max(len(unicodedata.normalize(form, name).encode('utf-8')) for form in ('NFC', 'NFD'))


# ---------------------------------------------------------------------------
# Template rendering (scratchpad 1.7) and length limit (scratchpad 1.8)
# ---------------------------------------------------------------------------
KEYWORDS = re.compile(r'release_date|work_age|workno|circle|author|title|type|cv')
SCRAPED_KEYWORDS = ('title', 'circle', 'work_age', 'release_date', 'type')


class NameTooLong(Exception):
    pass


def prepare_template(template, fields):
    """Apply the existing empty-list rules to the template before substitution."""
    if not fields.get('author'):
        template = template.replace('(author)', '')
    if not fields.get('cv'):
        template = template.replace('(CV. cv)', '')
    return template


def missing_fields(template, fields):
    """Scraped fields the template uses but DLsite did not provide."""
    used = set(KEYWORDS.findall(prepare_template(template, fields)))
    return [k for k in SCRAPED_KEYWORDS if k in used and not fields.get(k)]


def _render(template, values):
    # One pass over the template only: text that comes from a value is never scanned again.
    return KEYWORDS.sub(lambda m: values[m.group()], template)


def build_name(template, code, fields, ext, max_bytes):
    """Return (new_name, title_was_shortened). Raises NameTooLong.

    Only the title is shortened; every other field, the template text and the extension stay whole.
    """
    template = prepare_template(template, fields)
    values = {
        'workno': code,
        'circle': fields.get('circle') or '',
        'work_age': fields.get('work_age') or '',
        'release_date': fields.get('release_date') or '',
        'type': fields.get('type') or '',
        'author': ','.join(fields.get('author') or []),
        'cv': ','.join(fields.get('cv') or []),
    }
    ext = unicodedata.normalize('NFC', ext)
    title = normalize_name(fields.get('title') or '', collapse_spaces=True)

    def make(t):
        return normalize_name(_render(template, dict(values, title=t)), collapse_spaces=True) + ext

    full = make(title)
    if name_bytes(full) <= max_bytes:
        return full, False
    if 'title' not in KEYWORDS.findall(template):
        raise NameTooLong(full)

    # Valid cut points: never directly before a combining mark.
    cuts = [k for k in range(len(title)) if not unicodedata.combining(title[k])]
    lo, hi, best = 0, len(cuts) - 1, None
    while lo <= hi:  # largest cut that fits (name length grows with the cut)
        mid = (lo + hi) // 2
        candidate = make(title[:cuts[mid]].rstrip())
        if name_bytes(candidate) <= max_bytes:
            best, lo = candidate, mid + 1
        else:
            hi = mid - 1
    if best is None:
        raise NameTooLong(full)
    return best, True


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DEFAULT_TEMPLATE = 'workno title '
DEFAULT_MAX_NAME_BYTES = 255


@dataclass
class Config:
    templates: dict = field(default_factory=lambda: {'rj': DEFAULT_TEMPLATE, 'bj': DEFAULT_TEMPLATE,
                                                     'vj': DEFAULT_TEMPLATE})
    max_name_bytes: int = DEFAULT_MAX_NAME_BYTES

    def template_for(self, code):
        prefix = code[:2].lower()
        if prefix == 're':
            return self.templates.get('re', self.templates['rj'])
        return self.templates[prefix]


def load_config(basedir, log):
    """Read config.json (creating the default one if missing). The template format is unchanged."""
    fname = os.path.join(basedir, 'config.json')
    cfg = Config()
    if not os.path.exists(fname):
        default = {'replace_rules': [{'type': t, 'from': '', 'to': DEFAULT_TEMPLATE} for t in ('rj', 'bj', 'vj')]}
        try:
            with open(fname, 'w', encoding='utf-8') as f:
                json.dump(default, f, ensure_ascii=False, sort_keys=False, indent=4)
        except OSError as err:
            log(f"**無法建立配置文件: {err}")
        log("**使用默認命名模板:")
        log("  " + DEFAULT_TEMPLATE.strip() + "\n")
        return cfg
    try:
        with open(fname, 'r', encoding='utf-8') as f:
            config = json.load(f)
        for tag in config.get('replace_rules') or []:
            kind = str(tag.get('type', '')).lower()
            if 'workno' in tag.get('to', ''):
                cfg.templates[kind] = tag['to']
                log(f"**使用自定義{kind.upper()}命名模板:")
                log("  " + tag['to'].strip() + "\n")
            else:
                log("**模板格式錯誤: 模板中必須包含\"workno\"!")
                log("  使用默認命名模板: " + DEFAULT_TEMPLATE.strip() + "\n")
        if 'max_name_bytes' in config:
            cfg.max_name_bytes = int(config['max_name_bytes'])
    except (OSError, ValueError, TypeError, AttributeError) as err:
        log(f"**配置文件讀取失敗, 使用默認設定: {err}\n")
        return Config()
    return cfg


# ---------------------------------------------------------------------------
# DLsite access (moved unchanged from the old scripts; reworked in Phase 3)
# ---------------------------------------------------------------------------
RJ_WEBPATH = 'https://www.dlsite.com/maniax/work/=/product_id/'
RJ_G_WEBPATH = 'https://www.dlsite.com/home/work/=/product_id/'
BJ_WEBPATH = 'https://www.dlsite.com/books/work/=/product_id/'
BJ_G_WEBPATH = 'https://www.dlsite.com/comic/work/=/product_id/'
VJ_WEBPATH = 'https://www.dlsite.com/pro/work/=/product_id/'
VJ_G_WEBPATH = 'https://www.dlsite.com/soft/work/=/product_id/'
WEBPATHS = {'RJ': (RJ_WEBPATH, RJ_G_WEBPATH), 'RE': (RJ_WEBPATH, RJ_G_WEBPATH),
            'BJ': (BJ_WEBPATH, BJ_G_WEBPATH), 'VJ': (VJ_WEBPATH, VJ_G_WEBPATH)}
R_COOKIE = {'adultchecked': '1'}
FALLBACK_UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
               '(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36')
GAME_TYPES = ["アクション", "クイズ", "アドベンチャー", "ロールプレイング", "テーブル", "デジタルノベル",
              "シミュレーション", "タイピング", "シューティング", "パズル", "その他ゲーム"]


@dataclass
class WorkInfo:
    status: int
    url: str
    fields: dict = None   # None unless status == 200
    user_agent: str = ''
    error: str = ''       # network error message when status == 0


class DlsiteFetcher:
    def __init__(self):
        import requests
        from lxml import html
        self._html = html
        requests.adapters.DEFAULT_RETRIES = 5
        self.session = requests.session()
        try:
            from fake_useragent import UserAgent
            self._ua = UserAgent()
        except Exception:
            self._ua = None

    def user_agent(self):
        try:
            return self._ua.random if self._ua else FALLBACK_UA
        except Exception:
            return FALLBACK_UA

    def fetch(self, code):
        primary, general = WEBPATHS[code[:2]]
        user_agent = self.user_agent()
        url = primary + code
        r = self.session.get(url, allow_redirects=True, cookies=R_COOKIE, headers={'user-agent': user_agent})
        if r.status_code != 200:
            url = general + code  # general-audience site
            r = self.session.get(url, allow_redirects=False, cookies=R_COOKIE)
            if r.status_code != 200:
                return WorkInfo(r.status_code, url, None, user_agent)
        return WorkInfo(200, url, self._parse(r.content), user_agent)

    def _parse(self, content):
        tree = self._html.fromstring(content)

        def first(*queries):
            for q in queries:
                for v in tree.xpath(q):
                    if v and v.strip():
                        return v.strip()
            return None

        img_url = first('//meta[@property="og:image"]/@content',
                        '//meta[@name="twitter:image:src"]/@content') or ''
        if img_url.startswith('//'):
            img_url = 'https:' + img_url
        work_type = first('//*[@id="work_outline"]/tr/th[contains(text(), "作品形式")]/../td/div/a/span/text()')
        if work_type in GAME_TYPES:
            work_type = "ゲーム"
        release_date = first('//*[@id="work_outline"]/tr/th[contains(text(), "販売日")]/../td/a/text()')
        if release_date and len(release_date) >= 11:  # 20ab年cd月ef日 => abcdef
            release_date = release_date[2:4] + release_date[5:7] + release_date[8:10]
        return {
            'img_url': img_url,
            'title': first('//h1[@id="work_name"]/text()'),
            'circle': first('//span[@itemprop="brand" and @class="maker_name"]/*/text()'),
            'cv': [v.strip() for v in tree.xpath(
                '//*[@id="work_outline"]/tr/th[contains(text(), "声優")]/../td/a/text()') if v.strip()],
            'author': [v.strip() for v in tree.xpath(
                '//*[@id="work_maker"]/tr/th[contains(text(), "著者")]/../td/a/text()') if v.strip()],
            'type': work_type,
            'work_age': first(
                '//*[@id="work_outline"]/tr/th[contains(text(), "年齢指定")]/../td/div/a/span/text()',
                '//*[@id="work_outline"]/tr/th[contains(text(), "年齢指定")]/../td/div/span/text()'),
            'release_date': release_date,
        }

    def download_cover(self, img_url, store_path, user_agent):
        import urllib.request
        opener = urllib.request.build_opener()
        opener.addheaders = [('User-agent', user_agent)]
        urllib.request.install_opener(opener)
        urllib.request.urlretrieve(img_url, store_path)


# ---------------------------------------------------------------------------
# Processing
# ---------------------------------------------------------------------------
@dataclass
class Options:
    delete_brackets: bool = False   # remove 【...】 from the title
    download_cover: bool = False
    recursive: bool = False
    dry_run: bool = False


# Order of categories in the end-of-run list (scratchpad 1.6)
CATEGORIES = ['missing fields', 'name too long', 'not found', 'http error', 'network error',
              'conflict', 'rename failed', 'error']


@dataclass
class Problem:
    code: str
    category: str
    detail: str
    path: str


@dataclass
class Report:
    renamed: int = 0
    repaired: int = 0
    unchanged: int = 0
    shortened: int = 0
    problems: list = field(default_factory=list)

    def add(self, code, category, detail, path):
        self.problems.append(Problem(code or '-', category, detail, path))

    def summary_lines(self, dry_run=False):
        verb = "將" if dry_run else "已"
        lines = ["===== 結果" + (" (預覽, 未更改任何檔案)" if dry_run else "") + " =====",
                 f"{verb}重命名: {self.renamed}   {verb}修復檔名: {self.repaired}   "
                 f"未變更: {self.unchanged}   標題已縮短: {self.shortened}"]
        if self.problems:
            lines.append(f"\n未重命名 ({len(self.problems)}):")
            for p in sorted(self.problems, key=lambda p: CATEGORIES.index(p.category)):
                lines.append(f"  {p.code:<11} {p.category}: {p.detail}")
                lines.append(f"  {'':<11} {p.path}")
        return lines


def iter_entries(root, recursive):
    """(directory, name) pairs. Bottom-up, so a folder is handled after everything inside it."""
    if recursive:
        for dirpath, dirnames, filenames in os.walk(root, topdown=False, followlinks=False):
            for name in sorted(filenames) + sorted(dirnames):
                yield dirpath, name
    else:
        for name in sorted(os.listdir(root)):
            yield root, name


def conflicts(src, dst):
    """True if dst is a different, existing file (NFC/NFD- and case-only renames on macOS are fine)."""
    if not os.path.lexists(dst):
        return False
    try:
        return not os.path.samefile(src, dst)
    except OSError:
        return True


def process(root, options, config, log, fetcher=None, delay=0.1):
    """Rename DLsite works and repair every other visited name. Returns a Report."""
    report = Report()
    if fetcher is None:
        fetcher = DlsiteFetcher()
    cache = {}
    log("選擇路徑: " + root + ("   [預覽模式]" if options.dry_run else "") + "\n")

    for directory, name in iter_entries(root, options.recursive):
        if name.startswith('.'):
            continue  # hidden files, including macOS ._ metadata
        src = os.path.join(directory, name)
        if os.path.islink(src) and not os.path.exists(src):
            continue  # broken symlink
        codes = find_work_codes(name)
        code = codes[0] if codes else None
        try:
            if code is None:
                new_name, kind = repair_name(name), 'repair'
            else:
                if len(codes) > 1:
                    log(f"**名稱中有多個作品編號, 使用 {code}: {name}")
                new_name, kind = _new_name_for(src, name, code, options, config, log, fetcher, cache, report,
                                               delay)
            if new_name == name:
                report.unchanged += 1
                continue
            dst = os.path.join(directory, new_name)
            if conflicts(src, dst):
                log(f"**目標已存在, 跳過: {new_name}")
                report.add(code, 'conflict', f'another item is already named "{new_name}"', src)
                continue
            label = "修復檔名" if kind == 'repair' else "重命名"
            if kind == 'rename_shortened':
                log(f"  標題已縮短以符合 {config.max_name_bytes} bytes 限制")
                report.shortened += 1
            log(f"  {'[預覽] ' if options.dry_run else ''}{label}: {name}\n    -> {new_name}")
            if not options.dry_run:
                os.rename(src, dst)
            if kind != 'repair':
                report.renamed += 1
            else:
                report.repaired += 1
        except OSError as err:
            if err.errno == errno.ENAMETOOLONG:
                detail = 'name too long for this file system'
            else:
                detail = str(err)
            log(f"**重命名失敗: {detail}")
            report.add(code, 'rename failed', detail, src)
        except Exception as err:  # one bad item must never stop the run
            log(f"**處理時出現錯誤: {err!r}")
            report.add(code, 'error', repr(err), src)

    for line in report.summary_lines(options.dry_run):
        log(line)
    log("*******完成!*******\n")
    return report


def _new_name_for(src, name, code, options, config, log, fetcher, cache, report, delay):
    """Return (new_name, kind); kind is 'rename' / 'rename_shortened' (from the template) or 'repair'."""
    log('Processing: ' + code)

    if code not in cache:
        try:
            cache[code] = fetcher.fetch(code)
        except OSError as err:  # requests' network errors are OSError subclasses
            cache[code] = WorkInfo(0, '', None, error=str(err))
        time.sleep(delay)  # avoid being blocked by the server
    info = cache[code]

    if info.status != 200:
        if info.status == 404:
            log("**爬取DLsite過程中出現錯誤!\n  請檢查本作是否已經下架或被收入合集")
            report.add(code, 'not found', 'removed or merged into a bundle (HTTP 404)', src)
        elif info.status == 0:
            log("**請求失敗! 請檢查網絡連接")
            report.add(code, 'network error', info.error, src)
        else:
            log(f"**爬取DLsite過程中出現錯誤!\n  網頁 URL: {info.url}\n  HTTP 狀態碼: {info.status}")
            report.add(code, 'http error', f'HTTP {info.status} {info.url}', src)
        return repair_name(name), 'repair'

    fields = dict(info.fields)
    if options.delete_brackets and fields.get('title'):
        fields['title'] = re.sub(r'【.*?】', '', fields['title'])

    is_dir = os.path.isdir(src)
    if options.download_cover and is_dir and fields.get('img_url') and not options.dry_run:
        store_path = os.path.join(src, 'cover.jpg')
        if os.path.isfile(store_path):
            log("**封面已存在，跳過下載!")
        else:
            log("  下載封面...")
            try:
                fetcher.download_cover(fields['img_url'], store_path, info.user_agent)
            except OSError as err:
                log(f"**下載封面過程中出現錯誤! {err}")

    template = config.template_for(code)
    missing = missing_fields(template, fields)
    if missing:
        log("**缺少欄位, 不重命名: " + ', '.join(missing))
        report.add(code, 'missing fields', ', '.join(missing), src)
        return repair_name(name), 'repair'

    ext = os.path.splitext(name)[1] if os.path.isfile(src) else ''
    try:
        new_name, shortened = build_name(template, code, fields, ext, config.max_name_bytes)
    except NameTooLong as err:
        log(f"**名稱過長, 即使縮短標題仍超過 {config.max_name_bytes} bytes, 不重命名")
        report.add(code, 'name too long', f'too long even after shortening the title: {err}', src)
        return repair_name(name), 'repair'
    return new_name, ('rename_shortened' if shortened else 'rename')
