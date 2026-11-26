"""Quick self-checks for the agent team. No model, no internet, no Minecraft needed.

Run from this folder:   .venv\\Scripts\\python run_tests.py
Every check prints PASS or FAIL; the last line says how many passed. Takes about a second.
Added 10 Nov 2026. Add a check here whenever a bug is fixed, so it can't come back.
"""
import os
import shutil
import sys
import tempfile
import traceback

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
os.chdir(_HERE)

import research_tools as rt  # noqa: E402

# No real web requests during tests: any attempt fails loudly.
def _no_network(*a, **k):
    raise AssertionError("a test tried to use the internet")


rt.urllib.request.urlopen = _no_network

import agents  # noqa: E402  (building the model clients doesn't contact Ollama)

FAKE_STATS = {   # same shape the Create wiki lookup returns
    "Mechanical Press": ["stress impact: 8x RPM"],
    "Mechanical Mixer": ["stress impact: 4x RPM", "speed requirement: 30 RPM"],
    "Encased Fan": ["stress impact: 2x RPM"],
    "Water Wheel": ["stress capacity: 256 SU", "speed generated: 8 RPM"],
    "Large Water Wheel": ["stress capacity: 512 SU", "speed generated: 4 RPM"],
}
TASK = "Review the Works Hall: one press, one mixer and both fan lines. Is the water wheel power enough?"
NOTES = ("## Built\n- Mill on the river\n\n## Progress log\n- 2026-11-09: Calamity Watch done\n"
         "- 2026-11-10: Works Hall mill currently has 4 water wheels (4 x 256 SU = 1024 SU at 8 RPM).\n")

results = []


def check(name):
    def wrap(fn):
        try:
            fn()
            results.append(True)
            print(f"PASS  {name}")
        except Exception:
            results.append(False)
            print(f"FAIL  {name}\n" + "".join("      " + ln for ln in traceback.format_exc().splitlines(True)[-4:]))
        return fn
    return wrap


def calc(task=TASK, notes=NOTES):
    rt._COMPONENT_CACHE.clear()
    rt._COMPONENT_CACHE.update(FAKE_STATS)
    return rt.stress_calculator(task, notes)


# ---------------------------------------------------------------- stress calculator
@check("calculator: totals for both plans")
def _():
    out = calc()
    assert "Everything at 32 RPM: 256 SU" in out and "= 512 SU" in out, out
    assert "= 320 SU" in out, out


@check("calculator: uses the built wheel count from the notes")
def _():
    out = calc()
    assert "YOUR BUILD: 4 Water Wheels = 4 x 256 = 1,024 SU" in out, out
    assert "ENOUGH, 512 SU spare" in out and "ENOUGH, 704 SU spare" in out, out


@check("calculator: says SHORT when the build is too small")
def _():
    out = calc(notes="- the mill has 1 water wheel")
    assert "SHORT by 256 SU" in out and "SHORT by 64 SU" in out, out


@check("calculator: per-machine totals in the bottom line (fans 128 SU for both at 32 RPM)")
def _():
    out = calc()
    bottom = out.split("Machine SU =")[0]
    assert "Encased Fan x2 together at 32 RPM = 128 SU (64 SU each)" in bottom, bottom


@check("calculator: asks for a count when the notes don't say")
def _():
    out = calc(notes="")
    assert "isn't known here" in out and "YOUR BUILD" not in out, out


@check("built_sources: large vs normal wheels, plans ignored, newest line wins")
def _():
    got = rt.built_sources("we built 2 large water wheels\nNeed 3 water wheels\nthe mill has two water wheels\n"
                           "- currently 5 water wheels")
    assert got["Large Water Wheel"][0] == 2, got
    assert got["Water Wheel"][0] == 5, got


@check("calculator: with no minimum-speed machine the second plan is 'Everything at 16 RPM'")
def _():
    out = calc(task="one press and both fans on water wheels")
    assert "Everything at 8 RPM = 96 SU" in out and "Everything at 16 RPM = 192 SU" in out, out


@check("calculator: with a known build, the supply table shows only that build")
def _():
    out = calc()
    table = out.split("Supply vs demand:")[1]
    assert "- 4 x Water Wheel = 1,024 SU" in table and "- 2 x Water Wheel" not in table, table
    assert "Large Water Wheel" not in table, table
    assert "1 Large Water Wheel (192 SU spare)" in out          # alternatives stay in the bottom line
    full = calc(notes="").split("Supply vs demand:")[1]
    assert "- 2 x Water Wheel" in full and "- 1 x Large Water Wheel" in full, full


# ---------------------------------------------------------------- worker checks
CALC_TEXT = calc()


