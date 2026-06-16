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

    def test_template_literal_dynamic_import(self):
        # await import(`./mod`) — static template literal must resolve to an edge
        _, nodes, edges, _ = graph({
            "src/a.js": "export async function f(){ const m = await import(`./mod`); return m; }\n",
            "src/mod.js": "export const x=1;\n",
        })
        self.assertIn(("src/a.js", "src/mod.js"), edge_pairs(nodes, edges))

    def test_export_star_as_namespace(self):
        _, nodes, edges, _ = graph({
            "src/a.ts": "export * as utils from './util';\n",
            "src/util.ts": "export const x=1;\n",
        })
        self.assertIn(("src/a.ts", "src/util.ts"), edge_pairs(nodes, edges))

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

    def test_missing_guard_ignores_commented_keyword(self):
        # 4 sibling routes; 3 call requireAuth in CODE, health only mentions it in a
        # COMMENT → health must still be flagged as the missing-guard outlier.
        files = {"src/app.ts": "export const x=1\n",
                 "src/api/guard.ts": "export const requireAuth=(q)=>!!q;\n"}
        for r in ("users", "orders", "admin"):
            files["src/api/routes/%s.ts" % r] = (
                "import {requireAuth} from '../guard';\n"
                "export const %s=(q)=>requireAuth(q);\n" % r)
        files["src/api/routes/health.ts"] = (
            "// TODO: add requireAuth to this route\n"
            "export const health=(q)=>({ok:1});\n")
        types = issue_types(*graph(files)[1:])
        self.assertTrue(any("Missing" in t for t in types),
                        "a guard named only in a comment must not satisfy the check")

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
        self.assertEqual(out.count("docs/spec.md"), 1, "doc must not be double-listed")


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


# ── Unified graph: docs + assets as connected nodes ───────────────────────────
class TestGraphIntegration(unittest.TestCase):
    def test_doc_links_to_module_by_stem(self):
        from nodo import graphmerge
        d, nodes, edges, _ = graph({
            "src/AudioEngine.ts": "export class AudioEngine {}\n",
            "src/main.ts": "export const x=1\n",
            "docs/spec.md": "# Audio\nThe AudioEngine module implements the features.\n",
        })
        docs = scanner.discover_docs(d, scanner.DEFAULT_IGNORE_DIRS)
        comms = {n["id"]: 0 for n in nodes}
        unodes, uedges, _ = graphmerge.integrate(nodes, edges, comms, docs, [], d)
        rel2id = {n["rel"]: n["id"] for n in unodes}
        kinds = {n["rel"]: n["kind"] for n in unodes}
        self.assertEqual(kinds.get("docs/spec.md"), "doc")
        ref = {(e["source"], e["target"]) for e in uedges if e.get("kind") == "reference"}
        self.assertIn((rel2id["docs/spec.md"], rel2id["src/AudioEngine.ts"]), ref,
                      "a doc naming the module should link to it")

    def test_asset_becomes_connected_node(self):
        from nodo import graphmerge, assets as assetmod
        d, nodes, edges, _ = graph({
            "docs/readme.md": "![logo](img/logo.png)\n",
            "src/ui.ts": "export const x=1\n",
        })
        os.makedirs(os.path.join(d, "docs/img"), exist_ok=True)
        Path(d, "docs/img/logo.png").write_bytes(b"PNG")
        raw = scanner.discover_assets(d, scanner.DEFAULT_IGNORE_DIRS)
        docs = scanner.discover_docs(d, scanner.DEFAULT_IGNORE_DIRS)
        linked = assetmod.link_assets(d, raw, nodes, docs)
        comms = {n["id"]: 0 for n in nodes}
        unodes, uedges, _ = graphmerge.integrate(nodes, edges, comms, docs, linked, d)
        rel2id = {n["rel"]: n["id"] for n in unodes}
        kinds = {n["rel"]: n["kind"] for n in unodes}
        self.assertEqual(kinds.get("docs/img/logo.png"), "asset")
        targets = {e["target"] for e in uedges if e.get("kind") == "reference"}
        self.assertIn(rel2id["docs/img/logo.png"], targets, "asset should be referenced")

    def test_query_blast_radius_excludes_reference_edges(self):
        d = make_project({
            "src/Engine.ts": "export class Engine {}\n",
            "src/use.ts": "import {Engine} from './Engine';\nnew Engine();\n",
            "docs/spec.md": "The Engine module is documented here.\n",
        })
        subprocess.run([sys.executable, "-m", "nodo", d], cwd=str(REPO), capture_output=True)
        r = subprocess.run([sys.executable, "-m", "nodo", d, "--query", "src/Engine.ts"],
                           cwd=str(REPO), capture_output=True, text=True)
        self.assertIn("use.ts", r.stdout)               # real import dependent
        self.assertNotIn("spec.md", r.stdout)           # doc reference must NOT count


# ── Incremental cache, diagnostics, new detectors ────────────────────────────
class TestCacheAndDiagnostics(unittest.TestCase):
    def test_parse_cache_reused_and_identical(self):
        d = make_project({"a.js": "import {b} from './b';\n", "b.js": "export const b=1;\n"})
        cache, diag = {}, {}
        n1, e1, _ = scanner.build_graph(d, cache=cache, diagnostics=diag)
        self.assertGreater(diag.get("parsed", 0), 0)
        self.assertTrue(cache)                       # cache populated
        diag2 = {}
        n2, e2, _ = scanner.build_graph(d, cache=cache, diagnostics=diag2)
        self.assertEqual(diag2.get("parsed", 0), 0)  # nothing re-parsed
        self.assertEqual(diag2.get("cache_hits", 0), len(n2))
        self.assertEqual(edge_pairs(n1, e1), edge_pairs(n2, e2))  # identical result

    def test_cache_invalidates_on_change(self):
        d = make_project({"a.js": "import {b} from './b';\n", "b.js": "export const b=1;\n"})
        cache = {}
        scanner.build_graph(d, cache=cache)
        Path(d, "a.js").write_text("import {b} from './b';\nimport {c} from './c';\n")
        Path(d, "c.js").write_text("export const c=1;\n")
        n, e, _ = scanner.build_graph(d, cache=cache)
        self.assertIn(("a.js", "c.js"), edge_pairs(n, e))  # change picked up

    def test_oversized_file_reported_not_silent(self):
        d = make_project({"ok.js": "export const x=1;\n"})
        Path(d, "big.js").write_text("//\n" + ("a" * (600 * 1024)))
        diag = {}
        scanner.build_graph(d, max_file_kb=512, diagnostics=diag)
        self.assertIn("big.js", diag.get("skipped_large", []))


