"""Conservative omitted-recipient appointment chains in source-attested biographies."""
import re
from search import zh
from search.annal_events import OFFICE, SENTENCE


def observations(chunk, plan, page_chunks):
    if (chunk.get('layer') != 'body' or not plan.get('subject')
            or plan.get('constraints') or plan.get('answer_slots') != ['office']
            or {g['kind'] for g in plan['relation_groups']} != {'appointment'}):
        return []
    name=plan['subject'][0]['text']
    if zh.to_simplified(chunk.get('heading','')).strip() != name:
        return []
    keys=('page_id','book_id','revision_id','source_sha256','heading','layer')
    peers=[c for c in page_chunks if all(c.get(k)==chunk.get(k) for k in keys)]
    # Reassemble only source-indexed blocks, refusing gaps or conflicting overlap.
    blocks={}
    for c in peers:
        offset=0
        for part in c.get('parts',[]):
            size=part['end']-part['start']
            piece=c['text_orig'][offset:offset+size];offset+=size
            if len(piece)!=size:return []
            positions=blocks.setdefault(part['block_index'],{})
            for i,char in enumerate(piece,part['start']):
                if i in positions and positions[i]!=char:return []
                positions[i]=char
    openings=[]
    for positions in blocks.values():
        if 0 in positions:
            end=0
            while end in positions:end+=1
            openings.append(''.join(positions[i] for i in range(end)))
    opening=next((t for t in openings if re.match(re.escape(name)+r'[，,]?字',zh.to_simplified(t).lstrip())),None)
    if not opening:return []
    parts=chunk.get('parts',[])
    if not parts:return []
    first=parts[0];positions=blocks.get(first['block_index'],{})
    if any(i not in positions for i in range(first['start'])):return []
    prefix=''.join(positions[i] for i in range(first['start']))
    # A candidate starting inside a quotation must not regain narrative scope.
    before=prefix
    switched=False
    office=r'(?:'+OFFICE+r'|[左右]司郎中|[吏户礼兵刑工]部(?:郎中|员外郎))'
    jurisdiction=r'[\u3400-\u9fff]{1,4}府'
    clause=r'(?:复官|徙'+jurisdiction+r'|(?:迁|左授|授|拜|转)'+office+r'|召权知'+jurisdiction+r')'
    chain=re.compile(r'复官[，,]'+clause+r'(?:[，,]'+clause+r')*[。；]?')
    result=[]
    for m in SENTENCE.finditer(chunk['text_orig']):
        quote=m.group();text=zh.to_simplified(quote).strip()
        # A preceding explicit actor transition invalidates inherited scope.
        transition=re.match(r'([\u3400-\u9fff]{2,4}?)(?:复官|为|拜|授|卒|来|曰)',text)
        if transition and transition[1] != name:
            switched=True
        in_quote=any(before.count(a)>before.count(b) for a,b in (('「','」'),('『','』'),('“','”')))
        if not switched and not in_quote and not re.search(r'[「」『』“”：:曰云言奏称谓]',quote) and chain.fullmatch(text):
            if not re.search(office,text) and not re.search(r'召权知'+jurisdiction,text):
                before+=quote;continue
            # Source-backed scope is evidence, not a claim of resolved truth.
            result.append(dict(kind='appointment',polarity='positive_syntax',qualifications=[],
                quote=quote,start=m.start(),end=m.end(),subject_omitted=True,
                subject_basis='biography_recipient_chain',subject_scope=dict(
                    heading=chunk['heading'],opening=opening.split('。')[0],
                    page_id=chunk['page_id'],revision_id=chunk['revision_id'],
                    source_sha256=chunk['source_sha256']),relation_verified=False))
        before+=quote
    return result
