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


# Natural breaks for shortened titles (Phase 5). A cut is moved back to the nearest break that keeps
# at least MIN_KEEP of the longest title that fits.
MIN_KEEP = 0.6
_OPENING = set('「『【（([［〈《〔｛{“‘')
_BREAK_BEFORE = _OPENING | set('～〜')                            # subtitle markers start a new part
_BREAK_AFTER = set(' \u3000、。，．,!！?？…‥」』】）)］]〉》〕｝}♪★☆♡♥')
_TRAILING_JUNK = set(' \u3000、，,・/／|｜×✖⇒→＋+&＆#＃-－:：～〜~') | _OPENING


def _script(ch):
    """Script class for weak breaks; None = continues the previous run (ー, combining marks)."""
    if ch == 'ー' or unicodedata.combining(ch):
        return None
    o = ord(ch)
    if 0x30A0 <= o <= 0x30FF or 0x31F0 <= o <= 0x31FF:
        return 'kata'
    if 0x3040 <= o <= 0x309F:
        return 'hira'
    if 0x4E00 <= o <= 0x9FFF or 0x3400 <= o <= 0x4DBF or ch in '々〆':
        return 'kanji'
    return 'latin' if ch.isalnum() else 'other'


def _weak_break(title, j):
    """After an emoji/symbol, or where the script changes (never kanji→hiragana: that splits okurigana)."""
    if unicodedata.category(title[j - 1]) == 'So':
        return True
    a, b = _script(title[j - 1]), _script(title[j])
    return bool(a and b and a != b and 'other' not in (a, b) and (a, b) != ('kanji', 'hira'))


def natural_cut(title, k):
    """Shorten title to at most k characters, ending at a natural break where possible."""
    if k >= len(title):
        return title
    low = max(1, int(k * MIN_KEEP))
    cut = k
    ok = [j for j in range(k, low - 1, -1) if not unicodedata.combining(title[j])]
    strong = [j for j in ok if title[j - 1] in _BREAK_AFTER or title[j] in _BREAK_BEFORE]
    if strong:
        cut = strong[0]
    else:
        weak = [j for j in ok if _weak_break(title, j)]
        if weak:
            cut = weak[0]
    t = title[:cut]
    while t and t[-1] in _TRAILING_JUNK:
        t = t[:-1]
    return t or title[:k].rstrip()


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
    lo, hi, best, best_k = 0, len(cuts) - 1, None, 0
    while lo <= hi:  # largest cut that fits (name length grows with the cut)
        mid = (lo + hi) // 2
        candidate = make(title[:cuts[mid]].rstrip())
        if name_bytes(candidate) <= max_bytes:
            best, best_k, lo = candidate, cuts[mid], mid + 1
        else:
            hi = mid - 1
    if best is None:
        raise NameTooLong(full)
    # Move the cut back to a natural break (never longer, so it still fits).
    natural = make(natural_cut(title, best_k))
    return (natural if name_bytes(natural) <= max_bytes else best), True


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
    asmr_one_fallback: bool = True                 # second fallback: ASMR.ONE (RJ/VJ voice works)

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
        if 'asmr_one_fallback' in config:
            cfg.asmr_one_fallback = bool(config['asmr_one_fallback'])
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
    """Stop asking DLsite for more works (still failing after the cool-down, or the fallback covers it)."""

    def __init__(self, message, fallback=False):
        super().__init__(message)
        self.fallback = fallback  # True: ASMR.ONE supplied the data, so no pause was needed


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


def _is_translation(t):
    return isinstance(t, dict) and bool(t.get('is_parent') or t.get('is_child'))


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
        'translation': _is_translation(w.get('translation_info')),
    }


class SourceError(Exception):
    """One DLsite source could not answer. status: HTTP status, 0 = network error, -2 = unusable response."""

    def __init__(self, status, reason):
        super().__init__(reason)
        self.status, self.reason = status, reason


DEFAULT_SITE = {'RJ': 'maniax', 'RE': 'maniax', 'BJ': 'books', 'VJ': 'pro'}  # when basic info is unavailable
_WORK_PAGE_URL = re.compile(r'https://www\.dlsite\.com/[\w-]+/(work|announce)/=/product_id/(\w+)\.html')


def _valid_product(w):
    return isinstance(w, dict) and 'work_name' in w and 'maker_name' in w


