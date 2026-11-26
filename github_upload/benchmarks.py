"""Benchmark tasks with known answers (added 25 Nov 2026). Standard library only.

A benchmark runs the whole team (model included) on a task whose right answer we already know,
then checks the saved report. The self-checks (run_tests.py) test the code; benchmarks test the team.
"""
import json
import os

BENCH_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "benchmarks.json")


def load_benchmarks() -> dict:
    with open(BENCH_FILE, encoding="utf-8") as f:
        data = json.load(f)
    return {k: v for k, v in data.items() if not k.startswith("_")}


def evaluate(report: str, bench: dict) -> tuple[bool, list[str]]:
    """(passed, one line per check). Every 'contains' group needs one match; no 'not_contains' may appear."""
    low = (report or "").lower()
    lines, ok = [], True
    for group in bench.get("contains", []):
        hit = next((alt for alt in group if alt.lower() in low), None)
        ok = ok and hit is not None
        lines.append(f"{'PASS' if hit else 'FAIL'}  has: {hit or ' / '.join(group)}")
    for bad in bench.get("not_contains", []):
        found = bad.lower() in low
        ok = ok and not found
        lines.append(f"{'FAIL' if found else 'PASS'}  free of: {bad}")
    return ok, lines
