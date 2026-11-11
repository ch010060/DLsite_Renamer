import json
import os
import re
import shutil
import sys
import tempfile
import unicodedata
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import renamer_core as core  # noqa: E402

NFD = lambda s: unicodedata.normalize('NFD', s)  # noqa: E731

CONFIG_TEMPLATES = {'rj': 'workno title (CV. cv) ', 'bj': '[workno][circle (author)] title ',
                    'vj': '[workno][circle] title '}

FIELDS = {'title': 'ライフガードホリック', 'circle': 'Big S Studio', 'cv': ['三橋渡', '他'], 'author': [],
          'type': 'ゲーム', 'work_age': '全年齢', 'release_date': '260818', 'img_url': ''}


def old_build(template, code, f):
    """The pre-Phase-2 algorithm, copied from the old scripts, for comparison."""
    n = template.replace("workno", code).replace("title", f['title']).replace("circle", f['circle'])
    n = n.replace("work_age", f['work_age']).replace("release_date", f['release_date']).replace("type", f['type'])
    n = n.replace("author", ','.join(f['author'])) if f['author'] else n.replace("(author)", "")
    n = n.replace("cv", ','.join(f['cv'])) if f['cv'] else n.replace("(CV. cv)", "")
    fixed = ''.join(chr(ord(c) + 65248) if re.match('[\\\\/:"*?<>|]+', c) else c for c in n)
    return ' '.join(fixed.split()).strip()


class WorkCodeTest(unittest.TestCase):
    def test_valid(self):
        for name, code in [('RJ123456', 'RJ123456'), ('rj01386956 音声', 'RJ01386956'),
                           ('[RJ123456] title', 'RJ123456'), ('RE100000', 'RE100000'),
                           ('BJ01234567.zip', 'BJ01234567'), ('(同人) VJ01005806_v2', 'VJ01005806')]:
            self.assertEqual(core.find_work_codes(name), [code], name)

    def test_invalid(self):
        for name in ['RJ1234567', 'RJ012345678', 'XRJ123456', 'photos_20231105.zip', 'VE000100',
                     'BE000100', '123456', 'RJ12345']:
            self.assertEqual(core.find_work_codes(name), [], name)

    def test_multiple(self):
        self.assertEqual(core.find_work_codes('RJ123456 + RJ01386956 + rj123456'), ['RJ123456', 'RJ01386956'])


class NormalizeTest(unittest.TestCase):
    def test_nfc(self):
        self.assertEqual(core.normalize_name(NFD('ガイド')), 'ガイド')

    def test_samba_private_use(self):
        self.assertEqual(core.normalize_name('abx'), "a：b？'x'")
        self.assertEqual(core.normalize_name('ab'), 'ab')
        self.assertEqual(core.normalize_name('title'), 'title')
        self.assertEqual(core.normalize_name('title'), 'title')

    def test_quotes(self):
        self.assertEqual(core.normalize_name('"Hello"？'), "'Hello'？")
        self.assertEqual(core.normalize_name('「“愛”の歌」'), "「'愛'の歌」")
        self.assertEqual(core.normalize_name('＂a„b‟'), "'a'b'")
        self.assertEqual(core.normalize_name('What’s ‛up‘'), 'What’s ‛up‘')  # single quotes unchanged

    def test_halfwidth_katakana(self):
        self.assertEqual(core.normalize_name('ｶﾀｶﾅ ｶﾞｲﾄﾞ ﾊﾟﾝﾂ'), 'カタカナ ガイド パンツ')
        self.assertEqual(core.normalize_name('｢ｱｲｳ｣､･ｰ'), '「アイウ」、・ー')
        self.assertEqual(core.normalize_name('A ﾞ ﾟ'), 'A ゛ ゜')

    def test_fullwidth_symbols_kept(self):
        self.assertEqual(core.normalize_name('タイトル：＊？'), 'タイトル：＊？')

    def test_illegal_and_control(self):
        self.assertEqual(core.normalize_name('a:b*c?d<e>f|g\\h/i\x01j\x7f'), 'a：b＊c？d＜e＞f｜g＼h／ij')

    def test_ends(self):
        self.assertEqual(core.normalize_name('  name. . '), 'name')
        self.assertEqual(core.repair_name('...'), '...')  # nothing usable left -> unchanged
        self.assertEqual(core.normalize_name('a  b'), 'a  b')
        self.assertEqual(core.normalize_name('a  b', collapse_spaces=True), 'a b')

    def test_idempotent(self):
        for s in ['"Hello"？ ｶﾞ  x. ', NFD('ガイド: ｱ')]:
            once = core.normalize_name(s)
            self.assertEqual(core.normalize_name(once), once)


class BuildNameTest(unittest.TestCase):
    def test_same_as_old_for_normal_titles(self):
        for fields in (FIELDS, dict(FIELDS, cv=[], author=['作者A', '作者B'])):
            for t in CONFIG_TEMPLATES.values():
                for code in ('RJ01386956', 'BJ01234567'):
                    new, short = core.build_name(t, code, fields, '', 255)
                    self.assertEqual(new, old_build(t, code, fields), (t, fields))
                    self.assertFalse(short)

    def test_keywords_inside_title_are_not_replaced(self):
        fields = dict(FIELDS, title='Archive type cv circle title')
        new, _ = core.build_name(CONFIG_TEMPLATES['rj'], 'RJ123456', fields, '.zip', 255)
        self.assertEqual(new, 'RJ123456 Archive type cv circle title (CV. 三橋渡,他).zip')

    def test_empty_lists(self):
        new, _ = core.build_name(CONFIG_TEMPLATES['rj'], 'RJ123456', dict(FIELDS, cv=[]), '', 255)
        self.assertEqual(new, 'RJ123456 ライフガードホリック')
        new, _ = core.build_name(CONFIG_TEMPLATES['bj'], 'BJ123456', FIELDS, '', 255)
        self.assertEqual(new, '[BJ123456][Big S Studio ] ライフガードホリック')

    def test_shortens_title_only(self):
        fields = dict(FIELDS, title='あ' * 200)  # 600 bytes
        new, short = core.build_name(CONFIG_TEMPLATES['rj'], 'RJ01386956', fields, '.zip', 255)
        self.assertTrue(short)
        self.assertLessEqual(core.name_bytes(new), 255)
        self.assertTrue(new.startswith('RJ01386956 あ'))
        self.assertTrue(new.endswith(' (CV. 三橋渡,他).zip'))
        self.assertGreater(core.name_bytes(new), 255 - 3)  # used as much of the budget as possible

    def test_nfd_length_counted(self):
        fields = dict(FIELDS, title='ガ' * 100)  # NFC 300 bytes, NFD 600 bytes
        new, short = core.build_name('workno title', 'RJ123456', fields, '', 255)
        self.assertTrue(short)
        self.assertLessEqual(len(NFD(new).encode()), 255)

    def test_cut_never_splits_combining_mark(self):
        title = 'a' + '́' * 1 + 'b' * 300  # a + combining acute (no precomposed form issue: á exists)
        title = 'x̣̂' * 100            # x + two combining marks, no precomposed form
        new, _ = core.build_name('workno title', 'RJ123456', dict(FIELDS, title=title), '', 120)
        stem_title = new[len('RJ123456 '):]
        self.assertEqual(stem_title.count('x') * 2, stem_title.count('̂') + stem_title.count('̣'))

    def test_too_long_without_title(self):
        fields = dict(FIELDS, cv=['声' * 30] * 5)
        with self.assertRaises(core.NameTooLong):
            core.build_name(CONFIG_TEMPLATES['rj'], 'RJ123456', fields, '', 255)
        with self.assertRaises(core.NameTooLong):
            core.build_name('workno cv', 'RJ123456', fields, '', 255)

    def test_missing_fields(self):
        self.assertEqual(core.missing_fields(CONFIG_TEMPLATES['vj'], dict(FIELDS, circle=None)), ['circle'])
        self.assertEqual(core.missing_fields(CONFIG_TEMPLATES['rj'], dict(FIELDS, circle=None)), [])
        self.assertEqual(core.missing_fields(CONFIG_TEMPLATES['rj'], dict(FIELDS, cv=[])), [])