def is_work_page(url, code):
    """The final URL after redirects is this work's DLsite page (a translation may show its parent page)."""
    m = _WORK_PAGE_URL.match(url or '')
    return bool(m) and (m.group(2) == code or f'translation={code}' in url)


_PAGE_CIRCLE_LABELS = ('サークル名', 'ブランド名', '出版社名')


def parse_work_page(content):
    """Fields from a DLsite work page; supports the legacy (th/td) and the new (dt/dd) layout.

    Labels are Japanese because the client sends the locale=ja_JP cookie.
    """
    from lxml import html
    if isinstance(content, bytes):  # DLsite pages are UTF-8; do not let lxml guess (it assumes Latin-1)
        content = content.decode('utf-8', 'replace')
    tree = html.fromstring(content)

    def clean(text):
        return re.sub(r'\s+', ' ', text or '').strip()

    def cell(*labels):
        for label in labels:
            for key in tree.xpath('//th | //dt'):
                if clean(key.text_content()) == label and key.getnext() is not None:
                    return key.getnext()
        return None

    def links(c):
        if c is None:
            return []
        values = [clean(a.text_content()) for a in c.xpath('.//a')]
        return [v for v in values if v and v != 'フォローする']

    def first_text(c):
        if c is None:
            return None
        values = links(c) or [clean(t) for t in c.xpath('.//span//text()') if clean(t)] or [clean(c.text_content())]
        return values[0] if values and values[0] else None

    h1 = tree.xpath('//h1[@id="work_name"]')
    cv_cell = cell('声優')
    cv = links(cv_cell) or ([v.strip() for v in clean(cv_cell.text_content()).split('/') if v.strip()]
                            if cv_cell is not None else [])
    date = re.search(r'(\d{4})年(\d{1,2})月(\d{1,2})日', clean(cell('販売日').text_content())
                     if cell('販売日') is not None else '')
    work_type = first_text(cell('作品形式'))
    og = tree.xpath('//meta[@property="og:image"]/@content')
    return {
        'title': clean(h1[0].text_content()) or None if h1 else None,
        'circle': first_text(cell(*_PAGE_CIRCLE_LABELS)),
        'cv': cv,
        'author': links(cell('著者')),
        'type': _work_type(work_type) if work_type else None,
        'work_age': first_text(cell('年齢指定')),
        'release_date': f'{date.group(1)[2:]}{int(date.group(2)):02d}{int(date.group(3)):02d}' if date else None,
        'img_url': _cover_url(og[0] if og else ''),
    }


