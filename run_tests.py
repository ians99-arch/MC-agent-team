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
    assert "developer" not in line and "architect" not in line and "researcher" in line and "FINISH" in line, line


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
        out = rt.search_modrinth("Saint's Dragons NeoForge 1.21.1 boss mods")
    finally:
        restore()
        rt._get_json = old
    assert calls[0] == "Saint's Dragons boss" and "Saint's Dragons" in calls, calls
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
        assert agents.orchestrator(st)["next"] == "master_builder"   # 'build spec' asks for the Master Builder
        st["history"].append("master_builder: spec written")
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
    old_get, rt._get_json = rt._get_json, lambda *a, **k: {"items": []}
    try:
        os.environ["GITHUB_TOKEN"] = "ghp_test"
        out = rt.search_github("create crash")
    finally:
        rt._get_json = old_get
        if old_tok is None:
            os.environ.pop("GITHUB_TOKEN", None)
        else:
            os.environ["GITHUB_TOKEN"] = old_tok
    assert "[token used]" in out and "ghp_test" not in out, out


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


@check("github: a token GitHub rejects (HTTP 422) falls back to searching without it")
def _():
    calls = []

    def fake(url, headers=None, *a, **k):
        calls.append("Authorization" in (headers or {}))
        if "Authorization" in (headers or {}):
            raise rt.urllib.error.HTTPError(url, 422, "Validation Failed", {}, None)
        return {"items": []}
    old_get, rt._get_json = rt._get_json, fake
    old_tok = os.environ.get("GITHUB_TOKEN")
    os.environ["GITHUB_TOKEN"] = "ghp_test"
    try:
        out = rt.search_github("create deployer crash")
    finally:
        rt._get_json = old_get
        if old_tok is None:
            os.environ.pop("GITHUB_TOKEN", None)
        else:
            os.environ["GITHUB_TOKEN"] = old_tok
    assert calls == [True, False] and "[token refused - searched without it]" in out, (calls, out)


@check("pack: version ranges are checked the Maven way, unknowns stay unknown")
def _():
    r = rt.version_in_range
    assert r("0.12.11", "[0.12.8,)") and r("9.6.1+1.21.1", "[9.3.1+1.21.1,)") and r("13.2.5", "[13.2.5]")
    assert r("0.12.2", "[0.12.8,)") is False and r("13.2.6", "[13.2.5]") is False and r("2.0", "[1.0,2.0)") is False
    assert r("1.5", "(1.0,2.0]") and r("1.0", "(1.0,2.0]") is False and r("3.1", ">=3.0") and r("x", "*")
    assert r("abc", "[1.0,)") is None


FAKE_PACK = [
    {"jar": "relics-1.21.1-0.12.11.jar", "kind": "mod", "text": "Relics (id relics) version 0.12.11, file relics-1.21.1-0.12.11.jar"},
    {"jar": "relics-1.21.1-0.12.11.jar", "kind": "dependency", "text": "relics requires curios [9.3.1+1.21.1,)"},
    {"jar": "relics-1.21.1-0.12.11.jar", "kind": "dependency", "text": "relics requires neoforge [21.1,)"},
    {"jar": "curios.jar", "kind": "mod", "text": "Curios API (id curios) version 9.6.1+1.21.1, file curios.jar"},
    {"jar": "ra.jar", "kind": "mod", "text": "reliquified_artifacts (id reliquified_artifacts) version 1.0.9, file ra.jar"},
    {"jar": "ra.jar", "kind": "dependency", "text": "reliquified_artifacts requires artifacts [13.2.5]"},
    {"jar": "ra.jar", "kind": "dependency", "text": "reliquified_artifacts requires octolib [0.6,)"},
    {"jar": "ra.jar", "kind": "dependency", "text": "reliquified_artifacts optionally uses jei [1,)"},
    {"jar": "art.jar", "kind": "mod", "text": "Artifacts (id artifacts) version 13.2.4, file art.jar"},
]


