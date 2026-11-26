"""Exposes the agent team to the Claude desktop app as tools (MCP server).

Claude starts this automatically once it's added to claude_desktop_config.json.
Runs happen in the background, so Claude can start one and check back later.
Note: nothing here may print to stdout (that channel is used to talk to Claude).
"""
import glob
import os
import sys
import threading
import traceback
import uuid
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
_ERROR_LOG = os.path.join(_HERE, "mcp_error.log")
os.chdir(_HERE)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)


def _log_crash(exc_type, exc, tb):
    with open(_ERROR_LOG, "a", encoding="utf-8") as f:
        f.write(f"\n=== {datetime.now():%Y-%m-%d %H:%M:%S} ===\n")
        traceback.print_exception(exc_type, exc, tb, file=f)


sys.excepthook = _log_crash

# mcp 2.x renamed FastMCP to MCPServer; support both.
try:
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:
    from mcp.server.fastmcp import FastMCP as _Server

import config
from graph import build_graph

mcp = _Server("fort-redux-team")
_app = build_graph()
_runs: dict[str, dict] = {}
_lock = threading.Lock()


def _fresh_app():
    """Reload the team's code before each run, so edits to config/agents/tools apply without
    restarting the Claude app (added 9 Nov 2026). Falls back to the startup graph if a reload fails."""
    import importlib
    import agents, graph, project_context, research_tools
    try:
        for mod in (config, project_context, research_tools, agents, graph):
            importlib.reload(mod)
        return graph.build_graph()
    except Exception:
        _log_crash(*sys.exc_info())
        # The startup graph is built for fort_redux; never run another project on it (25 Nov 2026).
        if os.environ.get("GRAPH_PROJECT", "fort_redux") != "fort_redux":
            raise RuntimeError("Reloading the team's code failed (see mcp_error.log), so this "
                               f"'{os.environ['GRAPH_PROJECT']}' run was stopped instead of using Fort Redux settings.")
        return _app


def _use_project(project: str) -> str:
    """Point config at a project before the modules are reloaded (25 Nov 2026). Returns an error text
    for an unknown project, else ''."""
    key = (project or "fort_redux").strip().lower()
    if key not in config.PROJECTS:
        return f"Unknown project '{project}'. Use one of: {', '.join(config.PROJECTS)}"
    os.environ["GRAPH_PROJECT"] = key
    return ""


def _worker(run_id: str, task: str):
    run = _runs[run_id]
    try:
        _use_project(run["project"])     # set again here: another tool call may have switched it
        state = {"task": task, "steps": 0, "history": []}
        final = _fresh_app().invoke(state, {"recursion_limit": config.MAX_STEPS * 2 + 5})
        run["report_path"] = final.get("report_path", "")
        run["history"] = final.get("history", [])
        if run.get("bench"):
            _score_benchmark(run)
        run["status"] = "done"
    except Exception:
        run["status"] = "failed"
        run["error"] = traceback.format_exc(limit=3)
    finally:
        run["finished"] = datetime.now().strftime("%H:%M:%S")


def _score_benchmark(run: dict) -> None:
    import benchmarks
    try:
        with open(run["report_path"], encoding="utf-8") as f:
            report = f.read()
        ok, lines = benchmarks.evaluate(report, run["bench"])
        run["bench_result"] = ("PASS" if ok else "FAIL") + "\n" + "\n".join(lines)
    except Exception as e:
        run["bench_result"] = f"FAIL (could not score the report: {e})"


def _start(task: str, project: str, bench: dict | None = None, name: str = "") -> str:
    with _lock:
        busy = [r for r in _runs.values() if r["status"] == "running"]
        if busy:
            return f"A run is already in progress (id {busy[0]['id']}). Wait for it to finish."
        err = _use_project(project)
        if err:
            return err
        run_id = uuid.uuid4().hex[:8]
        _runs[run_id] = {"id": run_id, "task": task, "status": "running", "project": os.environ["GRAPH_PROJECT"],
                         "started": datetime.now().strftime("%H:%M:%S"), "bench": bench, "bench_name": name}
    threading.Thread(target=_worker, args=(run_id, task), daemon=True).start()
    label = f" benchmark '{name}'" if name else ""
    return f"Started{label} run {run_id} [{os.environ['GRAPH_PROJECT']}]: {task}"