class DlsiteClient:
    """Talks to DLsite. One desktop browser identity per session; a fresh session after a block."""

    def __init__(self, log=print, sleep=time.sleep, backoff=(5, 15, 45), block_limit=3, cooldown=300,
                 timeout=(5, 20), session_factory=None):
        self.log, self.sleep = log, sleep
        self.backoff, self.block_limit, self.cooldown, self.timeout = backoff, block_limit, cooldown, timeout
        self._session_factory = session_factory or self._make_session
        self._ua = None
        self.blocked_streak = 0      # works in a row without full data (any source)
        self.cooled_down = False
        self.api_failures = 0        # product API failures in a row
        self.api_down = False        # skip the product API for the rest of the run
        self.rescued = 0             # works in the current failure streak that ASMR.ONE supplied
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
        """Return a WorkInfo for one work code. Raises BatchAborted.

        product.json -> (if it fails in any way) basic info + the work's web page.
        """
        if self.api_down:
            api_error = SourceError(-2, 'skipped (product API failed earlier in this run)')
        else:
            try:
                data = self._product(code)
            except SourceError as err:
                api_error = err
                self._api_failed(err)
            else:
                self.api_failures = 0
                if data:
                    self._service_ok()
                    fields = fields_from_product(data[0])
                    return WorkInfo(200, WORK_PAGE.format(site=fields['site_id'], code=code), fields)
                # Empty: no full data. Some listings (per-chapter comics 【話販売】, unreleased works) only have
                # basic info; unknown codes have nothing.
                return self._basic_only(code)
        return self._fallback(code, api_error)

    # -- sources -------------------------------------------------------------
    def _request(self, url, params):
        """GET (with the blocked back-off); network errors become SourceError."""
        try:
            return self._get(url, params)
        except OSError as err:  # requests' network errors are OSError subclasses
            raise SourceError(0, f'network error: {err}')

    @staticmethod
    def _check(r):
        if r.status_code in BLOCKED_STATUSES:
            raise SourceError(r.status_code, f'HTTP {r.status_code} (blocked)')
        if r.status_code != 200:
            raise SourceError(r.status_code, f'HTTP {r.status_code}')

    @staticmethod
    def _json(r):
        try:
            return r.json()
        except ValueError:
            raise SourceError(-2, 'response is not JSON (maintenance page or API changed)')

    def _product(self, code):
        """product.json entries (a list; empty when DLsite has no full data). Raises SourceError."""
        r = self._request(PRODUCT_URL, {'workno': code})
        self._check(r)
        data = self._json(r)
        if not isinstance(data, list) or (data and not _valid_product(data[0])):
            raise SourceError(-2, 'unexpected product data format (API changed?)')
        return data

    def _basic(self, code):
        """Basic-info fields, or None when DLsite does not know the code. Raises SourceError."""
        r = self._request(INFO_URL, {'product_id': code, 'cdn_cache_min': 1})
        self._check(r)
        data = self._json(r)
        if not data:
            return None
        if not isinstance(data, dict) or not isinstance(data.get(code), dict):
            raise SourceError(-2, 'unexpected basic info format')
        return fields_from_info(data[code])

    def _page(self, code, site):
        """Fields parsed from the work's own web page, or None if it has none. Raises SourceError."""
        url = WORK_PAGE.format(site=site, code=code)
        r = self._request(url, None)
        if r.status_code in (404, 410):
            return None
        self._check(r)
        if not is_work_page(getattr(r, 'url', url), code):
            return None  # redirected somewhere else (another site, a top page): not this work's data
        try:
            return parse_work_page(r.content)
        except ImportError:
            raise SourceError(-2, 'lxml is not installed (pip install -r requirements.txt)')

    # -- combining sources -----------------------------------------------------
    def _basic_only(self, code):
        try:
            info = self._basic(code)
        except SourceError as err:
            return self._failed(code, [f'product data: none; basic info: {err.reason}'], [err])
        self._service_ok()
        if info is None:
            return WorkInfo(404, WORK_PAGE.format(site='maniax', code=code))
        return WorkInfo(200, WORK_PAGE.format(site=info['site_id'], code=code), info,
                        partial='DLsite has only basic info for this work')

    def _fallback(self, code, api_error):
        if not self.api_down:
            self.log(f"**作品資料 API 失敗 ({api_error.reason}), 改用基本資料 + 網頁")
        errors, failures = [f'product API: {api_error.reason}'], [api_error]
        info = None
        try:
            info = self._basic(code)
            if info is None:  # DLsite's basic info does not know this code either
                self._service_ok()
                return WorkInfo(404, WORK_PAGE.format(site=DEFAULT_SITE[code[:2]], code=code))
        except SourceError as err:
            errors.append(f'basic info: {err.reason}')
            failures.append(err)
        site = (info or {}).get('site_id') or DEFAULT_SITE[code[:2]]
        page = None
        try:
            page = self._page(code, site)
            if page is None:
                errors.append('web page: no work page')
        except SourceError as err:
            errors.append(f'web page: {err.reason}')
            failures.append(err)
        url = WORK_PAGE.format(site=site, code=code)

        if page:
            fields = dict(page, site_id=site)
            reason = 'product API unavailable; data from the web page'
            if info:  # title always from the input code itself; fill gaps from basic info
                fields['title'] = info.get('title') or page.get('title')
                for key in ('work_age', 'release_date', 'img_url'):
                    fields[key] = fields.get(key) or info.get(key)
                if info.get('translation'):
                    # A translation's page shows the ORIGINAL circle, but the rule is the input code's own
                    # maker, which only the product API has: leave it unknown rather than write another circle.
                    fields['circle'] = None
                    reason += "; a translation's own circle is only in the product API"
            self._service_ok()
            return WorkInfo(200, url, fields, partial=reason)
        if info:
            result = WorkInfo(200, url, info, partial='product API and web page unavailable; only basic info')
            self._service_failed(errors)
            return result
        return self._failed(code, errors, failures)

    def _failed(self, code, errors, failures):
        """No usable data at all."""
        detail = '; '.join(errors)
        blocked = [f.status for f in failures if f.status in BLOCKED_STATUSES]
        if blocked:
            result = WorkInfo(blocked[0], WORK_PAGE.format(site=DEFAULT_SITE[code[:2]], code=code), error=detail)
        elif failures and all(f.status == 0 for f in failures):
            result = WorkInfo(0, '', error=detail)
        else:
            result = WorkInfo(-2, '', error=detail)
        self._service_failed(errors)
        return result

    # -- circuit breakers -------------------------------------------------------
    def _api_failed(self, err):
        """After block_limit product API failures in a row, skip the API for the rest of the run."""
        self.api_failures += 1
        if self.api_failures >= self.block_limit and not self.api_down:
            self.api_down = True
            self.log(f"**作品資料 API 連續 {self.api_failures} 次失敗 ({err.reason}), "
                     f"本次執行其餘作品改用基本資料 + 網頁")

    def _service_ok(self):
        self.blocked_streak = 0
        self.rescued = 0
        self.cooled_down = False

    def fallback_ok(self):
        """Called by process() when ASMR.ONE supplied the data for a work DLsite could not give."""
        self.rescued += 1

    def _service_failed(self, errors):
        """After block_limit works in a row without full data, cool down once; then stop."""
        self.blocked_streak += 1
        if self.blocked_streak < self.block_limit:
            return
        if self.rescued:
            # DLsite keeps failing but ASMR.ONE is supplying the data: stop asking DLsite now, without the
            # cool-down pause (it would only delay the run). Works ASMR.ONE does not have (e.g. BJ) are listed.
            raise BatchAborted(f'DLsite unavailable ({errors[-1]}); using ASMR.ONE for the remaining works',
                               fallback=True)
        if self.cooled_down:
            raise BatchAborted(f'DLsite still unavailable after a {self.cooldown}s cool-down ({errors[-1]})')
        self.log(f"**連續 {self.blocked_streak} 個作品無法從 DLsite 取得完整資料, 暫停 {self.cooldown} 秒...")
        self.sleep(self.cooldown)
        self.session = self._session_factory()
        self.cooled_down = True
        self.api_down, self.api_failures = False, 0  # give the product API another chance
        self.blocked_streak = self.block_limit - 1  # one more failure after the pause stops the batch

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
# ASMR.ONE (Phase 7): second fallback for RJ/VJ voice works when DLsite cannot give the work info.
# API notes: /Volumes/KioxiaG2/github/asmr.one-downloader/scratchpad.md ("ASMR.ONE API — full processing workflow").
# ---------------------------------------------------------------------------
ASMR_ONE_MIRRORS = ('https://api.asmr-200.com', 'https://api.asmr.one', 'https://api.asmr-100.com',
                    'https://api.asmr-300.com')
