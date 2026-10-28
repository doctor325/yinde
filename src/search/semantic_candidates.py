"""Bounded general query expansion for the optional reranking experiment."""
import json
import re
import sqlite3
from search import engine
from search.history_qa import local_plan
from search.semantic_bridge import state_dir
from search import question, zh

VERSION = 'grounded-plan-v2'
# Query language only: no person, event name, date or answer is encoded here.
PREDICATES = (
    (('即位','登基','继位','登上君位'), ('即位','立','踐祚')),
    (('担任','出任','任命','宰相','官职'), ('拜','為相','任','官')),
    (('征伐','攻打','战胜','战败','战争','围困'), ('伐','戰','敗','圍')),
    (('迁移','迁往','迁都','逃往','逃离','驻军'), ('遷','徙','奔','軍')),
)
QUESTION_PARTS = ('查找','查','有关','关于','哪些文献','哪些史料','史料','证据','记载','引文',
    '什么时候','在哪一年','什么地方','在哪里','何时','为什么','为何','如何','怎样','什么',
    '哪些','是否','多少年','多久','多少','几年','的经过','的时间','的经历','的结果','的关系',
    '的','了','与','和','把','从','到','被','在','对','与')


def plan_query(cur, query, book=None, *, literal_exists=None, search_fn=None):
    """Bounded corpus-grounded lexical hypotheses, never an identity assertion."""
    search = search_fn or engine.run_search
    plan_exists = literal_exists
    if plan_exists is None and search_fn is not None:
        # Preserve local_plan's original unscoped grounding, independently of
        # the scoped probes below. Only the explicit experiment supplies this.
        plan_exists = lambda term: bool(search(cur,term,page_size=1,variants=True)['total'])
    plan = (local_plan(cur, query) if plan_exists is None else
            local_plan(cur, query, literal_exists=plan_exists))
    normalized = zh.to_traditional(query)
    triggers = list(QUESTION_PARTS)
    terms = list(plan['relation_terms'])
    for asks, expressions in PREDICATES:
        triggers.extend(asks)
        if any(zh.to_traditional(t) in normalized for t in asks):
            terms.extend(expressions)
    for vocab in question.INTENT_VOCAB.values():
        triggers.extend(t for t in vocab['ask'] if len(t) > 1)
    pattern = '|'.join(re.escape(zh.to_traditional(t)) for t in sorted(set(triggers), key=len, reverse=True))
    fragments = re.split(pattern, normalized)
    anchors = list(dict.fromkeys(plan['entities'] + plan.get('lexical_anchors', [])))
    checks, ambiguous = 0, []
    for fragment in fragments:
        for run in re.findall(r'[\u3400-\u9fff]{2,12}', fragment):
            if len(anchors) >= 4 or checks >= 20:
                break
            forms = [run]
            # A substring remains explicitly ambiguous (e.g. a title without its
            # dynasty prefix), rather than becoming a curated identity alias.
            if len(run) > 2:
                forms += [run[i:i+n] for n in range(min(6,len(run)-1),1,-1) for i in range(len(run)-n+1)]
            for form in dict.fromkeys(forms):
                if checks >= 20:
                    break
                checks += 1
                present = (literal_exists(form) if literal_exists is not None else
                           bool(search(cur, form, book=book, page_size=1, variants=True)['total']))
                if present:
                    if form not in anchors:
                        anchors.append(form)
                        if form != run:
                            ambiguous.append({'fragment':run,'literal_hypothesis':form})
                    break
    terms = list(dict.fromkeys(terms))[:8]
    searches = []
    if anchors:
        # Reserve subject-only fallbacks and multi-anchor conjunction before
        # predicate expansion; do not let the first guessed predicate consume
        # all slots. Literal anchor presence never proves role satisfaction.
        if len(anchors)>1:
            searches.append(' '.join(anchors)[:80])
        searches.extend(anchors[:3])
        for term in terms:
            searches.append(anchors[0]+' '+term)
        searches = list(dict.fromkeys(searches))[:6]
    else:
        searches = plan['search_queries']
    return {**plan, 'planner_version':VERSION, 'literal_hypotheses':anchors,
            'relation_terms':terms, 'search_queries':searches, 'substring_ambiguities':ambiguous,
            'grounding_checks':checks}


def lexical(cur, query, book=None, *, search_fn=None):
    search = search_fn or engine.run_search
    raw = search(cur, query, book=book, page_size=40, variants=True)
    searches = [{'query': query, 'total': raw['total'], 'sampled': len(raw['items'])}]
    batches = [raw['items']]
    plan = None
    # Keep exact lexical searches intact; interpret the question when literal
    # full-question matching has no hits. This is explicitly a rule channel.
    if not raw['total']:
        plan = plan_query(cur, query, book, search_fn=search_fn)
        for expanded in plan['search_queries']:
            if expanded == query:
                continue
            result = search(cur, expanded, book=book, page_size=20, variants=True)
            batches.append(result['items'])
            searches.append({'query': expanded, 'total': result['total'], 'sampled': len(result['items'])})
    hits, used, passage_ids = [], set(), set()
    connection = sqlite3.connect((state_dir() / 'index/chunks.sqlite').as_uri()+'?mode=ro', uri=True)
    try:
        # Interleave expanded predicates so a broad noun query cannot monopolize candidates.
        for offset in range(max((len(x) for x in batches), default=0)):
            for batch in batches:
                if offset >= len(batch):
                    continue
                pid = batch[offset]['passage_id']
                for cid, payload in connection.execute('SELECT c.chunk_id,c.payload FROM chunks c JOIN chunk_passages p ON p.chunk_id=c.chunk_id WHERE p.passage_id=? ORDER BY c.chunk_id', (pid,)):
                    if cid not in used:
                        used.add(cid); passage_ids.add(pid)
                        hits.append({'chunk_id': cid, 'chunk': json.loads(payload)})
                        if len(hits) == 40:
                            return hits, searches, plan, sorted(passage_ids)
    finally:
        connection.close()
    return hits, searches, plan, sorted(passage_ids)