@check("calc check: catches 'Encased Fans x2 at 32 RPM = 256 SU'")
def _():
    out = agents._calc_check("- Encased Fans x2 at 32 RPM = 256 SU", CALC_TEXT)
    assert "(calculator: 64 SU each, 128 SU for all 2 at 32 RPM)" in out, out


@check("calc check: leaves correct and ambiguous figures alone")
def _():
    good = ["- Encased Fans at 32 RPM use 128 SU", "- One fan at 32 RPM uses 64 SU",
            "- Mechanical Press at 32 RPM = 256 SU", "- Fans at 32 RPM; the network has 1024 SU",
            "- Press and mixer at 32 RPM = 384 SU", "- Mixer uses 4 SU per RPM at 32 RPM"]
    for g in good:
        assert agents._calc_check(g, CALC_TEXT) == g, g


@check("fact check: removes invented links, flags invented SU numbers")
def _():
    src = "Water Wheel stress capacity: 256 SU https://create.fandom.com/wiki/Water_Wheel stress impact: 4x RPM"
    out = agents._fact_check("See https://example.com/fake. Wheel gives 256 SU, fan uses 999 SU, mixer 4 SU per RPM.", src)
    assert "[link not in sources - removed]" in out and "example.com" not in out, out
    assert "999 SU (unverified" in out and "256 SU (unverified" not in out, out
    assert "4 SU per RPM (unverified" not in out, out


@check("timestamps: kept inside a transcript chunk, flagged otherwise")
def _():
    ev = "## Video\n[3:40] put the fan behind the press [5:10] more"
    out = agents._check_timestamps("In the video at 3:55 he adds a fan; at 12:10 he adds a saw.", ev)
    assert "3:55 (timestamp" not in out and "12:10 (timestamp not in the transcript - check)" in out, out
    assert agents._check_timestamps("ratio 1:20 of iron", ev) == "ratio 1:20 of iron"


@check("tips: untagged tips are labelled; tagged tips and other sections left alone")
def _():
    ev = "water wheels power the mill. Undercroft is below the hall. radar on the roof."
    text = ("### Build steps\n- Place the press\n### Gotchas\n- Keep wheels clear of leaves\n"
            "- Keep water wheels away from the Undercroft: radar interference [research]\n"
            "- Water wheels need flowing water [game knowledge]\n- Water wheels power the mill [notes]")
    out = agents._label_tips(text, ev).split("\n")
    assert out[1] == "- Place the press", out
    assert out[3].endswith("[unverified - check in-game]"), out
    assert out[4] == "- Keep water wheels away from the Undercroft: radar interference [research]", out
    assert out[5] == "- Water wheels need flowing water [game knowledge]", out
    assert out[6] == "- Water wheels power the mill [notes]", out
    more = ("### Gotchas\n- Set up cogwheels properly to achieve the speed [research]\n"
            "- Keep sentries clear [rules]\n- Odd tip [rules] [unverified \u2013 check in-game]")
    out = agents._label_tips(more, "cogwheels speed sentries clear").split("\n")
    assert out[1] == "- Set up cogwheels properly to achieve the speed [research]", out
    assert out[2] == "- Keep sentries clear [rules]", out
    assert out[3].count("unverified") == 1, out


@check("known mistakes: arms carrying power, stone water wheels, steel cogwheels get a correction")
def _():
    bad = ["- Power Distribution: All machines are powered through a centralized shaft system, using Create's mechanical arms and cogs.",
           "| Water Wheels           | Deepslate             | Positioned at the edge of the riverbank. |",
           "| Cogwheels               | Iron or Steel        | Two steps. |",
           "Mechanical arms transmit power to the press",
           "- **Mechanical Press**: Its reinforced steel plating (vscarmor:reinforced_armor) gives it a robust look.",
           "- All belts are made of reinforced steel, running along deepslate channels"]
    for b in bad:
        assert "(correction:" in agents._known_mistakes(b), b
    fine = ["- A Mechanical Arm feeds the press, which is powered by the shaft",
            "- Hoppers collect the output; the fans are powered by the shaft",
            "- Water wheels sit beside the deepslate wall", "| Walls | Deepslate | dark |"]
    for f in fine:
        assert agents._known_mistakes(f) == f, f


@check("new parts: a Create machine not in task/notes/research is marked once; plumbing is not")
def _():
    out = agents._label_new_parts("Add a Mechanical Arm. The Mechanical Arm and a Gearbox feed the press.",
                                  "one press and a mixer")
    assert out.count("(new idea - not in the task, notes or research)") == 1, out
    assert "Mechanical Arm (new idea" in out and "Gearbox (new idea" not in out, out
    assert agents._label_new_parts("The press runs at 16 RPM.", "one press") == "The press runs at 16 RPM."