ASMR_ONE_PREFIXES = ('RJ', 'VJ')
# DLsite work_type code (found in ASMR.ONE work_attributes) -> DLsite label; verified against product.json.
WORK_TYPE_LABELS = {
    'SOU': 'ボイス・ASMR', 'MUS': '音楽', 'AMT': '音素材', 'IMT': '画像素材', 'ICG': 'CG・イラスト', 'MNG': 'マンガ',
    'TOL': 'ツール/アクセサリ', 'ETC': 'その他', 'ET3': 'その他', 'ACN': 'アクション', 'ADV': 'アドベンチャー',
    'RPG': 'ロールプレイング', 'SLN': 'シミュレーション', 'DNV': 'デジタルノベル', 'TBL': 'テーブル',
}
ASMR_ONE_AGES = {'general': '全年齢', 'r15': 'R-15', 'adult': 'R18'}
ASMR_ONE_TYPES = ('ボイス・ASMR', '音楽')  # what ASMR.ONE has; other DLsite types are never asked for CV/cover


def fields_from_asmr_one(w):
    """Fields from an ASMR.ONE workInfo response (no author; the title may be a Chinese translation)."""
    attrs = str(w.get('work_attributes') or '').split(',')
    code = next((a for a in attrs if a in WORK_TYPE_LABELS), None)
    circle = (w.get('circle') or {}).get('name') or w.get('name') or None
    cv = []
    for v in w.get('vas') or []:
        name = v.get('name') if isinstance(v, dict) else None
        if name and name not in cv:
            cv.append(name)
    translation = _is_translation(w.get('translation_info'))
    return {
        'title': w.get('title') or None,
        'circle': None if translation else circle,  # a translation's circle name is localized on ASMR.ONE
        'cv': cv,
        'author': None,
        'type': _work_type(WORK_TYPE_LABELS[code]) if code else None,
        'work_age': ASMR_ONE_AGES.get(w.get('age_category_string')),
        'release_date': _yymmdd(w.get('release')),
        'img_url': w.get('mainCoverUrl') or '',
        'translation': translation,
    }


