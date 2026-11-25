import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_script():
    spec = importlib.util.spec_from_file_location("modularity", ROOT / "scripts" / "modularity.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["modularity"] = module
    spec.loader.exec_module(module)
    return module


def package(root: Path, files: dict[str, str]) -> Path:
    """A tool's package folder holding *files*, each named by its path in the package."""
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def measure(modularity, folder: Path) -> dict:
    return modularity.run([folder], 10)[0][folder.name]


def test_code_lines_leave_out_blanks_comments_and_docstrings_but_keep_template_and_regex_lines(tmp_path):
    """A template literal over three lines with a ${} in it is three code lines, a regular
    expression holding a slash and a brace does not end the code early, and a Python docstring is
    not code."""
    modularity = load_script()
    folder = package(tmp_path / "tool", {
        "static/viewer.js": "// what the page is\n\n/* a block\n   comment */\nconst a = `one\n${b({ c: 1 })}\nthree`;\n"
                            "const d = /[/{]+/.test(a);\nfunction e() { return d; }\n",
        "server.py": '"""What the server is."""\n\n# a comment\nimport os\n\n\ndef f():\n    """What f does."""\n'
                     "    return os.sep\n",
    })
    result = measure(modularity, folder)
    assert result["unparsed"] == {}
    assert result["js"]["own"]["per_module"] == {"viewer.js": 5}
    assert result["python"]["own"]["per_module"] == {"server.py": 3}


def test_a_file_whose_brackets_do_not_pair_is_reported_unparsed(tmp_path):
    modularity = load_script()
    folder = package(tmp_path / "tool", {"static/viewer.js": "function a() {\n  return [1, 2;\n}\n"})
    assert "viewer.js" in measure(modularity, folder)["unparsed"]


def test_an_import_cycle_is_found_through_relative_and_static_paths(tmp_path):
    """page/a.js imports page/b.js by ./, b imports c by /static/, and c imports a by ../page/: the
    three are one cycle, and viewer.js, which imports a, is outside it."""
    modularity = load_script()
    folder = package(tmp_path / "tool", {
        "static/viewer.js": 'import { a } from "./page/a.js";\na();\n',
        "static/page/a.js": 'import { b } from "./b.js";\nexport function a() { return b(); }\n',
        "static/page/b.js": 'import { c } from "/static/page/c.js";\nexport function b() { return c(); }\n',
        "static/page/c.js": 'import { a } from "../page/a.js";\nexport function c() { return a; }\n',
    })
    imports = measure(modularity, folder)["imports"]
    assert imports["cycles"] == [["page/a.js", "page/b.js", "page/c.js"]]
    assert imports["fan_in"]["page/a.js"] == 2
    assert imports["fan_out"]["viewer.js"] == 1


def test_a_factory_call_counts_its_hooks_the_parts_they_name_handles_and_late_bindings(tmp_path):
    """createA takes five hooks: b, wait, show, and x and y from the literal spread into it. Three
    name the part b: the handle `() => b`, the arrow calling b, and y, whose arrow calls b too. b is
    created after a, so all three are late bindings; b's hook names a, so the two parts reach each
    other."""
    modularity = load_script()
    folder = package(tmp_path / "tool", {
        "static/page/a.js": "export function createA({ b, wait, show, x, y }) { return {}; }\n",
        "static/page/b.js": "export function createB({ a }) { return { show() {}, y() {} }; }\n",
        "static/viewer.js": 'import { createA } from "./page/a.js";\nimport { createB } from "./page/b.js";\n'
                            "function reach(part, ...names) { return part; }\n"
                            "const more = { x: 1, y: () => b.y() };\n"
                            'const a = createA({ b: reach(() => b, "show"), wait: 1000, show: () => b.show(), ...more });\n'
                            "const b = createB({ a });\n",
    })
    wiring = measure(modularity, folder)["wiring"]
    call = next(c for c in wiring["calls"] if c["factory"] == "createA")
    assert [h["hook"] for h in call["hooks"]] == ["b", "wait", "show", "x", "y"]
    assert call["naming_parts"] == 3
    assert [(s["part"], s["late"]) for s in wiring["handles"]] == [("b", True)]
    assert sorted(s["line"] for s in wiring["late"]) == [4, 5, 5]
    assert wiring["part_cycles"] == [["a", "b"]]


def test_a_function_handed_to_a_part_after_it_exists_counts_as_one_handed_to_its_creation(tmp_path):
    """The same connections, written once as hooks handed to the parts' creation and once as
    calls on the parts after they exist, give the same part graph: b reads a's state, a hands its
    change on to b, and a takes the whole of c, whose creation names a."""
    modularity = load_script()
    imports = ('import { createA } from "./page/a.js";\nimport { createB } from "./page/b.js";\n'
               'import { createC } from "./page/c.js";\n')
    pages = {
        "static/page/a.js": "export function createA({ changed, c } = {}) { return { state() {}, changed() {}, use() {} }; }\n",
        "static/page/b.js": "export function createB({ state }) { return { render() {} }; }\n",
        "static/page/c.js": "export function createC({ a }) { return {}; }\n",
    }
    hooks = package(tmp_path / "hooks", {**pages, "static/viewer.js": imports
        + "const a = createA({ changed: () => b.render(), c: () => c });\n"
        + "const b = createB({ state: () => a.state() });\n"
        + "const c = createC({ a });\n"})
    listeners = package(tmp_path / "listeners", {**pages, "static/viewer.js": imports
        + "const a = createA();\n"
        + "const b = createB({ state: a.state });\n"
        + "const c = createC({ a });\n"
        + "a.changed(() => b.render());\n"
        + "a.use(c);\n"})
    by_hooks, by_listeners = measure(modularity, hooks)["wiring"], measure(modularity, listeners)["wiring"]
    assert [(s["line"], s["part"], s["parts"]) for s in by_listeners["subscriptions"]] == [
        (7, "a", ["b"]), (8, "a", ["c"])]
    assert by_listeners["late"] == [] and len(by_hooks["late"]) == 2
    assert by_hooks["part_cycles"] == by_listeners["part_cycles"] == [["a", "b", "c"]]


def test_writes_into_a_handed_object_and_reads_of_its_state_are_counted_with_their_sites(tmp_path):
    """The factory is handed a store and the panel's hooks. Setting a field, a field's key and
    adding to a field's set are writes; reading a field and finding in a list are reads; calling
    a function the store gives, a sub-part's function or a handed function passed along, and a DOM
    property, are neither. A state reached by `withDefaults(given)` and destructuring is followed."""
    modularity = load_script()
    folder = package(tmp_path / "tool", {
        "static/page/x.js": "export function createX({ store, panel, button }) {\n"
                            "  store.count = 1;\n"
                            "  store.byId[2] = 3;\n"
                            "  store.seen.add(4);\n"
                            "  const n = store.count;\n"
                            "  const found = store.items.find((item) => item.id === n);\n"
                            "  store.load();\n"
                            "  panel.keys.hint();\n"
                            "  later(panel.refresh);\n"
                            "  panel.refresh();\n"
                            "  button.hidden = true;\n"
                            "  return found;\n"
                            "}\n",
        "static/page/y.js": "export function createY(given) {\n"
                            "  const host = withDefaults(given);\n"
                            "  const { state } = host;\n"
                            "  state.comments = [];\n"
                            "}\n",
    })
    result = measure(modularity, folder)
    writes = [(s["module"], s["line"], s["object"], s["field"], s["kind"]) for s in result["writes"]]
    assert writes == [("page/x.js", 2, "store", "count", "assign"), ("page/x.js", 3, "store", "byId", "assign"),
                      ("page/x.js", 4, "store", "seen", "change"), ("page/y.js", 4, "state", "comments", "assign")]
    reads = [(s["line"], s["object"], s["field"]) for s in result["reads"]]
    assert reads == [(5, "store", "count"), (6, "store", "items")]
    assert result["summary"]["writes into handed objects"] == 4


def test_a_name_given_to_a_handed_object_holds_only_in_the_block_that_gives_it(tmp_path):
    """read() names the store's git `now`; show(now), an inner function with a parameter of the
    same name, reads a value of its own, which is not the store's state."""
    modularity = load_script()
    folder = package(tmp_path / "tool", {
        "static/page/x.js": "export function createX({ store }) {\n"
                            "  function show(now) { return now.behind; }\n"
                            "  function read() {\n"
                            "    const now = store.git;\n"
                            "    return now.ahead;\n"
                            "  }\n"
                            "  return { show, read };\n"
                            "}\n",
    })
    reads = [(s["line"], s["object"], s["field"]) for s in measure(modularity, folder)["reads"]]
    assert reads == [(4, "store", "git"), (5, "now", "ahead")]


def test_an_owner_counts_its_own_access_to_a_handed_out_object_only_on_fields_a_receiver_touches(tmp_path):
    """viewer.js hands its state whole to createX, which writes state.open. The viewer's own
    writes of state.open are owner sites; its writes of state.mine, which no receiver touches,
    are not."""
    modularity = load_script()
    folder = package(tmp_path / "tool", {
        "static/page/x.js": "export function createX({ state }) { state.open = 1; }\n",
        "static/viewer.js": 'import { createX } from "./page/x.js";\n'
                            "const state = { open: 0, mine: 0 };\n"
                            "createX({ state });\n"
                            "state.open = 2;\n"
                            "state.mine = 3;\n",
    })
    result = measure(modularity, folder)
    owner = [(s["line"], s["field"], s["crossing"]) for s in result["writes"] if s["owner"] == "(owner)"]
    assert owner == [(4, "open", True), (5, "mine", False)]
    assert result["summary"]["owner writes of handed-out objects"] == 1


def test_a_shared_copy_is_left_out_of_a_tools_own_code_and_of_duplication(tmp_path):
    """Two tools hold a copy of shared/dom.js, which their manifests name, and one long line in
    page code of their own. Only the own line is duplicated between them; dom.js counts as shared
    code in the reuse ratio, and extra.js, in shared/ but not in the manifest, as the tool's own."""
    modularity = load_script()
    line = "const answer = await fetch(`/comments/${id}`);\n"
    dom = "export function $(selector) { return document.querySelector(selector); }\n"
    folders = []
    for name in ("one", "two"):
        files = {"static/viewer.js": line, "static/shared/dom.js": dom,
                 "static/shared/MANIFEST.json": json.dumps({"files": {"dom.js": ""}})}
        if name == "one":
            files["static/shared/extra.js"] = "export const extra = 1;\n"
        folders.append(package(tmp_path / name, files))
    tools, shared = modularity.run(folders, 10)
    assert shared["tool_pairs"] == {"one | two": 1}
    assert shared["file_pairs"] == [{"files": ["one/static/viewer.js", "two/static/viewer.js"], "lines": 1}]
    assert set(tools["one"]["js"]["own"]["per_module"]) == {"viewer.js", "shared/extra.js"}
    assert tools["two"]["reuse"] == {"shared": 1, "page": 2, "ratio": 0.5}


def test_a_line_in_two_of_a_tools_own_files_is_duplication_within_it(tmp_path):
    modularity = load_script()
    line = "if (!response.ok) throw new Error(await responseError(response));\n"
    folder = package(tmp_path / "tool", {"static/page/a.js": line, "static/page/b.js": line,
                                          "static/page/c.js": line + line + "const x = 1;\n"})
    duplicate = measure(modularity, folder)["duplication"]
    assert duplicate["lines"] == 1
    assert duplicate["across_files"] == 1
    assert len(duplicate["sites"][0]["at"]) == 4


def test_change_coupling_is_skipped_with_a_note_outside_a_git_repository(tmp_path, monkeypatch):
    modularity = load_script()
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    folder = package(tmp_path / "tool", {"static/viewer.js": "const a = 1;\n"})
    assert "skipped" in measure(modularity, folder)["change_coupling"]


def test_compare_prints_each_number_before_and_after(tmp_path, capsys):
    modularity = load_script()
    folder = package(tmp_path / "tool", {"static/viewer.js": "const a = 1;\n"})
    old, new = tmp_path / "old.json", tmp_path / "new.json"
    modularity.main([str(folder), "--json", str(old)])
    (folder / "static" / "page").mkdir()
    (folder / "static" / "page" / "b.js").write_text("export const b = 2;\nexport const c = 3;\n", encoding="utf-8")
    modularity.main([str(folder), "--json", str(new)])
    capsys.readouterr()
    modularity.main(["--compare", str(old), str(new)])
    printed = capsys.readouterr().out
    assert "js own lines" in printed and "1 -> 3" in printed
    assert "js own modules" in printed and "1 -> 2" in printed
