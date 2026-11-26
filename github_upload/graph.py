"""Builds and runs the agent graph.

Usage:
    python graph.py "Plan an underground train station for my base"
    python graph.py --project rpg "Write the build spec for the Frost Keep"
    python graph.py --diagram      # print a Mermaid diagram of the graph
"""
import argparse
import os
import sys
from datetime import datetime

# --project must be known before config is imported (config picks the notes and modpack folder).
if "--project" in sys.argv:
    _i = sys.argv.index("--project")
    if _i + 1 < len(sys.argv):
        os.environ["GRAPH_PROJECT"] = sys.argv[_i + 1]

from langgraph.graph import END, StateGraph

import config
from agents import WORKERS, TeamState, architect, designer, developer, orchestrator, researcher


# Markers the checks in agents.py add to the agents' text, with how the summary names them (25 Nov 2026).
FLAG_KINDS = [
    ("(correction:", "corrections"),
    ("(file name not in sources", "unverified file names"),
    ("[link not in sources - removed]", "invented links removed"),
    ("(unverified", "unverified facts"),
    ("(not in research - check)", "numbers not in the research"),
    ("(timestamp not in the transcript", "unchecked video timestamps"),
    ("(new idea", "new ideas not in the task or research"),
    ("(not a known block", "unknown blocks"),
    ("(code check:", "code problems"),
    ("[cut off at the length limit]", "answers cut off"),
    ("[model timed out", "model timeouts"),
]


def checks_summary(text: str) -> str:
    """One line saying how many problems the checks flagged, so a report's reliability shows at a glance."""
    counts = [(text.count(marker), label) for marker, label in FLAG_KINDS]
    found = [f"{n} {label}" for n, label in counts if n]
    return "Checks: " + (", ".join(found) if found else "nothing flagged")


def finalize(state: TeamState) -> dict:
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    path = os.path.join(config.OUTPUT_DIR, f"report_{stamp}.md")
    body = "\n".join(state.get(k) or "" for k in ("plan", "research", "design", "code"))
    sections = [
        f"# {state['task']}",
        f"Project: {config.PROJECT_NAME}",
        checks_summary(body),
        "## Architect plan", state.get("plan") or "_not needed_",
        "## Minecraft research", state.get("research") or "_not needed_",
        "## Design", state.get("design") or "_not needed_",
        "## Code", state.get("code") or "_not needed_",
        "## Work log", "\n".join(f"- {h}" for h in state.get("history", [])),
    ]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n\n".join(sections))
    return {"report_path": path, "history": [f"finalize: saved {path}"]}


def build_graph():
    g = StateGraph(TeamState)
    g.add_node("orchestrator", orchestrator)
    g.add_node("architect", architect)
    g.add_node("designer", designer)
    g.add_node("developer", developer)
    g.add_node("researcher", researcher)
    g.add_node("finalize", finalize)

    g.set_entry_point("orchestrator")
    g.add_conditional_edges(
        "orchestrator",
        lambda s: s["next"],
        {**{w: w for w in WORKERS}, "FINISH": "finalize"},
    )
    for w in WORKERS:                 # every worker reports back to the Orchestrator
        g.add_edge(w, "orchestrator")
    g.add_edge("finalize", END)
    return g.compile()


def main():
    parser = argparse.ArgumentParser(description="Orchestrator + 4 agent graph")
    parser.add_argument("task", nargs="*", help="What you want the team to do")
    parser.add_argument("--diagram", action="store_true", help="Print Mermaid diagram and exit")
    parser.add_argument("--project", default=config.PROJECT, choices=list(config.PROJECTS),
                        help="Which project's notes and modpack to use (default fort_redux)")
    args = parser.parse_args()

    app = build_graph()
    if args.diagram:
        print(app.get_graph().draw_mermaid())
        return

    task = " ".join(args.task).strip() or input("Task for the team: ").strip()
    if not task:
        print("No task given.")
        return

    print(f"\nProject: {config.PROJECT_NAME} | Model: {config.MODEL} | max handoffs: {config.MAX_STEPS}\n")
    state = {"task": task, "steps": 0, "history": []}
    for update in app.stream(state, {"recursion_limit": config.MAX_STEPS * 2 + 5}, stream_mode="updates"):
        for node, change in update.items():
            for line in (change or {}).get("history", []):
                print(f"  [{node}] {line}")
    print("\nDone. Check the output folder for the full report.")


if __name__ == "__main__":
    main()
