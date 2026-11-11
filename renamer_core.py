"""Shared logic for the DLsite renamer GUI (dlsite_renamer.py) and CLI (dlsite_renamer-cli.py).

The design notes are in scratchpad.md (sections 1.1 - 1.11).
"""
import errno
import json
import os
import random
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
    """Fields the template uses but DLsite did not provide.

    An empty CV/author list is normal (the template's "(CV. cv)" / "(author)" is removed);
    None means unknown (only basic data was available), which counts as missing.
    """
    raw = set(KEYWORDS.findall(template))
    used = set(KEYWORDS.findall(prepare_template(template, fields)))
    missing = [k for k in SCRAPED_KEYWORDS if k in used and not fields.get(k)]
    return missing + [k for k in ('cv', 'author') if k in raw and fields.get(k) is None]


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
DEFAULT_REQUEST_DELAY = 1.0


@dataclass
class Config:
    templates: dict = field(default_factory=lambda: {'rj': DEFAULT_TEMPLATE, 'bj': DEFAULT_TEMPLATE,
                                                     'vj': DEFAULT_TEMPLATE})
    max_name_bytes: int = DEFAULT_MAX_NAME_BYTES
    request_delay: float = DEFAULT_REQUEST_DELAY   # seconds between DLsite requests (plus random jitter)

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
        if 'request_delay' in config:
            cfg.request_delay = float(config['request_delay'])
    except (OSError, ValueError, TypeError, AttributeError) as err:
        log(f"**配置文件讀取失敗, 使用默認設定: {err}\n")
        return Config()
    return cfg


# ---------------------------------------------------------------------------
# DLsite access (scratchpad 1.3, 1.4 and the Phase 3 product.json research)
#   1. product.json  -> every field, every DLsite section, one request
#   2. blocked (403/429/503/529) -> back off, fresh session, retry
#   3. still blocked -> ajax product info (title, date, age, cover only)
# ---------------------------------------------------------------------------
PRODUCT_URL = 'https://www.dlsite.com/maniax/api/=/product.json'
INFO_URL = 'https://www.dlsite.com/maniax/product/info/ajax'
WORK_PAGE = 'https://www.dlsite.com/{site}/work/=/product_id/{code}.html'
BLOCKED_STATUSES = (403, 429, 503, 529)
FALLBACK_UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
               '(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36')
GAME_TYPES = ["アクション", "クイズ", "アドベンチャー", "ロールプレイング", "テーブル", "デジタルノベル",
              "シミュレーション", "タイピング", "シューティング", "パズル", "その他ゲーム"]
AGE_LABELS = {1: '全年齢', 2: 'R-15', 3: 'R18'}  # same text the work pages show


@dataclass
class WorkInfo:
    status: int           # 200 ok, 404 not on DLsite, 0 network error, else the HTTP status
    url: str
    fields: dict = None   # None unless status == 200
    error: str = ''       # network error message when status == 0
    partial: str = ''      # why only basic info is available ('' = full data)


class BatchAborted(Exception):
    """DLsite kept blocking after the cool-down; stop asking it for more works."""


def _yymmdd(date):
    m = re.match(r'(\d{4})-(\d{2})-(\d{2})', date or '')
    return m.group(1)[2:] + m.group(2) + m.group(3) if m else None


def _cover_url(url):
    """Absolute cover URL, or '' for none (DLsite uses a no_img_main.gif placeholder for works without one)."""
    if not url or '/no_img' in url:
        return ''
    return 'https:' + url if url.startswith('//') else url


def _work_type(label):
    return "ゲーム" if label in GAME_TYPES else label


def fields_from_product(w):
    """Fields from one product.json entry."""
    creaters = w.get('creaters')
    voices = creaters.get('voice_by') if isinstance(creaters, dict) else None
    image = w.get('image_main')
    return {
        'title': w.get('work_name') or None,
        'circle': w.get('maker_name') or None,
        'cv': [v['name'] for v in voices or [] if isinstance(v, dict) and v.get('name')],
        'author': [a['author_name'] for a in w.get('author') or [] if isinstance(a, dict) and a.get('author_name')],
        'type': _work_type(w.get('work_type_string')) or None,
        'work_age': AGE_LABELS.get(w.get('age_category')),
        'release_date': _yymmdd(w.get('regist_date')),
        'img_url': _cover_url(image.get('url') if isinstance(image, dict) else ''),
        'site_id': w.get('site_id'),
    }


