"""Replay a saved search in a local cited-interpretation prototype."""
import argparse
import hashlib
from html import escape
import json
from pathlib import Path
import time
from uuid import uuid4

from search import local_rag, punctuated
from search.local_evidence_reader import ROOT, LocalReader
from search.polity_evidence import Sources


def render(report):
    evidence = report['packet']['evidence']
    body = ['<!doctype html><html lang="zh-Hans"><meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            '<title>引得 · 本地史料释读</title>',
            '<style>body{max-width:48rem;margin:2rem auto;padding:0 1rem;font:1rem/1.65 system-ui}'
            'blockquote{white-space:pre-wrap;margin:1rem 0}article{padding:.5rem 0;border-top:1px solid #ccc}'
            'article:target{background:#fff3cc}</style><h1>引得 · 本地史料释读</h1>']
    for row in report['results']:
        body.append(f"<section><h2>{escape(row['question'])}</h2>")
        if answer := row.get('answer'):
            if 'verified_support' in answer:
                heading = {'answer': '史料作答', 'correction': '史料纠正问句',
                           'partial': '史料支持部分作答', 'insufficient': '关键证据缺失'}
                body.append(f"<p>{heading[answer['status']]}</p>")
            else:
                body.append('<p>模型释读草稿 · 引用位置已核对</p>')
            if answer['status'] == 'insufficient':
                body.append('<p>现有史料不足以作答。</p>')
            for claim in answer['claims']:
                citations = ' '.join(f'<a href="#{key}">[{key}]</a>' for key in claim['evidence_ids'])
                body.append(f"<p>{escape(claim['text'])} {citations}</p>")
            for rejected in answer.get('rejected_claims', ()):
                reason = rejected.get('unsupported_quantities') or rejected.get('missing_chapters') or []
                note = f"（{escape('、'.join(map(str, reason)))}）" if reason else ''
                body.append(f"<p>已拒答：{escape(rejected['text'])}{note}</p>")
            for label, ref in answer.get('verified_support', {}).items():
                body.append(f'<p><a href="#{ref["id"]}">{escape(label)}</a> '
                            f'<q>{escape(ref["quote"])}</q></p>')
            if missing := answer.get('missing_support'):
                body.append(f"<p>尚缺依据。{escape('、'.join(missing))}</p>")
            if 'verified_support' in answer and 'output' in row:
                body.append(f'<details><summary>模型原始作答与引用</summary><pre>'
                            f'{escape(json.dumps(row["output"], ensure_ascii=False, indent=2))}'
                            '</pre></details>')
        elif row.get('error'):
            body.append(f"<p>{escape(row['error'])}</p>")
        body.append('</section>')
    body.append('<h2>原文与出处</h2>')
    for ref in evidence:
        layer = '注文' if ref['layer'] == 'annotation' else '正文'
        title = escape(ref['title'])
        heading = escape(ref['heading'])
        source = escape(ref['source_url'], quote=True)
        edition = escape(ref['edition'])
        block, start, end = ref['ranges'][0]
        quote = ('……' if ref.get('truncated_before') else '') + ref['quote']
        quote += '……' if ref.get('truncated_after') else ''
        note = ' · 节选' if ref.get('excerpt') else ''
        body.append(f'<article id="{ref["id"]}"><h3>{ref["id"]} · {title} · {layer}{note}</h3>'
                    f'<p>{heading} · {edition} · 修订 {ref["revision_id"]} · 原文块 {block} 字符 {start}–{end}</p>'
                    f'<blockquote>{escape(quote)}</blockquote>')
        related = [item for item in evidence if item is not ref and set(item['ranks']).intersection(ref['ranks'])]
        if related:
            links = ' '.join(f'<a href="#{item["id"]}">[{item["id"]}]</a>' for item in related)
            body.append(f'<p>同一搜索结果的附带材料 {links}</p>')
        if source.startswith('https://'):
            body.append(f'<p><a href="{source}">查看固定版本</a></p>')
        body.append('</article>')
    body.append('</html>')
    return '\n'.join(body)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--saved-search', type=Path, required=True)
    parser.add_argument('--question', action='append')
    parser.add_argument('--run', action='store_true', help='加载本机 Qwen3-4B 生成释读')
    args = parser.parse_args()
    saved_path = args.saved_search.resolve()
    if not saved_path.is_relative_to(ROOT):
        parser.error('搜索快照必须位于项目内')
    snapshot = saved_path.read_bytes()
    saved = json.loads(snapshot)
    questions = args.question or [saved['query']]
    if any(not 1 <= len(question.strip()) <= 500 for question in questions):
        parser.error('问题须为1至500字')
    out = ROOT / 'src/data/local-rag-pilot' / uuid4().hex[:12]
    if not out.resolve().is_relative_to(ROOT):
        parser.error('输出目录必须位于项目内')
    out.mkdir(parents=True)
    report = dict(saved_search=str(saved_path.relative_to(ROOT)),
                  saved_search_sha256=hashlib.sha256(snapshot).hexdigest(),
                  model='Qwen3-4B Q4_K_M', backend='CPU', results=[])
    print(str(out), flush=True)
    with punctuated.connect() as library:
        sources = Sources(None, library, {})
        report['packet'] = packet = local_rag.evidence_packet(saved, sources)
        payload = json.dumps(packet, ensure_ascii=False, sort_keys=True).encode('utf8')
        report['packet_sha256'] = hashlib.sha256(payload).hexdigest()
        if args.run and packet['evidence']:
            try:
                with LocalReader(model='qwen3-4b', timeout=120, log_dir=out) as reader:
                    report['startup_ms'] = reader.startup_ms
                    for question in questions:
                        row = dict(question=question)
                        report['results'].append(row)
                        started = time.perf_counter()
                        try:
                            row.update(local_rag.read(reader, question, packet))
                        except (OSError, ValueError, KeyError) as exc:
                            row.update(error=f'释读中断（{type(exc).__name__} · {exc}）',
                                       elapsed_ms=round((time.perf_counter()-started)*1000, 2))
                            break
                        try:
                            row['answer'] = local_rag.resolve_answer(row['output'], question, packet, sources)
                        except ValueError as exc:
                            row['error'] = str(exc)
            except (RuntimeError, OSError, ValueError, KeyError) as exc:
                report['startup_error'] = f'{type(exc).__name__} · {exc}'
        elif args.run:
            report['results'] = [dict(question=q, model_calls=0, elapsed_ms=0,
                                     answer=dict(status='insufficient', claims=[])) for q in questions]
    (out/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf8')
    (out/'report.html').write_text(render(report), encoding='utf8')
    return int(bool(report.get('startup_error')) or any('error' in row for row in report['results']))


if __name__ == '__main__':
    raise SystemExit(main())
