"""General structural regressions; fixture names have no historical assertions."""
import json
from pathlib import Path
import sqlite3
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from search import punctuated_search as S, punctuated_relevance as R


def chunk(text, cid=1, title='甲史'):
    return dict(chunk_id=cid,page_id=cid,book_id='a',book_title=title,text_orig=text,
                parts=[dict(block_index=0,start=0,end=len(text))])


class Relevance(unittest.TestCase):
    def test_names_ending_in_conjunction_are_not_split(self):
        for name in ('甲和','乙和','丙和'):
            p=R.plan(name+'再次出使西方的记载有哪些？')
            self.assertEqual([c['text'] for c in p['clues']],[name,'西方'])

    def test_generic_relation_does_not_invent_kinship(self):
        p=R.plan('甲乙与开通西域有什么关系？')
        self.assertNotIn('kinship',[g['kind'] for g in p['relation_groups']])
        self.assertEqual([c['text'] for c in p['clues']],['甲乙','西域'])

    def test_passive_assignment_and_location(self):
        for name in ('甲乙','丙丁','戊己'):
            p=R.plan(name+'被任命为大将军的记载有哪些？')
            self.assertEqual([c['text'] for c in p['clues']],[name,'大将军'])
            p=R.plan(name+'在北城击败敌军的记载是什么？')
            self.assertEqual([c['text'] for c in p['clues']],[name,'北城','敌军'])

    def test_title_hypothesis_is_scoped_to_witness(self):
        p=R.plan('唐太宗在什么地方即位？')
        self.assertTrue(R.features(chunk('太宗即位于东宫。',title='舊唐書'),p)['all_clues'])
        self.assertFalse(R.features(chunk('太宗即位于东宫。',title='宋史'),p)['all_clues'])

    def test_sentence_boundary_does_not_bind_other_person(self):
        p=R.plan('甲乙去世的记载在哪里？')
        self.assertEqual(R.features(chunk('甲乙出兵。丙丁卒。'),p)['relation_groups_near'],[])
        self.assertEqual(R.features(chunk('甲乙卒。'),p)['relation_groups_near'],['death'])
        self.assertFalse(R.features(chunk('甲乙杀丙丁，丙丁卒。'),p)['relation_verified'])

    def test_negative_and_roles_never_marked_verified(self):
        for text in ('甲乙未死。','甲乙杀丙丁，丙丁卒。','传言甲乙卒，实未死。'):
            self.assertFalse(R.features(chunk(text),R.plan('甲乙去世的记载'))['relation_verified'])

    def test_candidate_budget_scope_and_no_source_mutation(self):
        db=sqlite3.connect(':memory:');self.addCleanup(db.close)
        db.executescript('CREATE TABLE books(book_id TEXT,title TEXT); INSERT INTO books VALUES("a","甲史"),("b","乙史"); CREATE TABLE chunks(chunk_id INTEGER PRIMARY KEY,book_id TEXT,search_text TEXT,payload TEXT); CREATE INDEX chunks_lookup ON chunks(book_id,search_text);')
        for i in range(1,5):
            c=chunk('甲乙卒。',i);c['book_id']='a' if i<4 else 'b'
            db.execute('INSERT INTO chunks VALUES(?,?,?,?)',(i,c['book_id'],S.normalize(c['text_orig']),json.dumps(c)))
        before=db.total_changes
        hits,scan=R.candidates(db,R.plan('甲乙去世'),book='a',budget=2)
        self.assertEqual([h['chunk']['chunk_id'] for h in hits],[1,2]);self.assertTrue(scan['truncated'])
        hits,scan=R.candidates(db,R.plan('甲乙去世'),book='b',budget=2)
        self.assertEqual([h['chunk']['chunk_id'] for h in hits],[4]);self.assertFalse(scan['truncated'])
        self.assertEqual(db.total_changes,before)

    def test_evidence_beats_name_only_but_is_not_answer(self):
        p=R.plan('甲乙去世的记载')
        dense=[dict(chunk=chunk('甲乙。',1),semantic_score=.9)]
        lexical=[dict(chunk=chunk('甲乙卒。',2),channels=['relation_clues'])]
        hits,_=R.rank(dense,lexical,p)
        self.assertEqual(hits[0]['chunk']['chunk_id'],2)
        self.assertFalse(hits[0]['relation_clues']['relation_verified'])
        self.assertNotIn('relation_clues',dense[0])


if __name__=='__main__':unittest.main()
