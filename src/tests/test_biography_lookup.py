import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from search.biography_lookup import _continuations, _toc_rows, opening_aliases


class BiographyLookup(unittest.TestCase):
    def test_source_openings(self):
        self.assertEqual(opening_aliases('宋史','\n神宗某某皇帝，諱頊，'),['宋神宗','赵顼'])
        self.assertEqual(opening_aliases('舊唐書','高祖某某皇帝諱淵，'),['唐高祖','李渊'])
        self.assertEqual(opening_aliases('明史','太祖某某皇帝，諱元璋。'),['明太祖','朱元璋'])

    def test_non_openings_and_unknown_house(self):
        for book,text in [('宋史','臣言神宗某某皇帝諱頊，'),
                          ('宋史','神宗二'),('未知书','高祖皇帝諱淵，')]:
            self.assertEqual(opening_aliases(book,text),[])

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