def _with_pack(fn):
    old_index, old_dir = rt._MOD_INDEX, config.MODPACK_DIR
    tmp = tempfile.mkdtemp()
    os.makedirs(os.path.join(tmp, "mods"))
    rt._MOD_INDEX, config.MODPACK_DIR = list(FAKE_PACK), tmp
    try:
        return fn()
    finally:
        rt._MOD_INDEX, config.MODPACK_DIR = old_index, old_dir
        shutil.rmtree(tmp, ignore_errors=True)


@check("pack: named mods get every required mod checked; wrong versions and missing mods are problems")
def _():
    out = _with_pack(lambda: rt.dependency_report("Check Relics and Reliquified Artifacts"))
    assert "mods named in the task" in out and "Relics 0.12.11 (relics-1.21.1-0.12.11.jar)" in out, out
    assert "needs curios [9.3.1+1.21.1,) -> installed 9.6.1+1.21.1 OK" in out, out
    assert "needs artifacts [13.2.5] -> installed 13.2.4 (wrong version)  << PROBLEM" in out, out
    assert "needs octolib [0.6,) -> NOT INSTALLED  << PROBLEM" in out, out
    assert "jei" not in out and "neoforge [21.1" not in out, out      # optional deps and the loader are skipped
    assert out.endswith("Problems: 2"), out


@check("pack: with no mods named, the whole pack is scanned and only mods with problems are listed")
def _():
    out = _with_pack(lambda: rt.dependency_report("Is my pack healthy?"))
    assert "whole pack, 4 mods" in out and "Reliquified Artifacts 1.0.9" in out and "Relics 0.12.11" not in out, out


@check("pack engineer: the check and its summary are both written by code, matching each other")
def _():
    out = _with_pack(lambda: agents.pack_engineer({"task": "Check Relics and Reliquified Artifacts", "history": []}))
    assert out["pack"].startswith("## DEPENDENCY CHECK") and out["history"] == ["pack_engineer: checked"], out
    assert "- Missing: install octolib (version [0.6,))." in out["pack"], out["pack"]
    assert "- Wrong version: artifacts 13.2.4 is installed, but [13.2.5] is required." in out["pack"], out["pack"]
    ok = agents._pack_summary("## DEPENDENCY CHECK\nX 1 (x.jar)\n  - needs y [1,) -> installed 2 OK\nProblems: none")
    assert ok.startswith("- No problems") and "Wrong" not in ok, ok


@check("range check: a false 'outside the range' claim is corrected, a true one is left alone")
def _():
    out = agents._range_check("- Create version 6.0.8 is outside the allowed range [6.0.7, 6.1.0).\n"
                              "- Artifacts 13.2.4 doesn't satisfy [13.2.5]; downgrade not needed.")
    assert "(correction: 6.0.8 IS within [6.0.7,6.1.0), so it's compatible)" in out, out
    assert out.count("(correction:") == 1, out


@check("orchestrator: compatibility tasks bring in the Pack Engineer; it only runs once")
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
        st = {"task": "Review only, no code. Are these mods compatible?", "steps": 1, "history": []}
        assert agents.orchestrator(st)["next"] == "pack_engineer"
        st["history"].append("pack_engineer: checked")
        assert agents.orchestrator(st)["next"] == "FINISH"
        assert not agents._allowed(st, "pack_engineer")
    finally:
        agents.router_llm, agents.installed_mod_names = old_router, old_names


@check("orchestrator: on compatibility tasks the Researcher waits for the Pack Engineer")
def _():
    st = {"task": "Are these mods compatible?", "history": []}
    assert not agents._allowed(st, "researcher")
    st["history"].append("pack_engineer: checked")
    assert agents._allowed(st, "researcher")
    assert agents._WANTS[2][0] == "pack_engineer"


@check("projects: an agent not on the project's roster is never picked")
def _():
    old = config.WORKERS
    config.WORKERS = ["architect", "designer", "developer", "researcher"]
    try:
        assert not agents._allowed({"task": "compatible?", "history": []}, "pack_engineer")
    finally:
        config.WORKERS = old