@mcp.tool()
def run_team(task: str, project: str = "fort_redux") -> str:
    """Start the agent team (Orchestrator, Architect, Designer, Developer, Minecraft Researcher)
    on a task for one project: "fort_redux" (Zombies modpack, 1.20.1 Forge) or "rpg" (cinematic RPG
    modpack, NeoForge 1.21.1). Each project has its own notes and modpack folder.
    Returns a run id right away; the run takes a few minutes on the local model.
    Check it with get_run_status, then read get_report."""
    return _start(task, project)


@mcp.tool()
def run_benchmark(name: str = "") -> str:
    """Run one benchmark task with a known answer (from benchmarks.json) and score its report.
    No name: lists the benchmarks. Check the result with get_run_status (shows PASS/FAIL per check)."""
    import importlib
    import benchmarks
    importlib.reload(benchmarks)
    try:
        benches = benchmarks.load_benchmarks()
    except (OSError, ValueError) as e:
        return f"Could not read benchmarks.json: {e}"
    if not name:
        return "Benchmarks: " + ", ".join(f"{k} [{v.get('project', 'fort_redux')}]" for k, v in benches.items())
    bench = benches.get(name.strip())
    if not bench:
        return f"Unknown benchmark '{name}'. Use one of: {', '.join(benches)}"
    return _start(bench["task"], bench.get("project", "fort_redux"), bench, name.strip())


@mcp.tool()
def self_test() -> str:
    """Run run_tests.py (the code self-checks: no model, no internet) on this computer and return the
    result. Run it after every change to the team's code."""
    import subprocess
    try:
        done = subprocess.run([sys.executable, os.path.join(_HERE, "run_tests.py")], cwd=_HERE,
                              capture_output=True, text=True, timeout=180,
                              env={**os.environ, "GRAPH_PROJECT": "fort_redux"})
    except subprocess.TimeoutExpired:
        return "Self-test timed out after 180 seconds."
    out = (done.stdout or "") + (done.stderr or "")
    fails = [ln for ln in out.splitlines() if not ln.startswith("PASS")]
    return "\n".join(fails).strip()[-6000:] or "(no output)"


@mcp.tool()
def get_run_status(run_id: str = "") -> str:
    """Status of a run (or the latest run if no id is given)."""
    if not _runs:
        return "No runs yet this session."
    run = _runs.get(run_id) or list(_runs.values())[-1]
    lines = [f"Run {run['id']} [{run.get('project', 'fort_redux')}] - {run['status']} (started {run['started']})",
             f"Task: {run['task']}"]
    if run.get("finished"):
        lines.append(f"Finished {run['finished']}")
    if run.get("report_path"):
        lines.append(f"Report: {run['report_path']}")
    if run.get("bench_result"):
        lines.append(f"Benchmark '{run['bench_name']}': {run['bench_result']}")
    if run.get("error"):
        lines.append(f"Error:\n{run['error']}")
    return "\n".join(lines)


@mcp.tool()
def get_report(run_id: str = "") -> str:
    """Full text of a run's report, or the newest saved report if no id is given."""
    path = _runs.get(run_id, {}).get("report_path") if run_id else ""
    if not path:
        reports = sorted(glob.glob(os.path.join(config.OUTPUT_DIR, "report_*.md")))
        if not reports:
            return "No reports saved yet."
        path = reports[-1]
    with open(path, encoding="utf-8") as f:
        return f"({os.path.basename(path)})\n\n" + f.read()


