"""Source-local relative dates, preserving the anchor instead of inventing a year."""
import re
from search import zh

TITLE = r'(?:大将军|丞相|太尉|太傅|司空|司徒)?'
RELATIVE = r'(?:明年|翌年|是年|其年)'


def reference(chunk, observation):
    text=chunk['text_orig'];normalized=zh.to_simplified(text)
    if len(text)!=len(normalized):return None
    subject=observation.get('subject_span')
    if not subject:return None
    start=observation['start']
    prefix=normalized[start:subject[0]]
    match=re.fullmatch(r'\s*('+RELATIVE+r')[，,\s]*'+TITLE,prefix)
    if not match:return None
    # Only the same paragraph, starting with an explicit regnal year, can
    # provide an anchor. Never cross a paragraph or another relative date.
    left=normalized.rfind('\n\n',0,start)+2
    if left==1:left=0
    preceding=normalized[left:start]
    anchor=re.match(r'\s*[\u3400-\u9fff]{2,10}(?:元|[一二三四五六七八九十]+)年',preceding)
    if not anchor or re.search(RELATIVE,preceding[anchor.end():]):return None
    if re.search(r'[「」『』“”：:]|曰|传言|传闻',preceding[:anchor.end()]):return None
    # Another explicit year would make the anchor ambiguous.
    if re.search(r'(?:元|[一二三四五六七八九十]+)年',preceding[anchor.end():]):return None
    a,b=left+anchor.start(),left+anchor.end()
    return dict(status='anchored_relative',expression=text[start+match.start(1):start+match.end(1)],
                anchor=dict(start=a,end=b,quote=text[a:b]),
                absolute_year=None,relation_verified=False)