LONG_TITLES = json.load(open(os.path.join(os.path.dirname(__file__), 'fixtures', 'long_titles.json'),
                             encoding='utf-8'))


class NaturalCutTest(unittest.TestCase):
    """Phase 5: shortened titles end at a natural break (21 real DLsite titles of 200-256 bytes)."""

    def shorten(self, code, limit):
        fields = dict(FIELDS, title=LONG_TITLES[code], cv=['三橋渡'])
        name, short = core.build_name(CONFIG_TEMPLATES['rj'], code, fields, '.zip', limit)
        self.assertTrue(name.startswith(code + ' ') and name.endswith(' (CV. 三橋渡).zip'), name)
        return name[len(code) + 1:-len(' (CV. 三橋渡).zip')], short

    def test_examples(self):
        cases = [  # (code, limit, expected end of the shortened title)
            ('RJ01671271', 255, '連続絶頂えっち♡'),            # was "…えっち♡ #"
            ('RJ01450049', 255, '絶対、離さない…'),             # was "…【関西弁×3P×緊縛×絶"
            ('RJ01551013', 255, '吹いても止めない✨'),          # was "…✨ノン"
            ('RJ01450015', 255, '詰め込み豪華'),                # was "…豪華パ"
            ('RJ01551009', 255, '虜になりました。'),            # was "…〜はじめてのケイケ"
            ('RJ01671271', 143, '俺の全部だ。」'),
            ('RJ01650076', 143, '信者に堕とされる'),            # okurigana kept whole
            ('RJ01650095', 143, 'オホ声チン媚び常識改変'),
        ]
        for code, limit, end in cases:
            title, short = self.shorten(code, limit)
            self.assertTrue(short, code)
            self.assertTrue(title.endswith(end), f'{code}@{limit}: {title}')

    def test_all_titles_both_limits(self):
        for limit in (255, 143):
            for code in LONG_TITLES:
                title, short = self.shorten(code, limit)
                if not short:
                    continue
                full = core.normalize_name(LONG_TITLES[code], collapse_spaces=True)
                self.assertTrue(full.startswith(title), code)                      # a prefix, nothing invented
                self.assertNotIn(title[-1], core._TRAILING_JUNK, f'{code}@{limit}: {title}')
                self.assertLessEqual(core.name_bytes(core.build_name(
                    CONFIG_TEMPLATES['rj'], code, dict(FIELDS, title=LONG_TITLES[code], cv=['三橋渡']),
                    '.zip', limit)[0]), limit)

    def test_keeps_at_least_60_percent(self):
        title = 'あ' * 30 + '、' + 'い' * 100   # only break is far back -> not used
        self.assertEqual(core.natural_cut(title, 100), title[:100])
        title = 'あ' * 70 + '、' + 'い' * 100   # break within 60 % -> used, trailing 、 removed
        self.assertEqual(core.natural_cut(title, 100), 'あ' * 70)

    def test_okurigana_not_split(self):
        self.assertEqual(core.natural_cut('信者に堕とされる快楽', 6), '信者に')

    def test_no_break_falls_back_to_char_cut(self):
        self.assertEqual(core.natural_cut('あ' * 50, 20), 'あ' * 20)


class FakeFetcher:
    def __init__(self, works, fail=()):
        self.works, self.fail, self.calls, self.covers = works, fail, [], []

    def fetch(self, code):
        self.calls.append(code)
        if code in self.fail:
            raise RuntimeError('boom')
        if code not in self.works:
            return core.WorkInfo(404, 'url/' + code)
        return core.WorkInfo(200, 'url/' + code, dict(self.works[code]))

    def download_cover(self, url, path):
        self.covers.append(path)
        open(path, 'w').close()


class ProcessTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.config = core.Config(templates=dict(CONFIG_TEMPLATES))
        self.log = []

    def tearDown(self):
        shutil.rmtree(self.root)

    def touch(self, *parts):
        path = os.path.join(self.root, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, 'w').close()

    def run_process(self, fetcher, **opts):
        return core.process(self.root, core.Options(**opts), self.config, self.log.append, fetcher, delay=0)

    def tree(self):
        return sorted(os.path.relpath(os.path.join(d, n), self.root)
                      for d, ds, fs in os.walk(self.root) for n in ds + fs)

    def test_recursive_does_not_rename_children_with_parent_code(self):
        self.touch('RJ123456', '01 ﾄﾗｯｸ.mp3')
        self.touch('RJ123456', 'cover.jpg')
        report = self.run_process(FakeFetcher({'RJ123456': dict(FIELDS, title='Work')}), recursive=True)
        self.assertEqual(self.tree(), ['RJ123456 Work (CV. 三橋渡,他)', 'RJ123456 Work (CV. 三橋渡,他)/01 トラック.mp3',
                                       'RJ123456 Work (CV. 三橋渡,他)/cover.jpg'])
        self.assertEqual((report.renamed, report.repaired), (1, 1))

    def test_dry_run_changes_nothing(self):
        self.touch('RJ123456.zip')
        self.touch('a"b".txt')
        os.mkdir(os.path.join(self.root, 'RJ01386956'))
        fetcher = FakeFetcher({'RJ123456': FIELDS, 'RJ01386956': dict(FIELDS, img_url='http://x')})
        before = self.tree()
        report = self.run_process(fetcher, dry_run=True, download_cover=True)
        self.assertEqual(self.tree(), before)
        self.assertEqual(fetcher.covers, [])
        self.assertEqual((report.renamed, report.repaired), (2, 1))

    def test_problems_are_listed_and_run_continues(self):
        self.touch('RJ111111.zip')  # missing circle (BJ/VJ template needs it) -> uses VJ
        self.touch('VJ111111.zip')
        self.touch('RJ222222.zip')  # not found
        self.touch('RJ333333.zip')  # fetch raises
        self.touch('RJ444444.zip')  # ok
        fetcher = FakeFetcher({'VJ111111': dict(FIELDS, circle=None), 'RJ444444': FIELDS}, fail={'RJ333333'})
        report = self.run_process(fetcher)
        cats = {p.code: p.category for p in report.problems}
        self.assertEqual(cats, {'VJ111111': 'missing fields', 'RJ111111': 'not found',
                                'RJ222222': 'not found', 'RJ333333': 'error'})
        self.assertIn('RJ444444 ライフガードホリック (CV. 三橋渡,他).zip', self.tree())
        self.assertIn('VJ111111.zip', self.tree())
        summary = '\n'.join(self.log)
        self.assertLess(summary.index('missing fields'), summary.index('not found'))

    def test_no_overwrite(self):
        self.touch('RJ123456.zip')
        self.touch('RJ123456 Work (CV. 三橋渡,他).zip')
        report = self.run_process(FakeFetcher({'RJ123456': dict(FIELDS, title='Work')}))
        self.assertEqual([p.category for p in report.problems], ['conflict'])
        self.assertIn('RJ123456.zip', self.tree())

    def test_nfd_name_is_renamed_to_nfc(self):
        self.touch(NFD('ガイド.txt'))
        report = self.run_process(FakeFetcher({}))
        if sys.platform == 'darwin':  # APFS: NFC and NFD are the same file
            self.assertEqual(report.repaired, 1)
            self.assertEqual(report.problems, [])
        self.assertEqual(os.listdir(self.root), ['ガイド.txt'])

    def test_already_correct_name_unchanged_and_fetch_cached(self):
        self.touch('RJ123456 Work (CV. 三橋渡,他).zip')
        self.touch('RJ123456 extra.txt')
        fetcher = FakeFetcher({'RJ123456': dict(FIELDS, title='Work')})
        report = self.run_process(fetcher)
        self.assertEqual(fetcher.calls, ['RJ123456'])
        self.assertEqual(report.unchanged, 1)
        self.assertEqual(report.renamed, 1)

    def test_shortened_counted_only_when_renamed(self):
        self.touch('RJ123456.zip')
        fetcher = FakeFetcher({'RJ123456': dict(FIELDS, title='あ' * 200)})
        self.assertEqual(self.run_process(fetcher).shortened, 1)
        second = self.run_process(fetcher)
        self.assertEqual((second.shortened, second.renamed, second.unchanged), (0, 0, 1))

    def test_hidden_and_system_folders_not_entered(self):  # PR #1 review (Codex P2) + Synology @eaDir
        self.touch('.git', 'refs', 'heads', 'ｶﾞｲﾄﾞ')
        self.touch('.git', 'objects', 'note "x".txt')
        self.touch('@eaDir', 'RJ123456.zip@SynoEAStream')
        self.touch('@eaDir', 'RJ123456.zip', 'SYNOPHOTO_THUMB_M.jpg')
        self.touch('#recycle', 'RJ123456.zip')
        self.touch('sub', '@eaDir', 'ｶﾞｲﾄﾞ.txt')
        self.touch('sub', 'ｶﾞｲﾄﾞ.txt')
        self.touch('RJ123456.zip')
        before = [p for p in self.tree() if p.startswith(('.git', '@eaDir', '#recycle', 'sub/@eaDir'))]
        fetcher = FakeFetcher({'RJ123456': dict(FIELDS, title='W')})
        self.run_process(fetcher, recursive=True)
        after = self.tree()
        for path in before:
            self.assertIn(path, after)                         # untouched
        self.assertIn('sub/ガイド.txt', after)                   # visible entries still processed
        self.assertIn('RJ123456 W (CV. 三橋渡,他).zip', after)
        self.assertEqual(fetcher.calls, ['RJ123456'])            # nothing inside system folders looked up

    def test_bottom_up_order_kept(self):
        self.touch('A', 'B', 'f.txt')
        order = [os.path.relpath(os.path.join(d, n), self.root) for d, n in core.iter_entries(self.root, True)]
        self.assertLess(order.index('A/B/f.txt'), order.index('A/B'))
        self.assertLess(order.index('A/B'), order.index('A'))

    def test_progress_events(self):  # Phase 8: GUI work list
        self.touch('RJ123456.zip')       # renamed
        self.touch('VJ111111.zip')       # missing circle -> problem
        self.touch('ｶﾞｲﾄﾞ.txt')           # repaired
        self.touch('plain.txt')          # unchanged
        self.touch('.DS_Store')          # never listed
        events = []
        fetcher = FakeFetcher({'RJ123456': dict(FIELDS, title='W'), 'VJ111111': dict(FIELDS, circle=None)})
        core.process(self.root, core.Options(), self.config, self.log.append, fetcher, delay=0,
                     progress=lambda *e: events.append(e))
        begin = events[0]
        self.assertEqual(begin[:3], ('begin', 0, 4))
        self.assertEqual([os.path.basename(p) for p in begin[4]],
                         ['RJ123456.zip', 'VJ111111.zip', 'plain.txt', 'ｶﾞｲﾄﾞ.txt'])
        starts = [e for e in events if e[0] == 'start']
        self.assertEqual([e[4] for e in starts], ['RJ123456', 'VJ111111', '', ''])
        results = {os.path.basename(e[3]): e[4] for e in events if e[0] == 'result'}
        self.assertEqual(results['RJ123456.zip'], ('renamed', 'RJ123456 W (CV. 三橋渡,他).zip'))
        self.assertEqual(results['VJ111111.zip'], ('problem', 'missing fields: circle'))
        self.assertEqual(results['ｶﾞｲﾄﾞ.txt'], ('repaired', 'ガイド.txt'))
        self.assertEqual(results['plain.txt'], ('unchanged', 'plain.txt'))

    def test_stop_after_current_item(self):
        import threading
        for n in ('RJ111111.zip', 'RJ222222.zip', 'RJ333333.zip'):
            self.touch(n)
        stop = threading.Event()
        fetcher = FakeFetcher({k: dict(FIELDS, title='W' + k) for k in ('RJ111111', 'RJ222222', 'RJ333333')})

        def progress(event, index, total, path, detail):
            if event == 'result' and index == 0:
                stop.set()                                   # user pressed 停止 during the first item
        report = core.process(self.root, core.Options(), self.config, self.log.append, fetcher, delay=0,
                              progress=progress, stop=stop)
        self.assertEqual((report.renamed, report.stopped), (1, 1))
        self.assertEqual(fetcher.calls, ['RJ111111'])           # nothing after the stop
        self.assertIn('RJ222222.zip', self.tree())
        self.assertTrue(any('已停止' in line for line in self.log))

    def test_hidden_files_skipped(self):
        self.touch('._RJ123456"x".zip')
        fetcher = FakeFetcher({'RJ123456': FIELDS})
        self.run_process(fetcher)
        self.assertEqual(fetcher.calls, [])
        self.assertEqual(self.tree(), ['._RJ123456"x".zip'])

    def test_cover_downloaded_into_folder(self):
        os.mkdir(os.path.join(self.root, 'RJ123456'))
        fetcher = FakeFetcher({'RJ123456': dict(FIELDS, title='W', img_url='http://x')})
        self.run_process(fetcher, download_cover=True)
        self.assertIn('RJ123456 W (CV. 三橋渡,他)/cover.jpg', self.tree())

    def test_aborted_batch_lists_remaining_works(self):
        for n in ('RJ111111.zip', 'RJ222222.zip', 'RJ333333.zip', 'note "x".txt'):
            self.touch(n)

        class AbortingFetcher(FakeFetcher):
            def fetch(self, code):
                self.calls.append(code)
                raise core.BatchAborted('still blocked')
        fetcher = AbortingFetcher({})
        report = self.run_process(fetcher)
        self.assertEqual(fetcher.calls, ['RJ111111'])  # no more requests after the abort
        self.assertEqual(sorted(p.code for p in report.problems if p.category == 'unavailable'),
                         ['RJ111111', 'RJ222222', 'RJ333333'])
        self.assertIn("note 'x'.txt", self.tree())  # local repair still runs


