# Agent Graph — Orchestrator + 6 agents

A LangGraph "supervisor" graph that runs on your local Ollama model (no API costs).

```
            ┌──────────────┐
   start ──▶│ Orchestrator │──FINISH──▶ finalize ──▶ end
            └──────┬───────┘
     ┌─────────┬───┴─────┬──────────┐
     ▼         ▼         ▼          ▼
 Architect  Designer  Developer  Minecraft Researcher
     └─────────┴────┬────┴──────────┘
                    └── report back to Orchestrator
```

- **Orchestrator** picks one agent at a time (or FINISH), with a step cap so it can't loop forever.
- **Architect** writes the plan, **Designer** the look and layout, **Developer** the code/configs.
- **Minecraft Researcher** searches minecraft.wiki (vanilla) and Modrinth (mods like Create, TACZ) — no API keys needed.
- All agents share one state, so each sees what the others produced.

## Setup (Windows)
1. Make sure Ollama is running and pull the model once:
   `ollama pull llama3.1:8b`
2. Put this folder anywhere and double-click `run_graph.bat`
   (first run creates `.venv` and installs packages).
3. Type a task, e.g. *"Plan the underground train station level for my base"*.

Or from a terminal in this folder:
```
run_graph.bat "Design a storage room layout using Create"
run_graph.bat --diagram
```

Reports are saved to `output\report_<date>.md`.

## Projects (added 25 Nov 2026)
The team works on more than one project, and each keeps its own notes and modpack folder so they never mix:

| Project | Notes file | Modpack instance | Game |
|---|---|---|---|
| `fort_redux` (default) | `project.md` | `Zombies` | Forge 1.20.1 |
| `rpg` | `projects/rpg.md` | `Cinema` | NeoForge 1.21.1 |

- From Claude: `run_team(task, project="rpg")`; `read_project_notes`, `add_project_note` and `test_research_source` take `project` too.
- From a terminal: `run_graph.bat --project rpg "task"`.
- Add a project in `PROJECTS` in `config.py`. Modrinth results are filtered to the project's loader and version;
  if a mod has no build for them, the Researcher is told so (that mod is "Pinned").
- Tip: add "no code" or "review only" to a task that shouldn't produce code, so the Developer stays out of it.

## Pack Engineer (added 25 Nov 2026)
Checks installed mods in code with `dependency_report` (research_tools.py): for the mods a task names (or the whole
pack if it names none), every required mod is looked up in the jars and its version checked against the range the
mod asks for. Problems are marked `<< PROBLEM`; the agent only explains the result. Tasks mentioning compatibility,
dependencies, the mod list, conflicts or crashes bring it in automatically. Each project lists its agents in
`PROJECTS[...]["workers"]` in config.py.

## Master Builder (added 25 Nov 2026)
Turns a plan or design into a buildable spec: a size table (Part | W x D x H | blocks), layer-by-layer steps and
terrain shaping (the Terraformer is folded in). Block counts are worked out in code from the size table
(`BLOCK ESTIMATES`), and every agent's "A x B = C" sums are checked. Tasks asking for a build spec, blueprint,
layer-by-layer plan, block counts or terrain bring it in.

## Keeping it working (added 25 Nov 2026)
- **Self-checks:** `run_tests.py` (or the `self_test` tool from Claude) tests the code in about a second, no model needed.
  Run it after every change; add a check whenever a bug is fixed.
- **Benchmarks:** `benchmarks.json` holds tasks with known answers. `run_benchmark(name)` runs the whole team on one
  and scores the report (PASS/FAIL per check in `get_run_status`). Run them after changing prompts, the model or tools.
- **Report header:** every report starts with a `Checks:` line counting what the checks flagged
  (corrections, unverified file names, removed links...). "nothing flagged" is the goal.

## API keys (optional, added 25 Nov 2026)
Set these as Windows **user environment variables**, then restart the Claude app. They stay out of the code and notes.
- `GITHUB_TOKEN`: a fine-grained, read-only token for public repos. Lifts GitHub's search limit.
- `CURSEFORGE_API_KEY`: from console.curseforge.com (API keys). Adds CurseForge as a source, filtered to each
  project's loader and version. Without it the source just says it wasn't searched.
Check them with `test_research_source("curseforge", "relics", project="rpg")` and `test_research_source("github", "create crash")`.

## Fort Redux project hookup
- `project.md` holds your base notes. Every agent reads it, so keep it updated as the base changes.
- The Researcher and Developer get the real mod list from your Zombies instance's `mods` folder (read-only; nothing in the modpack is changed).
- To point it at a different modpack, change `MODPACK_DIR` in `config.py`.

## Tweaks (config.py or environment variables)
| Setting | Default | What it does |
|---|---|---|
| `GRAPH_MODEL` | `llama3.1:8b` | Any Ollama model, e.g. `qwen2.5:7b` (often better at routing JSON) |
| `GRAPH_MAX_STEPS` | `8` | Max Orchestrator handoffs |
| `GRAPH_NUM_CTX` | `8192` | Context window |
| `GRAPH_MODPACK_CONTEXT` | 1.20.1 Forge, Create, TACZ | Background the Researcher assumes |

## Adding another agent
1. Write a node function in `agents.py` that returns `{"some_field": ..., "history": [...]}`.
2. Add its name to `WORKERS` and to the `Route.next` Literal, and describe it in `ORCHESTRATOR_PROMPT`.
3. `g.add_node("name", fn)` in `graph.py` — the edges loop is automatic.

## Notes
- If the 8B model sends bad routing JSON, the Orchestrator falls back to Architect → Designer → Developer → Finish.
- The Researcher needs internet; if a lookup fails it says so and the others carry on.