@check("length: an answer cut off at the limit says so")
def _():
    class Msg:
        content = "The fans run"
        response_metadata = {"done_reason": "length"}

    class Model:
        def invoke(self, m): return Msg()

    assert agents._ask("s", "u", model=Model()).endswith("[cut off at the length limit]")
    Msg.response_metadata = {"done_reason": "stop"}
    assert agents._ask("s", "u", model=Model()) == "The fans run"


# ---------------------------------------------------------------- project notes size
@check("notes: short notes unchanged")
def _():
    assert agents._notes_for_agents(NOTES, 10000) == NOTES


@check("notes: long log drops OLDEST entries and keeps the Rules section")
def _():
    log = "\n".join(f"- 2026-10-{i:02d}: entry {i} " + "x" * 80 for i in range(1, 31))
    notes = "# Fort\n## World\nriver base\n\n## Rules / lessons\n- never break the dam\n\n## Progress log\n" + log
    out = agents._notes_for_agents(notes, 1500)
    assert len(out) <= 1500, len(out)
    assert "never break the dam" in out and "entry 30" in out and "entry 1 " not in out, out
    assert "oldest" in out


# ---------------------------------------------------------------- web cache
@check("cache: repeat GitHub lookups come from disk; POSTs, YouTube pages and errors are not cached")
def _():
    tmp = tempfile.mkdtemp()
    old = rt.WEB_CACHE_DIR
    rt.WEB_CACHE_DIR = tmp
    calls = []

    class Resp:
        def __init__(self, b): self.b = b
        def read(self): return self.b
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake(req, timeout=15):
        calls.append(req.full_url)
        if "boom" in req.full_url:
            raise rt.urllib.error.URLError("down")
        return Resp(b'{"ok": 1}')

    rt.urllib.request.urlopen = fake
    try:
        gh = "https://api.github.com/search/issues?q=create"
        assert rt._get(gh) == rt._get(gh) == b'{"ok": 1}' and calls.count(gh) == 1, calls
        rt._get("https://www.youtube.com/watch?v=abc"); rt._get("https://www.youtube.com/watch?v=abc")
        assert calls.count("https://www.youtube.com/watch?v=abc") == 2, calls
        rt._get(gh, data=b"x"); assert calls.count(gh) == 2, calls
        for _ in range(2):
            try:
                rt._get("https://api.github.com/boom")
            except Exception:
                pass
        assert calls.count("https://api.github.com/boom") == 2, calls
        rt._yt_transcript_live = lambda vid: [(1.0, "hello")]
        assert rt._yt_transcript("v1") == [(1.0, "hello")]
        rt._yt_transcript_live = _no_network
        assert rt._yt_transcript("v1") == [(1.0, "hello")]      # second time from disk
    finally:
        rt.urllib.request.urlopen = _no_network
        rt.WEB_CACHE_DIR = old
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- orchestrator guards
@check("guards: no developer on review-only tasks; 2 runs per worker; research budget")
def _():
    st = {"task": "Works Hall review only", "history": []}
    assert not agents._allowed(st, "developer")
    st = {"task": "build it", "history": ["architect: plan written"] * 2}
    assert not agents._allowed(st, "architect") and agents._allowed(st, "designer")
    st = {"task": "x", "history": ["researcher: looked up 'a'"] * agents.config.MAX_RESEARCH_CALLS}
    assert not agents._allowed(st, "researcher")


@check("orchestrator: told which agents it may pick (no developer on review-only tasks)")
def _():
    seen = {}

    class Router:
        def with_structured_output(self, cls):
            class W:
                def invoke(_, msgs):
                    seen["user"] = msgs[1].content
                    return cls(next="architect", instructions="plan", reason="r")
            return W()

    old_router, agents.router_llm = agents.router_llm, Router()
    try:
        agents.orchestrator({"task": "Works Hall review only (no code)", "history": [], "steps": 0})
    finally:
        agents.router_llm = old_router
    line = [ln for ln in seen["user"].splitlines() if ln.startswith("Agents you may pick now:")][0]
    assert "developer" not in line and "architect" in line and "FINISH" in line, line


# ---------------------------------------------------------------- projects (25 Nov 2026)
import importlib  # noqa: E402
import urllib.parse  # noqa: E402

config = rt.config


def _with_project(key):
    """Reload config for a project; returns a function that puts the old one back."""
    old = os.environ.get("GRAPH_PROJECT")
    os.environ["GRAPH_PROJECT"] = key
    importlib.reload(config)

    def restore():
        if old is None:
            os.environ.pop("GRAPH_PROJECT", None)
        else:
            os.environ["GRAPH_PROJECT"] = old
        importlib.reload(config)
    return restore