FIXTURES = os.path.join(os.path.dirname(__file__), 'fixtures')
PRODUCT = json.load(open(os.path.join(FIXTURES, 'product_json.json'), encoding='utf-8'))
INFO = json.load(open(os.path.join(FIXTURES, 'product_info.json'), encoding='utf-8'))


class FakeResponse:
    def __init__(self, status, data=None, headers=None, body=b'img', url='fake', content=b'', not_json=False):
        self.status_code, self._data, self.headers, self._body = status, data, headers or {}, body
        self.url, self.content, self._not_json = url, content, not_json

    def json(self):
        if self._not_json:
            raise ValueError('Expecting value: line 1 column 1 (char 0)')
        return self._data

    def iter_content(self, size):
        yield self._body


class FakeSession:
    """Returns queued responses per URL kind ('product', 'info', 'image')."""
    def __init__(self, queue):
        self.queue = queue
        self.calls = []

    def get(self, url, params=None, timeout=None, stream=False, allow_redirects=True):
        kind = ('product' if 'product.json' in url else 'info' if 'info/ajax' in url
                else 'page' if '/work/=/product_id/' in url else 'image')
        self.calls.append(kind)
        if not self.queue.get(kind):
            return FakeResponse(404)  # nothing queued: this source has nothing
        return self.queue[kind].pop(0)


class ClientMixin:
    def client(self, **queue):
        self.sessions, self.sleeps, self.logs = [], [], []
        queue = {k: list(v) for k, v in queue.items()}

        def factory():
            s = FakeSession(queue)
            self.sessions.append(s)
            return s
        return core.DlsiteClient(log=self.logs.append, sleep=self.sleeps.append, session_factory=factory,
                                 backoff=(5, 15), block_limit=2, cooldown=300)