@check("math check: wrong multiplication is corrected, right sums and unit sums are left alone")
def _():
    out = agents._math_check("Walls 9 x 9 = 72, floor 9 x 9 = 81, tower 10 x 10 x 8 = 800, 4 x 256 = 1,024, 8 x 32 RPM = 256 SU")
    assert "9 x 9 = 72 (correction: = 81)" in out and out.count("(correction:") == 1, out


@check("block estimates: each W x D x H row of the size table gets counts from code")
def _():
    spec = ("| Part | Size (W x D x H blocks) | Main blocks |\n|---|---|---|\n"
            "| Stump wall | 9 x 9 x 8 | stone bricks |\n| **Archway** | 3 x 1 x 5 | stone bricks |\n"
            "| Surrounding Terrain | 10 x 10 x 3 | tuff |")
    est = agents._block_estimates(spec)
    assert "Stump wall (9 x 9 x 8): walls 256 blocks (4 stacks); floor 81 and roof 81 more if it has them" in est, est
    assert "Archway (3 x 1 x 5): walls 15 blocks (1 stack)" in est and "Part (" not in est and "Terrain" not in est, est
    assert agents._block_estimates("no table here") == ""


@check("master builder: build-spec tasks bring it in, and its output carries the code's estimates")
def _():
    assert agents._WANTS[3][0] == "master_builder" and agents._WANTS[3][2].search("Write a build spec")
    old_ask = agents._ask
    agents._ask = lambda *a, **k: "| Part | Size | Blocks |\n|---|---|---|\n| Tower | 5 x 5 x 4 | stone bricks |\n1. Lay the floor."
    old_names = agents.known_block_names
    agents.known_block_names = lambda: set()
    try:
        out = agents.master_builder({"task": "build spec for a tower", "history": []})
    finally:
        agents._ask, agents.known_block_names = old_ask, old_names
    assert out["history"] == ["master_builder: spec written"] and "## BLOCK ESTIMATES" in out["build"], out
    assert "Tower (5 x 5 x 4): walls 64 blocks (1 stack)" in out["build"], out["build"]


@check("known mistakes: 'blue ice emits light' gets a correction (it gives no light)")
def _():
    out = agents._known_mistakes("- Place Blue Ice on the outer wall to emit cold blue light.\n- Lanterns give warm light.")
    assert "(correction: ice and snow give off no light" in out and out.count("(correction:") == 1, out



@check("lore check: near-miss names and a wrong cause for Dahaad's fall are flagged; canon text passes")
def _():
    restore = _with_project("rpg")
    try:
        bad = agents._lore_check("The road to Selhaaven was long.\n\nThe kingdom of Dahaad fell to war and fire.")
        assert "(lore check: 'Selhaaven' looks like a misspelling of 'Selhaven')" in bad, bad
        assert "fell to something other than the frost" in bad, bad
        old_kb = agents.known_block_names
        agents.known_block_names = lambda: {"deepslate", "polished deepslate"}
        try:
            assert "(lore check:" not in agents._lore_check("Walls of Deepslate rise over the ruins.")
        finally:
            agents.known_block_names = old_kb
        good = "The frost came down on Dahaad and the kingdom fell. Selhaven kept its fires. Page 1"
        assert agents._lore_check(good) == good, agents._lore_check(good)
        long = agents._lore_check("**Page 1**\n" + "The frost came. " * 30 + "\n\n**Page 2**\nShort.")
        assert "(lore check: Page 1 is 479 characters" in long and "Page 2 is" not in long, long
    finally:
        restore()
    assert agents._lore_check("Selhaaven fell to war") == "Selhaaven fell to war"  # fort_redux: no canon, no check


@check("lore writer: rpg only, lore tasks bring it in, and its output goes through the lore check")
def _():
    restore = _with_project("rpg")
    try:
        assert "lore_writer" in config.WORKERS
        assert agents._WANTS[4][0] == "lore_writer" and agents._WANTS[4][2].search("Write the king's last journal")
        old_ask, old_names = agents._ask, agents.known_block_names
        agents._ask = lambda *a, **k: "Page 1\nThe ice took Dahaad. I write from Selhavn."
        agents.known_block_names = lambda: set()
        try:
            out = agents.lore_writer({"task": "journal", "history": []})
        finally:
            agents._ask, agents.known_block_names = old_ask, old_names
        assert out["history"] == ["lore_writer: written"] and "misspelling of 'Selhaven'" in out["lore"], out
    finally:
        restore()
    restore = _with_project("fort_redux")
    try:
        assert "lore_writer" not in config.WORKERS
    finally:
        restore()


