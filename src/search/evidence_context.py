"""Source-local context for the isolated reader experiment (no search imports it).

Reconstruct from saved blocks, not overlapping index neighbours. Headings,
editions, pages, layers and revision/hash mismatches are hard boundaries.
"""
import hashlib
import json
import re

VERSION = 'source-event-context-v1'
IDENTITY = ('page_id', 'book_id', 'revision_id', 'source_sha256')


def quotes_balanced(text):
    stack=[]
    for char in text:
        if char in '「『':stack.append({'「':'」','『':'』'}[char])
        elif char in '」』':
            if not stack or stack.pop()!=char:return False
    return not stack


def units(text):
    """Sentence/paragraph ends outside quoted speech; retain original offsets."""
    stack, ends = [], [0]
    for i, char in enumerate(text):
        if char in '「『':
            stack.append({'「': '」', '『': '』'}[char])
        elif stack and char == stack[-1]:
            stack.pop()
        if not stack and (char in '。！？；' or char == '\n' and i and text[i-1] == '\n'):
            ends.append(i+1)
    if ends[-1] != len(text):
        ends.append(len(text))
    return list(zip(ends, ends[1:]))


def assemble(library, chunk, max_chars=3200):
    if max_chars < 512:
        raise ValueError('Context budget must be at least 512 source characters')
    row = library.execute('SELECT * FROM chapters WHERE page_id=?', (chunk['page_id'],)).fetchone()
    if not row:
        raise ValueError('Source page missing')
    page = dict(row)
    if any(page.get(k) != chunk.get(k) for k in IDENTITY):
        raise ValueError('Source edition/revision/hash mismatch')
    blocks = json.loads(page['blocks_json'])
    parts = chunk.get('parts', [])
    if not parts:
        raise ValueError('Candidate lacks source offsets')
    restored = ''
    for p in parts:
        i, a, b = p['block_index'], p['start'], p['end']
        if not (0 <= i < len(blocks) and 0 <= a < b <= len(blocks[i].get('text', ''))):
            raise ValueError('Invalid source offsets')
        restored += blocks[i]['text'][a:b]
    if restored != chunk['text_orig']:
        raise ValueError('Candidate does not reconstruct from the saved source')
    # Locate the original contiguous run containing every part.
    runs, current, heading, layer = [], [], '', None
    for i, block in enumerate(blocks):
        kind = block.get('kind')
        next_layer = 'body' if kind in ('body', 'glyph') else 'annotation' if kind == 'annotation' else None
        if next_layer != layer or next_layer is None:
            if current:
                runs.append((current, layer, heading)); current = []
        if kind == 'heading':
            heading = block.get('text', '')
        layer = next_layer
        if layer and block.get('text'):
            current.append(i)
    if current:
        runs.append((current, layer, heading))
    run = next((ids for ids, layer, head in runs
                if layer == chunk['layer'] and head == chunk.get('heading', '')
                and all(p['block_index'] in ids for p in parts)), None)
    if run is None:
        raise ValueError('Candidate crosses a source layer or heading boundary')
    offsets, text = {}, ''
    for i in run:
        offsets[i] = len(text)
        text += blocks[i]['text']
    intervals = [(offsets[p['block_index']]+p['start'], offsets[p['block_index']]+p['end']) for p in parts]
    if any(left[1] != right[0] for left, right in zip(intervals, intervals[1:])):
        raise ValueError('Non-contiguous or duplicate candidate source parts')
    anchor_a, anchor_b = intervals[0][0], intervals[-1][1]
    spans = units(text)
    first = next(i for i, (_, b) in enumerate(spans) if b > anchor_a)
    last = next(i for i, (_, b) in enumerate(spans) if b >= anchor_b)
    a, b = spans[first][0], spans[last][1]
    cut = b-a > max_chars
    if cut:
        if anchor_b-anchor_a > max_chars:
            raise ValueError('Candidate exceeds context budget')
        a = max(a, anchor_a-(max_chars-(anchor_b-anchor_a))//2)
        b = min(b, a+max_chars)
    else:
        # Balanced expansion by complete units; a large quotation is never
        # silently sliced to make it look complete.
        while True:
            changed = False
            if first and b-spans[first-1][0] <= max_chars:
                first -= 1; a = spans[first][0]; changed = True
            if last+1 < len(spans) and spans[last+1][1]-a <= max_chars:
                last += 1; b = spans[last][1]; changed = True
            if not changed:
                break
    segments = []
    def add(lo, hi, role):
        for i in run:
            base, value = offsets[i], blocks[i]['text']
            start, end = max(0, lo-base), min(len(value), hi-base)
            if start < end:
                segments.append(dict(id='s'+str(len(segments)), role=role,
                    block_index=i, start=start, end=end, text=value[start:end]))
    add(a, b, 'event_context')
    # Biography introduction is a separately labelled source, NOT an assertion
    # that every later event has this actor. Never jump across a heading/layer.
    if a > 0:
        limit = min(a, max_chars-(b-a), 400)
        opening_end = max((end for _, end in spans if end <= limit), default=0)
        if opening_end:
            add(0, opening_end, 'opening_context')
    identity = {k: page[k] for k in IDENTITY + ('title', 'source_url', 'license_url', 'attribution')}
    return dict(version=VERSION, source=identity, heading=chunk.get('heading', ''),
        layer=chunk['layer'], anchor_chunk_id=chunk['chunk_id'], segments=segments,
        budget_chars=max_chars, source_chars=sum(len(s['text']) for s in segments),
        scope_chars=len(text), scope_truncated=a>0 or b<len(text),
        boundary_cut=cut, unbalanced_quote=not quotes_balanced(text[a:b]),
        event_extent={'start':a, 'end':b},
        notes=['Heading/opening are identity clues, not proof of event subject.',
               'Source text may itself mix editorial notes; attribution still requires judgement.'])


def rule_view(chunk, context):
    segments = [s for s in context['segments'] if s['role'] == 'event_context']
    text = ''.join(s['text'] for s in segments)
    return {**chunk, 'text_orig':text, 'parts':[
        {k:s[k] for k in ('block_index','start','end')} for s in segments],
        'text_sha256':hashlib.sha256(text.encode()).hexdigest()}


def locate_quote(context, segment_id, quote):
    segment = next((s for s in context['segments'] if s['id'] == segment_id), None)
    if not segment or not isinstance(quote, str) or not quote.strip():
        raise ValueError('Missing citation segment/quote')
    occurrences = [m.start() for m in re.finditer(re.escape(quote), segment['text'])]
    if len(occurrences) != 1:
        raise ValueError('Citation is absent or not uniquely located')
    start = segment['start'] + occurrences[0]
    return {**context['source'], 'block_index':segment['block_index'],
            'start':start, 'end':start+len(quote), 'quote':quote}