class TestKnowledgeGraph(unittest.TestCase):
    def test_topics_cluster_related_docs(self):
        from nodo.knowledge import build_knowledge
        k = build_knowledge({
            "a.md": "jwt token session login logout token session jwt",
            "b.md": "session token jwt login verify token session jwt",
            "c.md": "stripe charge refund invoice stripe charge subscription",
            "d.md": "charge refund stripe invoice billing charge stripe",
        })

        def topic_of(doc):
            return next((t for t in k["topics"] if any(doc in x for x in t["docs"])), None)
        ta, tc = topic_of("a.md"), topic_of("c.md")
        self.assertIsNotNone(ta)
        self.assertIsNotNone(tc)
        self.assertNotEqual(ta["id"], tc["id"], "auth and payments should be different topics")
        self.assertTrue(any("b.md" in x for x in ta["docs"]), "a.md & b.md should co-cluster")
        self.assertTrue(any("d.md" in x for x in tc["docs"]), "c.md & d.md should co-cluster")

    def test_concept_nodes_added_to_graph(self):
        from nodo import graphmerge, knowledge
        d, nodes, edges, _ = graph({"src/a.ts": "export const x=1\n"})
        docs = {"docs/x.md": "alpha beta gamma alpha beta gamma",
                "docs/y.md": "beta gamma delta beta gamma delta"}
        know = knowledge.build_knowledge(docs)
        comms = {n["id"]: 0 for n in nodes}
        un, ue, _ = graphmerge.integrate(nodes, edges, comms, docs, [], ".", knowledge=know)
        self.assertIn("concept", {n["kind"] for n in un})
        concept_ids = {n["id"] for n in un if n["kind"] == "concept"}
        self.assertTrue(any(e.get("kind") == "reference" and e["target"] in concept_ids for e in ue),
                        "a doc should link to a concept node")

    def test_empty_knowledge_no_docs(self):
        from nodo.knowledge import build_knowledge
        self.assertEqual(build_knowledge({})["topics"], [])

    def test_god_nodes(self):
        from nodo.knowledge import build_knowledge
        k = build_knowledge({
            "a.md": "token session login token session",
            "b.md": "token session jwt token session",
            "c.md": "token auth token session profile",
        })
        gods = {g["concept"] for g in k["god_nodes"]}
        self.assertIn("token", gods)   # appears in all 3 docs → most-connected


