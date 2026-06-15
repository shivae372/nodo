"""
Nodo test suite — zero-dependency (stdlib unittest), runs with either

    python -m unittest discover -s tests
    pytest tests/

Covers the reliability-critical behaviour: bundler-like import resolution
(including the cases that used to produce false orphans), corpus tiering, the
cross-file detectors and their anti-false-positive guards, symbol queries, doc
recall, robust test-file detection, and adversarial/edge-case inputs. The point
of this file is that "it works" is *checked*, not asserted in prose.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from nodo import scanner, detectors, crossfile, symbols  # noqa: E402
from nodo.query import explain_concept  # noqa: E402


def make_project(files):
    """Write {relpath: content} into a fresh temp dir; return its path."""
    d = tempfile.mkdtemp(prefix="nodo_test_")
    for rel, content in files.items():
        p = Path(d) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return d


def graph(files):
    d = make_project(files)
    nodes, edges, texts = scanner.build_graph(d)
    return d, nodes, edges, texts


def edge_pairs(nodes, edges):
    by_id = {n["id"]: n["rel"] for n in nodes}
    return {(by_id[e["source"]], by_id[e["target"]]) for e in edges}


def issue_types(nodes, edges, texts, **kw):
    return [i["type"] for i in detectors.detect_all(nodes, edges, texts, **kw)]


# ── Import resolution ─────────────────────────────────────────────────────────
class TestResolver(unittest.TestCase):
    def test_extensionless_cross_extension(self):
        # a .jsx importing './x' must resolve to x.js (the false-orphan bug)
        _, nodes, edges, _ = graph({
            "src/App.jsx": "import {x} from './x';\n",
            "src/x.js": "export const x = 1;\n",
        })
        self.assertIn(("src/App.jsx", "src/x.js"), edge_pairs(nodes, edges))

    def test_directory_index_import(self):
        _, nodes, edges, _ = graph({
            "src/App.jsx": "import {m} from './audio';\n",
            "src/audio/index.ts": "export const m = 1;\n",
        })
        self.assertIn(("src/App.jsx", "src/audio/index.ts"), edge_pairs(nodes, edges))

    def test_alias_at_root(self):
        _, nodes, edges, _ = graph({
            "src/components/App.tsx": "import {u} from '@/lib/util';\n",
            "src/lib/util.ts": "export const u = 1;\n",
        })
        self.assertIn(("src/components/App.tsx", "src/lib/util.ts"), edge_pairs(nodes, edges))

    def test_offbyone_relative_unique_suffix(self):
        # '../../lib/foo' is one level off, but the target is unique → resolve it
        # rather than emit a false orphan.
        _, nodes, edges, _ = graph({
            "src/app/components/View.jsx": "import {f} from '../../lib/GLRenderer';\n",
            "src/app/lib/GLRenderer.js": "export const f = 1;\n",
        })
        self.assertIn(("src/app/components/View.jsx", "src/app/lib/GLRenderer.js"),
                      edge_pairs(nodes, edges))

    def test_python_relative_import(self):
        _, nodes, edges, _ = graph({
            "pkg/__init__.py": "from .core import run\n",
            "pkg/core.py": "def run():\n    return 1\n",
        })
        self.assertIn(("pkg/__init__.py", "pkg/core.py"), edge_pairs(nodes, edges))

    def test_ambiguous_basename_makes_no_phantom_edge(self):
        # two files share a basename; an unresolvable import must NOT invent an edge
        d, nodes, edges, _ = graph({
            "a/shared.js": "export const s = 1;\n",
            "b/shared.js": "export const s = 2;\n",
            "c/main.js": "import {s} from './shared';\n",  # no c/shared.js
        })
        pairs = edge_pairs(nodes, edges)
        self.assertNotIn(("c/main.js", "a/shared.js"), pairs)
        self.assertNotIn(("c/main.js", "b/shared.js"), pairs)


# ── Corpus tiering ────────────────────────────────────────────────────────────
class TestTiering(unittest.TestCase):
    def test_reference_dirs_tagged_and_excluded(self):
        files = {
            "src/app.js": "export const a = 1;\n",
            "reference/vendor_lib/huge.js": "console.log('noise');\nconst r = Math.random();\n",
            "examples/demo.js": "console.log('demo');\n",
        }
        d, nodes, edges, texts = graph(files)
        tiers = {n["rel"]: n["tier"] for n in nodes}
        self.assertEqual(tiers["reference/vendor_lib/huge.js"], "reference")
        self.assertEqual(tiers["examples/demo.js"], "reference")
        self.assertEqual(tiers["src/app.js"], "app")
        # default run: no issue should come from reference-tier files
        issues = detectors.detect_all(nodes, edges, texts)
        self.assertFalse(any("reference/" in i.get("file", "") or "examples/" in i.get("file", "")
                             for i in issues))
        # opt-in: include-vendor surfaces them
        inc = detectors.detect_all(nodes, edges, texts, include_reference=True)
        self.assertTrue(any("Math.random" in i["type"] for i in inc))


# ── Cross-file contracts (true positive + false-positive guard) ───────────────
class TestContracts(unittest.TestCase):
    def test_true_broken_contract_flagged(self):
        types = issue_types(*graph({
            "src/api.ts": "export function getUser(id){return id;}\n",
            "src/consumer.ts": "import {getUserX} from './api';\nexport const w=()=>getUserX(1);\n",
            "src/index.ts": "import './consumer';\n",
        })[1:])
        self.assertTrue(any("not exported" in t for t in types))

    def test_export_star_barrel_not_falsely_flagged(self):
        # consumer imports doThing via a barrel that does `export * from './good'`
        types = issue_types(*graph({
            "src/good.ts": "export const doThing = (x)=>x*2;\n",
            "src/index.ts": "export * from './good';\n",
            "src/consumer.ts": "import {doThing} from './index';\nexport const r=()=>doThing(2);\n",
            "src/main.ts": "import './consumer';\n",
        })[1:])
        self.assertFalse(any("not exported" in t for t in types),
                         "export * barrel must suppress the broken-contract false positive")


# ── Cycles, disconnected features, platform gate ──────────────────────────────
class TestStructuralDetectors(unittest.TestCase):
    def test_import_cycle(self):
        types = issue_types(*graph({
            "a.js": "import {b} from './b';\nexport const a=()=>b;\n",
            "b.js": "import {a} from './a';\nexport const b=()=>a;\n",
        })[1:])
        self.assertTrue(any("Import cycle" in t for t in types))

    def test_disconnected_feature_flagged(self):
        body = "\n".join("export function f%d(){return %d;}" % (i, i) for i in range(6))
        types = issue_types(*graph({
            "src/main.ts": "export const boot=()=>1;\n",
            "src/features/Orphan.ts": body,  # 6 exports, nobody imports it
        })[1:])
        self.assertTrue(any("Disconnected feature" in t for t in types))

    def test_disconnected_does_not_flood(self):
        # many disconnected files → ratio gate suppresses (silence beats a flood)
        files = {"src/main.ts": "export const boot=()=>1;\n"}
        for i in range(20):
            files["src/loose/m%d.ts" % i] = "\n".join(
                "export function g%d_%d(){return %d;}" % (i, j, j) for j in range(5))
        types = issue_types(*graph(files)[1:])
        n_disc = sum("Disconnected feature" in t for t in types)
        self.assertLessEqual(n_disc, 8, "disconnected detector must cap / suppress floods")

    def test_platform_gated_dead_ui(self):
        types = issue_types(*graph({
            "src/main.ts": "import {P} from './Panel';\nexport const x=()=>P;\n",
            "src/Panel.jsx": ("export function P(){\n"
                              "  const a=()=>window.electronAPI?.open();\n"
                              "  const b=()=>window.electronAPI?.save();\n"
                              "  return {a,b};\n}\n"),
        })[1:])
        self.assertTrue(any("Platform-gated" in t for t in types))

    def test_per_type_cap_bounds_noise(self):
        # 60 files each with a console.log → no more than 25 individual findings,
        # plus a single "+N more" summary line. Noise can't dominate the report.
        files = {"src/main.js": "export const x=1;\n"}
        for i in range(60):
            files["src/m%d.js" % i] = "import {x} from './main';\nconsole.log(%d);\n" % i
        types = issue_types(*graph(files)[1:])
        n = sum(1 for t in types if t == "console.log left in code")
        self.assertLessEqual(n, 25, "per-type cap must bound a single detector")
        self.assertTrue(any("more" in t for t in types), "expected a capped-summary line")

    def test_platform_gate_with_fallback_not_flagged(self):
        types = issue_types(*graph({
            "src/main.ts": "import {P} from './Panel';\nexport const x=()=>P;\n",
            "src/Panel.jsx": ("export function P(){\n"
                              "  if (typeof window !== 'undefined') {}\n"
                              "  const a=()=>window.electronAPI?.open();\n"
                              "  const b=()=>window.electronAPI?.save() || fallback();\n"
                              "  return {a,b};\n}\n"),
        })[1:])
        self.assertFalse(any("Platform-gated" in t for t in types))


# ── Symbols + doc recall ──────────────────────────────────────────────────────
class TestSymbolsAndDocs(unittest.TestCase):
    def test_symbol_definition_and_references(self):
        d, nodes, edges, texts = graph({
            "src/engine.ts": "export class AudioEngine {}\n",
            "src/use.ts": "import {AudioEngine} from './engine';\nnew AudioEngine();\n",
        })
        out = symbols.query_symbol(nodes, texts, "AudioEngine")
        self.assertIsNotNone(out)
        self.assertIn("src/engine.ts", out)
        self.assertIn("src/use.ts", out)

    def test_symbol_zero_references(self):
        d, nodes, edges, texts = graph({
            "src/engine.ts": "export class Lonely {}\n",
            "src/other.ts": "export const x = 1;\n",
        })
        out = symbols.query_symbol(nodes, texts, "Lonely")
        self.assertIsNotNone(out)
        self.assertIn("0 files", out)

    def test_doc_recall_in_explain(self):
        d, nodes, edges, texts = graph({
            "src/audio.ts": "export const mix = 1;\n",
            "docs/spec.md": "# Audio features\nThe audio features live in the mixer.\n",
        })
        # write a map first so explain_concept can load context
        subprocess.run([sys.executable, "-m", "nodo", d],
                       cwd=str(REPO), capture_output=True)
        docs = scanner.discover_docs(d, scanner.DEFAULT_IGNORE_DIRS)
        out = explain_concept(str(Path(d) / ".nodo"), "audio features",
                              file_texts=texts, docs=docs)
        self.assertIn("docs/spec.md", out)


# ── Robust test-file detection (the express false-positive root cause) ────────
class TestTestDetection(unittest.TestCase):
    def test_top_level_test_dir_recognized(self):
        for rel in ("test/foo.js", "tests/bar.py", "spec/baz.rb",
                    "test_foo.py", "foo.test.ts", "src/__tests__/x.js"):
            self.assertTrue(crossfile._is_test(rel), "%s should be a test path" % rel)

    def test_non_test_not_flagged(self):
        for rel in ("src/latest.js", "src/contest.ts", "lib/attestation.py"):
            self.assertFalse(crossfile._is_test(rel), "%s is NOT a test path" % rel)

    def test_tests_excluded_from_disconnected(self):
        body = "\n".join("export function f%d(){return %d;}" % (i, i) for i in range(6))
        files = {"src/main.ts": "export const boot=()=>1;\n"}
        for i in range(6):
            files["test/case%d.test.ts" % i] = body  # standalone tests, nobody imports
        types = issue_types(*graph(files)[1:])
        self.assertFalse(any("Disconnected feature" in t for t in types),
                         "test files must not be flagged as disconnected features")


# ── Adversarial / edge cases (must never crash) ───────────────────────────────
class TestEdgeCases(unittest.TestCase):
    def test_empty_project(self):
        nodes, edges, texts = scanner.build_graph(tempfile.mkdtemp(prefix="nodo_empty_"))
        self.assertEqual(nodes, [])
        self.assertEqual(detectors.detect_all(nodes, edges, texts), [])

    def test_unicode_and_weird_names(self):
        _, nodes, edges, texts = graph({
            "src/café.js": "export const x=1;\n",
            "src/日本語.ts": "import {x} from './café';\n",
        })
        # should not raise; both files discovered
        self.assertEqual(len(nodes), 2)
        detectors.detect_all(nodes, edges, texts)  # must not raise

    def test_huge_file_skipped(self):
        d = make_project({"src/normal.js": "export const x=1;\n"})
        Path(d, "src/huge.js").write_text("//x\n" + ("a" * (600 * 1024)), encoding="utf-8")
        nodes, edges, texts = scanner.build_graph(d, max_file_kb=512)
        rels = {n["rel"] for n in nodes}
        self.assertIn("src/normal.js", rels)
        self.assertNotIn("src/huge.js", rels)  # over the size cap → skipped

    def test_malformed_source_does_not_crash(self):
        _, nodes, edges, texts = graph({
            "src/broken.ts": "import { from './x\nfunction (((\nexport const =\n",
            "src/x.ts": "export const x=1;\n",
        })
        detectors.detect_all(nodes, edges, texts)  # must not raise

    def test_self_import_no_edge(self):
        _, nodes, edges, _ = graph({"src/a.js": "import {a} from './a';\nexport const a=1;\n"})
        self.assertEqual(edges, [])  # a file never depends on itself


# ── End-to-end CLI ────────────────────────────────────────────────────────────
class TestEndToEnd(unittest.TestCase):
    def test_cli_writes_valid_artifacts(self):
        d = make_project({
            "src/main.ts": "import {h} from './helper';\nexport const boot=()=>h();\n",
            "src/helper.ts": "export const h=()=>1;\n",
        })
        r = subprocess.run([sys.executable, "-m", "nodo", d],
                           cwd=str(REPO), capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        ctx_path = Path(d) / ".nodo" / "nodo-context.json"
        self.assertTrue(ctx_path.exists())
        ctx = json.loads(ctx_path.read_text())
        self.assertEqual(len(ctx["files"]), 2)
        self.assertTrue(any(e for e in ctx["edges"]))
        for art in ("nodo.html", "nodo-context.md", "nodo-issues.txt"):
            self.assertTrue((Path(d) / ".nodo" / art).exists(), art)

    def test_query_symbol_cli(self):
        d = make_project({
            "src/engine.ts": "export class AudioEngine {}\n",
            "src/use.ts": "import {AudioEngine} from './engine';\nnew AudioEngine();\n",
        })
        r = subprocess.run([sys.executable, "-m", "nodo", d, "--query", "AudioEngine"],
                           cwd=str(REPO), capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("AudioEngine", r.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