class ClientTest(ClientMixin, unittest.TestCase):
    def test_fields_from_real_product_json(self):
        f = core.fields_from_product(PRODUCT['RJ01671271'])
        self.assertEqual((f['circle'], f['cv'], f['type'], f['work_age'], f['release_date'], f['site_id']),
                         ('エーテル@オメガバース専門サークル', ['三橋渡'], 'ボイス・ASMR', 'R18', '260819', 'girls'))
        self.assertTrue(f['img_url'].startswith('https://img.dlsite.jp/'))
        f = core.fields_from_product(PRODUCT['BJ100000'])
        self.assertEqual((f['circle'], f['author'], f['cv'], f['work_age']), ('CLLENN', ['戸池みすき'], [], '全年齢'))
        f = core.fields_from_product(PRODUCT['VJ01001000'])
        self.assertEqual(f['cv'], ['乙倉ゆい', '赤月ゆむ', 'くどう彩織', '倉下撫子'])
        self.assertEqual(f['type'], 'ゲーム')  # アドベンチャー -> ゲーム, as before
        self.assertEqual(core.fields_from_product(PRODUCT['RJ01386956'])['type'], 'ゲーム')
        self.assertEqual(core.fields_from_product(PRODUCT['RJ01552342'])['work_age'], 'R-15')
        self.assertEqual(core.fields_from_product(PRODUCT['RE100000'])['circle'], '丸美企画')
        self.assertEqual(core.fields_from_product(PRODUCT['RJ01700000'])['circle'], '晓月')  # input code's own

    def test_every_section_renames_with_repo_templates(self):
        for code, w in PRODUCT.items():
            fields = core.fields_from_product(w)
            template = CONFIG_TEMPLATES['bj' if code.startswith('BJ') else 'vj' if code.startswith('VJ') else 'rj']
            self.assertEqual(core.missing_fields(template, fields), [], code)
            name, _ = core.build_name(template, code, fields, '.zip', 255)
            self.assertTrue(name.startswith(code if code[:2] in ('RJ', 'RE') else '[' + code), name)

    def test_ok(self):
        c = self.client(product=[FakeResponse(200, [PRODUCT['RJ01386956']])])
        info = c.fetch('RJ01386956')
        self.assertEqual((info.status, info.partial, info.fields['title']), (200, '', 'ライフガードホリック'))
        self.assertEqual(info.url, 'https://www.dlsite.com/maniax/work/=/product_id/RJ01386956.html')

    def test_not_found(self):
        c = self.client(product=[FakeResponse(200, [])], info=[FakeResponse(200, [])])
        self.assertEqual(c.fetch('RJ999999').status, 404)

    def test_basic_info_only_listing(self):
        chapter = {'BJ01500000': {'site_id': 'comic', 'work_name': '【話販売】ブルーピリオド（１１）', 'age_category': 1,
                                  'regist_date': '5100-01-01 00:00:00', 'work_image': '//img/x.jpg'}}
        c = self.client(product=[FakeResponse(200, [])], info=[FakeResponse(200, chapter)])
        info = c.fetch('BJ01500000')
        self.assertEqual((info.status, info.partial), (200, 'DLsite has only basic info for this work'))
        self.assertIsNone(info.fields['release_date'])  # 5100-01-01 is a placeholder, not a date
        self.assertEqual(core.missing_fields(CONFIG_TEMPLATES['bj'], info.fields), ['circle', 'author'])
        self.assertEqual(self.sleeps, [])  # not blocked: no back-off

    def test_blocked_then_ok_uses_retry_after_and_fresh_session(self):
        c = self.client(product=[FakeResponse(429, headers={'Retry-After': '7'}),
                                 FakeResponse(200, [PRODUCT['RJ01386956']])])
        self.assertEqual(c.fetch('RJ01386956').status, 200)
        self.assertEqual(self.sleeps, [7])
        self.assertEqual(len(self.sessions), 2)

    def test_backoff_without_retry_after(self):
        c = self.client(product=[FakeResponse(503), FakeResponse(529), FakeResponse(200, [PRODUCT['RE100000']])])
        self.assertEqual(c.fetch('RE100000').status, 200)
        self.assertEqual(len(self.sleeps), 2)
        self.assertTrue(5 <= self.sleeps[0] <= 5 + 5 / 3 and 15 <= self.sleeps[1] <= 20)

    def test_blocked_falls_back_to_basic_info(self):
        c = self.client(product=[FakeResponse(403)] * 3, info=[FakeResponse(200, INFO)])
        info = c.fetch('RJ01700000')
        self.assertEqual((info.status, info.partial),
                         (200, 'product API and web page unavailable; only basic info'))
        self.assertTrue(info.fields['title'].startswith('【繁体中文版】'))
        self.assertIsNone(info.fields['circle'])
        self.assertEqual(core.missing_fields(CONFIG_TEMPLATES['rj'], info.fields), ['cv'])
        self.assertEqual(core.missing_fields('workno title', info.fields), [])

    def test_blocked_everywhere(self):
        c = self.client(product=[FakeResponse(403)] * 3, info=[FakeResponse(403)] * 3)
        self.assertEqual(c.fetch('RJ123456').status, 403)

    def test_server_error_falls_back_without_backoff(self):
        c = self.client(product=[FakeResponse(500)], info=[FakeResponse(200, [])])  # basic info: unknown code
        info = c.fetch('RJ123456')
        self.assertEqual((info.status, info.error), (404, ''))
        self.assertEqual(self.sleeps, [])

    def test_circuit_breaker(self):
        blocked = [FakeResponse(403)] * 3
        c = self.client(product=blocked * 4, info=blocked * 4)
        c.fetch('RJ111111')                       # streak 1
        c.fetch('RJ222222')                       # streak 2 -> cool-down
        self.assertIn(300, self.sleeps)
        with self.assertRaises(core.BatchAborted):
            c.fetch('RJ333333')                   # still blocked after the pause -> stop

    def test_success_resets_breaker(self):
        blocked = [FakeResponse(403)] * 3
        c = self.client(product=blocked + [FakeResponse(200, [PRODUCT['RJ01386956']])] + blocked,
                        info=blocked * 2)
        c.fetch('RJ111111')
        c.fetch('RJ01386956')
        c.fetch('RJ222222')
        self.assertNotIn(300, self.sleeps)

    def test_no_image_placeholder_is_no_cover(self):
        info = {'work_name': 'x', 'age_category': 3, 'regist_date': None,
                'work_image': '//www.dlsite.com/images/web/home/no_img_main.gif'}
        self.assertEqual(core.fields_from_info(info)['img_url'], '')
        self.assertEqual(core.fields_from_product({'image_main': {'url': '//www.dlsite.com/images/web/home/no_img_main.gif'}})['img_url'], '')

    def test_cover_rejects_non_image(self):
        d = tempfile.mkdtemp()
        try:
            c = self.client(image=[FakeResponse(200, headers={'Content-Type': 'text/html'})])
            with self.assertRaises(OSError):
                c.download_cover('https://img/x.jpg', os.path.join(d, 'cover.jpg'))
            self.assertEqual(os.listdir(d), [])
        finally:
            shutil.rmtree(d)

    def test_cover_download_atomic(self):
        d = tempfile.mkdtemp()
        try:
            target = os.path.join(d, 'cover.jpg')
            c = self.client(image=[FakeResponse(200, body=b'\xff\xd8jpeg'), FakeResponse(404)])
            c.download_cover('https://img/x.jpg', target)
            with open(target, 'rb') as f:
                self.assertEqual(f.read(), b'\xff\xd8jpeg')
            os.remove(target)
            with self.assertRaises(OSError):
                c.download_cover('https://img/x.jpg', target)
            self.assertEqual(os.listdir(d), [])  # no cover.jpg, no .part left behind
        finally:
            shutil.rmtree(d)


PAGES = json.load(open(os.path.join(FIXTURES, 'work_pages.json'), encoding='utf-8'))


def page_response(code):
    p = PAGES[code]
    return FakeResponse(p['status'], url=p['url'], content=p['html'].encode('utf-8'))


def info_response(code, w):
    return FakeResponse(200, {code: {'site_id': w['site_id'], 'work_name': w['work_name'],
                                     'age_category': w['age_category'], 'regist_date': w['regist_date'],
                                     'work_image': w['image_main']['url']}})