class TestInstall(unittest.TestCase):
    def test_install_agents_writes_files_idempotently(self):
        from nodo import hookinstall
        d = make_project({"src/a.ts": "export const x=1\n"})
        launcher = os.path.join(d, "nodo.py")
        hookinstall.install_agents(d, launcher)
        agents = Path(d) / "AGENTS.md"
        rule = Path(d) / ".cursor" / "rules" / "nodo.mdc"
        self.assertTrue(agents.exists() and rule.exists())
        self.assertIn("Nodo", agents.read_text())
        self.assertIn("alwaysApply", rule.read_text())
        hookinstall.install_agents(d, launcher)           # run again
        self.assertEqual(agents.read_text().count("<!-- nodo:start -->"), 1)  # no duplication

    def test_topics_cli(self):
        d = make_project({
            "src/a.ts": "export const x=1\n",
            "docs/auth.md": "jwt token session login token session jwt login",
            "docs/auth2.md": "token session jwt login verify token session jwt",
        })
        r = subprocess.run([sys.executable, "-m", "nodo", d, "--topics"],
                           cwd=str(REPO), capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Knowledge topics", r.stdout)


class TestConversion(unittest.TestCase):
    def test_convert_to_markdown_plain_text(self):
        from nodo import assets as A
        d = make_project({"x.html": "<h1>Title</h1><p>Hello world quarterly report</p>"})
        md = A.convert_to_markdown(os.path.join(d, "x.html"))
        self.assertIsNotNone(md)
        self.assertIn("Hello world", md)

    def test_convert_assets_saves_pins_and_ingests(self):
        # a convertible asset → saved .md under .nodo/converted/, pinned on the
        # asset, and its text folded into the knowledge corpus (token-cheap path).
        from nodo import scanner, assets as A
        d = make_project({
            "docs/readme.md": "the data table is attached\n",
            "docs/data.csv": "name,role\nalice,admin\nbob,user\n",
        })
        out = Path(d) / ".nodo"
        out.mkdir(exist_ok=True)
        nodes, _, _ = scanner.build_graph(d)
        docs = scanner.discover_docs(d, scanner.DEFAULT_IGNORE_DIRS)
        raw = scanner.discover_assets(d, scanner.DEFAULT_IGNORE_DIRS)
        linked = A.link_assets(d, raw, nodes, docs)
        doc_texts = dict(docs)
        n = A.convert_assets(d, out, linked, doc_texts)
        self.assertGreaterEqual(n, 1)
        csv = next(a for a in linked if a["rel"].endswith("data.csv"))
        self.assertIn("converted", csv)
        self.assertTrue((out / csv["converted"]).exists())
        self.assertIn("docs/data.csv", doc_texts)
        self.assertIn("alice", doc_texts["docs/data.csv"])


class TestVisionLoop(unittest.TestCase):
    def test_agent_vision_description_is_ingested(self):
        # An image can't be text-converted, but if an agent's vision wrote a
        # description into .nodo/converted/, nodo preserves it and folds it into
        # the knowledge corpus (image understanding entering the graph, offline).
        from nodo import scanner, assets as A
        d = make_project({"docs/readme.md": "see the diagram for the flow\n"})
        Path(d, "docs/diagram.png").write_bytes(b"\x89PNG\r\n fake")
        out = Path(d) / ".nodo"
        (out / "converted").mkdir(parents=True, exist_ok=True)
        (out / "converted" / "docs__diagram.png.md").write_text(
            "Architecture diagram: the auth service calls the database layer.")
        nodes, _, _ = scanner.build_graph(d)
        docs = scanner.discover_docs(d, scanner.DEFAULT_IGNORE_DIRS)
        raw = scanner.discover_assets(d, scanner.DEFAULT_IGNORE_DIRS)
        linked = A.link_assets(d, raw, nodes, docs)
        doc_texts = dict(docs)
        A.convert_assets(d, out, linked, doc_texts)
        self.assertIn("docs/diagram.png", doc_texts)
        self.assertIn("auth service", doc_texts["docs/diagram.png"])

    def test_short_vision_description_rejected(self):
        # quality gate: a too-short/vague description is NOT ingested
        from nodo import scanner, assets as A
        d = make_project({"docs/readme.md": "see image\n"})
        Path(d, "docs/i.png").write_bytes(b"PNG")
        out = Path(d) / ".nodo"
        (out / "converted").mkdir(parents=True, exist_ok=True)
        (out / "converted" / "docs__i.png.md").write_text("img")   # too short
        nodes, _, _ = scanner.build_graph(d)
        docs = scanner.discover_docs(d, scanner.DEFAULT_IGNORE_DIRS)
        raw = scanner.discover_assets(d, scanner.DEFAULT_IGNORE_DIRS)
        linked = A.link_assets(d, raw, nodes, docs)
        doc_texts = dict(docs)
        A.convert_assets(d, out, linked, doc_texts)
        self.assertNotIn("docs/i.png", doc_texts)

    def test_ask_describe_returns_pinned_vision(self):
        d = make_project({"src/app.js": "export const a=1;\n", "docs/readme.md": "see the diagram\n"})
        Path(d, "docs/arch.png").write_bytes(b"PNGDATA")
        subprocess.run([sys.executable, "-m", "nodo", d, "--full"], cwd=str(REPO), capture_output=True)
        conv = Path(d) / ".nodo" / "converted"
        conv.mkdir(parents=True, exist_ok=True)
        (conv / "docs__arch.png.md").write_text(
            "Architecture diagram showing the auth service calling the database layer.")
        subprocess.run([sys.executable, "-m", "nodo", d, "--full"], cwd=str(REPO), capture_output=True)
        r = subprocess.run([sys.executable, "-m", "nodo", d, "--ask", "describe the architecture diagram"],
                           cwd=str(REPO), capture_output=True, text=True)
        self.assertIn("auth service", r.stdout)


class TestConfidence(unittest.TestCase):
    def test_every_issue_has_confidence(self):
        issues = detectors.detect_all(*graph({
            "a.js": "import {b} from './b';\nconsole.log(1);\n",
            "b.js": "import {a} from './a';\nexport const b=1;\n",
        })[1:])
        self.assertTrue(issues)
        for i in issues:
            self.assertIn(i.get("confidence"), ("high", "medium", "low"))

    def test_confidence_levels_make_sense(self):
        # cycle = high (structural fact); console.log = low (noisy hint)
        issues = detectors.detect_all(*graph({
            "a.js": "import {b} from './b';\nconsole.log('x');\n",
            "b.js": "import {a} from './a';\nexport const b=1;\n",
        })[1:])
        by_type = {i["type"]: i["confidence"] for i in issues}
        self.assertEqual(by_type.get("Import cycle"), "high")
        self.assertEqual(by_type.get("console.log left in code"), "low")


class TestNewDetectors(unittest.TestCase):
    def test_high_complexity_flagged(self):
        body = "export function big(){\n" + "".join(
            "  if (x%d) doit();\n" % i for i in range(70)) + "}\n"
        types = issue_types(*graph({"src/big.ts": body,
                                    "src/main.ts": "import './big';\n"})[1:])
        self.assertTrue(any("complexity" in t.lower() for t in types))

    def test_sql_injection_flagged(self):
        types = issue_types(*graph({
            "src/db.js": "export function q(id){ return run('SELECT * FROM users WHERE id=' + id); }\n",
            "src/main.js": "import './db';\n",
        })[1:])
        self.assertTrue(any("SQL injection" in t for t in types))

    def test_unsafe_deserialization_flagged(self):
        types = issue_types(*graph({
            "src/u.py": "import pickle\ndef load(b):\n    return pickle.loads(b)\n",
            "src/main.py": "from .u import load\n",
        })[1:])
        self.assertTrue(any("deserialization" in t.lower() for t in types))

    def test_duplication_detects_literal_only_differences(self):
        def block(v, n):
            return ("export function f%s(){\n  const url = '%s';\n  const max = %d;\n"
                    "  log(url);\n  send(url, max);\n  retry(url);\n  finish(url);\n}\n"
                    % (v, v, n))
        types = issue_types(*graph({
            "src/a.ts": block("alpha", 3),
            "src/b.ts": block("betaval", 9),
            "src/main.ts": "import './a';\nimport './b';\n",
        })[1:])
        self.assertTrue(any("Duplicat" in t for t in types),
                        "blocks differing only in literals should still be duplication")


# ── AST-backed arg-count contract (only runs in AST mode) ─────────────────────
class TestArgMismatchAST(unittest.TestCase):
    def _types(self, files):
        from nodo import ast_index
        if not ast_index.available():
            self.skipTest("tree-sitter not installed")
        scanner.enable_ast()
        try:
            d, nodes, edges, texts = graph(files)
            return [i["type"] for i in detectors.detect_all(nodes, edges, texts)]
        finally:
            scanner._USE_AST = False

    def test_too_many_args_flagged(self):
        types = self._types({
            "src/lib.ts": "export function add(a, b){ return a + b; }\n",
            "src/use.ts": "import {add} from './lib';\nexport const r = () => add(1, 2, 3);\n",
            "src/main.ts": "import './use';\n",
        })
        self.assertTrue(any("Call passes" in t for t in types))

    def test_rest_param_not_flagged(self):
        types = self._types({
            "src/lib.ts": "export function logme(...xs){ return xs; }\n",
            "src/use.ts": "import {logme} from './lib';\nexport const r = () => logme(1,2,3,4,5);\n",
            "src/main.ts": "import './use';\n",
        })
        self.assertFalse(any("Call passes" in t for t in types))

    def test_optional_param_not_flagged(self):
        types = self._types({
            "src/lib.ts": "export function greet(a, b?){ return a; }\n",
            "src/use.ts": "import {greet} from './lib';\nexport const r = () => greet(1, 2);\n",
            "src/main.ts": "import './use';\n",
        })
        self.assertFalse(any("Call passes" in t for t in types))

    def test_method_call_not_flagged(self):
        types = self._types({
            "src/lib.ts": "export function add(a, b){ return a + b; }\n",
            "src/use.ts": "import {add} from './lib';\nexport const r = (o) => o.add(1,2,3,4);\n",
            "src/main.ts": "import './use';\n",
        })
        self.assertFalse(any("Call passes" in t for t in types))

    def test_comment_between_args_not_miscounted(self):
        # a comment line inside the call must not be counted as an argument
        types = self._types({
            "src/lib.ts": "export function add(a, b){ return a + b; }\n",
            "src/use.ts": ("import {add} from './lib';\n"
                           "export const r = () => add(\n  1, // first\n  2\n);\n"),
            "src/main.ts": "import './use';\n",
        })
        self.assertFalse(any("Call passes" in t for t in types))

    def test_disabled_in_regex_mode(self):
        # regex mode must NOT run arg-mismatch (regex arg-counting is unsafe)
        d, nodes, edges, texts = graph({
            "src/lib.ts": "export function add(a, b){ return a + b; }\n",
            "src/use.ts": "import {add} from './lib';\nexport const r = () => add(1, 2, 3);\n",
            "src/main.ts": "import './use';\n",
        })
        types = [i["type"] for i in detectors.detect_all(nodes, edges, texts)]  # _USE_AST False
        self.assertFalse(any("Call passes" in t for t in types))


# ── Optional tree-sitter AST backend (skips if not installed) ─────────────────
class TestAST(unittest.TestCase):
    def test_ast_extracts_and_ignores_normal_calls(self):
        from nodo import ast_index
        if not ast_index.available():
            self.skipTest("tree-sitter not installed")
        imps = ast_index.extract_imports_ast(
            "a.js", "import {x} from './x';\nconst y=require('./y');\nfoo('z');\nimport('./w');\n")
        self.assertIn("./x", imps)
        self.assertIn("./y", imps)
        self.assertIn("./w", imps)
        self.assertNotIn("z", imps)   # a normal call is not an import

    def test_ast_mode_resolves_same_edges(self):
        from nodo import ast_index
        if not ast_index.available():
            self.skipTest("tree-sitter not installed")
        scanner.enable_ast()
        try:
            _, nodes, edges, _ = graph({
                "a.js": "import {b} from './b';\n",
                "b.js": "export const b = 1;\n",
            })
            self.assertIn(("a.js", "b.js"), edge_pairs(nodes, edges))
        finally:
            scanner._USE_AST = False

    def test_ast_defs_extraction(self):
        from nodo import ast_index
        if not ast_index.available():
            self.skipTest("tree-sitter not installed")
        defs = ast_index.extract_defs_ast(
            "a.ts", "export class AudioEngine {}\nexport const make = () => 1;\nconst LOCAL = 5;\n")
        names = {n for n, _ in defs}
        self.assertIn("AudioEngine", names)
        self.assertIn("make", names)
        self.assertNotIn("LOCAL", names)  # non-function const is not a "definition"

    def test_ast_defs_many_languages(self):
        # grammar-agnostic AST symbol extraction across the mainstream language set
        from nodo import ast_index
        if not ast_index.available():
            self.skipTest("tree-sitter not installed")
        cases = [
            ("a.go", "package m\nfunc Hello(x int) int { return x }\n", "Hello", "x"),
            ("a.rs", "pub fn run(a: i32) -> i32 { a }\nstruct S;\n", "run", None),
            ("a.java", "class C { void doIt(int a){ go(); } }\n", "doIt", "go"),
            ("a.c", "int add(int a, int b){ return a+b; }\n", "add", "a"),
            ("a.cpp", "class K{}; int g(int x){ return x; }\n", "g", "x"),
            ("a.cs", "namespace N{ class C{ void M(){} } }\n", "M", None),
            ("a.rb", "def bar(a); a; end\n", "bar", None),
            ("a.php", "<?php\nfunction h($a){ return $a; }\n", "h", None),
            ("a.kt", "fun hello(x: Int): Int { return x }\n", "hello", None),
            ("a.swift", "func hello(x: Int) -> Int { x }\n", "hello", None),
            ("a.scala", "def hello(x: Int) = x\n", "hello", None),
            ("a.dart", "int hello(int x) => x;\n", "hello", None),
            ("a.lua", "local function hello() end\n", "hello", None),
        ]
        for f, code, must, mustnot in cases:
            names = {n for n, _ in (ast_index.extract_defs_ast(f, code) or [])}
            self.assertIn(must, names, "%s: expected %r in %s" % (f, must, names))
            if mustnot:
                self.assertNotIn(mustnot, names, "%s: %r (a call/param) wrongly captured" % (f, mustnot))


# ── Determinism (hash-seed independence) ──────────────────────────────────────
class TestDeterminism(unittest.TestCase):
    def test_output_is_hash_seed_independent(self):
        # The full graph + knowledge must be byte-identical regardless of Python's
        # per-process hash seed (sets/dicts must never leak iteration order).
        d = make_project({
            "src/a.ts": "import {b} from './b';\nexport const a = () => b;\n",
            "src/b.ts": "export const b = 1;\n",
            "docs/x.md": "token session login jwt token session auth login token",
            "docs/y.md": "session token jwt login verify session token auth jwt",
            "docs/z.md": "charge stripe refund invoice charge stripe billing refund",
        })

        def run(seed):
            env = dict(os.environ, PYTHONHASHSEED=str(seed))
            subprocess.run([sys.executable, "-m", "nodo", d], cwd=str(REPO),
                           capture_output=True, env=env)
            c = json.loads((Path(d) / ".nodo" / "nodo-context.json").read_text())
            return json.dumps([c["files"], c["edges"], c["issues"], c["knowledge"]],
                              sort_keys=True)
        a, b, c = run(0), run(1), run(13)
        self.assertEqual(a, b)
        self.assertEqual(b, c)


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

    def test_query_change_impact_transitive(self):
        # a → b → c : changing c transitively affects both a and b
        d = make_project({
            "src/c.ts": "export const c = 1;\n",
            "src/b.ts": "import {c} from './c';\nexport const b = () => c;\n",
            "src/a.ts": "import {b} from './b';\nexport const a = () => b;\n",
        })
        r = subprocess.run([sys.executable, "-m", "nodo", d, "--query", "src/c.ts"],
                           cwd=str(REPO), capture_output=True, text=True)
        self.assertIn("CHANGE IMPACT", r.stdout)
        self.assertIn("2 file(s) transitively", r.stdout)

    def test_query_symbol_cli(self):
        d = make_project({
            "src/engine.ts": "export class AudioEngine {}\n",
            "src/use.ts": "import {AudioEngine} from './engine';\nnew AudioEngine();\n",
        })
        r = subprocess.run([sys.executable, "-m", "nodo", d, "--query", "AudioEngine"],
                           cwd=str(REPO), capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("AudioEngine", r.stdout)


# ── --ask natural-language router ─────────────────────────────────────────────
class TestAsk(unittest.TestCase):
    PROJECT = {
        "src/db.ts": "export function query(s){ return s; }\nexport function connect(){ return 1; }\n"
                     "export function add(a, b){ return a + b; }\n",
        "src/service.ts": "import {query} from './db';\nexport function getUser(id){ return query('x'); }\n",
        "src/api.ts": "import {getUser} from './service';\nexport const route = () => getUser(1);\n",
        "src/main.ts": "import './api';\nconsole.log('boot');\n",
    }

    def _ask(self, question):
        from nodo import scanner, ask
        d = make_project(self.PROJECT)
        subprocess.run([sys.executable, "-m", "nodo", d], cwd=str(REPO), capture_output=True)
        nodes, edges, texts = scanner.build_graph(d)
        docs = scanner.discover_docs(d, scanner.DEFAULT_IGNORE_DIRS)
        return ask.answer(question, nodes, edges, texts, str(Path(d) / ".nodo"), docs=docs)

    def test_blast_radius(self):
        out = self._ask("what breaks if I change src/db.ts")
        self.assertIn("blast radius", out)
        self.assertIn("DEPENDENTS", out)

    def test_path(self):
        out = self._ask("how does api connect to db")
        self.assertIn("reaches", out)

    def test_symbol(self):
        out = self._ask("who uses getUser")
        self.assertIn("SYMBOL", out)

    def test_issues(self):
        out = self._ask("what issues or bugs are here")
        self.assertIn("issue", out.lower())

    def test_hubs(self):
        out = self._ask("what are the key files")
        self.assertIn("hub", out.lower())

    def test_overview(self):
        out = self._ask("what does this project do")
        self.assertIn("overview", out)
        self.assertIn("code files", out)

    def test_biggest_files(self):
        out = self._ask("what is the largest file")
        self.assertIn("Largest files", out)

    def test_common_verb_not_treated_as_symbol(self):
        # db.ts defines add(); "how do I add a route" must NOT route to symbol `add`
        out = self._ask("how do I add a route")
        self.assertNotIn("symbol: add", out)

    def test_fallback_menu(self):
        out = self._ask("zzzqqq nonsense")
        self.assertIn("I can answer", out)


# ── personalization: changed-since-last-scan + query log ──────────────────────
class TestPersonalization(unittest.TestCase):
    def test_changed_since_last_scan(self):
        d = make_project({"src/a.ts": "export const a=1;\n", "src/b.ts": "export const b=2;\n"})
        subprocess.run([sys.executable, "-m", "nodo", d], cwd=str(REPO), capture_output=True)
        Path(d, "src/a.ts").write_text("export const a=1;\nexport const c=3;\n")
        subprocess.run([sys.executable, "-m", "nodo", d], cwd=str(REPO), capture_output=True)
        ctx = json.loads((Path(d) / ".nodo" / "nodo-context.json").read_text())
        self.assertIn("src/a.ts", ctx["diagnostics"].get("changed", []))
        self.assertNotIn("src/b.ts", ctx["diagnostics"].get("changed", []))

    def test_querylog_frequent_files(self):
        from nodo import querylog
        d = make_project({"src/db.ts": "export const x=1;\n"})
        out = Path(d) / ".nodo"
        out.mkdir(parents=True, exist_ok=True)
        for _ in range(3):
            querylog.record(out, "query", "src/db.ts")
        freq = dict(querylog.frequent_files(out, ["src/db.ts"]))
        self.assertGreaterEqual(freq.get("src/db.ts", 0), 3)


# ── MCP server (tool logic testable without the mcp package) ──────────────────
class TestMCPServer(unittest.TestCase):
    def _state(self):
        from nodo import serve
        d = make_project({
            "src/db.ts": "export function query(s){ return s; }\nexport function connect(){ return 1; }\n",
            "src/svc.ts": "import {query} from './db';\nexport function getUser(id){ return query('x'); }\n",
            "src/api.ts": "import {getUser} from './svc';\nexport const route = () => getUser(1);\n",
            "src/main.ts": "import './api';\nconsole.log('boot');\n",
        })
        return serve._State(d)

    def test_tools_dispatch(self):
        from nodo import serve
        st = self._state()
        self.assertIn("DEPENDENTS", serve.dispatch(st, "nodo_blast_radius", {"file": "src/db.ts"}))
        self.assertIn("getUser", serve.dispatch(st, "nodo_who_uses", {"symbol": "getUser"}))
        self.assertIn("reaches", serve.dispatch(st, "nodo_path", {"a": "src/svc.ts", "b": "src/db.ts"}))
        self.assertIn("overview", serve.dispatch(st, "nodo_ask", {"question": "what does this do"}).lower())
        self.assertIsInstance(serve.dispatch(st, "nodo_overview", {}), str)
        self.assertIn("Unknown tool", serve.dispatch(st, "bogus", {}))
        self.assertEqual(len(serve.tool_specs()), 19)
        names = {s["name"] for s in serve.tool_specs()}
        for nm in ("nodo_self_check", "nodo_teach", "nodo_fix_context", "nodo_changed",
                   "nodo_calls", "nodo_surprises", "nodo_what_if", "nodo_symbols",
                   "nodo_vibe_summary"):
            self.assertIn(nm, names)
        self.assertIsInstance(serve.dispatch(st, "nodo_changed", {}), str)
        fx = serve.dispatch(st, "nodo_fix_context", {"file": "src/main.ts"})
        self.assertIn("<context", fx)        # emits the evidence prompt (main.ts has a console.log)

    def test_serve_without_mcp_is_graceful(self):
        from nodo import serve
        try:
            import mcp  # noqa: F401
            self.skipTest("mcp is installed in this env")
        except Exception:
            pass
        self.assertEqual(serve.serve(make_project({"a.py": "x=1\n"})), 1)

    def test_install_mcp_registers_server(self):
        from nodo import hookinstall
        d = make_project({"src/a.ts": "export const x=1;\n"})
        hookinstall.install_mcp(d, os.path.join(d, "nodo.py"))
        cfg = json.loads((Path(d) / ".mcp.json").read_text())
        self.assertIn("nodo", cfg["mcpServers"])
        self.assertIn("--mcp", cfg["mcpServers"]["nodo"]["args"])


# ── Self-healing & self-learning (Claude tutors nodo; lessons persist) ────────
class TestSelfHealing(unittest.TestCase):
    def setUp(self):
        self._ast = scanner._USE_AST
        scanner._USE_AST = False
        scanner.disable_lessons()

    def tearDown(self):
        scanner._USE_AST = self._ast
        scanner.disable_lessons()

    TOY = {
        "src/main.toy": 'import "./util.toy"\nfn greet(n){ return hello(n) }\nfn main(){ greet("w") }\n',
        "src/util.toy": 'fn hello(n){ return n }\n',
    }
    TOY_LESSON = {"languages": {"toy": {
        "extensions": [".toy"],
        "category": "lib",
        "def_patterns": [r"\bfn\s+([A-Za-z_]\w*)"],
        "import_patterns": [r'import\s+"([^"]+)"'],
    }}}

    def test_unknown_language_detected_and_blind(self):
        from nodo import lessons, health
        d = make_project(dict(self.TOY, **{"readme.md": "# hi\n"}))
        nodes, edges, texts = scanner.build_graph(d)
        self.assertEqual(len(nodes), 0)   # nodo is blind to .toy with no lesson
        hc = health.self_check(d, nodes, edges, texts, lessons.empty(),
                               set(scanner.DEFAULT_IGNORE_DIRS) | {".nodo"})
        kinds = {g["kind"] for g in hc["gaps"]}
        self.assertIn("unknown_language", kinds)
        self.assertIn(".toy", {g.get("ext") for g in hc["gaps"]})
        self.assertIsNotNone(hc["teach_template"])

    def test_teach_makes_language_first_class(self):
        from nodo import lessons
        d = make_project(self.TOY)
        out = Path(d) / ".nodo"
        ok, errs, summ = lessons.merge_lessons(out, self.TOY_LESSON)
        self.assertTrue(ok, errs)
        self.assertEqual(summ["languages_added"], ["toy"])
        scanner.enable_lessons(lessons.load_lessons(out))
        nodes, edges, texts = scanner.build_graph(d)
        self.assertEqual({n["rel"] for n in nodes}, {"src/main.toy", "src/util.toy"})
        self.assertEqual(len(edges), 1)                     # import "./util.toy" resolved
        self.assertIn("greet", symbols.query_symbol(nodes, texts, "greet"))  # symbol now visible

    def test_self_check_clears_after_teaching(self):
        from nodo import lessons, health
        d = make_project(self.TOY)
        out = Path(d) / ".nodo"
        lessons.merge_lessons(out, self.TOY_LESSON)
        scanner.enable_lessons(lessons.load_lessons(out))
        nodes, edges, texts = scanner.build_graph(d)
        hc = health.self_check(d, nodes, edges, texts, lessons.load_lessons(out),
                               set(scanner.DEFAULT_IGNORE_DIRS) | {".nodo"})
        self.assertFalse(any(g["kind"] == "unknown_language" for g in hc["gaps"]))

    def test_lesson_round_trips_and_reteach_updates(self):
        from nodo import lessons
        d = make_project({"a.py": "x=1\n"})
        out = Path(d) / ".nodo"
        L = {"languages": {"zig": {"extensions": [".zig"], "def_patterns": [r"\bfn\s+(\w+)"]}},
             "keep_alive": ["src/Foo.ts"], "resolver_hints": {"@app/x": "src/x.ts"}}
        ok, _, _ = lessons.merge_lessons(out, L)
        self.assertTrue(ok)
        r = lessons.load_lessons(out)
        self.assertIn(".zig", lessons.taught_extensions(r))
        self.assertIn("src/Foo.ts", lessons.keep_alive_set(r))
        self.assertEqual(lessons.resolve_hint("@app/x", r), "src/x.ts")
        _, _, summ2 = lessons.merge_lessons(out, L)   # re-teach same → update, not add
        self.assertEqual(summ2["languages_added"], [])

    def test_malformed_lesson_rejected(self):
        from nodo import lessons
        ok, errs, _ = lessons.validate_lesson(
            {"languages": {"bad": {"extensions": [".bad"], "def_patterns": ["("]}}})  # bad regex
        self.assertFalse(ok)
        self.assertTrue(errs)
        ok2, _, _ = lessons.validate_lesson(
            {"languages": {"bad": {"extensions": [], "def_patterns": [r"(\w+)"]}}})  # no ext
        self.assertFalse(ok2)
        ok3, _, _ = lessons.validate_lesson(
            {"languages": {"ok": {"extensions": [".ok"], "def_patterns": [r"\bfn\s+(\w+)"]}}})
        self.assertTrue(ok3)

    def test_keep_alive_suppresses_false_dead_code(self):
        body = "\n".join("export function f%d(){return %d;}" % (i, i) for i in range(6))
        d = make_project({
            "src/main.ts": "export const boot=()=>1;\n",
            "src/features/Orphan.ts": body,  # 6 exports, nobody imports it
        })
        nodes, edges, texts = scanner.build_graph(d)
        base = {i["type"] for i in detectors.detect_all(nodes, edges, texts)}
        self.assertTrue(any("Disconnected feature" in t for t in base))
        healed = {i["type"] for i in detectors.detect_all(
            nodes, edges, texts, keep_alive={"src/features/Orphan.ts"})}
        self.assertFalse(any("Disconnected feature" in t for t in healed))
        # a non-dead-code finding in a kept-alive file is NOT hidden
        d2 = make_project({
            "src/main.ts": "export const boot=()=>1;\n",
            "src/features/Orphan.ts": body + "\nexport function noisy(){ console.log('x'); }\n",
        })
        n2, e2, t2 = scanner.build_graph(d2)
        kept = {i["type"] for i in detectors.detect_all(
            n2, e2, t2, keep_alive={"src/features/Orphan.ts"})}
        self.assertFalse(any("Disconnected feature" in t for t in kept))   # dead-code suppressed
        self.assertTrue(any("console.log" in t for t in kept))             # real lint NOT hidden

    def test_mcp_self_check_and_teach(self):
        from nodo import serve
        d = make_project(self.TOY)
        st = serve._State(d)
        self.assertIn(".toy", serve.dispatch(st, "nodo_self_check", {}))
        res = serve.dispatch(st, "nodo_teach", {"lesson": self.TOY_LESSON})
        self.assertIn("Taught", res)
        self.assertIn("greet", serve.dispatch(st, "nodo_who_uses", {"symbol": "greet"}))

    # ── auto-drafted lessons (deterministic regex induction) ──
    def test_auto_drafted_lesson_works_verbatim(self):
        from nodo import induce, lessons
        samples = [self.TOY["src/main.toy"], self.TOY["src/util.toy"]]
        lesson, stats = induce.draft_lesson(".toy", samples)
        self.assertTrue(stats["induced"])
        self.assertGreaterEqual(stats["defs"], 2)       # greet, main, hello
        self.assertGreaterEqual(stats["imports"], 1)    # import "./util.toy"
        self.assertTrue(lessons.validate_lesson(lesson)[0])   # teachable with no edits
        # teach the DRAFT verbatim → the language becomes first-class
        d = make_project(self.TOY)
        out = Path(d) / ".nodo"
        ok, errs, _ = lessons.merge_lessons(out, lesson)
        self.assertTrue(ok, errs)
        scanner.enable_lessons(lessons.load_lessons(out))
        nodes, edges, texts = scanner.build_graph(d)
        self.assertEqual({n["rel"] for n in nodes}, {"src/main.toy", "src/util.toy"})
        self.assertEqual(len(edges), 1)
        self.assertIn("greet", symbols.query_symbol(nodes, texts, "greet"))

    def test_self_check_emits_induced_draft(self):
        from nodo import lessons, health
        d = make_project(self.TOY)
        nodes, edges, texts = scanner.build_graph(d)    # blind (no lesson)
        hc = health.self_check(d, nodes, edges, texts, lessons.empty(),
                               set(scanner.DEFAULT_IGNORE_DIRS) | {".nodo"})
        self.assertTrue(hc["draft_stats"]["induced"])
        spec = hc["teach_template"]["languages"]["toy"]
        self.assertTrue(any("[A-Za-z_]" in p for p in spec["def_patterns"]))   # real regex
        self.assertFalse(any("<regex" in p for p in spec["def_patterns"]))     # not the stub

    def test_induce_falls_back_to_stub_when_nothing_recognized(self):
        from nodo import induce
        lesson, stats = induce.draft_lesson(".dat", ["foo bar baz\n123 456 789\nqux\n"])
        self.assertFalse(stats["induced"])
        spec = lesson["languages"]["dat"]
        self.assertTrue(any("<regex" in p for p in spec["def_patterns"]))


# ── Roadmap batch: config power, detection cache, re-export drift, grammar,
#    community lessons, parallel reads ──────────────────────────────────────────
class TestRoadmapBatch(unittest.TestCase):
    def setUp(self):
        self._ast = scanner._USE_AST
        scanner._USE_AST = False
        scanner.disable_lessons()

    def tearDown(self):
        scanner._USE_AST = self._ast
        scanner.disable_lessons()

    def test_config_suppress_and_severity(self):
        d = make_project({"src/a.js": "export function f(){ console.log(1); }\n// TODO: x\n"})
        nodes, edges, texts = scanner.build_graph(d)
        base = {t for t, _ in {(i["type"], i["severity"]) for i in
                               detectors.detect_all(nodes, edges, texts)}}
        self.assertTrue(any("TODO" in t for t in base))
        out = detectors.detect_all(nodes, edges, texts, suppress=[{"type": "TODO"}],
                                   severity_overrides={"console.log left in code": "warn"})
        pairs = {(i["type"], i["severity"]) for i in out}
        self.assertFalse(any("TODO" in t for t, _ in pairs))             # suppressed
        self.assertTrue(any("console.log" in t and s == "warn" for t, s in pairs))  # re-weighted

    def test_detection_cache_signature_and_roundtrip(self):
        from nodo import cache
        s1 = cache.detect_signature({"a": "h1", "b": "h2"}, "regex", {"x": 1})
        s2 = cache.detect_signature({"b": "h2", "a": "h1"}, "regex", {"x": 1})
        self.assertEqual(s1, s2)                                          # order-insensitive
        self.assertNotEqual(s1, cache.detect_signature({"a": "h1", "b": "Z"}, "regex", {"x": 1}))
        self.assertNotEqual(s1, cache.detect_signature({"a": "h1", "b": "h2"}, "tree-sitter", {"x": 1}))
        out = Path(make_project({"a.py": "x=1\n"})) / ".nodo"
        cache.save_detect(out, s1, [{"type": "X"}])
        sig, issues = cache.load_detect(out)
        self.assertEqual((sig, issues), (s1, [{"type": "X"}]))

    def test_reexport_drift_flagged(self):
        d = make_project({
            "src/y.ts": "export function realThing(){ return 1; }\n",
            "src/barrel.ts": 'export { ghost } from "./y";\n',
            "src/index.ts": 'import { ghost } from "./barrel";\nexport const go=()=>ghost();\n',
        })
        nodes, edges, texts = scanner.build_graph(d)
        types = {i["type"] for i in detectors.detect_all(nodes, edges, texts)}
        self.assertTrue(any("not exported by source" in t.lower() for t in types))

    def test_reexport_present_not_flagged(self):
        d = make_project({
            "src/y.ts": "export function ok(){ return 1; }\n",
            "src/barrel.ts": 'export { ok } from "./y";\n',
            "src/index.ts": 'import { ok } from "./barrel";\nexport const go=()=>ok();\n',
        })
        nodes, edges, texts = scanner.build_graph(d)
        types = {i["type"] for i in detectors.detect_all(nodes, edges, texts)}
        self.assertFalse(any("not exported by source" in t.lower() for t in types))

    def test_parallel_reads_identical(self):
        files = {}
        for i in range(20):
            files[f"src/m{i}.js"] = (f"import './m{i-1}';\nexport const f{i}=()=>{i};\n"
                                     if i else "export const f0=()=>0;\n")
        d = make_project(files)
        n1, e1, t1 = scanner.build_graph(d, jobs=1)
        n2, e2, t2 = scanner.build_graph(d, jobs=4)
        self.assertEqual([x["rel"] for x in n1], [x["rel"] for x in n2])
        self.assertEqual(sorted((e["source"], e["target"]) for e in e1),
                         sorted((e["source"], e["target"]) for e in e2))
        self.assertEqual(t1, t2)

    def test_lesson_grammar_field_enables_ast(self):
        from nodo import ast_index, lessons
        if not ast_index.available():
            self.skipTest("tree-sitter not installed")
        d = make_project({"src/mod.pyx": "def compute(x):\n    return x+1\nclass Engine:\n    pass\n"})
        out = Path(d) / ".nodo"
        ok, errs, _ = lessons.merge_lessons(out, {"languages": {
            "cython": {"extensions": [".pyx"], "grammar": "python"}}})
        self.assertTrue(ok, errs)
        scanner._USE_AST = True
        scanner.enable_lessons(lessons.load_lessons(out))
        nodes, edges, texts = scanner.build_graph(d)
        self.assertIn("compute", symbols.query_symbol(nodes, texts, "compute") or "")

    def test_reassigned_import_detector(self):
        d = make_project({
            "src/cfg.js": "export let config = {a:1};\nexport const VERSION='1';\n",
            "src/bug.js": 'import { config } from "./cfg";\nconfig = {a:2};\nexport const go=()=>config;\n',
            "src/prop.js": 'import { config } from "./cfg";\nconfig.a = 9;\nexport const f=()=>config.a;\n',
            "src/param.js": 'import { VERSION } from "./cfg";\nfunction h(VERSION){ VERSION="x"; return VERSION; }\nexport const g=h;\n',
        })
        nodes, edges, texts = scanner.build_graph(d)
        flagged = [i["file"] for i in detectors.detect_all(nodes, edges, texts)
                   if i["type"] == "Reassignment of an imported binding"]
        self.assertEqual(flagged, ["src/bug.js"])     # only the real reassign — no FP on prop/param

    def test_shared_mutable_export_detector(self):
        d = make_project({
            "src/cfg.js": "export let config = {a:1};\nexport const VERSION='1';\n",
            "src/use.js": 'import { config, VERSION } from "./cfg";\nexport const f=()=>config.a+VERSION;\n',
        })
        nodes, edges, texts = scanner.build_graph(d)
        mut = [i for i in detectors.detect_all(nodes, edges, texts)
               if i["type"] == "Shared mutable export"]
        self.assertEqual([i["file"] for i in mut], ["src/cfg.js"])
        self.assertIn("config", mut[0]["detail"])     # the mutable export, not the const VERSION

    def test_call_graph_resolves_function_edges(self):
        from nodo import ast_index, callgraph
        if not ast_index.available():
            self.skipTest("tree-sitter not installed")
        scanner._USE_AST = True
        texts = {
            "a.js": "export function helper(){ return 1; }\n",
            "b.js": ("import {helper} from './a';\n"
                     "export function main(){ return helper() + helper(); }\n"
                     "function inner(){ return main(); }\n"),
        }
        nodes = [{"rel": r} for r in texts]
        cg = callgraph.build_call_graph(nodes, texts)
        self.assertTrue(cg["available"])
        edges = {(e["from"], e["to"]) for e in cg["edges"]}
        self.assertIn(("main", "helper"), edges)   # resolved call edge
        self.assertIn(("inner", "main"), edges)
        self.assertEqual(cg["callers"].get("helper"), ["main"])
        # a call to an UNDEFINED function makes no edge (resolved-only → low FP)
        self.assertFalse(any(t == "console" or t == "log" for _f, t in edges))

    def test_edge_provenance_tags(self):
        d = make_project({
            "src/a.ts": "export const x = 1;\n",
            "src/b.ts": "import { x } from './a';\nexport const y = x;\n",
        })
        nodes, edges, texts = scanner.build_graph(d)
        self.assertTrue(edges)
        provs = {e.get("prov") for e in edges}
        self.assertTrue(provs <= {"extracted", "ambiguous", "inferred"}, provs)
        self.assertIn("extracted", provs)            # exact relative import → EXTRACTED

    def test_surprises_rank_bridges_and_skip_mundane(self):
        from nodo import surprises
        nodes = [{"id": 0, "label": "a.ts", "rel": "src/mod1/a.ts", "kind": "code"},
                 {"id": 1, "label": "b.ts", "rel": "src/mod2/b.ts", "kind": "code"},
                 {"id": 2, "label": "README.md", "rel": "docs/README.md", "kind": "doc"},
                 {"id": 3, "label": "c.ts", "rel": "src/mod1/c.ts", "kind": "code"}]
        edges = [{"source": 0, "target": 3, "kind": "import"},        # same module → mundane
                 {"source": 0, "target": 1, "kind": "import"},        # cross-community
                 {"source": 2, "target": 1, "kind": "reference"}]     # cross-modal doc→code
        comm = {0: 0, 3: 0, 1: 1, 2: 2}
        sur = surprises.build_surprises(nodes, edges, comm, top=10)
        self.assertTrue(sur)
        self.assertEqual(sur[0]["kind"], "reference")                # cross-modal ranks highest
        pairs = {(s["from_file"], s["to_file"]) for s in sur}
        self.assertNotIn(("src/mod1/a.ts", "src/mod1/c.ts"), pairs)  # mundane same-module excluded

    def test_vibe_check_narrative(self):
        from nodo import vibe
        ctx = {
            'files': [{'rel': 'src/a.ts', 'kind': 'code', 'category': 'api', 'loc': 50},
                      {'rel': 'src/b.ts', 'kind': 'code', 'category': 'lib', 'loc': 30}],
            'edges': [{'source': 0, 'target': 1, 'kind': 'import'}],
            'hubs': [{'file': 'src/b.ts', 'edges': 5}],
            'stats': {'issues': {'error': 0, 'warn': 1}},
            'issues': [], 'knowledge': {'topics': [{'name': 'auth'}]},
        }
        s = vibe.vibe_check(ctx)
        self.assertIn('[nodo · vibe]', s)
        self.assertIn('TypeScript', s)            # language inferred from extensions
        self.assertIn('src/b.ts', s)              # load-bearing file surfaced
        self.assertIn('auth', s)                  # theme from knowledge topics

    def test_symbol_graph_nodes_and_edges(self):
        from nodo import ast_index, symgraph
        if not ast_index.available():
            self.skipTest("tree-sitter not installed")
        scanner._USE_AST = True
        texts = {
            "base.ts": "export class Animal { speak(){ return 1; } }\n",
            "dog.ts": ("import {Animal} from './base';\n"
                       "export class Dog extends Animal { bark(){ return 2; } }\n"),
            "main.ts": ("import {Dog} from './dog';\n"
                        "export function main(){ return 1; }\n"
                        "function helper(){ return main(); }\n"),
        }
        nodes = [{"rel": r} for r in texts]
        sg = symgraph.build_symbol_graph(nodes, texts)
        self.assertTrue(sg["available"])
        symtypes = {n.get("symtype") for n in sg["nodes"] if n["kind"] == "symbol"}
        self.assertTrue({"class", "func", "method"} <= symtypes)        # first-class symbol nodes
        etypes = {e["type"] for e in sg["edges"]}
        self.assertTrue({"defines", "calls", "inherits"} <= etypes)     # all three edge kinds
        inh = {(e["from"].split(":")[-1], e["to"].split(":")[-1])
               for e in sg["edges"] if e["type"] == "inherits"}
        self.assertIn(("Dog", "Animal"), inh)

    def test_community_lessons_are_valid(self):
        from nodo import lessons
        ldir = Path(__file__).resolve().parent.parent / "examples" / "lessons"
        files = sorted(ldir.glob("*.json"))
        self.assertTrue(files, "no community lessons shipped")
        for f in files:
            ok, errs, _ = lessons.validate_lesson(json.loads(f.read_text(encoding="utf-8")))
            self.assertTrue(ok, f"{f.name}: {errs}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
