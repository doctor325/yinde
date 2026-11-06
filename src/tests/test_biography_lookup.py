import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from search.biography_lookup import opening_aliases


class BiographyLookup(unittest.TestCase):
    def test_source_openings(self):
        self.assertEqual(opening_aliases('宋史','\n神宗某某皇帝，諱頊，'),['宋神宗','赵顼'])
        self.assertEqual(opening_aliases('舊唐書','高祖某某皇帝諱淵，'),['唐高祖','李渊'])
        self.assertEqual(opening_aliases('明史','太祖某某皇帝，諱元璋。'),['明太祖','朱元璋'])

    def test_non_openings_and_unknown_house(self):
        for book,text in [('宋史','臣言神宗某某皇帝諱頊，'),
                          ('宋史','神宗二'),('未知书','高祖皇帝諱淵，')]:
            self.assertEqual(opening_aliases(book,text),[])


if __name__=='__main__':
    unittest.main()