@check("projects: rpg uses its own notes, modpack folder and NeoForge 1.21.1")
def _():
    restore = _with_project("rpg")
    try:
        assert config.PROJECT_FILE.endswith(os.path.join("projects", "rpg.md")), config.PROJECT_FILE
        assert config.MODPACK_DIR.endswith("Cinema"), config.MODPACK_DIR
        assert (config.LOADER, config.GAME_VERSION) == ("neoforge", "1.21.1")
        assert "1.20.1 Forge" not in config.MODPACK_CONTEXT
    finally:
        restore()
    assert config.PROJECT == "fort_redux" and config.MODPACK_DIR.endswith("Zombies")


@check("projects: an unknown project name is refused, not silently run as Fort Redux")
def _():
    try:
        _with_project("rgp")
    except ValueError:
        pass
    else:
        raise AssertionError("no error for an unknown project")
    finally:
        os.environ.pop("GRAPH_PROJECT", None)
        importlib.reload(config)


@check("projects: rpg notes exist, fit what agents read, and keep their rules section")
def _():
    path = config.PROJECTS["rpg"]["notes"]
    with open(path, encoding="utf-8") as f:
        text = f.read()
    assert len(text) < config.NOTES_CHARS and "## Rules" in text and "NeoForge 1.21.1" in text
    with open(config.PROJECTS["fort_redux"]["notes"], encoding="utf-8") as f:
        assert "## RPG Modpack" not in f.read(), "RPG notes still inside the Fort Redux file"


@check("modrinth: loader/version words leave the query and go in facets")
def _():
    assert rt._modrinth_query("neoforge 1.21.1 monster hunter mods") == "monster hunter"
    assert rt._modrinth_query("Saint's Dragons NeoForge 1.21.1 build?") == "Saint's Dragons"
    urls = []
    old = rt._get_json
    rt._get_json = lambda url, *a, **k: (urls.append(url), {"hits": [{"title": "Wild Hunt", "slug": "wild-hunt",
                                                                      "versions": ["1.21.1"], "categories": ["neoforge"]}]})[1]
    restore = _with_project("rpg")
    try:
        out = rt.search_modrinth("neoforge 1.21.1 monster hunter mods")
    finally:
        restore()
        rt._get_json = old
    q = urllib.parse.parse_qs(urllib.parse.urlparse(urls[0]).query)
    assert q["query"] == ["monster hunter"] and "categories:neoforge" in q["facets"][0] \
        and "versions:1.21.1" in q["facets"][0], urls[0]
    assert "Builds for neoforge 1.21.1" in out and "Wild Hunt" in out, out


@check("modrinth: no build for the project's version is reported as such (a Pinned mod)")
def _():
    calls = []

    def fake(url, *a, **k):
        calls.append(url)
        return {"hits": []} if "facets" in url else {"hits": [{"title": "Saints Dragons", "slug": "sd",
                                                                "versions": ["1.20.1"], "categories": ["forge"]}]}
    old, rt._get_json = rt._get_json, fake
    restore = _with_project("rpg")
    try:
        out = rt.search_modrinth("saints dragons")
    finally:
        restore()
        rt._get_json = old
    assert len(calls) == 2 and out.startswith("NO neoforge 1.21.1 build"), out


@check("wiki: unrelated pages are dropped instead of passed on as facts")
def _():
    old = rt._mediawiki
    rt._mediawiki = lambda *a, **k: "## ModJam (minecraft.wiki)\nModJam is a community event for mods."
    try:
        out = rt.search_minecraft_wiki("Snow Real Magic")
    finally:
        rt._mediawiki = old
    assert out.startswith("[minecraft.wiki] No relevant page"), out


@check("local mods: each project gets its own mod index cache")
def _():
    tmp = tempfile.mkdtemp()
    try:
        os.makedirs(os.path.join(tmp, "mods"))
        restore = _with_project("rpg")
        old_dir, old_cache = config.MODPACK_DIR, rt.CACHE_DIR
        config.MODPACK_DIR, rt.CACHE_DIR = tmp, os.path.join(tmp, "cache")
        try:
            rt._build_mod_index()
            assert os.path.exists(os.path.join(tmp, "cache", "mod_index_rpg.json"))
        finally:
            config.MODPACK_DIR, rt.CACHE_DIR = old_dir, old_cache
            restore()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@check("local mods: NeoForge jars (neoforge.mods.toml) give version and dependency entries")