@check("tier check: an early tier using better material than every later tier is flagged; a rising ladder passes")
def _():
    bad = agents._tier_check("| Tier 1 | wolves | netherite sword | hide |\n| Tier 2 | bears | diamond axe | fang |")
    assert "(tier check: Tier 1 uses netherite, better than anything in the later tiers" in bad, bad
    good = "| Tier 1 | wolves | stone spear | hide |\n| Tier 2 | bears | iron axe |\n| Tier 3 | wyverns | diamond |"
    assert agents._tier_check(good) == good


@check("item check: unknown bold items are flagged, real and '(custom)' ones pass")
def _():
    old = agents.known_block_names
    agents.known_block_names = lambda: {"iron sword", "leather boots"}
    old_mods = agents.installed_mod_names
    agents.installed_mod_names = lambda: ["Fantasy Armor"]
    try:
        out = agents._check_items("**Iron Sword**, **Frost Fang Sword**, **Dahaad Scale** (custom), **Leather Boots**, "
                                  "**Fantasy Armor**")
    finally:
        agents.known_block_names, agents.installed_mod_names = old, old_mods
    assert out.count("(not a known item - check)") == 1 and "**Frost Fang Sword** (not a known item" in out, out


@check("gameplay designer: rpg only, runs after the Pack Engineer, output goes through tier and lore checks")
def _():
    restore = _with_project("rpg")
    try:
        task = "Design the first three hunting tiers"
        assert agents._still_needed({"task": task, "history": []}) == "pack_engineer"
        assert agents._still_needed({"task": "Design the trophy hall", "history": []}) == "designer"
        assert not agents._allowed({"task": task, "history": []}, "gameplay_designer")
        hist = ["pack_engineer: checked"]
        assert agents._still_needed({"task": task, "history": hist}) == "gameplay_designer"
        old_ask, old_names = agents._ask, agents.known_block_names
        agents._ask = lambda *a, **k: "| Tier 1 | netherite |\n| Tier 2 | iron |\nThe war ended Dahaad. Selhavn."
        agents.known_block_names = lambda: set()
        try:
            out = agents.gameplay_designer({"task": task, "history": hist})
        finally:
            agents._ask, agents.known_block_names = old_ask, old_names
        g = out["gameplay"]
        assert out["history"] == ["gameplay_designer: designed"] and "(tier check:" in g and "(lore check:" in g, g
    finally:
        restore()
    restore = _with_project("fort_redux")
    try:
        assert not agents._allowed({"task": "quest rewards", "history": ["pack_engineer: checked"]}, "gameplay_designer")
    finally:
        restore()


@check("tier check reads roman numerals; mods-used check flags mods that aren't installed")
def _():
    bad = agents._tier_check("| **Tier I** | netherite blade |\n| **Tier II** | iron |")
    assert "(tier check: Tier 1 uses netherite" in bad, bad
    old = agents.installed_mods
    agents.installed_mods = lambda: [("Reliquified Artifacts", "1", "reliquified_artifacts.jar"),
                                     ("Iron's Spells 'n Spellbooks", "1", "irons_spellbooks.jar"),
                                     ("Waystones", "1", "waystones.jar"),
                                     ("Tarnished Legacy", "1", "soulscraft_tarnished_legacy-1.21.1.jar")]
    try:
        out = agents._mods_used_check("## Mods used\n- **Iron's Spells 'n Spellbooks**: spells\n"
                                      "- **Soulscraft**: daggers\n- Waystones: travel\n- **Resourceful Lib**: crafting\n"
                                      "- **Frostcraft**: made up\n")
    finally:
        agents.installed_mods = old
    assert out.count("(mod check:") == 2 and "'Frostcraft' is not an installed mod" in out, out
    assert "Soulscraft" not in out.split("(mod check:", 1)[1], out
    assert "'Resourceful Lib' is a library" in out, out