class AsmrOneClient:
    """ASMR.ONE workInfo lookup over its mirrors. 404 = not on ASMR.ONE (authoritative)."""

    def __init__(self, log=print, sleep=time.sleep, timeout=(5, 20), fail_limit=3, session_factory=None,
                 mirrors=ASMR_ONE_MIRRORS):
        self.log, self.sleep, self.timeout, self.fail_limit, self.mirrors = log, sleep, timeout, fail_limit, mirrors
        self.session = (session_factory or self._make_session)()
        self.failures = 0
        self.down = False

    @staticmethod
    def _make_session():
        import requests
        session = requests.Session()
        session.headers.update({'User-Agent': FALLBACK_UA, 'Accept': 'application/json',
                                'Referer': 'https://asmr.one/', 'Origin': 'https://asmr.one'})
        return session

    def work(self, code):
        """Fields for code, or None if ASMR.ONE does not have it. Raises SourceError if unreachable."""
        if self.down:
            raise SourceError(-2, 'skipped (ASMR.ONE failed earlier in this run)')
        errors = []
        for base in self.mirrors:
            host = base.split('//')[1]
            for attempt in range(3):
                try:
                    r = self.session.get(f'{base}/api/workInfo/{code}', timeout=self.timeout)
                except OSError as err:
                    errors.append(f'{host}: network error ({err.__class__.__name__})')
                    break
                if r.status_code == 404:
                    self.failures = 0
                    return None
                if r.status_code == 429 and attempt < 2:
                    value = r.headers.get('Retry-After', '')
                    self.sleep(min(int(value), 60) if value.isdigit() else 4 * 2 ** attempt)
                    continue
                if r.status_code != 200:
                    errors.append(f'{host}: HTTP {r.status_code}')
                    break
                try:
                    w = r.json()
                except ValueError:
                    errors.append(f'{host}: not JSON')
                    break
                if not isinstance(w, dict) or not w.get('title'):
                    errors.append(f'{host}: unexpected format')
                    break
                self.failures = 0
                if str(w.get('source_id') or code).upper() != code:
                    return None  # resolved to a different work: not this one
                return fields_from_asmr_one(w)
        self.failures += 1
        if self.failures >= self.fail_limit and not self.down:
            self.down = True
            self.log(f"**ASMR.ONE 連續 {self.failures} 次無法連線, 本次執行不再使用 (可能有地區限制)")
        raise SourceError(-2, 'ASMR.ONE unavailable: ' + '; '.join(errors))