def _():
    import io, zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("META-INF/neoforge.mods.toml",
                   '[[mods]]\nmodId="reliquified_artifacts"\nversion="1.0.9"\ndisplayName="Reliquified Artifacts"\n'
                   '[[dependencies.reliquified_artifacts]]\nmodId="relics"\ntype="required"\nversionRange="[0.12,)"\n'
                   '[[dependencies.reliquified_artifacts]]\nmodId="neoforge"\ntype="required"\n')
    with zipfile.ZipFile(buf) as z:
        meta = rt._read_mod_meta(z, "reliquified_artifacts-1.21.1-1.0.9.jar")
    texts = [t for _, t in meta]
    assert any("Reliquified Artifacts (id reliquified_artifacts) version 1.0.9" in t for t in texts), texts
    assert any("requires relics [0.12,)" in t for t in texts) and not any("neoforge" in t for t in texts[1:]), texts


@check("local mods: mods listed before item names, dependencies marked installed or missing")
def _():
    old = rt._MOD_INDEX
    rt._MOD_INDEX = (
        [{"jar": "relics-1.21.1-0.12.11.jar", "kind": "lang", "text": f"item.relics.thing{i} = Relics thing {i}"}
         for i in range(20)]
        + [{"jar": "reliquified_artifacts-1.21.1-1.0.9.jar", "kind": "mod",
            "text": "Reliquified Artifacts (id reliquified_artifacts) version 1.0.9, file reliquified_artifacts-1.21.1-1.0.9.jar"},
           {"jar": "relics-1.21.1-0.12.11.jar", "kind": "mod",
            "text": "Relics (id relics) version 0.12.11, file relics-1.21.1-0.12.11.jar"},
           {"jar": "reliquified_artifacts-1.21.1-1.0.9.jar", "kind": "dependency",
            "text": "reliquified_artifacts requires relics [0.12,)"},
           {"jar": "relics-1.21.1-0.12.11.jar", "kind": "dependency", "text": "relics requires octolib [0.6,)"}])
    tmp = tempfile.mkdtemp()
    os.makedirs(os.path.join(tmp, "mods"))
    old_dir, config.MODPACK_DIR = config.MODPACK_DIR, tmp
    try:
        out = rt.search_local_mods("relics reliquified")
    finally:
        rt._MOD_INDEX, config.MODPACK_DIR = old, old_dir
        shutil.rmtree(tmp, ignore_errors=True)
    body = out.split("\n")
    assert body[1].startswith("- [mod]") and body[2].startswith("- [mod]"), out
    assert "requires relics [0.12,)  (reliquified_artifacts-1.21.1-1.0.9.jar) -> installed: 0.12.11" in out, out
    assert "requires octolib [0.6,)  (relics-1.21.1-0.12.11.jar) -> NOT INSTALLED" in out, out


@check("fact check: a misquoted jar file name is flagged")
def _():
    src = "file relics-1.21.1-0.12.11.jar"
    out = agents._fact_check("Relics: relicts-1.21.1-0.12.11.jar and relics-1.21.1-0.12.11.jar", src)
    assert "relicts-1.21.1-0.12.11.jar (file name not in sources - check)" in out, out
    assert "relics-1.21.1-0.12.11.jar and" not in out or out.endswith("relics-1.21.1-0.12.11.jar"), out


@check("orchestrator: won't finish while the task names an installed mod the research skipped")
def _():
    old_names, agents.installed_mod_names = agents.installed_mod_names, lambda: [
        "Relics", "More Relics", "Reliquified Artifacts", "Create"]

    class Router:
        def with_structured_output(self, cls):
            class W:
                def invoke(_, msgs):
                    return cls(next="FINISH", instructions="", reason="done")
            return W()

    old_router, agents.router_llm = agents.router_llm, Router()
    try:
        st = {"task": "Check Relics, More Relics and Reliquified Artifacts. Review only, no code.",
              "research": "### Query: relics\n- Relics 0.12.11\n- More Relics 1.8.0", "steps": 1,
              "history": ["researcher: looked up 'relics' (local_mods)"]}
        out = agents.orchestrator(st)
        assert out["next"] == "researcher" and "Reliquified Artifacts" in out["instructions"], out
        st["research"] += "\n- Reliquified Artifacts 1.0.9"
        assert agents.orchestrator(st)["next"] == "FINISH"
    finally:
        agents.router_llm, agents.installed_mod_names = old_router, old_names


@check("researcher: 'not installed' is corrected when the mod's jar is in the modpack")
def _():
    old, agents.installed_mods = agents.installed_mods, lambda: [
        ("Reliquified Artifacts", "1.0.9", "reliquified_artifacts-1.21.1-1.0.9.jar"), ("Relics", "0.12.11", "relics-1.21.1-0.12.11.jar")]
    try:
        out = agents._installed_check("- Relics 0.12.11 is installed.\n- Reliquified Artifacts is **not installed** in the Cinema instance.\n- Foo Mod is not installed.")
    finally:
        agents.installed_mods = old
    assert "(correction: Reliquified Artifacts IS installed - version 1.0.9, file reliquified_artifacts-1.21.1-1.0.9.jar)" in out, out
    assert out.count("(correction:") == 1, out          # Relics line and the truly missing mod untouched