@check("'gameplay only' keeps the team to the Pack Engineer and Gameplay Designer; a blocked pick goes there")
def _():
    restore = _with_project("rpg")
    try:
        st = {"task": "Gameplay only. Design the first tiers", "history": []}
        assert not agents._allowed(st, "architect") and not agents._allowed(st, "lore_writer")
        assert agents._allowed(st, "pack_engineer")
    finally:
        restore()


@check("dependency check: a mod loaded through a bridge (Voxy via Roxy) isn't flagged for its Fabric needs")
def _():
    import research_tools as rt
    old = rt._mod_records
    recs = [{"id": "voxy", "name": "Voxy", "version": "0.2.16", "jar": "voxy.jar",
             "deps": [("fabric-api", ">=0.91.1", True)]},
            {"id": "roxy", "name": "Roxy", "version": "0.4", "jar": "roxy.jar", "deps": []}]
    rt._mod_records = lambda: recs
    try:
        whole = rt.dependency_report("check the pack")
        named = rt.dependency_report("is Voxy ok?")
        rt._mod_records = lambda: recs[:1]
        alone = rt.dependency_report("check the pack")
    finally:
        rt._mod_records = old
    assert "Problems: none" in whole and "loaded through Roxy" in named, (whole, named)
    assert "fabric-api" in alone and "Problems: 1" in alone, alone


@check("sheet snapshot: statuses are read, Dropped and planned mods get their own notes in the mods check")
def _():
    import tempfile, research_tools as rt
    restore = _with_project("rpg")
    tmp = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8")
    tmp.write("Mod,Status,In Cinema\nEpic Fight,Dropped,No\nWild Hunt,Recommended,No\nWaystones,Recommended,Yes\n")
    tmp.close()
    old_file, old_mods = config.SHEET_FILE, agents.installed_mods
    config.SHEET_FILE = tmp.name
    agents.installed_mods = lambda: [("Waystones", "1", "waystones.jar")]
    try:
        assert rt.sheet_status("Wild Hunt") == "Recommended" and rt.sheet_status("Frostcraft") == ""
        out = agents._mods_used_check("## Mods used\n- **Epic Fight**: combat\n- **Wild Hunt**: hunts\n"
                                      "- **Wild Hunt** (planned): contracts\n- **Waystones**: travel\n")
        assert "'Epic Fight' is Dropped on the mod sheet" in out, out
        assert out.count("'Wild Hunt' is on the mod sheet (Recommended) but not installed yet") == 1, out
        summary = agents._pack_summary("Problems: none")
        assert "1 Core/Recommended mods aren't in the pack yet: Wild Hunt" in summary, summary
    finally:
        config.SHEET_FILE, agents.installed_mods = old_file, old_mods
        os.unlink(tmp.name)
        restore()


@check("dependency check: 'fabric-api' is satisfied by Forgified Fabric API's 'fabric_api'")
def _():
    import research_tools as rt
    old = rt._mod_records
    rt._mod_records = lambda: [{"id": "somefabricmod", "name": "Some Mod", "version": "1", "jar": "x.jar",
                                "deps": [("fabric-api", ">=0.91.1", True)]},
                               {"id": "fabric_api", "name": "Forgified Fabric API", "version": "0.116.15", "jar": "f.jar",
                                "deps": []}]
    try:
        out = rt.dependency_report("check the pack")
    finally:
        rt._mod_records = old
    assert "Problems: none" in out, out


@check("routing: lore writer and gameplay designer only join tasks that ask for them")
def _():
    restore = _with_project("rpg")
    try:
        build = {"task": "Plan and design the trophy hall", "history": ["pack_engineer: checked"]}
        assert not agents._allowed(build, "lore_writer") and not agents._allowed(build, "gameplay_designer")
        assert agents._allowed({"task": "Write the legend of the crest", "history": []}, "lore_writer")
    finally:
        restore()