def with_asmr_one(code, info, template, asmr, cache, log, want_cover=False):
    """Second fallback (Phase 7): fill what DLsite could not give from ASMR.ONE.

    - DLsite gave no full data (any reason) -> merge: DLsite fields first, ASMR.ONE fills the gaps (cover included).
    - DLsite gave full data for a voice/music work but no CV (template uses cv) and/or no cover (want_cover)
      -> only those from ASMR.ONE (other work types are not on ASMR.ONE, so they are not asked).
    """
    if asmr is None or code[:2] not in ASMR_ONE_PREFIXES:
        return info
    full = info.status == 200 and not info.partial
    voice = full and info.fields.get('type') in ASMR_ONE_TYPES
    need_cv = voice and not info.fields.get('cv') and 'cv' in KEYWORDS.findall(template)
    need_cover = voice and want_cover and not info.fields.get('img_url')
    if full and not (need_cv or need_cover):
        return info
    key = 'asmr.one:' + code
    if key not in cache:
        try:
            cache[key] = asmr.work(code)
        except SourceError as err:
            cache[key] = err
    found = cache[key]
    if not isinstance(found, dict):
        note = 'not on ASMR.ONE' if found is None else found.reason
        if not full:
            log(f"  ASMR.ONE: {note}")
            if info.status == 200:
                return WorkInfo(info.status, info.url, info.fields, info.error, f'{info.partial}; {note}')
            return WorkInfo(info.status, info.url, info.fields, f'{info.error}; {note}' if info.error else info.error)
        return info
    if full:
        fields = dict(info.fields)
        if need_cv and found['cv']:
            fields['cv'] = found['cv']
            log("  CV 來自 ASMR.ONE (DLsite 沒有 CV 資料)")
        if need_cover and found['img_url']:
            fields['img_url'] = found['img_url']
            log("  封面來自 ASMR.ONE (DLsite 沒有封面)")
        return info if fields == info.fields else WorkInfo(200, info.url, fields)

    base = info.fields if info.status == 200 and info.fields else {}
    fields = {k: base.get(k) or found.get(k) for k in ('circle', 'type', 'work_age', 'release_date', 'img_url')}
    fields['title'] = base.get('title') or found['title']
    if not base.get('title'):
        log("**標題來自 ASMR.ONE, 可能與 DLsite 不同 (例如中文標題)")
    fields['cv'] = base.get('cv') or found['cv']
    fields['author'] = base.get('author')
    fields['site_id'] = base.get('site_id')
    if base.get('translation') or found['translation']:
        fields['circle'] = base.get('circle')  # only DLsite's own maker name is valid for a translation
    dlsite = info.partial or info.error or ('not found' if info.status == 404 else f'HTTP {info.status}')
    log("  資料來源: ASMR.ONE (DLsite: " + dlsite + ")")
    url = info.url or WORK_PAGE.format(site='maniax', code=code)
    return WorkInfo(200, url, fields, partial=f'data from ASMR.ONE; DLsite: {dlsite}')


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
CATEGORIES = ['missing fields', 'name too long', 'not found', 'blocked', 'unavailable', 'http error', 'network error',
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


def process(root, options, config, log, fetcher=None, delay=None, asmr=None):
    """Rename DLsite works and repair every other visited name. Returns a Report.

    asmr: ASMR.ONE client for the second fallback; created automatically (if enabled in the config)
    only when fetcher is also created here, so tests with a fake fetcher never reach the network.
    """
    report = Report()
    if fetcher is None:
        fetcher = DlsiteClient(log=log)
        if asmr is None and config.asmr_one_fallback:
            asmr = AsmrOneClient(log=log)
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
                                               delay, asmr)
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


def _new_name_for(src, name, code, options, config, log, fetcher, cache, report, delay, asmr=None):
    """Return (new_name, kind); kind is 'rename' / 'rename_shortened' (from the template) or 'repair'."""
    log('Processing: ' + code)

    asked_dlsite = False
    if code not in cache:
        asked_dlsite = not cache.get('__aborted__')
        if cache.get('__aborted__'):
            cache[code] = WorkInfo(-1, '', None, error=cache['__aborted__'])
        else:
            try:
                cache[code] = fetcher.fetch(code)
            except BatchAborted as err:
                if err.fallback:
                    log("**DLsite 無法使用, 其餘作品不再查詢 DLsite, 改用 ASMR.ONE")
                else:
                    log(f"**DLsite 持續無法使用, 停止查詢其餘作品: {err}")
                cache['__aborted__'] = str(err)
                cache[code] = WorkInfo(-1, '', None, error=str(err))
            except OSError as err:  # requests' network errors are OSError subclasses
                cache[code] = WorkInfo(0, '', None, error=str(err))
            if delay:
                time.sleep(delay + random.uniform(0, delay / 2))  # be polite to the server
    template = config.template_for(code)
    want_cover = options.download_cover and not options.dry_run and os.path.isdir(src)
    info = with_asmr_one(code, cache[code], template, asmr, cache, log, want_cover)
    if asked_dlsite and info.partial.startswith('data from ASMR.ONE') and hasattr(fetcher, 'fallback_ok'):
        fetcher.fallback_ok()  # lets the DLsite breaker skip its pause

    if info.status != 200:
        if info.status == 404:
            log("**DLsite 上找不到本作!\n  請檢查本作是否已經下架, 尚未發售或被收入合集")
            report.add(code, 'not found', 'not on DLsite (removed, unreleased or merged into a bundle)', src)
        elif info.status == -1:
            report.add(code, 'unavailable', f'not looked up: {info.error}', src)
        elif info.status in BLOCKED_STATUSES:
            log(f"**DLsite 拒絕連線 (HTTP {info.status}), 已重試仍失敗")
            report.add(code, 'blocked', info.error or f'HTTP {info.status} after retries', src)
        elif info.status == -2:
            log(f"**無法從 DLsite 取得資料: {info.error}")
            report.add(code, 'unavailable', info.error, src)
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