@check("local mods: a jar with only a mod id gets a readable name ('reliquified_artifacts')")
def _():
    old = rt._MOD_INDEX
    rt._MOD_INDEX = [{"jar": "r.jar", "kind": "mod", "text": "reliquified_artifacts (id reliquified_artifacts) version 1.0.9, file r.jar"},
                     {"jar": "a.jar", "kind": "mod", "text": "Artifacts (id artifacts) version 13.2.5, file a.jar"}]
    try:
        names = rt.installed_mod_names()
    finally:
        rt._MOD_INDEX = old
    assert "Reliquified Artifacts" in names and "Artifacts" in names, names


@check("reports: the checks summary counts each kind of flag")
def _():
    import graph
    text = ("x (correction: A IS installed) y (correction: B IS installed) "
            "relicts.jar (file name not in sources - check) [link not in sources - removed]")
    line = graph.checks_summary(text)
    assert line == "Checks: 2 corrections, 1 unverified file names, 1 invented links removed", line
    assert graph.checks_summary("all good") == "Checks: nothing flagged"


@check("benchmarks: file loads, every benchmark names a real project and has checks")
def _():
    import benchmarks
    benches = benchmarks.load_benchmarks()
    assert benches, "no benchmarks"
    for name, b in benches.items():
        assert b.get("project", "fort_redux") in config.PROJECTS, name
        assert b.get("task") and (b.get("contains") or b.get("not_contains")), name
        assert all(isinstance(g, list) and g for g in b.get("contains", [])), name


@check("benchmarks: scoring needs one match per group and none of the banned words")
def _():
    import benchmarks
    bench = {"contains": [["0.12.11"], ["pinned", "stay pinned"]], "not_contains": ["obsidian"]}
    ok, lines = benchmarks.evaluate("Relics 0.12.11 ... should stay PINNED", bench)
    assert ok and all(ln.startswith("PASS") for ln in lines), lines
    ok, lines = benchmarks.evaluate("Relics 0.12.11, obsidian walls", bench)
    assert not ok and sum(ln.startswith("FAIL") for ln in lines) == 2, lines


@check("orchestrator: no researcher again right after it skipped a repeat query")
def _():
    st = {"task": "x", "history": ["researcher: looked up 'a' (modrinth)", "orchestrator -> researcher: again",
                                   "researcher: skipped repeat query 'a'"]}
    assert not agents._allowed(st, "researcher")


@check("researcher: an answer or JSON written as the query is replaced by the task's key words")
def _():
    assert not agents._valid_query('{"has_build": "no", "should_remain_pinned": "yes"}')
    assert agents._valid_query("Saint's Dragons NeoForge 1.21.1")
    q = agents._task_query("Review only, no code. Does Saint's Dragons have a NeoForge 1.21.1 build? Answer yes or no.")
    assert q.startswith("Saint's Dragons") and "Review" not in q, q


@check("researcher: a JSON answer becomes plain bullet points")
def _():
    out = agents._json_to_bullets('{\n  "answer": "no",\n  "should_remain_pinned": true\n}')
    assert out == "- answer: no\n- should remain pinned: yes", out
    assert agents._json_to_bullets("- normal bullet") == "- normal bullet"


@check("modrinth: retries with fewer words when an extra word finds nothing")
def _():
    calls = []

    def fake(url, *a, **k):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["query"][0]
        calls.append(q)
        return {"hits": [{"title": "Saint's Dragons", "slug": "sd", "versions": ["1.20.1"], "categories": ["forge"]}]} \
            if q == "Saint's Dragons" and "facets" not in url else {"hits": []}
    old, rt._get_json = rt._get_json, fake
    restore = _with_project("rpg")
    try:
        out = rt.search_modrinth("Saint's Dragons NeoForge 1.21.1 Pinned mods")
    finally:
        restore()
        rt._get_json = old
    assert calls[0] == "Saint's Dragons Pinned" and "Saint's Dragons" in calls, calls
    assert out.startswith("NO neoforge 1.21.1 build found for 'Saint's Dragons'"), out


@check("blocks: invented block names are flagged, real ones and loose wording are not")
def _():
    old, agents.known_block_names = agents.known_block_names, lambda: {
        "gravel", "basalt", "smooth basalt", "polished blackstone bricks", "blue ice", "packed ice", "deepslate"}
    try:
        out = agents._check_blocks("**snowy gravel** floor, **basalt slabs**, walls of **blackstone bricks**, "
                                   "**blue ice** drips, **blue ice blocks**, **deepslate** pillars, **Trophy Hall**, **walls**, "
                                   "**No Non-Vanilla Blocks**: only vanilla")
    finally:
        agents.known_block_names = old
    assert "**snowy gravel** (not a known block - check)" in out and "**basalt slabs** (not a known block - check)" in out, out
    assert out.count("(not a known block") == 2, out


