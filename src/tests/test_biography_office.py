import unittest
from search import biography_office as B, evidence_pipeline as E

class BiographyOffice(unittest.TestCase):
    def fixture(self,text,name='甲公'):
        opening=name+'，字仲明。'
        def chunk(text,index):
            return dict(text_orig=text,chunk_id=index,page_id=1,book_id='test',book_title='甲史',
                revision_id=2,source_sha256='source',heading=name,layer='body',
                parts=[dict(block_index=index,start=0,end=len(text))])
        c=chunk(text,1)
        return c,[chunk(opening,0),c],E.parse(name+'担任过什么官职')

    def test_generic_recipient_chains(self):
        for name in ('甲公','乙侯','丙伯'):
            for text in ('復官，徙江寧府，召權知開封府，遷右司郎中。','復官，拜司徒。'):
                c,peers,p=self.fixture(text,name)
                obs=B.observations(c,p,peers)
                self.assertEqual(E.audit(c,p,[],obs)['status'],'clues_present')
                self.assertEqual(obs[0]['subject_scope']['revision_id'],2)

    def test_ambiguous_and_other_actors(self):
        for text in ('乙侯復官，拜司徒。','復官，命乙侯為司徒。','復官，未拜司徒。',
                     '復官，欲拜司徒。','傳言復官，拜司徒。','曰：「復官，拜司徒。」',
                     '乙侯卒。復官，拜司徒。','乙侯卒。召權知開封府。','召權知開封府。'):
            c,peers,p=self.fixture(text)
            self.assertEqual(B.observations(c,p,peers),[],text)

    def test_source_and_quote_boundaries(self):
        c,peers,p=self.fixture('復官，拜司徒。')
        peers[0]['revision_id']=3
        self.assertEqual(B.observations(c,p,peers),[])
        c,peers,p=self.fixture('復官，拜司徒。')
        c['parts'][0]['start']=2;c['parts'][0]['end']+=2
        self.assertEqual(B.observations(c,p,peers),[])
        c,peers,p=self.fixture('曰：「其人有功。復官，拜司徒。」')
        self.assertEqual(B.observations(c,p,peers),[])
