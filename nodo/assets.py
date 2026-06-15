"""
Multimodal pass — connect non-code assets (images, PDFs, video) to the graph.

Nodo's core stays no-network / zero-dependency, so it does NOT try to "understand"
an image or PDF with a model. Instead it does the part a deterministic tool can do
well: locate each asset and LINK it to the code/docs around it —

  - referenced_by : files whose text mentions the asset's filename (a real edge:
                    `<img src="diagram.png">`, `![](arch.pdf)`, `open("logo.svg")`)
  - dir_siblings  : source/doc files in the same directory (proximity link)

The emitted manifest (in nodo-context.json) tells the Claude skill exactly which
assets exist and which nodes they belong to; Claude then reads the image/PDF with
its own vision when the user asked for the multimodal pass. Optional local PDF
*text* extraction is available when `pypdf` is installed (still no network).
"""
import os
import re
from pathlib import Path


def extract_pdf_text(abs_path, max_chars=20000):
    """Best-effort local PDF text via optional `pypdf`/`PyPDF2`. Returns text or
    None if no extractor is installed or it fails. Never raises, never networks."""
    for mod in ('pypdf', 'PyPDF2'):
        try:
            m = __import__(mod)
            reader = m.PdfReader(abs_path)
            chunks = []
            for page in reader.pages:
                try:
                    chunks.append(page.extract_text() or '')
                except Exception:
                    continue
                if sum(len(c) for c in chunks) > max_chars:
                    break
            text = '\n'.join(chunks).strip()
            return text[:max_chars] if text else None
        except Exception:
            continue
    return None


def link_assets(root, assets, nodes, docs, include_reference=False):
    """Attach each asset to the nodes that reference it and its directory siblings.

    Returns [{rel, type, size, tier, referenced_by, dir_siblings, pdf_text?}].
    Reference-tier assets are dropped unless include_reference."""
    root = Path(root)
    # corpus to search for filename mentions: code + docs
    corpus = {}
    for n in nodes:
        corpus[n['rel']] = None  # text fetched lazily below from disk if needed
    code_rels = [n['rel'] for n in nodes]
    doc_rels = list(docs.keys())

    # Pre-read code/doc text once for mention scanning.
    text_by_rel = dict(docs)
    for n in nodes:
        rel = n['rel']
        if rel in text_by_rel:
            continue
        try:
            text_by_rel[rel] = (root / rel).read_text(encoding='utf-8', errors='ignore')
        except Exception:
            text_by_rel[rel] = ''

    out = []
    for a in assets:
        if a.get('tier') == 'reference' and not include_reference:
            continue
        rel = a['rel']
        fname = rel.split('/')[-1]
        adir = rel.rsplit('/', 1)[0] if '/' in rel else ''
        referenced_by = []
        for r, text in text_by_rel.items():
            if text and fname in text:
                referenced_by.append(r)
        siblings = [r for r in (code_rels + doc_rels)
                    if (r.rsplit('/', 1)[0] if '/' in r else '') == adir][:8]
        entry = {
            'rel': rel, 'type': a['type'], 'size': a['size'], 'tier': a.get('tier', 'app'),
            'referenced_by': sorted(set(referenced_by))[:10],
            'dir_siblings': siblings,
        }
        out.append(entry)
    return out


def attach_pdf_text(root, linked_assets):
    """For PDF assets, attach locally-extracted text when pypdf is available.
    Mutates and returns the list. Counts how many succeeded."""
    root = Path(root)
    n_ok = 0
    for a in linked_assets:
        if a['type'] == 'pdf':
            txt = extract_pdf_text(str(root / a['rel']))
            if txt:
                a['pdf_text_preview'] = txt[:1500]
                n_ok += 1
    return linked_assets, n_ok