@check("shared checks: a Designer's output gets the lore check too (not just the Lore Writer's)")
def _():
    restore = _with_project("rpg")
    old_ask, old_names = agents._ask, agents.known_block_names
    agents._ask = lambda *a, **k: "The hall honours Selhaaven's founders."
    agents.known_block_names = lambda: set()
    try:
        out = agents.designer({"task": "Design the hall", "history": []})
    finally:
        agents._ask, agents.known_block_names = old_ask, old_names
        restore()
    assert "misspelling of 'Selhaven'" in out["design"], out


@check("routing: no architect on one-part tasks; fallback finishes instead of padding with architect/designer")
def _():
    assert not agents._allowed({"task": "Design only, no code. Describe the hall", "history": []}, "architect")
    assert agents._allowed({"task": "Plan and design only, no code.", "history": []}, "architect")
    st = {"task": "Review only, no code. Is the press ok?", "research": "something", "history": ["researcher: looked up x"]}
    assert agents._fallback_route(st).next == "FINISH"
    assert agents._fallback_route({"task": "Help with the base", "history": []}).next == "architect"


@check("look check: obsidian in an RPG output is flagged once; Fort Redux keeps its obsidian")
def _():
    restore = _with_project("rpg")
    try:
        out = agents._look_check("A frame of obsidian and blue ice. More obsidian.")
        assert out.count("(look check: obsidian is the Fort Redux look") == 1, out
        assert agents._look_check(out) == out
    finally:
        restore()
    restore = _with_project("fort_redux")
    try:
        assert agents._look_check("obsidian core") == "obsidian core"
    finally:
        restore()


@check("hollow check: a one-block-thick 5x5 tower laid 25 blocks a layer is flagged (the ring is 16)")
def _():
    task = "a square tower 5 blocks wide, 5 deep and 10 tall, one block thick"
    bad = agents._hollow_check("1. Base layer: place 25 deepslate bricks.", task)
    assert "(math check: a one-block-thick 5 x 5 wall is 16 blocks per layer" in bad, bad
    assert agents._hollow_check("1. Place 16 bricks in a ring.", task) == "1. Place 16 bricks in a ring."
    assert "(math check:" in agents._hollow_check("2. Add another 5x5 deepslate bricks on top.", task)
    assert agents._hollow_check("Tower | 5 x 5 x 10 | bricks", task) == "Tower | 5 x 5 x 10 | bricks"
    assert agents._hollow_check("Place 25 bricks.", "a solid 5x5 platform") == "Place 25 bricks."


@check("block estimates read '- Part | 5 x 5 x 10 | ...' rows too; 'build spec only' keeps the designer out")
def _():
    est = agents._block_estimates("**Part | Size | Blocks**\n- Tower | 5 x 5 x 10 | Deepslate Bricks\n")
    assert "Tower (5 x 5 x 10): walls 160 blocks" in est, est
    est = agents._block_estimates("**Part | Size (W x D x H blocks) | Main blocks**\n---|---|---\nLookout Tower | 5 x 5 x 10 | x\n")
    assert "Lookout Tower (5 x 5 x 10)" in est and "Part (" not in est, est
    assert not agents._allowed({"task": "Build spec only, no code.", "history": []}, "designer")
    assert agents._allowed({"task": "Plan and design only, no code.", "history": []}, "designer")


@check("known mistakes: iron bars as a ladder get a correction; ladder rows get no wall estimate")
def _():
    out = agents._known_mistakes("- Place Iron Bars vertically inside the tower forming a ladder structure.")
    assert "(correction: iron bars can't be climbed" in out, out
    est = agents._block_estimates("| Tower | 5 x 5 x 10 | bricks |\n| Ladder | 1 x 1 x 10 | ladder |")
    assert "Tower (5 x 5 x 10)" in est and "Ladder (" not in est, est