@check("blocks: the list comes from the game jar's lang file plus installed mods; no jar = no flags")
def _():
    import json as _json, zipfile as _zip
    tmp = tempfile.mkdtemp()
    jar = os.path.join(tmp, "1.21.1.jar")
    with _zip.ZipFile(jar, "w") as z:
        z.writestr("assets/minecraft/lang/en_us.json", _json.dumps(
            {"block.minecraft.packed_ice": "Packed Ice", "item.minecraft.bone": "Bone", "block.minecraft.bed.occupied": "x"}))
    old_jar, old_names, old_index = config.MINECRAFT_JAR, rt._BLOCK_NAMES, rt._MOD_INDEX
    try:
        config.MINECRAFT_JAR, rt._BLOCK_NAMES = jar, None
        rt._MOD_INDEX = [{"jar": "w.jar", "kind": "lang", "text": "block.waystones.waystone = Waystone"}]
        names = rt.known_block_names()
        assert names == {"packed ice", "bone", "waystone"}, names
        config.MINECRAFT_JAR, rt._BLOCK_NAMES = os.path.join(tmp, "missing.jar"), None
        assert rt.known_block_names() == set()
    finally:
        config.MINECRAFT_JAR, rt._BLOCK_NAMES, rt._MOD_INDEX = old_jar, old_names, old_index
        shutil.rmtree(tmp, ignore_errors=True)


@check("researcher: sizes like 30x30x30 that aren't in the sources are flagged")
def _():
    out = agents._fact_check("Tower 30x30x30, cabin 8 x 8 x 8, dock 10x6", "the dock is 10x6 blocks", check_dims=True)
    assert "30x30x30 (unverified" in out and "8 x 8 x 8 (unverified" in out and "10x6 (unverified" not in out, out
    assert "(unverified" not in agents._fact_check("Tower 30x30x30", "nothing")          # off by default


@check("sources: Create/CC/AP docs are searched for Fort Redux only")
def _():
    names = {}
    for key in ("fort_redux", "rpg"):
        restore = _with_project(key)
        old = {n: getattr(agents, n) for n in ("search_local_mods", "search_mod_docs", "search_minecraft_wiki", "search_modrinth")}
        try:
            for n in old:
                setattr(agents, n, lambda *a, **k: "[x] none")
            _, used = agents._gather_sources("relics", {"task": "relics", "instructions": ""})
            text, _ = agents._gather_sources("relics", {"task": "relics", "instructions": ""})
            names[key] = text
        finally:
            for n, f in old.items():
                setattr(agents, n, f)
            restore()
    assert "MOD DOCS" in names["fort_redux"] and "MOD DOCS" not in names["rpg"], names


@check("orchestrator: a task asking for a design won't finish before the Designer runs")
def _():
    class Router:
        def with_structured_output(self, cls):
            class W:
                def invoke(_, msgs):
                    return cls(next="FINISH", instructions="", reason="done")
            return W()
    old_router, agents.router_llm = agents.router_llm, Router()
    old_names, agents.installed_mod_names = agents.installed_mod_names, lambda: []
    try:
        st = {"task": "Plan and design only, no code. Build spec for the keep.", "steps": 1,
              "history": ["architect: plan written"]}
        assert agents.orchestrator(st)["next"] == "designer"
        st["history"].append("designer: design written")
        assert agents.orchestrator(st)["next"] == "FINISH"       # 'no code': the Developer isn't required
        st = {"task": "Code only, no research needed. Write a datapack function.", "steps": 1, "history": []}
        assert agents.orchestrator(st)["next"] == "developer"
    finally:
        agents.router_llm, agents.installed_mod_names = old_router, old_names


@check("orchestrator: 'no research needed' keeps the Researcher out")
def _():
    assert not agents._allowed({"task": "Design only, no research needed.", "history": []}, "researcher")
    assert agents._allowed({"task": "Check the Relics versions.", "history": []}, "researcher")


@check("code check: // comments, ~ builds and the wrong function folder are flagged after the block")
def _():
    code = ("File data/zombiebase/function/x.mcfunction\n```mcfunction\n// hi\nfill ~ ~ ~ ~1 ~1 ~1 stone\n"
            "tellraw @a \"ok\"\n```")
    out = agents._check_code(code)               # fort_redux = 1.20.1 by default
    assert "(code check: line 1 uses a // comment" in out and "(code check: line 2 builds with ~" in out, out
    assert "(code check: 'function/' folder is 1.21+; 1.20.1 uses 'functions/')" in out, out
    assert out.index("```", out.index("```") + 3) < out.index("(code check:"), "notes must come after the block"
    clean = "data/zombiebase/functions/x.mcfunction\n```mcfunction\n# hi\ntellraw @a \"ok\"\n```"
    assert agents._check_code(clean) == clean


