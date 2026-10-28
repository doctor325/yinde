"""Standalone integration tests; no dependency on uncommitted search pipeline."""
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from search.punctuated_roles import assess


def run(text,kind='death',subject='甲公',obj=None):
    clues=[dict(forms=[subject])]+([dict(forms=[obj])] if obj else [])
    return assess(dict(text_orig=text),dict(clues=clues,relation_groups=[dict(kind=kind)]),lambda c,_:c['forms'])


class ScopedSyntax(unittest.TestCase):
    def test_unrelated_qualifier_and_titles(self):
        for name in ('甲公','乙侯','莫甲','若乙'):
            for text in (name+'卒，援军未至。','大将军'+name+'卒。'):
                self.assertEqual(run(text,subject=name)['positive_groups'],['death'])

    def test_direct_qualifiers_still_suppress_signal(self):
        for text in ('甲公未死。','传言甲公卒。','若甲公卒。','欲甲公死。','令甲公死。'):
            self.assertEqual(run(text)['positive_groups'],[])

    def test_appointment_command_vs_wish(self):
        self.assertEqual(run('王命甲公为司徒。','appointment',obj='司徒')['positive_groups'],['appointment'])
        self.assertEqual(run('王欲命甲公为司徒。','appointment',obj='司徒')['positive_groups'],[])

    def test_exact_source_offsets_after_whitespace_normalization(self):
        text='前文。傳言甲 公未 卒。'
        result=run(text)
        self.assertEqual(result['positive_groups'],[])
        self.assertTrue(result['observations'])
        for o in result['observations']:
            self.assertEqual(text[o['start']:o['end']],o['quote'])
            for e in o['context_evidence']:
                self.assertEqual(text[e['start']:e['end']],e['quote'])
            self.assertFalse(o['relation_verified'])

    def test_subject_swap_is_not_same_event(self):
        self.assertEqual(run('甲公杀乙侯，乙侯卒。')['positive_groups'],[])


if __name__=='__main__':unittest.main()