@mcp.tool()
def list_reports(limit: int = 10) -> str:
    """List the most recent saved reports with their titles."""
    reports = sorted(glob.glob(os.path.join(config.OUTPUT_DIR, "report_*.md")))[-limit:]
    if not reports:
        return "No reports saved yet."
    out = []
    for p in reversed(reports):
        with open(p, encoding="utf-8") as f:
            title = f.readline().lstrip("# ").strip()
        out.append(f"- {os.path.basename(p)}: {title}")
    return "\n".join(out)


@mcp.tool()
def test_research_source(source: str, query: str, project: str = "fort_redux") -> str:
    """Run one of the Researcher's lookups directly and return its raw text (for checking a source works).
    source: local_mods, mod_docs, github, youtube, wiki, modrinth, curseforge or blocks (the real block list the checks
    use). project: fort_redux or rpg
    (decides which modpack folder and which loader/version are searched). Read-only."""
    import importlib
    import research_tools as rt
    busy = [r for r in _runs.values() if r["status"] == "running"]
    if busy:      # reloading the tools mid-run would switch the running team's project/modpack folder
        return f"A run is in progress (id {busy[0]['id']}); test sources after it finishes."
    err = _use_project(project)
    if err:
        return err
    importlib.reload(config)
    importlib.reload(rt)                 # always test the file as it is on disk now
    fns = {"local_mods": rt.search_local_mods, "mod_docs": rt.search_mod_docs, "github": rt.search_github,
           "youtube": rt.search_youtube, "wiki": rt.search_minecraft_wiki, "modrinth": rt.search_modrinth,
           "blocks": rt.search_blocks, "curseforge": rt.search_curseforge}
    fn = fns.get(source.strip().lower())
    if not fn:
        return "Unknown source. Use one of: " + ", ".join(fns)
    started = datetime.now()
    text = fn(query)
    return f"[{source} | {(datetime.now() - started).total_seconds():.1f}s | {len(text)} chars]\n{text}"


def _notes_file(project: str) -> tuple[str, str]:
    """(path, error) for a project's notes file, without touching the running config."""
    p = config.PROJECTS.get((project or "fort_redux").strip().lower())
    if not p:
        return "", f"Unknown project '{project}'. Use one of: {', '.join(config.PROJECTS)}"
    return p["notes"], ""


@mcp.tool()
def read_project_notes(project: str = "fort_redux") -> str:
    """Read a project's notes, which every agent uses on that project's runs.
    project: fort_redux (project.md) or rpg (projects/rpg.md)."""
    path, err = _notes_file(project)
    if err:
        return err
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return f"Notes file not found: {path}"


@mcp.tool()
def add_project_note(note: str, section: str = "Progress log", project: str = "fort_redux") -> str:
    """Append a dated note (e.g. 'Storage room built') to a project's notes under a section,
    creating the section if needed. project: fort_redux or rpg. Agents read it on the next run."""
    path, err = _notes_file(project)
    if err:
        return err
    if not note.strip():
        return "Note is empty; nothing added."
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        text = f"# {config.PROJECTS[project.strip().lower()]['name']} - project notes\n"
    line = f"- {datetime.now():%Y-%m-%d}: {note.strip()}"
    header = f"## {section}"
    if header in text:
        start = text.index(header) + len(header)
        nxt = text.find("\n## ", start)
        insert_at = len(text) if nxt == -1 else nxt
        text = text[:insert_at].rstrip("\n") + "\n" + line + "\n" + text[insert_at:]
    else:
        text = text.rstrip("\n") + f"\n\n{header}\n{line}\n"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    msg = f"Added to '{section}' in {os.path.basename(path)}: {note.strip()}"
    # Agents read only the first NOTES_CHARS characters (older progress-log lines are dropped first).
    if len(text) > 0.8 * config.NOTES_CHARS:
        msg += (f"\nNote: {os.path.basename(path)} is now {len(text):,} characters; agents read {config.NOTES_CHARS:,}. "
                "Older progress-log entries will be left out of what they see - consider tidying the file.")
    return msg


if __name__ == "__main__":
    try:
        mcp.run()
    except Exception:
        _log_crash(*sys.exc_info())
        raise
