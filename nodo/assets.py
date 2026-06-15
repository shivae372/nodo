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

# File types worth converting to Markdown for token-cheap reading + knowledge
# ingestion. markitdown covers all of these on Python 3.10+; pypdf covers PDF
# everywhere. Plain-text formats are read directly.
CONVERTIBLE_EXTS = {
    '.pdf', '.docx', '.doc', '.pptx', '.ppt', '.xlsx', '.xls',
    '.html', '.htm', '.epub', '.csv', '.tsv', '.json', '.xml', '.rtf', '.odt',
    '.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp',
}


def markitdown_available():
    """True if Microsoft markitdown is importable (Python 3.10+). Never raises."""
    try:
        from markitdown import MarkItDown  # noqa: F401
        return True
    except Exception:
        return False


def convert_to_markdown(abs_path, max_chars=200000):
    """Convert a file to Markdown TEXT so an agent can read it cheaply instead of
    burning tokens on the raw binary (a PDF costs far more tokens than its text).

    Tries markitdown first (broad: PDF/Word/PowerPoint/Excel/HTML/images/…), then
    falls back to pypdf for PDFs, then to reading plain-text formats directly.
    Returns markdown text, or None. Never raises, never networks."""
    p = str(abs_path)
    ext = os.path.splitext(p)[1].lower()
    # 1) markitdown — the broad converter (optional, Python 3.10+)
    try:
        from markitdown import MarkItDown
        res = MarkItDown().convert(p)
        text = (getattr(res, 'text_content', '') or '').strip()
        if text:
            return text[:max_chars]
    except Exception:
        pass
    # 2) PDF fallback via pypdf (works everywhere)
    if ext == '.pdf':
        t = extract_pdf_text(abs_path, max_chars=max_chars)
        if t:
            return t
    # 3) plain-text-ish formats: read directly
    if ext in ('.html', '.htm', '.csv', '.tsv', '.json', '.xml'):
        try:
            t = Path(p).read_text(encoding='utf-8', errors='ignore').strip()
            return t[:max_chars] or None
        except Exception:
            return None
    return None


def convert_assets(root, out_dir, assets, doc_texts):
    """For each convertible asset: convert to Markdown, SAVE it to
    <out_dir>/converted/ (so Claude Code reads the cheap .md, not the raw file),
    PIN the converted path onto the asset, and FOLD its text into the knowledge
    corpus. Returns the number of assets converted. Mutates assets + doc_texts."""
    root, out_dir = Path(root), Path(out_dir)
    conv_dir = out_dir / 'converted'
    n = 0
    for a in assets:
        ext = os.path.splitext(a['rel'])[1].lower()
        if ext not in CONVERTIBLE_EXTS:
            continue
        flat = a['rel'].replace('\\', '/').replace('/', '__') + '.md'
        target = conv_dir / flat
        md = convert_to_markdown(str(root / a['rel']))
        if not md and target.exists():
            # No text conversion (e.g. an image/diagram), but a description was
            # written here by an agent's VISION — Claude Code reads the file and
            # saves what it sees. Preserve it and fold it into the knowledge graph:
            # this is how image/diagram understanding enters the graph, offline.
            # Quality gate: ignore too-short/vague descriptions (flag for re-run).
            try:
                vis = target.read_text(encoding='utf-8', errors='ignore').strip()
                md = vis if len(vis) >= 15 else None
            except Exception:
                md = None
        if not md:
            continue
        try:
            conv_dir.mkdir(parents=True, exist_ok=True)
            if not target.exists() or target.read_text(encoding='utf-8', errors='ignore') != md:
                target.write_text(md, encoding='utf-8')
            a['converted'] = 'converted/' + flat        # path under .nodo/ — read this, not the raw file
            a['converted_chars'] = len(md)
            doc_texts[a['rel']] = md                    # feed the knowledge graph
            n += 1
        except Exception:
            continue
    return n


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
