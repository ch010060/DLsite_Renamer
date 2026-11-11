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
        return core.WorkInfo(200, 'url/' + code, dict(self.works[code]), 'UA')

    def download_cover(self, url, path, ua):
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