def fields_from_info(w):
    """Fields from the basic info endpoint. circle/cv/author/type are unknown (None)."""
    date = _yymmdd(w.get('regist_date'))
    return {
        'title': w.get('work_name') or None,
        'circle': None, 'cv': None, 'author': None, 'type': None,
        'work_age': AGE_LABELS.get(w.get('age_category')),
        'release_date': None if (w.get('regist_date') or '').startswith('5100') else date,  # 5100 = no date
        'img_url': _cover_url(w.get('work_image')),
        'site_id': w.get('site_id'),
    }


class DlsiteClient:
    """Talks to DLsite. One desktop browser identity per session; a fresh session after a block."""

    def __init__(self, log=print, sleep=time.sleep, backoff=(5, 15, 45), block_limit=3, cooldown=300,
                 timeout=(5, 20), session_factory=None):
        self.log, self.sleep = log, sleep
        self.backoff, self.block_limit, self.cooldown, self.timeout = backoff, block_limit, cooldown, timeout
        self._session_factory = session_factory or self._make_session
        self._ua = None
        self.blocked_streak = 0
        self.cooled_down = False
        self.session = self._session_factory()

    # -- session -----------------------------------------------------------
    def _user_agent(self):
        try:
            if self._ua is None:
                from fake_useragent import UserAgent
                self._ua = UserAgent(browsers=['Chrome', 'Edge', 'Firefox', 'Safari'],
                                     os=['Windows', 'Mac OS X'], platforms='desktop')
            return self._ua.random
        except Exception:
            self._ua = False
            return FALLBACK_UA

    def _make_session(self):
        import requests
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry
        session = requests.Session()
        # Transient server errors only; blocking statuses are handled by _get() with longer waits.
        retry = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 504],
                      respect_retry_after_header=True, raise_on_status=False)
        session.mount('https://', HTTPAdapter(max_retries=retry))
        session.headers.update({
            'User-Agent': self._user_agent() if self._ua is not False else FALLBACK_UA,
            'Accept': 'application/json, text/javascript, */*; q=0.01',
            'Accept-Language': 'ja,en;q=0.8',
            'Referer': 'https://www.dlsite.com/',
        })
        session.cookies.update({'adultchecked': '1', 'locale': 'ja_JP'})
        return session

    # -- requests ----------------------------------------------------------
    def _retry_after(self, response, attempt):
        value = response.headers.get('Retry-After', '')
        if value.isdigit():
            return min(int(value), self.cooldown)
        return self.backoff[attempt] + random.uniform(0, self.backoff[attempt] / 3)

    def _get(self, url, params):
        """GET with the blocked-status back-off. Returns the final response."""
        for attempt in range(len(self.backoff) + 1):
            response = self.session.get(url, params=params, timeout=self.timeout)
            if response.status_code not in BLOCKED_STATUSES or attempt == len(self.backoff):
                return response
            wait = self._retry_after(response, attempt)
            self.log(f"**DLsite 回應 HTTP {response.status_code}, {wait:.0f} 秒後以新連線重試 "
                     f"({attempt + 1}/{len(self.backoff)})")
            self.sleep(wait)
            self.session = self._session_factory()
        return response

    def fetch(self, code):
        """Return a WorkInfo for one work code. Raises BatchAborted, or requests' (OSError) network errors."""
        params = {'workno': code}
        r = self._get(PRODUCT_URL, params)
        url = r.url if hasattr(r, 'url') else PRODUCT_URL
        if r.status_code == 200:
            self._not_blocked()
            data = r.json()
            if data:
                fields = fields_from_product(data[0])
                return WorkInfo(200, WORK_PAGE.format(site=fields['site_id'], code=code), fields)
            # No full data. Some listings (e.g. per-chapter comics 【話販売】) only have basic info.
            return self._basic_info(code, WorkInfo(404, url), 'DLsite has only basic info for this work')
        if r.status_code not in BLOCKED_STATUSES:
            return WorkInfo(r.status_code, url)

        # Still blocked: basic info as the second source.
        self.log("**完整資料被拒絕, 改用基本資料 (只有標題/日期/年齡/封面)")
        result = self._basic_info(code, WorkInfo(r.status_code, url),
                                  'DLsite blocked the full data; only basic info was available')
        self._blocked()
        return result

    def _basic_info(self, code, otherwise, reason):
        info = self._get(INFO_URL, {'product_id': code, 'cdn_cache_min': 1})
        if info.status_code != 200:
            return otherwise
        data = info.json()
        if not data:
            return WorkInfo(404, otherwise.url)
        if code not in data:
            return otherwise
        fields = fields_from_info(data[code])
        return WorkInfo(200, WORK_PAGE.format(site=fields['site_id'], code=code), fields, partial=reason)

    def _not_blocked(self):
        self.blocked_streak = 0
        self.cooled_down = False

    def _blocked(self):
        """Circuit breaker: after block_limit blocked works in a row, cool down once; then stop."""
        self.blocked_streak += 1
        if self.blocked_streak < self.block_limit:
            return
        if self.cooled_down:
            raise BatchAborted(f'DLsite still blocking after a {self.cooldown}s cool-down')
        self.log(f"**連續 {self.blocked_streak} 個作品被 DLsite 拒絕, 暫停 {self.cooldown} 秒...")
        self.sleep(self.cooldown)
        self.session = self._session_factory()
        self.cooled_down = True
        self.blocked_streak = self.block_limit - 1  # one more blocked work after the pause stops the batch

    def download_cover(self, img_url, store_path):
        """Download to a temporary file first, so a failed download never leaves a broken cover.jpg."""
        part = store_path + '.part'
        try:
            r = self.session.get(img_url, timeout=self.timeout, stream=True)
            if r.status_code != 200:
                raise OSError(f'HTTP {r.status_code} {img_url}')
            if not r.headers.get('Content-Type', 'image/').startswith('image/'):
                raise OSError(f'not an image: {r.headers.get("Content-Type")} {img_url}')
            with open(part, 'wb') as f:
                for chunk in r.iter_content(65536):
                    f.write(chunk)
            os.replace(part, store_path)
        finally:
            if os.path.exists(part):
                os.remove(part)


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
CATEGORIES = ['missing fields', 'name too long', 'not found', 'blocked', 'http error', 'network error',
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


def process(root, options, config, log, fetcher=None, delay=None):
    """Rename DLsite works and repair every other visited name. Returns a Report."""
    report = Report()
    if fetcher is None:
        fetcher = DlsiteClient(log=log)
    if delay is None:
        delay = config.request_delay
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
        if cache.get('__aborted__'):
            cache[code] = WorkInfo(-1, '', None, error=cache['__aborted__'])
        else:
            try:
                cache[code] = fetcher.fetch(code)
            except BatchAborted as err:
                log(f"**DLsite 持續拒絕連線, 停止查詢其餘作品: {err}")
                cache['__aborted__'] = str(err)
                cache[code] = WorkInfo(-1, '', None, error=str(err))
            except OSError as err:  # requests' network errors are OSError subclasses
                cache[code] = WorkInfo(0, '', None, error=str(err))
            if delay:
                time.sleep(delay + random.uniform(0, delay / 2))  # be polite to the server
    info = cache[code]

    if info.status != 200:
        if info.status == 404:
            log("**DLsite 上找不到本作!\n  請檢查本作是否已經下架, 尚未發售或被收入合集")
            report.add(code, 'not found', 'not on DLsite (removed, unreleased or merged into a bundle)', src)
        elif info.status == -1:
            report.add(code, 'blocked', f'not looked up: {info.error}', src)
        elif info.status in BLOCKED_STATUSES:
            log(f"**DLsite 拒絕連線 (HTTP {info.status}), 已重試仍失敗")
            report.add(code, 'blocked', f'HTTP {info.status} after retries', src)
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
                fetcher.download_cover(fields['img_url'], store_path)
            except OSError as err:
                log(f"**下載封面過程中出現錯誤! {err}")

    template = config.template_for(code)
    missing = missing_fields(template, fields)
    if missing:
        log("**缺少欄位, 不重命名: " + ', '.join(missing))
        detail = ', '.join(missing)
        if info.partial:
            detail += f' ({info.partial})'
        report.add(code, 'missing fields', detail, src)
        return repair_name(name), 'repair'

    ext = os.path.splitext(name)[1] if os.path.isfile(src) else ''
    try:
        new_name, shortened = build_name(template, code, fields, ext, config.max_name_bytes)
    except NameTooLong as err:
        log(f"**名稱過長, 即使縮短標題仍超過 {config.max_name_bytes} bytes, 不重命名")
        report.add(code, 'name too long', f'too long even after shortening the title: {err}', src)
        return repair_name(name), 'repair'
    return new_name, ('rename_shortened' if shortened else 'rename')