@check("context: sheet statuses of mods the task names reach every agent; query cleaner drops filler words")
def _():
    import tempfile, research_tools as rt
    tmp = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8")
    tmp.write("Mod,Status,In Cinema\nWild Hunt,Recommended,No\nEpic Fight,Dropped,No\n")
    tmp.close()
    old = config.SHEET_FILE
    config.SHEET_FILE = tmp.name
    try:
        ctx = agents._sheet_for_task("Does Wild Hunt have a NeoForge build?")
        assert "- Wild Hunt: Recommended on the sheet, NOT installed yet" in ctx and "Epic Fight" not in ctx, ctx
        assert agents._sheet_for_task("Design the hall") == ""
    finally:
        config.SHEET_FILE = old
        os.unlink(tmp.name)
    assert rt._modrinth_query("Wild Hunt NeoForge 1.21.1 build and dependencies") == "Wild Hunt"
    assert rt._modrinth_query("Dungeons and Taverns compatibility") == "Dungeons and Taverns"


@check("crash reader: names the mod that failed and whose code it's missing; ignores optional-hook warnings")
def _():
    import tempfile, research_tools as rt
    inst = tempfile.mkdtemp()
    for d in ("crash-reports", "logs", "mods"):
        os.makedirs(os.path.join(inst, d))
    log = ("[22:51:56] [main/WARN]: Error loading class: mekanism/client/render/RenderFlame "
           "(java.lang.ClassNotFoundException: mekanism.client.render.RenderFlame)\n"
           "[22:52:07] [modloading-worker-0/ERROR] [FMLModContainer/LOADING]: Failed to register automatic "
           "subscribers. ModID: morerelics\njava.lang.NoClassDefFoundError: it/hurts/sskirillss/relics/cast/CastType\n")
    open(os.path.join(inst, "crash-reports", "crash-1-client.txt"), "w").write("Description: Unexpected error\n")
    open(os.path.join(inst, "logs", "latest.log"), "w").write(log)
    old_dir, old_recs = config.MODPACK_DIR, rt._mod_records
    config.MODPACK_DIR = inst
    rt._mod_records = lambda: [{"id": "relics"}, {"id": "morerelics"}]
    try:
        out = rt.crash_report()
        summary = agents._pack_summary("Problems: none\n" + out)
    finally:
        config.MODPACK_DIR, rt._mod_records = old_dir, old_recs
    assert "- mod 'morerelics' failed to load" in out and "belongs to 'relics'" in out, out
    assert "mekanism" not in out, out
    assert "'morerelics' failed to load because it needs code that your installed 'relics' doesn't have" in summary
    assert "Nothing to change" not in summary, summary


@check("mod index: libraries bundled inside jars (META-INF/jarjar) count as installed")
def _():
    import io, tempfile, zipfile, research_tools as rt
    inst = tempfile.mkdtemp()
    os.makedirs(os.path.join(inst, "mods"))
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as zi:
        zi.writestr("META-INF/neoforge.mods.toml", '[[mods]]\nmodId="punchy_expressions"\nversion="1.0.9"\n')
    with zipfile.ZipFile(os.path.join(inst, "mods", "punchy-moves.jar"), "w") as z:
        z.writestr("META-INF/neoforge.mods.toml", '[[mods]]\nmodId="punchy_moves"\nversion="1.0.9b"\n'
                   '[[dependencies.punchy_moves]]\nmodId="punchy_expressions"\ntype="required"\nversionRange="[1.0.3,)"\n')
        z.writestr("META-INF/jarjar/punchy-expressions-1.0.9.jar", inner.getvalue())
    old_dir, old_cache, old_idx = config.MODPACK_DIR, rt.CACHE_DIR, rt._MOD_INDEX
    config.MODPACK_DIR, rt.CACHE_DIR, rt._MOD_INDEX = inst, tempfile.mkdtemp(), None
    try:
        out = rt.dependency_report("check the pack")
    finally:
        config.MODPACK_DIR, rt.CACHE_DIR, rt._MOD_INDEX = old_dir, old_cache, old_idx
    assert "Problems: none" in out, out


