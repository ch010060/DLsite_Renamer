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
        self.assertEqual(sorted(p.code for p in report.problems if p.category == 'blocked'),
                         ['RJ111111', 'RJ222222', 'RJ333333'])
        self.assertIn("note 'x'.txt", self.tree())  # local repair still runs


FIXTURES = os.path.join(os.path.dirname(__file__), 'fixtures')
PRODUCT = json.load(open(os.path.join(FIXTURES, 'product_json.json'), encoding='utf-8'))
INFO = json.load(open(os.path.join(FIXTURES, 'product_info.json'), encoding='utf-8'))


class FakeResponse:
    def __init__(self, status, data=None, headers=None, body=b'img'):
        self.status_code, self._data, self.headers, self._body = status, data, headers or {}, body
        self.url = 'fake'

    def json(self):
        return self._data

    def iter_content(self, size):
        yield self._body


class FakeSession:
    """Returns queued responses per URL kind ('product', 'info', 'image')."""
    def __init__(self, queue):
        self.queue = queue
        self.calls = []

    def get(self, url, params=None, timeout=None, stream=False):
        kind = 'product' if 'product.json' in url else 'info' if 'info/ajax' in url else 'image'
        self.calls.append(kind)
        return self.queue[kind].pop(0)


class ClientTest(unittest.TestCase):
    def client(self, **queue):
        self.sessions, self.sleeps, self.logs = [], [], []
        queue = {k: list(v) for k, v in queue.items()}

        def factory():
            s = FakeSession(queue)
            self.sessions.append(s)
            return s
        return core.DlsiteClient(log=self.logs.append, sleep=self.sleeps.append, session_factory=factory,
                                 backoff=(5, 15), block_limit=2, cooldown=300)

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
                         (200, 'DLsite blocked the full data; only basic info was available'))
        self.assertTrue(info.fields['title'].startswith('【繁体中文版】'))
        self.assertIsNone(info.fields['circle'])
        self.assertEqual(core.missing_fields(CONFIG_TEMPLATES['rj'], info.fields), ['cv'])
        self.assertEqual(core.missing_fields('workno title', info.fields), [])

    def test_blocked_everywhere(self):
        c = self.client(product=[FakeResponse(403)] * 3, info=[FakeResponse(403)] * 3)
        self.assertEqual(c.fetch('RJ123456').status, 403)

    def test_other_http_error_no_retry(self):
        c = self.client(product=[FakeResponse(500)])
        self.assertEqual(c.fetch('RJ123456').status, 500)
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
