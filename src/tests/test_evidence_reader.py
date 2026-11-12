"""Focused contract/source-boundary checks; no model, network or disk database."""
import copy
import hashlib
import json
import sqlite3
import unittest
from search import evidence_context as C, evidence_reader as R


class ReaderContract(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');self.db.row_factory=sqlite3.Row
        self.addCleanup(self.db.close)
        self.db.execute('CREATE TABLE chapters(page_id,book_id,revision_id,source_sha256,title,source_url,license_url,attribution,blocks_json)')
        self.blocks=[dict(kind='heading',text='甲生'),dict(kind='body',text='甲生字子和。'),
            dict(kind='body',text='召為郎。甲曰：「母老，\n\n未敢遠行。」遂辭不就。'),
            dict(kind='annotation',text='乙云：甲已赴任。'),
            dict(kind='heading',text='乙生'),dict(kind='body',text='乙生以病辭官。')]
        self.save()

    def save(self):
        self.db.execute('DELETE FROM chapters')
        self.db.execute('INSERT INTO chapters VALUES(?,?,?,?,?,?,?,?,?)',
            (1,'book',2,'hash','甲书','https://example.test/oldid=2','license','author',json.dumps(self.blocks)))

    def chunk(self,start=0,end=None):
        text=self.blocks[2]['text'];end=len(text) if end is None else end
        return dict(chunk_id=8,page_id=1,book_id='book',book_title='甲书',revision_id=2,
                    source_sha256='hash',heading='甲生',layer='body',text_orig=text[start:end],
                    parts=[dict(block_index=2,start=start,end=end)])

    def test_quote_and_layer_boundaries(self):
        context=C.assemble(self.db,self.chunk(9,13),512)
        self.assertEqual(''.join(s['text'] for s in context['segments']),self.blocks[1]['text']+self.blocks[2]['text'])
        self.assertFalse(context['boundary_cut'])
        self.assertNotIn('乙云',str(context));self.assertNotIn('乙生以病',str(context))
        self.assertEqual(C.rule_view(self.chunk(),context)['text_orig'],self.blocks[1]['text']+self.blocks[2]['text'])

    def test_identity_and_offsets_fail_closed(self):
        for bad in (dict(self.chunk(),revision_id=3),dict(self.chunk(),source_sha256='wrong'),
                    dict(self.chunk(),text_orig='改写正文'),dict(self.chunk(),heading='乙生')):
            with self.assertRaises(ValueError):C.assemble(self.db,bad)
        chunk=self.chunk();chunk['parts']*=2;chunk['text_orig']*=2
        with self.assertRaises(ValueError):C.assemble(self.db,chunk)

    def test_truncation_is_explicit_and_bounded(self):
        self.blocks[2]['text']='甲曰：「'+('非事件文字'*150)+'母老，不能赴任。」'
        self.save();context=C.assemble(self.db,self.chunk(600,640),512)
        self.assertTrue(context['boundary_cut']);self.assertTrue(context['scope_truncated'])
        self.assertLessEqual(context['source_chars'],512)
        self.assertEqual(context['anchor_chunk_id'],8)

    def valid_answer(self):
        event=dict.fromkeys(R.FIELDS,'')
        event.update(actor='甲生',action='拒任',reason='母老',state='refused',attribution='speaker',citations=[
            dict(field='actor',segment='s0',quote='甲生字子和。'),
            dict(field='action',segment='s1',quote='遂辭不就。'),
            dict(field='reason',segment='s1',quote='母老，')])
        return dict(question=dict(actor='甲生',action='拒任',object='郎',requested='reason'),
                    verdict='supported',events=[event],answer='本人说母老，遂辞不就。',uncertainty=[])

    def test_citations_verified_but_not_entailment(self):
        context=C.assemble(self.db,self.chunk());answer=self.valid_answer()
        checked=R.validate(answer,context)
        self.assertTrue(checked['eligible']);self.assertFalse(checked['semantic_verified'])
        for quote in checked['grounded_citations']:
            self.assertEqual(self.blocks[quote['block_index']]['text'][quote['start']:quote['end']],quote['quote'])
        # True quotes can still be attached to an incorrect paraphrase. The
        # integrity checker must not advertise semantic validation.
        answer['events'][0]['reason']='父亲去世'
        self.assertFalse(R.validate(answer,context)['semantic_verified'])

    def test_forged_or_ambiguous_quotes_and_missing_fields(self):
        context=C.assemble(self.db,self.chunk())
        for field,value in [('quote','母親去世'),('segment','s99')]:
            answer=self.valid_answer();answer['events'][0]['citations'][2][field]=value
            self.assertFalse(R.validate(answer,context)['eligible'])
        answer=self.valid_answer();answer['events'][0]['time']='建元三年'
        self.assertFalse(R.validate(answer,context)['eligible'])
        answer=self.valid_answer();answer['extra']='unexpected'
        self.assertFalse(R.validate(answer,context)['schema_valid'])
        self.assertFalse(R.validate(self.valid_answer(),dict(context,boundary_cut=True))['eligible'])

    def test_no_default_production_integration(self):
        # A schema contract is a hypothesis, not a newly recognized entity.
        self.assertFalse(R.query_contract('甲是谁',{})['identity_verified'])


if __name__=='__main__':unittest.main()