@check("prompts: Create guidance only on Fort Redux runs")
def _():
    restore = _with_project("rpg")
    try:
        assert not config.MOD_DOCS
    finally:
        restore()
    assert config.MOD_DOCS and "Create facts" in agents._CREATE_DESIGN_FACTS


@check("curseforge: no key = a clear note, never a crash or a web call")
def _():
    old, rt._secret = rt._secret, lambda name: ""       # also hides a real key in the Windows registry
    try:
        assert rt.search_curseforge("relics").startswith("[CurseForge] No API key set")
    finally:
        rt._secret = old


@check("curseforge: filtered to the project's loader/version, key only in the header, Pinned when no build")
def _():
    seen = []

    def fake(url, headers=None, *a, **k):
        seen.append((url, dict(headers or {})))
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        if "modLoaderType" in q:
            return {"data": []}
        return {"data": [{"name": "Saint's Dragons", "summary": "dragons", "links": {"websiteUrl": "https://cf/sd"},
                          "latestFilesIndexes": [{"gameVersion": "1.20.1", "filename": "sd-1.20.1.jar"}]}]}
    old_get, rt._get_json = rt._get_json, fake
    old_key = os.environ.get("CURSEFORGE_API_KEY")
    os.environ["CURSEFORGE_API_KEY"] = "test-key-123"
    restore = _with_project("rpg")
    try:
        out = rt.search_curseforge("Saint's Dragons NeoForge 1.21.1 build?")
    finally:
        restore()
        rt._get_json = old_get
        if old_key is None:
            os.environ.pop("CURSEFORGE_API_KEY", None)
        else:
            os.environ["CURSEFORGE_API_KEY"] = old_key
    q = urllib.parse.parse_qs(urllib.parse.urlparse(seen[0][0]).query)
    assert q["modLoaderType"] == ["6"] and q["gameVersion"] == ["1.21.1"] and q["searchFilter"] == ["Saint's Dragons"], seen[0]
    assert seen[0][1].get("x-api-key") == "test-key-123" and all("test-key-123" not in u for u, _ in seen)
    assert out.startswith("NO neoforge 1.21.1 build on CurseForge") and "test-key-123" not in out, out


@check("curseforge: a refused key says so without showing the key")
def _():
    def fake(url, headers=None, *a, **k):
        raise rt.urllib.error.HTTPError(url, 403, "Forbidden", {}, None)
    old_get, rt._get_json = rt._get_json, fake
    old_key = os.environ.get("CURSEFORGE_API_KEY")
    os.environ["CURSEFORGE_API_KEY"] = "secret-xyz"
    try:
        out = rt.search_curseforge("relics")
    finally:
        rt._get_json = old_get
        if old_key is None:
            os.environ.pop("CURSEFORGE_API_KEY", None)
        else:
            os.environ["CURSEFORGE_API_KEY"] = old_key
    assert "refused (HTTP 403)" in out and "secret-xyz" not in out and "10 characters" in out, out


@check("github: the token (if set) goes in the Authorization header")
def _():
    seen = []
    old_get, rt._get_json = rt._get_json, lambda url, headers=None, *a, **k: (seen.append(dict(headers or {})), {"items": []})[1]
    old_tok = os.environ.get("GITHUB_TOKEN")
    os.environ["GITHUB_TOKEN"] = "ghp_test"
    try:
        rt.search_github("create crash")
    finally:
        rt._get_json = old_get
        if old_tok is None:
            os.environ.pop("GITHUB_TOKEN", None)
        else:
            os.environ["GITHUB_TOKEN"] = old_tok
    assert seen and seen[0].get("Authorization") == "Bearer ghp_test", seen


@check("keys: read from the environment first; off Windows nothing else is tried")
def _():
    old = os.environ.get("GRAPH_TEST_SECRET")
    os.environ["GRAPH_TEST_SECRET"] = "  abc  "
    try:
        assert rt._secret("GRAPH_TEST_SECRET") == "abc"
    finally:
        if old is None:
            os.environ.pop("GRAPH_TEST_SECRET", None)
        else:
            os.environ["GRAPH_TEST_SECRET"] = old
    assert rt._secret("GRAPH_SURELY_UNSET_SECRET_NAME") == ""


passed = sum(results)
print(f"\n{passed}/{len(results)} checks passed" + ("" if passed == len(results) else "  <-- something is broken"))
sys.exit(0 if passed == len(results) else 1)