class WebPageTest(unittest.TestCase):
    """Phase 6: the web page is the fallback when product.json is unavailable."""

    def test_page_fields_match_product_json(self):
        # Real pages of both layouts give the same fields as product.json for the same works.
        for code in ('RJ01386956', 'RJ01671271', 'VJ01001000', 'BJ100000', 'RJ01552342'):
            page = core.parse_work_page(PAGES[code]['html'].encode('utf-8'))
            api = core.fields_from_product(PRODUCT[code])
            for key in ('title', 'circle', 'cv', 'author', 'type', 'release_date', 'img_url'):
                if code == 'RJ01552342' and key == 'circle':
                    continue  # translation: page shows the original circle (see test_translation_circle_unknown)
                self.assertEqual(page[key], api[key], f'{code} {key}')
            if code != 'BJ100000':  # the blpro page has no age row; basic info fills it in
                self.assertEqual(page['work_age'], api['work_age'], code)

    def test_layouts(self):
        legacy = core.parse_work_page(PAGES['BJ100000']['html'].encode('utf-8'))
        self.assertEqual((legacy['circle'], legacy['author']), ('CLLENN', ['戸池みすき']))  # 出版社名, no フォローする
        new = core.parse_work_page(PAGES['VJ01001000']['html'].encode('utf-8'))
        self.assertEqual((new['circle'], len(new['cv']), new['type']), ('アニム', 4, 'ゲーム'))  # ブランド名

    def test_is_work_page(self):
        self.assertTrue(core.is_work_page('https://www.dlsite.com/maniax/work/=/product_id/RJ01386956.html', 'RJ01386956'))
        self.assertTrue(core.is_work_page(PAGES['RJ01700000']['url'], 'RJ01700000'))  # translation -> parent page
        for url in ('https://www.comipo.app/product/BJ200000', 'https://www.dlsite.com/home/',
                    'https://www.dlsite.com/maniax/work/=/product_id/RJ01386957.html', ''):
            self.assertFalse(core.is_work_page(url, 'RJ01386956'), url)


class ApiFailureTest(ClientMixin, unittest.TestCase):
    """Phase 6: any product.json failure -> basic info + web page; clear reasons; breakers."""

    def run_failure(self, product_response):
        code = 'RJ01671271'
        c = self.client(product=[product_response], info=[info_response(code, PRODUCT[code])],
                        page=[page_response(code)])
        return c.fetch(code)

    def assert_full_from_page(self, info):
        self.assertEqual((info.status, info.partial), (200, 'product API unavailable; data from the web page'))
        self.assertEqual((info.fields['circle'], info.fields['cv']), ('エーテル@オメガバース専門サークル', ['三橋渡']))
        self.assertEqual(core.missing_fields(CONFIG_TEMPLATES['rj'], info.fields), [])

    def test_api_removed_404(self):
        self.assert_full_from_page(self.run_failure(FakeResponse(404)))

    def test_api_not_json(self):
        self.assert_full_from_page(self.run_failure(FakeResponse(200, not_json=True)))

    def test_api_format_changed(self):
        self.assert_full_from_page(self.run_failure(FakeResponse(200, [{'name': 'x'}])))

    def test_api_blocked_then_page(self):
        self.assert_full_from_page(self.run_failure(FakeResponse(403)))   # back-off -> still 403 (queue empty->404)

    def test_translation_title_from_own_code(self):
        code = 'RJ01700000'  # page shows the parent RJ01699999; title must be the input code's own
        c = self.client(product=[FakeResponse(404)], info=[info_response(code, PRODUCT[code])],
                        page=[page_response(code)])
        info = c.fetch(code)
        self.assertEqual(info.fields['title'], PRODUCT[code]['work_name'])

    def test_translation_circle_unknown(self):
        code = 'RJ01552342'  # 【繁体中文版】; product API maker みんなで翻訳, page shows the original circle えもこ本舗
        info_json = info_response(code, PRODUCT[code])
        info_json._data[code]['translation_info'] = {'is_parent': True, 'is_child': False}
        c = self.client(product=[FakeResponse(404)], info=[info_json], page=[page_response(code)])
        info = c.fetch(code)
        self.assertIsNone(info.fields['circle'])
        self.assertIn("translation's own circle", info.partial)
        self.assertEqual(core.missing_fields(CONFIG_TEMPLATES['rj'], info.fields), [])        # rj: no circle
        self.assertEqual(core.missing_fields(CONFIG_TEMPLATES['vj'], info.fields), ['circle'])

    def test_redirect_elsewhere_is_not_used(self):
        code = 'BJ200000'
        c = self.client(product=[FakeResponse(404)], info=[info_response(code, PRODUCT[code])],
                        page=[FakeResponse(200, url='https://www.comipo.app/product/BJ200000', content=b'<h1>x</h1>')])
        info = c.fetch(code)
        self.assertEqual(info.partial, 'product API and web page unavailable; only basic info')
        self.assertIsNone(info.fields['circle'])

    def test_api_skipped_after_three_failures(self):
        codes = ['RJ01386956', 'RJ01671271', 'VJ01001000', 'BJ100000']
        c = self.client(product=[FakeResponse(404)] * 3,
                        info=[info_response(k, PRODUCT[k]) for k in codes], page=[page_response(k) for k in codes])
        for k in codes:
            self.assertEqual(c.fetch(k).status, 200, k)
        calls = [call for sess in self.sessions for call in sess.calls]
        self.assertEqual(calls.count('product'), 2)  # block_limit=2: later works go straight to basic info + page
        self.assertTrue(c.api_down)

    def test_everything_down_is_reported_and_stops(self):
        c = self.client(product=[FakeResponse(500)] * 9, info=[FakeResponse(500)] * 9, page=[FakeResponse(500)] * 9)
        first = c.fetch('RJ111111')
        self.assertEqual(first.status, -2)
        self.assertIn('product API: HTTP 500', first.error)
        self.assertIn('basic info: HTTP 500', first.error)
        self.assertIn('web page: HTTP 500', first.error)
        c.fetch('RJ222222')                      # 2nd failure -> cool-down
        self.assertIn(300, self.sleeps)
        with self.assertRaises(core.BatchAborted):
            c.fetch('RJ333333')                  # still failing after the pause -> stop

    def test_network_down(self):
        def factory():
            class Down:
                calls = []
                def get(self, *a, **k):
                    raise ConnectionError('[Errno 61] Connection refused')
            return Down()
        c = core.DlsiteClient(log=lambda m: None, sleep=lambda s: None, session_factory=factory, block_limit=5)
        info = c.fetch('RJ111111')
        self.assertEqual(info.status, 0)
        self.assertIn('network error', info.error)

    def test_page_success_resets_breaker(self):
        code = 'RJ01671271'
        c = self.client(product=[FakeResponse(404)] * 5, info=[info_response(code, PRODUCT[code])] * 5,
                        page=[page_response(code)] * 5)
        for _ in range(5):
            self.assertEqual(c.fetch(code).status, 200)
        self.assertNotIn(300, self.sleeps)


