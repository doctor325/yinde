import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from search.biography_lookup import _continuations, _titled_opening, _toc_rows, opening_aliases


class BiographyLookup(unittest.TestCase):
    def test_source_openings(self):
        self.assertEqual(opening_aliases('宋史','\n神宗某某皇帝，諱頊，'),['宋神宗','赵顼'])
        self.assertEqual(opening_aliases('舊唐書','高祖某某皇帝諱淵，'),['唐高祖','李渊'])
        self.assertEqual(opening_aliases('明史','太祖某某皇帝，諱元璋。'),['明太祖','朱元璋'])

    def test_non_openings_and_unknown_house(self):
        for book,text in [('宋史','臣言神宗某某皇帝諱頊，'),
                          ('宋史','神宗二'),('未知书','高祖皇帝諱淵，')]:
            self.assertEqual(opening_aliases(book,text),[])

    def test_titled_annal_requires_catalog_and_opening_to_agree(self):
        rows = dict((path, label) for path, label, _ in _toc_rows('後漢書',
                    '*[[/卷1上|卷一上]]　文武帝紀　第一上\n*[[/卷2]]　列傳第2　文武帝'))
        self.assertEqual(rows, {'後汉书/卷1上': '文武帝上'})
        self.assertEqual(_titled_opening('後漢書', '世祖文武皇帝諱甲，字某，', '文武帝上'),
                         (['汉文武帝', '刘甲'], ['文武帝', '文武']))
        self.assertEqual(_titled_opening('後漢書', '世祖文武皇帝諱甲，字某，', '文德帝上'), ([], []))
        self.assertEqual(_titled_opening('隋書', '煬皇帝諱甲，一名某。', '炀帝上'),
                         (['隋炀帝', '杨甲'], ['炀帝']))

    def test_continuation_can_share_number_when_catalog_has_upper_and_lower(self):
        lines = '*[[/卷1上|卷一上]]　文武帝紀　第一上\n*[[/卷1下|卷一下]]　文武帝紀　第一下'
        labels = {target:(label,line) for target,label,line in _toc_rows('後漢書',lines)}
        start = dict(title='後漢書/卷1上', aliases=['汉文武帝','刘甲'], sort_number=1)
        second = dict(title='後漢書/卷1下',sort_number=1,page_id=15,revision_id=1,
                      source_url='page',source_sha256='test',opening='元年春正月')
        self.assertEqual(_continuations(start,[second],(labels,42,'catalog'))[0]['source_sha256'], 'test')

    def test_continuation_requires_agreeing_heading_and_single_ruler(self):
        lines = '*[[/卷014]] 本紀第十四 神宗一\n*[[/卷015]] 本紀第十五 神宗二\n*[[/卷016]] 本紀第十六 神宗三 哲宗'
        labels = {target:(label,line) for target,label,line in _toc_rows('宋史',lines)}
        start = dict(title='宋史/卷014',aliases=['宋神宗','赵顼'],sort_number=14)
        second = dict(title='宋史/卷015',sort_number=15,page_id=15,revision_id=1,source_url='page',opening='神宗二')
        third = dict(second,title='宋史/卷016',sort_number=16,page_id=16,opening='神宗三')
        toc = (labels,42,'catalog')
        links = _continuations(start,[second,third],toc)
        self.assertEqual([link['page_id'] for link in links],[15])
        self.assertEqual(links[0]['toc_revision_id'],42)
        self.assertEqual(_continuations(start,[dict(second,opening='哲宗二'),third],toc),[])
        self.assertEqual(_continuations(start,[third],toc),[])


if __name__=='__main__':
    unittest.main()