@check("mod index: bundled jars listed in jarjar/metadata.json are read; the newest copy of a mod wins")
def _():
    import io, json as js, tempfile, zipfile, research_tools as rt
    inst = tempfile.mkdtemp()
    os.makedirs(os.path.join(inst, "mods"))

    def jar(toml):
        b = io.BytesIO()
        with zipfile.ZipFile(b, "w") as zi:
            zi.writestr("META-INF/neoforge.mods.toml", toml)
        return b.getvalue()
    with zipfile.ZipFile(os.path.join(inst, "mods", "aaa-bundles-old-arch.jar"), "w") as z:
        z.writestr("META-INF/neoforge.mods.toml", '[[mods]]\nmodId="aaa"\nversion="1"\n')
        z.writestr("META-INF/jarjar/arch-13.0.8.jar", jar('[[mods]]\nmodId="architectury"\nversion="13.0.8"\n'))
    with zipfile.ZipFile(os.path.join(inst, "mods", "architectury-13.0.12.jar"), "w") as z:
        z.writestr("META-INF/neoforge.mods.toml", '[[mods]]\nmodId="architectury"\nversion="13.0.12"\n')
    with zipfile.ZipFile(os.path.join(inst, "mods", "lambdynamiclights.jar"), "w") as z:
        z.writestr("META-INF/neoforge.mods.toml", '[[mods]]\nmodId="lambdynlights"\nversion="4.8.11"\n'
                   '[[dependencies.lambdynlights]]\nmodId="lambdynlights_runtime"\ntype="required"\nversionRange="[4.8.11,)"\n'
                   '[[dependencies.lambdynlights]]\nmodId="architectury"\ntype="required"\nversionRange="[13.0.11,)"\n')
        z.writestr("META-INF/jarjar/metadata.json", js.dumps({"jars": [{"path": "META-INF/jars/runtime.jar"}]}))
        z.writestr("META-INF/jars/runtime.jar", jar('[[mods]]\nmodId="lambdynlights_runtime"\nversion="4.8.11"\n'))
    old_dir, old_cache, old_idx = config.MODPACK_DIR, rt.CACHE_DIR, rt._MOD_INDEX
    config.MODPACK_DIR, rt.CACHE_DIR, rt._MOD_INDEX = inst, tempfile.mkdtemp(), None
    try:
        out = rt.dependency_report("check the pack")
    finally:
        config.MODPACK_DIR, rt.CACHE_DIR, rt._MOD_INDEX = old_dir, old_cache, old_idx
    assert "Problems: none" in out, out


@check("versions: alpha < beta < rc < release, and plain versions still compare as before")
def _():
    import research_tools as rt
    assert rt.version_in_range("1.0.0-beta.1+1.21.1", "[1.0.0-alpha.13+1.21.1,)") is True
    assert rt.version_in_range("1.0.0-alpha.2", "[1.0.0-beta.1,)") is False
    assert rt.version_in_range("1.0.0", "[1.0.0-rc.1,)") is True
    assert rt.version_in_range("6.0.8", "[6.0.7,6.1.0)") is True and rt.version_in_range("13.0.8", "[13.0.11,)") is False


@check("crash reader: a clean log newer than the crash report says the crash is fixed")
def _():
    import tempfile, time, research_tools as rt
    inst = tempfile.mkdtemp()
    for d in ("crash-reports", "logs", "mods"):
        os.makedirs(os.path.join(inst, d))
    crash = os.path.join(inst, "crash-reports", "crash-old-client.txt")
    open(crash, "w").write("Caused by: java.lang.IllegalStateException: boom\n")
    os.utime(crash, (time.time() - 3600, time.time() - 3600))
    open(os.path.join(inst, "logs", "latest.log"), "w").write("[INFO] all good\n")
    old_dir, old_recs = config.MODPACK_DIR, rt._mod_records
    config.MODPACK_DIR, rt._mod_records = inst, lambda: []
    try:
        out = rt.crash_report()
    finally:
        config.MODPACK_DIR, rt._mod_records = old_dir, old_recs
    assert "no load errors" in out and "so that crash is fixed" in out, out

passed = sum(results)
print(f"\n{passed}/{len(results)} checks passed" + ("" if passed == len(results) else "  <-- something is broken"))
sys.exit(0 if passed == len(results) else 1)
