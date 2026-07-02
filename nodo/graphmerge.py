"""
Unify the graph: fold documentation and multimodal assets in as first-class
NODES connected to the code they describe/are-referenced-by.

The dependency graph from scanner.py is code-only (so the detectors, hubs,
communities, and blast-radius queries stay precise). This module takes that code
graph and ADDS:

  - doc nodes   (kind='doc')   — Markdown / spec files, linked to the code & assets
                                 they reference (markdown links + filename mentions)
  - asset nodes (kind='asset') — images / PDFs / video, linked from the code & docs
                                 that reference them

All added edges are tagged kind='reference' (vs code import edges kind='import'),
so the viewer can show the whole connected picture while `--query`/`--path`
keep operating on import edges only. This is what makes "everything connects in
the graph" true without polluting structural analysis.
"""
import os
import re
from collections import defaultdict

_MD_LINK = re.compile(r'\]\(\s*<?([^)\s>]+)>?\s*\)')          # [txt](path)
_MD_IMG = re.compile(r'!\[[^\]]*\]\(\s*<?([^)\s>]+)>?\s*\)')   # ![alt](path)
_HTML_SRC = re.compile(r'(?:src|href)\s*=\s*[\'"]([^\'"]+)[\'"]', re.I)


def _index(rels):
    by_rel = set(rels)
    by_base = defaultdict(list)
    for r in rels:
        by_base[r.split('/')[-1]].append(r)
    return by_rel, by_base


# stems too generic to link a doc mention to a specific file
_COMMON_STEMS = {
    'index', 'main', 'app', 'utils', 'util', 'config', 'types', 'type',
    'constants', 'const', 'helpers', 'helper', 'core', 'base', 'common',
    'shared', 'init', 'setup', 'styles', 'style', 'test', 'tests', 'spec',
    'models', 'model', 'routes', 'route', 'api', 'client', 'server', 'data',
}


def _stem(rel):
    return re.sub(r'\.[^./]+$', '', rel.split('/')[-1])


def _resolve(target, from_rel, by_rel, by_base):
    """Resolve a link/mention to a known rel path, or None."""
    target = target.split('#')[0].split('?')[0].strip()
    if not target:
        return None
    if target in by_rel:
        return target
    # relative to the referencing file's directory
    base_dir = from_rel.rsplit('/', 1)[0] if '/' in from_rel else ''
    cand = os.path.normpath(os.path.join(base_dir, target)).replace('\\', '/')
    if cand in by_rel:
        return cand
    # unique basename mention (e.g. "see scanner.py")
    name = target.split('/')[-1]
    if name in by_base and len(by_base[name]) == 1:
        return by_base[name][0]
    return None