ASMR = json.load(open(os.path.join(FIXTURES, 'asmr_one.json'), encoding='utf-8'))


class AsmrSession:
    """Fake ASMR.ONE: per mirror host a list of responses; default 404."""
    def __init__(self, plan):
        self.plan, self.calls = plan, []

    def get(self, url, timeout=None, **k):
        host = url.split('//')[1].split('/')[0]
        self.calls.append(host)
        queue = self.plan.get(host)
        if isinstance(queue, Exception):
            raise queue
        return queue.pop(0) if queue else FakeResponse(404)


def asmr_ok(code):
    return FakeResponse(200, ASMR[code])


class AsmrOneTest(unittest.TestCase):
    """Phase 7: ASMR.ONE as the second fallback (RJ/VJ voice works)."""

    def client(self, plan):
        self.sleeps, self.logs = [], []
        self.session = AsmrSession(plan)
        return core.AsmrOneClient(log=self.logs.append, sleep=self.sleeps.append, session_factory=lambda: self.session)

    def test_fields_from_real_workinfo(self):
        f = core.fields_from_asmr_one(ASMR['RJ01551365'])
        self.assertEqual((f['circle'], f['cv'], f['type'], f['work_age'], f['release_date']),
                         ('BrownDust2', ['安済知佳'], 'ボイス・ASMR', '全年齢', '260128'))
        self.assertTrue(f['title'].startswith('【全面肯定'))                     # Chinese title on ASMR.ONE
        self.assertTrue(f['img_url'].startswith('https://api.asmr-200.com/api/cover/'))
        self.assertIsNone(f['author'])
        self.assertEqual(core.fields_from_asmr_one(ASMR['RJ291235'])['work_age'], 'R-15')
        self.assertEqual(core.fields_from_asmr_one(ASMR['RJ01082472'])['cv'], ['柊 結水', '柊結水'])  # exact dups only
        tr = core.fields_from_asmr_one(ASMR['RJ01004652'])                     # translation: localized circle
        self.assertEqual((tr['translation'], tr['circle']), (True, None))

    def test_not_found_and_mirrors(self):
        c = self.client({})
        self.assertIsNone(c.work('RJ999999'))
        self.assertEqual(self.session.calls, ['api.asmr-200.com'])               # 404 is authoritative
        c = self.client({'api.asmr-200.com': ConnectionError('down'),
                         'api.asmr.one': [FakeResponse(503)], 'api.asmr-100.com': [asmr_ok('RJ01551365')]})
        self.assertEqual(c.work('RJ01551365')['circle'], 'BrownDust2')
        self.assertEqual(self.session.calls, ['api.asmr-200.com', 'api.asmr.one', 'api.asmr-100.com'])

    def test_rate_limit_then_ok(self):
        c = self.client({'api.asmr-200.com': [FakeResponse(429, headers={'Retry-After': '3'}),
                                              asmr_ok('RJ01551365')]})
        self.assertIsNotNone(c.work('RJ01551365'))
        self.assertEqual(self.sleeps, [3])

    def test_unreachable_and_disabled_after_failures(self):
        down = {m.split('//')[1]: ConnectionError('blocked') for m in core.ASMR_ONE_MIRRORS}
        c = self.client(down)
        for _ in range(3):
            with self.assertRaises(core.SourceError):
                c.work('RJ01551365')
        self.assertTrue(c.down)
        n = len(self.session.calls)
        with self.assertRaises(core.SourceError):
            c.work('RJ01551365')
        self.assertEqual(len(self.session.calls), n)                              # skipped: no more requests

    def test_other_work_is_ignored(self):
        c = self.client({'api.asmr-200.com': [asmr_ok('RJ01551365')]})
        self.assertIsNone(c.work('RJ01082472'))                                    # answer is a different work

    # -- merge (with_asmr_one) --------------------------------------------------
    def merged(self, code, info, template=CONFIG_TEMPLATES['rj'], plan=None, want_cover=False):
        if plan is None:
            plan = {'api.asmr-200.com': [asmr_ok(code)]} if code in ASMR else {}
        c = self.client(plan)
        return core.with_asmr_one(code, info, template, c, {}, self.logs.append, want_cover)

    def test_dlsite_not_found_uses_asmr_one(self):          # the "no longer sold / gone" special case
        info = self.merged('RJ01551365', core.WorkInfo(404, ''))
        self.assertEqual(info.status, 200)
        self.assertTrue(info.partial.startswith('data from ASMR.ONE'))
        self.assertTrue(info.fields['title'].startswith('【全面肯定'))              # (a): ASMR.ONE title + warning
        self.assertTrue(any('標題來自 ASMR.ONE' in l for l in self.logs))
        self.assertEqual(core.missing_fields(CONFIG_TEMPLATES['rj'], info.fields), [])

    def test_basic_info_title_wins(self):
        basic = core.fields_from_info({'work_name': 'DLsite の題名', 'age_category': 1, 'regist_date': None,
                                       'site_id': 'home', 'work_image': ''})
        info = self.merged('RJ01551365', core.WorkInfo(200, 'u', basic, partial='DLsite has only basic info'))
        self.assertEqual(info.fields['title'], 'DLsite の題名')
        self.assertEqual((info.fields['circle'], info.fields['cv']), ('BrownDust2', ['安済知佳']))
        self.assertEqual(info.fields['work_age'], '全年齢')

    def test_translation_circle_stays_unknown(self):
        info = self.merged('RJ01004652', core.WorkInfo(404, ''))
        self.assertIsNone(info.fields['circle'])
        self.assertEqual(core.missing_fields(CONFIG_TEMPLATES['vj'], info.fields), ['circle'])

    def test_full_dlsite_without_cv_gets_cv_only(self):
        dl = dict(core.fields_from_product(PRODUCT['RJ01671271']), cv=[])  # full DLsite data of a voice work, no CV
        info = self.merged('RJ01671271', core.WorkInfo(200, 'u', dl),
                           plan={'api.asmr-200.com': [FakeResponse(200, dict(ASMR['RJ105661'], source_id='RJ01671271'))]})
        self.assertEqual(info.fields['cv'], ['蒼紗', 'さくら真咲'])
        self.assertEqual({k: v for k, v in info.fields.items() if k != 'cv'}, {k: v for k, v in dl.items() if k != 'cv'})
        self.assertEqual(info.partial, '')

    def test_non_voice_work_not_asked(self):  # games, manga, CG are never on ASMR.ONE
        dl = core.fields_from_product(PRODUCT['RJ01386956'])  # シミュレーション -> ゲーム, no CV, has cover
        self.assertEqual(dl['type'], 'ゲーム')
        info = self.merged('RJ01386956', core.WorkInfo(200, 'u', dict(dl, img_url='')), want_cover=True)
        self.assertEqual(self.session.calls, [])
        self.assertEqual(info.fields['cv'], [])

    def run_dlsite_down(self, asmr_has_works):
        class Down:
            def get(self, *a, **k):
                return FakeResponse(500)

        class Asmr:
            def get(self, url, **k):
                code = url.rsplit('/', 1)[1]
                return FakeResponse(200, ASMR[code]) if asmr_has_works and code in ASMR else FakeResponse(404)
        sleeps, logs = [], []
        dl = core.DlsiteClient(log=logs.append, sleep=sleeps.append, session_factory=Down)
        asmr = core.AsmrOneClient(log=logs.append, sleep=sleeps.append, session_factory=Asmr)
        root = tempfile.mkdtemp()
        try:
            for code in list(ASMR) + ['BJ999999']:   # BJ sorts first and is never on ASMR.ONE
                open(os.path.join(root, code + '.zip'), 'w').close()
            report = core.process(root, core.Options(), core.Config(templates=dict(CONFIG_TEMPLATES)),
                                  logs.append, dl, delay=0, asmr=asmr)
        finally:
            shutil.rmtree(root)
        return report, sleeps, logs

    def test_dlsite_down_asmr_one_up_no_pause(self):
        report, sleeps, logs = self.run_dlsite_down(True)
        self.assertEqual(report.renamed, len(ASMR))
        self.assertEqual([(p.code, p.category) for p in report.problems], [('BJ999999', 'unavailable')])
        self.assertNotIn(300, sleeps)                                            # no cool-down pause
        self.assertTrue(any('改用 ASMR.ONE' in l for l in logs))

    def test_dlsite_down_asmr_one_lacks_works_still_pauses(self):
        report, sleeps, logs = self.run_dlsite_down(False)
        self.assertIn(300, sleeps)
        self.assertEqual(report.renamed, 0)

    def test_full_dlsite_with_cv_or_no_cv_template_not_asked(self):
        dl = core.fields_from_product(PRODUCT['RJ01671271'])                       # has CV
        info = self.merged('RJ01671271', core.WorkInfo(200, 'u', dl))
        self.assertIs(info.fields, dl)
        self.assertEqual(self.session.calls, [])
        dl = core.fields_from_product(PRODUCT['RJ01386956'])                       # no CV, template without cv
        self.merged('RJ01386956', core.WorkInfo(200, 'u', dl), template='workno title ')
        self.assertEqual(self.session.calls, [])

    def test_full_dlsite_without_cover_gets_cover_only(self):
        dl = dict(core.fields_from_product(PRODUCT['RJ01671271']), img_url='')     # e.g. DLsite no-image placeholder
        asmr = FakeResponse(200, dict(ASMR['RJ01551365'], source_id='RJ01671271'))
        info = self.merged('RJ01671271', core.WorkInfo(200, 'u', dl), plan={'api.asmr-200.com': [asmr]},
                           want_cover=True)
        self.assertTrue(info.fields['img_url'].startswith('https://api.asmr-200.com/api/cover/'))
        self.assertEqual(info.fields['cv'], ['三橋渡'])                              # DLsite CV kept
        self.assertTrue(any('封面來自 ASMR.ONE' in l for l in self.logs))
        info = self.merged('RJ01671271', core.WorkInfo(200, 'u', dl), want_cover=False)  # covers not requested
        self.assertEqual(self.session.calls, [])

    def test_merge_fills_cover(self):
        info = self.merged('RJ01551365', core.WorkInfo(404, ''))
        self.assertTrue(info.fields['img_url'].startswith('https://api.asmr-200.com/api/cover/'))

    def test_only_rj_vj(self):
        for code in ('BJ100000', 'RE100000'):
            info = core.WorkInfo(404, '')
            self.assertIs(self.merged(code, info), info)
            self.assertEqual(self.session.calls, [])

    def test_asmr_one_also_fails(self):
        down = {m.split('//')[1]: ConnectionError('blocked') for m in core.ASMR_ONE_MIRRORS}
        info = self.merged('RJ01551365', core.WorkInfo(-2, '', error='product API: HTTP 500'), plan=down)
        self.assertEqual(info.status, -2)
        self.assertIn('ASMR.ONE unavailable', info.error)

    def test_process_end_to_end(self):
        root = tempfile.mkdtemp()
        try:
            open(os.path.join(root, 'RJ01551365.zip'), 'w').close()
            open(os.path.join(root, 'BJ999999.zip'), 'w').close()
            asmr = self.client({'api.asmr-200.com': [asmr_ok('RJ01551365')]})
            report = core.process(root, core.Options(), core.Config(templates=dict(CONFIG_TEMPLATES)),
                                  self.logs.append, FakeFetcher({}), delay=0, asmr=asmr)
            self.assertIn('RJ01551365 【全面肯定・幼儿退化ASMR】宝宝的治愈时光～被泰瑞丝娇惯养大～【棕色尘埃2】 (CV. 安済知佳).zip',
                          os.listdir(root))
            self.assertEqual([p.code for p in report.problems], ['BJ999999'])      # BJ: no ASMR.ONE lookup
        finally:
            shutil.rmtree(root)

    def test_config_flag(self):
        self.assertTrue(core.Config().asmr_one_fallback)


class ConfigTest(unittest.TestCase):
    def test_bad_json_uses_defaults(self):
        d = tempfile.mkdtemp()
        try:
            with open(os.path.join(d, 'config.json'), 'w') as f:
                f.write('{bad json')
            msgs = []
            cfg = core.load_config(d, msgs.append)
            self.assertEqual(cfg.template_for('RJ123456'), core.DEFAULT_TEMPLATE)
            self.assertTrue(any('失敗' in m for m in msgs))
        finally:
            shutil.rmtree(d)

    def test_repo_config(self):
        cfg = core.load_config(os.path.join(os.path.dirname(__file__), '..'), lambda m: None)
        self.assertEqual(cfg.template_for('RJ123456'), CONFIG_TEMPLATES['rj'])
        self.assertEqual(cfg.template_for('RE123456'), CONFIG_TEMPLATES['rj'])
        self.assertEqual(cfg.max_name_bytes, 255)


if __name__ == '__main__':
    unittest.main()
