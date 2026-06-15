"""
Knowledge graph over non-code content (docs + PDFs) — zero-dependency.

Turns documentation and PDF text into a queryable knowledge graph:
  - **concepts**: the salient terms of each document (TF-IDF over the corpus +
    a code-aware tokenizer), so a doc is summarised by what it's actually about.
  - **topics (communities)**: documents and concepts are linked into a graph and
    clustered with the same label-propagation used for code modules — each
    cluster is a topic ("auth & sessions", "payments", …).
  - links: doc ↔ concept membership, so the graph connects content to the ideas
    it covers and (via shared concepts) related documents to each other.

This is the deterministic scaffold. The *semantic* understanding — answering
"why does X exist", reading an image/PDF — is done by the Claude skill on top
(vision + reasoning over this graph). Nodo stays offline and model-free.
"""
import math
from collections import Counter, defaultdict


def build_knowledge(doc_texts, max_concepts_per_doc=8, max_global_concepts=250):
    """doc_texts: {rel: text} for docs (md/txt/rst) and extracted PDF text.

    Returns {concepts, doc_concepts, concept_docs, topics} where topics are the
    community clusters (each {id, name, concepts, docs, size})."""
    from .search import tokenize
    from .clustering import detect_communities

    def _tf(text):
        tf = Counter(tokenize(text))
        for line in text.split('\n'):           # Markdown headings name the topic → weight them
            if line.lstrip().startswith('#'):
                for w in tokenize(line):
                    tf[w] += 2
        return tf

    docs = {rel: _tf(t) for rel, t in (doc_texts or {}).items() if t}
    N = len(docs)
    if N == 0:
        return {'concepts': [], 'doc_concepts': {}, 'concept_docs': {}, 'topics': []}

    df, total = Counter(), Counter()
    for tf in docs.values():
        df.update(tf.keys())
        total.update(tf)

    def ok(term):
        return len(term) >= 3 and not term.isdigit()

    # Concepts are the SHARED vocabulary — terms appearing in >=2 documents — which
    # is what links related docs into a topic. (TF-IDF's rare per-doc terms would
    # never cluster two docs about the same thing.) Single-doc corpus: fall back to
    # that doc's most frequent meaningful terms.
    if N >= 2:
        cands = [t for t in df if df[t] >= 2 and ok(t)]
        def score(t):
            return df[t] * (1 + math.log(1 + total[t]))
    else:
        cands = [t for t in total if ok(t)]
        def score(t):
            return total[t]
    concepts = sorted(cands, key=lambda t: (-score(t), t))[:max_global_concepts]

    # each doc → the concepts it contains, ranked by salience. Iterate the ordered
    # `concepts` list (NOT a set) and break tf ties by the term itself, so the
    # selection is fully deterministic (Python randomizes set iteration order per
    # process via hash seeding — that was making topics drift between runs).
    doc_concepts = {}
    concept_docs = defaultdict(set)
    for rel, tf in docs.items():
        present = sorted((c for c in concepts if c in tf),
                         key=lambda c: (-tf[c], c))[:max_concepts_per_doc]
        doc_concepts[rel] = present
        for c in present:
            concept_docs[c].add(rel)
    concepts = [c for c in concepts if c in concept_docs]   # drop concepts no doc kept

    # bipartite doc↔concept graph → community detection (topics)
    node_id = {}
    kinds = []
    for rel in docs:
        node_id[('doc', rel)] = len(kinds)
        kinds.append(('doc', rel))
    for c in concepts:
        node_id[('concept', c)] = len(kinds)
        kinds.append(('concept', c))
    edges = []
    for rel, cs in doc_concepts.items():
        for c in cs:
            edges.append({'source': node_id[('doc', rel)], 'target': node_id[('concept', c)]})

    comm = detect_communities(len(kinds), edges)
    groups = defaultdict(lambda: {'docs': [], 'concepts': []})
    for (kind, val), nid in node_id.items():
        groups[comm.get(nid, 0)]['docs' if kind == 'doc' else 'concepts'].append(val)

    topics = []
    for cid, grp in groups.items():
        if not grp['docs'] and not grp['concepts']:
            continue
        # name the topic by its concept shared across the most docs in the cluster
        name = None
        if grp['concepts']:
            name = max(grp['concepts'], key=lambda c: (len(concept_docs.get(c, ())), c))
        elif grp['docs']:
            name = grp['docs'][0].split('/')[-1]
        topics.append({
            'id': cid, 'name': name or 'topic',
            'concepts': sorted(grp['concepts'], key=lambda c: (-len(concept_docs.get(c, ())), c))[:10],
            'docs': sorted(grp['docs'])[:10],
            'size': len(grp['docs']) + len(grp['concepts']),
        })
    topics.sort(key=lambda t: (-t['size'], t['name']))

    # god-nodes: the most-connected concepts — the ideas the most documents flow
    # through. (Graphify's "god nodes", for the knowledge graph.)
    god_nodes = [{'concept': c, 'docs': len(concept_docs[c])}
                 for c in sorted(concepts, key=lambda c: (-len(concept_docs[c]), c))
                 if len(concept_docs[c]) >= 2][:15]

    return {
        'concepts': concepts,
        'doc_concepts': doc_concepts,
        'concept_docs': {c: sorted(concept_docs[c]) for c in concepts},
        'topics': topics,
        'god_nodes': god_nodes,
    }
