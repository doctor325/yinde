"""Deterministic source windows for the isolated numbered reader."""

import json
import re

from search import evidence_context


VERSION = 'source-event-window-v1'
_COORDS = ('block_index', 'start', 'end')
_TERMINATORS = '。！？!?'
_OPENING_IDENTITY = re.compile(
    r'[^「『。！？；，、：\s]{1,20}(?:者[，,][^「『。！？；，、：\s]{1,24}人也。'
    r'|[，,]字[^「『。！？；，、：\s]{1,12}[，,。])'
)


def _sentences(text):
    """Split by terminal punctuation outside validated quotations."""
    spans = []
    start = depth = 0
    for i, char in enumerate(text):
        if char in '「『':
            depth += 1
        elif char in '」』':
            depth -= 1
        elif not depth and char in _TERMINATORS:
            if i + 1 == len(text) or text[i + 1] not in _TERMINATORS:
                spans.append((start, i + 1, True))
                start = i + 1
    tail = text[start:].rstrip()
    if tail:
        complete = tail.rstrip('」』').endswith(tuple(_TERMINATORS))
        spans.append((start, len(text), complete))
    elif spans:
        begin, _, complete = spans[-1]
        spans[-1] = (begin, len(text), complete)
    return spans


def assemble(library, chunk, event_budget=1200, identity_budget=240, neighbors=1):
    """Return the selected source and a partition of its validated source run."""
    for name, value in (('event_budget', event_budget), ('identity_budget', identity_budget),
                        ('neighbors', neighbors)):
        if type(value) is not int or value < 0:
            raise ValueError(f'{name} must be a nonnegative integer')

    row = library.execute('SELECT blocks_json FROM chapters WHERE page_id=?',
                          (chunk['page_id'],)).fetchone()
    if row is None:
        raise ValueError('Source page missing')
    upper_bound = sum(len(block.get('text', '')) for block in json.loads(row[0]))
    context = evidence_context.assemble(library, chunk, max_chars=max(512, upper_bound))
    if context['boundary_cut'] or context['scope_truncated']:
        raise ValueError('Could not reconstruct the full source run')

    run = context['segments']
    text = ''.join(segment['text'] for segment in run)
    offsets = {}
    end = 0
    for segment in run:
        offsets[segment['block_index']] = end
        end += len(segment['text'])

    def slices(start, end):
        for segment in run:
            block = segment['block_index']
            base = offsets[block]
            lo, hi = max(0, start - base), min(len(segment['text']), end - base)
            if lo < hi:
                yield dict(block_index=block, start=lo, end=hi), segment['text'][lo:hi]

    anchor_parts = [{key: part[key] for key in _COORDS} for part in chunk['parts']]
    anchor_start = offsets[anchor_parts[0]['block_index']] + anchor_parts[0]['start']
    anchor_end = offsets[anchor_parts[-1]['block_index']] + anchor_parts[-1]['end']
    selection = dict(anchor_parts=anchor_parts, anchor_extent=dict(start=anchor_start, end=anchor_end),
                     neighbors=neighbors, sentences=[], event_parts=[], opening_parts=[],
                     identity=dict(status='unconfirmed', reason='opening_identity_unconfirmed',
                                   extent=None))
    reasons = []
    event_extent = None
    ranges = []
    if context['unbalanced_quote']:
        reasons.append('unbalanced_source_quotes')
    else:
        sentences = _sentences(text)
        covering = [i for i, (lo, hi, _) in enumerate(sentences)
                    if lo < anchor_end and anchor_start < hi]
        if not covering or not text[anchor_start:anchor_end].strip():
            reasons.append('anchor_sentence_boundary_unconfirmed')
        else:
            first, last = covering[0], covering[-1]
            left, right = max(0, first - neighbors), min(len(sentences), last + neighbors + 1)
            for i in range(left, right):
                lo, hi, complete = sentences[i]
                relation = 'left_neighbor' if i < first else 'right_neighbor' if i > last else 'anchor'
                selection['sentences'].append(dict(index=i, start=lo, end=hi, relation=relation,
                                                   complete=complete))
            if any(not sentence['complete'] for sentence in selection['sentences']):
                reasons.append('event_sentence_boundary_unconfirmed')
            else:
                event_start, event_end = sentences[left][0], sentences[right - 1][1]
                event_extent = dict(start=event_start, end=event_end)
                ranges.append((event_start, event_end, 'event_context'))
                if event_end - event_start > event_budget:
                    reasons.append('event_budget_exceeded')

                for lo, hi, complete in sentences[:2]:
                    if not complete:
                        break
                    if _OPENING_IDENTITY.match(text[lo:hi].lstrip()):
                        if lo < event_end and event_start < hi:
                            selection['identity'] = dict(status='already_in_event', reason=None,
                                                         extent=dict(start=lo, end=hi))
                        else:
                            opening_start = lo if event_start < hi else 0
                            ranges.append((opening_start, hi, 'opening_context'))
                            selection['identity'] = dict(status='opening_context', reason=None,
                                                         extent=dict(start=opening_start, end=hi))
                            if hi - opening_start > identity_budget:
                                reasons.append('identity_budget_exceeded')
                        break

    segments = []
    for start, end, role in ranges:
        part_list = selection['event_parts' if role == 'event_context' else 'opening_parts']
        for part, original in slices(start, end):
            part_list.append(part)
            segments.append(dict(id=f's{len(segments)}', role=role, text=original, **part))

    omissions = []
    cursor = 0
    omission_reason = 'outside_selected_window' if ranges else reasons[0]
    for start, end, _ in sorted(ranges) + [(len(text), len(text), '')]:
        omissions.extend(dict(**part, reason=omission_reason) for part, _ in slices(cursor, start))
        cursor = end

    return dict(context, version=VERSION, segments=segments, event_extent=event_extent,
                budget_chars=event_budget + identity_budget, event_budget=event_budget,
                identity_budget=identity_budget, source_chars=sum(len(s['text']) for s in segments),
                scope_truncated=bool(omissions), boundary_cut=False, input_ready=not reasons,
                ready_for_reader=not reasons, input_reasons=reasons, selection=selection,
                omissions=omissions, notes=context['notes'] + [
                    'A source-local window does not establish semantic sufficiency.'])