def integrate(code_nodes, code_edges, communities, docs, assets, root,
              max_doc_edges=40, knowledge=None):
    """Return (nodes, edges, communities) extended with doc + asset nodes and
    reference edges. Inputs are the code-only graph; outputs are for render +
    context.json only (detectors already ran on the code graph)."""
    from .scanner import tier_of

    nodes = [dict(n) for n in code_nodes]
    edges = [dict(e) for e in code_edges]
    communities = dict(communities)

    rel_to_id = {n['rel']: n['id'] for n in nodes}
    next_id = (max((n['id'] for n in nodes), default=-1)) + 1
    max_comm = max(communities.values(), default=-1)
    doc_comm, asset_comm, concept_comm = max_comm + 1, max_comm + 2, max_comm + 3

    asset_rels = [a['rel'] for a in (assets or [])]
    # index of everything a doc/code file might point at: code + assets
    link_by_rel, link_by_base = _index(list(rel_to_id) + asset_rels)
    # unique, distinctive module stems → link a doc that names the MODULE
    # (e.g. "AudioEngine") to its file (AudioEngine.js) even without the extension.
    stem_by = defaultdict(list)
    for n in code_nodes:
        stem_by[_stem(n['rel'])].append(n['rel'])

    # ── doc nodes ──
    doc_ids = {}
    for rel in sorted(docs or {}):
        text = docs[rel] or ''
        nid = next_id
        next_id += 1
        doc_ids[rel] = nid
        rel_to_id[rel] = nid
        communities[nid] = doc_comm
        nodes.append({
            'id': nid, 'label': rel.split('/')[-1], 'rel': rel,
            'category': 'doc', 'loc': text.count('\n') + 1 if text else 0,
            'tier': tier_of(rel), 'kind': 'doc',
        })

    # ── asset nodes ──
    asset_ids = {}
    for a in (assets or []):
        rel = a['rel']
        nid = next_id
        next_id += 1
        asset_ids[rel] = nid
        rel_to_id[rel] = nid
        communities[nid] = asset_comm
        nodes.append({
            'id': nid, 'label': rel.split('/')[-1], 'rel': rel,
            'category': 'asset', 'loc': 0, 'tier': a.get('tier', 'app'),
            'kind': 'asset', 'asset_type': a.get('type', ''),
        })

    seen = set()

    def add_edge(s, t):
        if s is None or t is None or s == t:
            return
        key = (s, t)
        if key in seen:
            return
        seen.add(key)
        edges.append({'source': s, 'target': t, 'kind': 'reference', 'prov': 'inferred'})

    # ── doc → (code | asset) edges, from markdown links + filename mentions ──
    # Mention matching is INVERTED for scale: the old form regex-searched every doc
    # for every unique basename/stem — O(docs × names) full-text scans, ~4M regex
    # runs on a 3k-file/700-doc repo and the dominant scan cost. Now each doc is
    # tokenized once, tokens are intersected with the name sets, and only that
    # handful of candidates is confirmed with the ORIGINAL guarded regex — same
    # semantics (the superset tokenizer + exact confirm can't miss or over-match),
    # near-linear cost.
    uniq_base = {n: rels[0] for n, rels in link_by_base.items() if len(rels) == 1}
    uniq_stem = {s: rels[0] for s, rels in stem_by.items()
                 if len(rels) == 1 and len(s) >= 4 and s.lower() not in _COMMON_STEMS}
    _pat_cache = {}

    def _confirm(name, text, kind):
        pat = _pat_cache.get((kind, name))
        if pat is None:
            if kind == 'base':   # original basename guards
                pat = re.compile(r'(^|[^\w./])' + re.escape(name) + r'($|[^\w])')
            else:                # original stem guards
                pat = re.compile(r'(?<![\w.])' + re.escape(name) + r'(?![\w.])')
            _pat_cache[(kind, name)] = pat
        return pat.search(text) is not None

    _TOKEN = re.compile(r'[\w.\-]+')

    def _doc_candidates(text):
        """Superset of every substring the guarded regexes could match: tokens on
        the guards' boundary characters, plus all dash-delimited spans (both guard
        sets admit '-' as a boundary), stripped of leading/trailing dots."""
        cands = set()
        for tok in set(_TOKEN.findall(text)):
            parts = tok.split('-')
            k = len(parts)
            for i in range(k):
                acc = parts[i]
                span = acc.strip('.')
                if span:
                    cands.add(span)
                for j in range(i + 1, k):
                    acc = acc + '-' + parts[j]
                    span = acc.strip('.')
                    if span:
                        cands.add(span)
        return cands

    for rel, nid in doc_ids.items():
        text = docs[rel] or ''
        targets = set()
        for rx in (_MD_IMG, _MD_LINK, _HTML_SRC):
            for m in rx.findall(text):
                r = _resolve(m, rel, link_by_rel, link_by_base)
                if r:
                    targets.add(r)
        cands = _doc_candidates(text)
        # unique-basename mentions of code files in prose/code-fences
        for name in cands.intersection(uniq_base):
            if _confirm(name, text, 'base'):
                targets.add(uniq_base[name])
        # …and unique, distinctive MODULE-name mentions (no extension), e.g. a
        # spec that says "AudioEngine" links to AudioEngine.js.
        for name in cands.intersection(uniq_stem):
            if _confirm(name, text, 'stem'):
                targets.add(uniq_stem[name])
        for r in sorted(targets)[:max_doc_edges]:   # sorted: deterministic order + cap
            add_edge(nid, rel_to_id.get(r))

    # ── (code | doc) → asset edges, from the asset's referenced_by list ──
    for a in (assets or []):
        aid = asset_ids[a['rel']]
        for ref in a.get('referenced_by', []):
            add_edge(rel_to_id.get(ref), aid)

    # ── concept nodes (knowledge graph) + doc/pdf → concept edges ──
    if knowledge and knowledge.get('concepts'):
        concept_ids = {}
        for c in knowledge['concepts']:
            nid = next_id
            next_id += 1
            concept_ids[c] = nid
            communities[nid] = concept_comm
            nodes.append({
                'id': nid, 'label': c, 'rel': 'concept:' + c,
                'category': 'concept', 'loc': 0, 'tier': 'app', 'kind': 'concept',
            })
        for rel, cs in (knowledge.get('doc_concepts') or {}).items():
            src = rel_to_id.get(rel)
            for c in cs:
                add_edge(src, concept_ids.get(c))

    return nodes, edges, communities
