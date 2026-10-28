"""Offline qualifier-scope experiment, independent of search/ranking defaults.

The caller supplies an already identified subject/predicate span. This module
does not identify people, prove relations or infer that an event occurred.
Offsets and quotations always refer to the caller's unchanged source string.
"""
import re
from search import zh

VERSION = 'qualifier-scope-v2'
_BOUNDARY = re.compile(r'[。！？；\n]')
_REPORT = re.compile(r'传言|传闻|或言|或谓|闻言|谣言|诈称|诈言|讹言')
_HYPOTHESIS = re.compile(r'若|假使|倘|苟')
_MODAL = re.compile(r'将欲|欲|将(?!军)|当|宜|愿|恐|庶|必')
_NEGATION = re.compile(r'未|不|无|毋|勿|莫|非|弗')


def assess_context(text, subject_start, predicate_start, predicate_end, *, subject_end=None, assignment=False):
    """Return conservative qualifications of a supplied predicate, not truth.

    Reporting/conditional introductions can govern following comma clauses.
    Ordinary negation/modality is limited to the subject-predicate bridge;
    punctuation in that bridge leaves attribution unresolved. A qualifier
    later in the sentence is retained as context rather than transferred to
    this event. No candidate is dropped by this function.
    """
    if subject_end is None:
        raise ValueError('Explicit subject_end is required to exclude name characters')
    if not (isinstance(text,str) and
            all(type(i) is int for i in (subject_start,subject_end,predicate_start,predicate_end)) and
            0 <= subject_start < subject_end <= predicate_start < predicate_end <= len(text)):
        raise ValueError('Expected ordered source subject/predicate offsets')
    before=list(_BOUNDARY.finditer(text,0,subject_start))
    left=before[-1].end() if before else 0
    after=_BOUNDARY.search(text,predicate_end)
    right=after.end() if after else len(text)
    source=text[left:right]
    # Windows script mapping is character-preserving here; if a platform ever
    # changes length, do not manufacture source offsets for normalized text.
    normalized=zh.to_simplified(source)
    if len(normalized)!=len(source):
        return dict(version=VERSION,qualifications=['unresolved_mapping'],evidence=[],
                    relation_verified=False)
    a,b,c=subject_start-left,predicate_start-left,predicate_end-left
    subject_stop=subject_end-left
    evidence=[]
    def collect(pattern,label,start,end):
        for match in pattern.finditer(normalized,start,end):
            x,y=left+match.start(),left+match.end()
            evidence.append(dict(kind=label,start=x,end=y,quote=text[x:y]))
    collect(_REPORT,'reported',0,a)
    collect(_REPORT,'reported',subject_stop,b)
    collect(_HYPOTHESIS,'hypothetical',0,a)
    collect(_HYPOTHESIS,'hypothetical',subject_stop,b)
    collect(_MODAL,'hypothetical',subject_stop,b)
    collect(_NEGATION,'negated',subject_stop,b)
    # A wish can precede the subject (欲甲公死); do not silently promote it.
    leading=re.search(r'(?:(?:欲|愿|恐|将)?(?:令|使|命)|欲|愿|恐)\s*$',normalized[:a])
    if leading and not (assignment and leading.group(0).strip()=='命'):
        x,y=left+leading.start(),left+leading.end()
        evidence.append(dict(kind='unresolved_scope',start=x,end=y,quote=text[x:y]))
    bridge=normalized[a:b]
    if re.search(r'[，,：:。！？；\n]',bridge):
        evidence.append(dict(kind='unresolved_scope',start=subject_start,end=predicate_start,
                             quote=text[subject_start:predicate_start]))
    # Postposed denial of the report must not be treated as an asserted event.
    suffix=normalized[c:]
    if re.match(r'(?:之说|之言)',suffix):
        evidence.append(dict(kind='reported',start=predicate_end,end=predicate_end+2,
                             quote=text[predicate_end:predicate_end+2]))
    return dict(version=VERSION,qualifications=sorted({e['kind'] for e in evidence}),
                evidence=evidence,relation_verified=False)
