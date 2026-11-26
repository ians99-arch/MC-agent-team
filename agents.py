"""The five agents. Each node reads the shared state and writes its own section back."""
import json
import operator
import re
from typing import Annotated, Literal, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_ollama import ChatOllama
from pydantic import BaseModel, Field

import config
from project_context import load_project_notes, mod_list_text
from research_tools import (crash_report, sheet_rows, sheet_status, dependency_report, installed_mod_names, installed_mods, known_block_names, prune_web_cache, search_curseforge, search_github, search_local_mods, search_minecraft_wiki,
                            search_mod_docs, search_modrinth, search_youtube, stress_calculator)

PROJECT_NOTES = load_project_notes()
MOD_LIST = mod_list_text()

WORKERS = ["architect", "designer", "developer", "researcher", "pack_engineer", "master_builder", "lore_writer",
           "gameplay_designer"]   # every agent; projects pick theirs in config


# ---------- Shared state ----------
class TeamState(TypedDict, total=False):
    task: str
    plan: str          # Architect
    design: str        # Designer
    code: str          # Developer
    research: str      # Minecraft Researcher
    pack: str          # Pack Engineer (dependency check + explanation)
    build: str         # Master Builder (build spec, terrain steps, block estimates)
    lore: str          # Lore Writer (in-world text)
    gameplay: str      # Gameplay Designer (loop, tiers, quests, rewards)
    instructions: str  # Orchestrator's note to the next agent
    next: str
    steps: int
    report_path: str
    history: Annotated[list[str], operator.add]


_GUARDS = dict(repeat_penalty=config.REPEAT_PENALTY, repeat_last_n=config.REPEAT_LAST_N,
               reasoning=config.THINKING, client_kwargs={"timeout": config.REQUEST_TIMEOUT})

llm = ChatOllama(model=config.MODEL, base_url=config.OLLAMA_URL, temperature=0.3,
                 num_ctx=config.NUM_CTX, num_predict=config.MAX_OUTPUT_TOKENS, **_GUARDS)
research_llm = ChatOllama(model=config.MODEL, base_url=config.OLLAMA_URL, temperature=0.2,
                          num_ctx=config.NUM_CTX, num_predict=config.RESEARCH_OUTPUT_TOKENS, **_GUARDS)
design_llm = ChatOllama(model=config.MODEL, base_url=config.OLLAMA_URL, temperature=0.3,
                        num_ctx=config.NUM_CTX, num_predict=config.DESIGN_OUTPUT_TOKENS, **_GUARDS)
query_llm = ChatOllama(model=config.MODEL, base_url=config.OLLAMA_URL, temperature=0.2,
                       num_ctx=config.NUM_CTX, num_predict=24, **_GUARDS)
router_llm = ChatOllama(model=config.MODEL, base_url=config.OLLAMA_URL,
                        temperature=0, num_ctx=config.NUM_CTX, num_predict=300,
                        reasoning=config.THINKING, client_kwargs={"timeout": config.REQUEST_TIMEOUT})


def _dedupe(text: str) -> str:
    """Drop paragraphs the model already wrote earlier in the same answer."""
    seen, kept = set(), []
    for para in text.split("\n\n"):
        key = " ".join(para.split()).lower()
        if key and key in seen:
            continue
        seen.add(key)
        kept.append(para)
    return "\n\n".join(kept).strip()


def _ask(system: str, user: str, model=None) -> str:
    try:
        msg = (model or llm).invoke([SystemMessage(content=system), HumanMessage(content=user)])
    except Exception as e:                      # timeout or Ollama error: note it and let the run continue
        return f"[model timed out or failed: {type(e).__name__}. Try again, ideally with Minecraft closed.]"
    out = _dedupe(msg.content.strip())
    meta = getattr(msg, "response_metadata", None) or {}
    if meta.get("done_reason") == "length":     # hit its length limit: say so instead of ending mid-sentence
        out += "\n\n[cut off at the length limit]"
    return out


def _clip(text: str, n: int = 2500) -> str:
    text = text or ""
    return text if len(text) <= n else text[:n] + "\n...[trimmed]"


def _notes_for_agents(notes: str, limit: int | None = None) -> str:
    """Project notes cut to what agents read without losing the important parts (10 Nov 2026).
    The progress log grows with every note, so when the file is too long its OLDEST entries go first,
    and the 'Rules / lessons' section is always kept whole (a plain cut used to drop the file's end)."""
    limit = limit or config.NOTES_CHARS
    notes = notes or ""
    if len(notes) <= limit:
        return notes
    parts = re.split(r"(?m)^(?=## )", notes)
    log_i = next((i for i, p in enumerate(parts) if p.lower().startswith("## progress log")), None)
    if log_i is not None:
        head, *entries = parts[log_i].rstrip("\n").split("\n")
        dropped = 0
        while len("".join(parts)) > limit and len(entries) > 5:
            entries.pop(0)
            dropped += 1
            parts[log_i] = "\n".join([head, f"(oldest {dropped} entries left out - full log in project.md)"]
                                     + entries) + "\n\n"
    text = "".join(parts)
    if len(text) <= limit:
        return text.strip()
    rules = next((p for p in parts if p.lower().startswith("## rules")), "")
    rest = "".join(p for p in parts if p is not rules)
    room = max(limit - len(rules) - 40, 0)
    return (rest[:room].rstrip() + "\n...[notes trimmed]\n\n" + rules).strip()


def _sheet_for_task(task: str) -> str:
    """The mod sheet's status for each sheet mod the task names (25 Nov 2026: the researcher said Wild Hunt
    wasn't a recommended mod; the sheet says Recommended)."""
    low = task.lower()
    lines = []
    for r in sheet_rows():
        core = re.sub(r"\(.*?\)", "", r["Mod"]).strip()
        if len(core) >= 4 and core.lower() in low:
            have = (r.get("In Cinema") or "").strip()
            lines.append(f"- {core}: {r.get('Status') or '?'} on the sheet"
                         + ({"Yes": ", installed", "No": ", NOT installed yet"}.get(have, "")))
    return ("MOD SHEET (your decisions so far):\n" + "\n".join(lines) + "\n\n") if lines else ""


def _context(state: TeamState) -> str:
    notes = load_project_notes()          # re-read every time, so edits to project.md apply to the next run
    return (
        f"PROJECT NOTES:\n{_notes_for_agents(notes) or '(none)'}\n\n"
        f"TASK:\n{state['task']}\n\n"
        + _sheet_for_task(state.get("task", "")) +
        f"ARCHITECT PLAN:\n{_clip(state.get('plan')) or '(none yet)'}\n\n"
        f"DESIGN:\n{_clip(state.get('design')) or '(none yet)'}\n\n"
        f"MINECRAFT RESEARCH:\n{_clip(state.get('research'), 5000) or '(none yet)'}\n\n"
        f"PACK CHECK:\n{_clip(state.get('pack'), 2500) or '(none yet)'}\n\n"
        f"BUILD SPEC:\n{_clip(state.get('build'), 3000) or '(none yet)'}\n\n"
        f"LORE:\n{_clip(state.get('lore'), 2000) or '(none yet)'}\n\n"
        f"GAMEPLAY:\n{_clip(state.get('gameplay'), 3000) or '(none yet)'}\n\n"
        f"CODE:\n{_clip(state.get('code'), 3000) or '(none yet)'}\n\n"
        f"ORCHESTRATOR'S INSTRUCTIONS FOR YOU:\n{state.get('instructions', '')}"
    )


# ---------- Orchestrator ----------
class Route(BaseModel):
    next: Literal["architect", "designer", "developer", "researcher", "pack_engineer", "master_builder", "lore_writer", "gameplay_designer", "FINISH"]
    instructions: str = Field(description="What the chosen agent should do next")
    reason: str = Field(description="One short sentence on why")


ORCHESTRATOR_PROMPT = """You are the Orchestrator of a small team:
- architect: breaks the task into a structure/plan (components, data, steps)
- designer: visual/UX/aesthetic design, layouts, palettes, build look and feel
- developer: writes code, configs, scripts, KubeJS/datapacks, commands
- researcher: looks up Minecraft facts, mechanics, recipes, mods (Create, TACZ, etc.)
- pack_engineer: checks the installed mods' versions, required mods and compatibility from the real mod files
- master_builder: turns a plan/design into a buildable spec (size table, layer-by-layer steps, terrain shaping)
- lore_writer: writes in-world text (books, journals, signs, inscriptions, legends) that fits the project's lore
- gameplay_designer: designs how the pack plays (hunting loop, progression tiers, quests and bounties, loot and rewards, the start of the game) using the installed mods
Pick ONE agent to act next, or FINISH when the task is fully covered.
Don't repeat an agent unless its output needs revising. Prefer research before
design/code when Minecraft facts matter. Reply only with the JSON requested."""


MAX_RUNS_PER_AGENT = 2      # architect / designer / developer (the researcher has its own limit)
_NO_CODE = re.compile(r"\b(review only|no code|design task only|do not redesign|status report)\b", re.I)
_NO_RESEARCH = re.compile(r"\bno research\b", re.I)
# What the task asks for, so the orchestrator can't finish without it (25 Nov 2026: a "plan and design"
# task ended after the Architect; the Designer never ran).
_GAMEPLAY = re.compile(r"\b(gameplay|hunting loop|game loop|progression|tiers?|quests?|quest boards?|bount(?:y|ies)|"
                       r"loot|rewards?|early game|first hours|crafting tree|upgrade path)\b", re.I)
_BUILDISH = re.compile(r"\b(build\w*|rooms?|halls?|interior|towers?|keep|walls?|layout|look)\b", re.I)
_WANTS = [("designer", "designer: design written", re.compile(r"\bdesign\b", re.I)),
          ("developer", "developer: code written",
           re.compile(r"\b(code|function|datapack|mcfunction|script|config file|kubejs)\b", re.I)),
          ("pack_engineer", "pack_engineer: checked",
           re.compile(r"\b(compatib\w*|dependenc\w*|required mods?|mod list|conflicts?|crash\w*|missing mods?)\b", re.I)),
          ("master_builder", "master_builder: spec written",
           re.compile(r"\b(build spec|blueprint|layer[- ]by[- ]layer|block (?:list|counts?)|how many blocks|"
                      r"terraform\w*|terrain)\b", re.I)),
          ("lore_writer", "lore_writer: written",
           re.compile(r"\b(lore|backstory|story|legend|myths?|journal|diary|book text|written book|"
                      r"inscriptions?|epitaph|sign text|in-world text)\b", re.I)),
          ("pack_engineer", "pack_engineer:", _GAMEPLAY),       # gameplay leans on mods: check them first
          ("gameplay_designer", "gameplay_designer: designed", _GAMEPLAY)]


def _runs(state: TeamState, agent: str) -> int:
    return sum(1 for h in state.get("history", []) if h.startswith(agent + ":"))


_ONE_PART = re.compile(r"(?<!and )\b(design|review|code|lore|gameplay|build spec|research)(?: task)? only\b", re.I)
_ONLY = [(re.compile(r"\bgameplay only\b", re.I), {"pack_engineer", "gameplay_designer"}),
         (re.compile(r"\blore only\b", re.I), {"lore_writer"}),
         (re.compile(r"\bbuild spec only\b", re.I), {"master_builder"}),   # 25 Nov: designer ran twice on one
         (re.compile(r"(?<!and )\bdesign only\b", re.I), {"designer"}),
         (re.compile(r"\bresearch only\b", re.I), {"researcher", "pack_engineer"}),
         (re.compile(r"\bcode only\b", re.I), {"developer", "researcher"})]


def _allowed(state: TeamState, agent: str) -> bool:
    if agent not in config.WORKERS:
        return False                               # not on this project's roster
    for rx, team in _ONLY:
        if rx.search(state.get("task", "")) and agent not in team:
            return False                           # "gameplay only": 25 Nov the architect, designer and lore
                                                   # writer all ran and padded the report
    if agent == "developer" and _NO_CODE.search(state.get("task", "")):
        return False                               # the task said no code
    if (agent in ("architect", "designer", "developer", "master_builder", "lore_writer", "gameplay_designer")
            and _runs(state, agent) >= MAX_RUNS_PER_AGENT):
        return False
    if agent in ("pack_engineer", "gameplay_designer", "lore_writer") and _runs(state, agent) >= 1:
        return False                               # its check is code: running it twice gives the same answer
    if (agent == "researcher" and "pack_engineer" in config.WORKERS and _WANTS[2][2].search(state.get("task", ""))
            and not any(h.startswith("pack_engineer:") for h in state.get("history", []))):
        return False                               # compatibility tasks: the code check first (25 Nov: research
                                                   # ran first and added a false dependency claim)
    if agent == "architect" and _ONE_PART.search(state.get("task", "")):
        return False                               # "design only", "review only"...: no plan wanted
    if agent == "lore_writer" and not _WANTS[4][2].search(state.get("task", "")):
        return False                               # 25 Nov: joined a gameplay task nobody asked lore for
    if agent == "gameplay_designer" and not _GAMEPLAY.search(state.get("task", "")):
        return False
    if (agent == "gameplay_designer" and "pack_engineer" in config.WORKERS
            and not any(h.startswith("pack_engineer:") for h in state.get("history", []))):
        return False                               # it must design from the checked mod list, not from memory
    if agent == "researcher" and _NO_RESEARCH.search(state.get("task", "")):
        return False                               # the task said no research (25 Nov: invented sizes)
    if agent == "researcher" and _research_calls(state) >= config.MAX_RESEARCH_CALLS:
        return False
    hist = state.get("history", [])
    if agent == "researcher" and hist and hist[-1].startswith("researcher: skipped repeat"):
        return False                               # it has nothing new to look up (25 Nov: asked twice more)
    return True


_SECTIONS = ("plan", "research", "pack", "design", "build", "lore", "gameplay", "code")


def _fallback_route(state: TeamState) -> Route:
    """When the model's pick can't be used: what the task asks for next; otherwise, if someone has already
    written something, finish (25 Nov: the old architect -> designer -> developer order padded reports)."""
    need = _still_needed(state)
    if need:
        return Route(next=need, instructions="Do your part of the task.", reason="fallback: the task asks for it")
    if not any(state.get(f) for f in _SECTIONS):
        for agent in ("architect", "designer", "developer"):
            if _allowed(state, agent):
                return Route(next=agent, instructions="Do your part of the task.", reason="fallback order")
    return Route(next="FINISH", instructions="", reason="fallback: nothing useful left to do")


_REVISE = ("REVISE your earlier section using the NEW research (the latest '### Query' in MINECRAFT RESEARCH). "
           "Replace every number marked '(not in research - check)' or 'unverified' with the sourced value if "
           "the research now has it, redo any sums and conclusions that depended on it, and keep the rest.")


def _stale_section(state: TeamState) -> str | None:
    """First worker whose section was written before the latest successful research lookup."""
    hist = state.get("history", [])
    last_lookup = max((i for i, h in enumerate(hist) if h.startswith("researcher: looked up")), default=-1)
    for agent, marker in (("architect", "architect: plan"), ("designer", "designer: design"),
                          ("developer", "developer: code")):
        written = [i for i, h in enumerate(hist) if h.startswith(marker)]
        if written and written[-1] < last_lookup and _allowed(state, agent):
            return agent
    return None


_NOT_INSTALLED = re.compile(r"not (?:be )?installed|isn'?t installed|is missing|not (?:found|present|listed) in", re.I)


def _installed_check(out: str) -> str:
    """Correct 'X is not installed' when X's jar is in the modpack (25 Nov 2026: the model said
    Reliquified Artifacts wasn't installed three runs in a row). Ground truth = the jars."""
    mods = sorted(installed_mods(), key=lambda m: len(m[0]), reverse=True)
    if not mods:
        return out
    pieces = re.split(r"(\n|;|\.\s)", out)
    for i in range(0, len(pieces), 2):
        clause = pieces[i]
        if not _NOT_INSTALLED.search(clause) or "(correction:" in clause:
            continue
        low = clause.lower().replace("*", "")
        hit = next((m for m in mods if len(m[0]) >= 4 and m[0].lower() in low), None)
        if hit:
            pieces[i] = clause.rstrip() + f" (correction: {hit[0]} IS installed - version {hit[1]}, file {hit[2]})"
    return "".join(pieces)


# Last words that mark a phrase as a block name ('basalt slabs', 'snowy gravel').
_BLOCKISH = set("block brick slab stair plank log glass pane gravel sand stone ice wool concrete terracotta tile "
                "lantern pillar ore cobblestone deepslate blackstone basalt calcite tuff".split())
_BLOCK_FLAG = " (not a known block - check)"
_BOLD = re.compile(r"\*\*([A-Za-z][A-Za-z '’-]{2,40}?)\*\*")


def _stem(word: str) -> str:
    w = word.lower().strip("'’-")
    return w[:-1] if w.endswith("s") and not w.endswith("ss") and len(w) > 3 else w


_ITEMISH = set("sword axe pickaxe shovel hoe helmet chestplate leggings boots bow crossbow trident shield ingot "
               "nugget hide leather scale scute shard fang claw horn bone gem armor armour mace spear".split())
_ITEM_FLAG = " (not a known item - check)"


def _check_items(out: str) -> str:
    """Same check as _check_blocks for bold item names (gear, materials). Custom items the pack would add
    with KubeJS are marked '(custom' by the writer and skipped."""
    mods = {n.lower() for n in installed_mod_names()}       # "Fantasy Armor" is a mod, not an item
    return _check_blocks(out, _ITEMISH, _ITEM_FLAG, skip=mods)


def _check_blocks(out: str, endings: set | None = None, flag: str = _BLOCK_FLAG, skip: set = frozenset()) -> str:
    """Flag bold block names that no vanilla or installed-mod block matches (25 Nov 2026: 'snowy gravel',
    'basalt slabs'). A phrase is fine if all its words appear in one real block's name."""
    endings = endings or _BLOCKISH
    names = known_block_names()
    if not names:
        return out
    name_words = [{_stem(w) for w in re.findall(r"[a-z'’-]+", n)} for n in names]

    def check(m):
        phrase = m.group(1)
        words = [_stem(w) for w in re.findall(r"[A-Za-z'’-]+", phrase)]
        after = out[m.end():m.end() + 40]
        if phrase.strip().lower() in skip:
            return m.group(0)
        if (len(words) < 2 or words[-1] not in endings or {"no", "non", "non-vanilla", "vanilla", "modded"} & set(words)
                or after.startswith(flag) or re.match(r"\s*\(custom", after, re.I)):   # headings, not blocks
            return m.group(0)
        core = set(words) - {"block"} or set(words)     # 'blue ice blocks' is Blue Ice
        if any(core <= nw for nw in name_words):
            return m.group(0)
        return m.group(0) + flag

    return _BOLD.sub(check, out)


def _still_needed(state: TeamState) -> str | None:
    """First agent the task asks for (design / code) that hasn't written its part, if it may still run."""
    task, hist = state.get("task", ""), state.get("history", [])
    for agent, done, wants in _WANTS:
        if agent == "designer" and _GAMEPLAY.search(task) and not _BUILDISH.search(task):
            continue                               # "design the hunting loop" is gameplay, not a building design
        if wants.search(task) and not any(h.startswith(done) for h in hist) and _allowed(state, agent):
            return agent
    return None


_RANGE = re.compile(r"[\[(]\s*[\w.+-]*\s*,\s*[\w.+-]*\s*[\])]|[\[(][\w.+-]+[\])]")
_VERSION = re.compile(r"(?<![\w.+-])\d+(?:\.\d+)+(?:[+-][\w.]+)?(?![\w.])")
_OUT_OF_RANGE = re.compile(r"outside|not within|not in (?:the )?(?:allowed |required )?range|doesn'?t (?:satisfy|match|meet)|"
                           r"does not (?:satisfy|match|meet)|too (?:new|old|high|low)|exceeds|below the|incompatible|"
                           r"downgrad|upgrad", re.I)


def _range_check(out: str) -> str:
    """Test any claim that a version is outside a range, in code (25 Nov 2026: 'Create 6.0.8 is outside
    [6.0.7, 6.1.0)' - it isn't). A false claim gets a correction; true ones are left alone."""
    from research_tools import version_in_range
    pieces = re.split(r"(\n|;|\.\s)", out)
    for i in range(0, len(pieces), 2):
        clause = pieces[i]
        if "(correction:" in clause or not _OUT_OF_RANGE.search(clause):
            continue
        rng = _RANGE.search(clause)
        if not rng:
            continue
        rest = clause[:rng.start()] + " " + clause[rng.end():]
        for v in _VERSION.findall(rest):
            if version_in_range(v, re.sub(r"\s+", "", rng.group(0))) is True:
                pieces[i] = clause.rstrip() + f" (correction: {v} IS within {re.sub(chr(32), '', rng.group(0))}, so it's compatible)"
                break
    return "".join(pieces)


def _uncovered_mods(state: TeamState) -> list[str]:
    """Installed mods named in the task that the research doesn't mention yet."""
    task, research = (state.get("task") or "").lower(), (state.get("research") or "").lower()
    names = sorted(set(installed_mod_names()), key=len, reverse=True)   # longest first: "More Relics" before "Relics"
    found = []
    for n in names:
        low = n.lower()
        if len(low) >= 4 and low in task and low not in research and not any(low in f.lower() for f in found):
            found.append(n)
    return found


def orchestrator(state: TeamState) -> dict:
    steps = state.get("steps", 0)
    if steps >= config.MAX_STEPS:
        return {"next": "FINISH", "history": [f"orchestrator: step limit ({config.MAX_STEPS}) reached"]}
    log = "\n".join(state.get("history", [])[-10:]) or "(nothing yet)"
    can = [a for a in WORKERS if _allowed(state, a)]
    user = (f"{_context(state)}\n\nWORK LOG SO FAR:\n{log}\n\n"
            f"Agents you may pick now: {', '.join(can + ['FINISH'])}. Who acts next?")
    try:
        route = router_llm.with_structured_output(Route).invoke(
            [SystemMessage(content=ORCHESTRATOR_PROMPT), HumanMessage(content=user)]
        )
        if route is None:
            route = _fallback_route(state)
    except Exception:
        route = _fallback_route(state)
    # Hard limits the model can't talk its way past: research budget, 2 runs per worker, no code on
    # review-only tasks (on 9 Nov the developer ran 4 times in a row on a review task).
    if route.next != "FINISH" and not _allowed(state, route.next):
        blocked = route.next
        need = _still_needed(state)                # 25 Nov: gameplay picked before the Pack Engineer fell
        route = (Route(next=need, instructions="Do your part of the task.", reason="the task needs it next")
                 if need else _fallback_route(state))   # back to architect + designer, which nobody asked for
        route.reason = f"{blocked} not allowed yet; " + route.reason
    # The task asked for a design or code that nobody has written yet: do that before finishing.
    if route.next == "FINISH":
        need = _still_needed(state)
        if need:
            route = Route(next=need, instructions="Do your part of the task.",
                          reason=f"the task asks for the {need}'s part and it hasn't run yet")
    # The task names installed mods the research never covered: look them up before finishing
    # (25 Nov 2026: one 'Relics' lookup missed Reliquified Artifacts, then said 'all compatible').
    missing = _uncovered_mods(state)
    if route.next == "FINISH" and missing and _allowed(state, "researcher"):
        route = Route(next="researcher", instructions=f"Search for exactly this mod: {missing[0]}",
                      reason=f"task names {', '.join(missing)} but the research doesn't cover it yet")
    # New research arrived after a section was written: send its author back once to revise it
    # before moving on (10 Nov: the corrected 256/512 SU never reached the plan).
    stale = _stale_section(state)
    if stale and route.next != "researcher":
        route = Route(next=stale, instructions=_REVISE, reason=f"new research since the {stale} wrote; revising")
    return {
        "next": route.next,
        "instructions": route.instructions,
        "steps": steps + 1,
        "history": [f"orchestrator -> {route.next}: {route.reason}"],
    }


# ---------- Workers ----------
def architect(state: TeamState) -> dict:
    out = _ask(
        "You are the Architect. Produce a clear structured plan: goals, components, "
        "how they connect, and ordered build steps. Be concrete and concise." + _GROUNDED,
        _context(state),
    )
    return {"plan": _worker_check(out, state), "history": ["architect: plan written"]}


# Create-only guidance: Fort Redux runs only (25 Nov 2026: kept out of RPG runs, where it wasted context).
_CREATE_DESIGN_FACTS = (
    "Create facts: machines (press, mixer, fans, water wheels, cogwheels, shafts) have a fixed look, "
    "so only give materials for the building around them (water wheels are wooden). Rotation and "
    "power travel only through shafts, cogwheels, gearboxes, belts and chain drives; Mechanical Arms, "
    "funnels, chutes and hoppers move items, never power.")


def designer(state: TeamState) -> dict:
    out = _ask(
        "You are the Designer. Turn the plan into a concrete design: layout, look and "
        "feel, palette/materials, user flow or room-by-room description. Stay consistent "
        "with any research provided. Don't repeat the Architect's numbers or tables: refer to "
        "them ('as in the plan') and spend your words on layout and look. Keep it under about 600 words. "
        + (_CREATE_DESIGN_FACTS if config.MOD_DOCS else "") + _GROUNDED,
        _context(state), model=design_llm,
    )
    return {"design": _worker_check(out, state), "history": ["designer: design written"]}


_FENCE = re.compile(r"```[^\n]*\n(.*?)```", re.S)
_BUILD_CMD = re.compile(r"^\s*(?:execute\b.*\brun\s+)?(fill|setblock|clone|place)\b", re.I)


def _check_code(out: str) -> str:
    """Datapack rules the Developer must follow, checked in code (25 Nov 2026). Notes go AFTER each code
    block, never inside it, so the code stays copy-paste clean."""
    def check(m):
        body, notes = m.group(1), []
        for i, line in enumerate(body.splitlines(), 1):
            if line.lstrip().startswith("//"):
                notes.append(f"line {i} uses a // comment; .mcfunction comments start with #")
            if _BUILD_CMD.match(line) and "~" in line and config.PROJECT == "fort_redux":
                notes.append(f"line {i} builds with ~ relative coordinates; Fort Redux builds use absolute ones")
        head = out[max(0, m.start() - 200):m.start()] + body[:200]
        old_style = config.GAME_VERSION.startswith("1.20") or config.GAME_VERSION.startswith("1.19")
        if old_style and re.search(r"data/[\w-]+/function/", head):
            notes.append(f"'function/' folder is 1.21+; {config.GAME_VERSION} uses 'functions/'")
        if not old_style and re.search(r"data/[\w-]+/functions/", head):
            notes.append(f"'functions/' folder is pre-1.21; {config.GAME_VERSION} uses 'function/'")
        if not notes:
            return m.group(0)
        return m.group(0) + "\n" + "\n".join(f"(code check: {n})" for n in notes)
    return _FENCE.sub(check, out)


def developer(state: TeamState) -> dict:
    out = _ask(
        "You are the Developer. Write the code, configs, scripts or commands needed. "
        "Use fenced code blocks with file names. Only use Minecraft features the research "
        "confirms exist for the stated version. Note any assumptions." + _GROUNDED + "\n"
        f"Installed mods: {MOD_LIST}",
        _context(state),
    )
    return {"code": _check_code(_worker_check(out, state)), "history": ["developer: code written"]}


_CRASHY = re.compile(r"\b(crash\w*|launch\w*|won'?t start|log|errors?)\b", re.I)


def pack_engineer(state: TeamState) -> dict:
    """Checks installed mods in code and summarises the result in code too (25 Nov 2026: the model, asked only
    to explain a correct check, claimed Create 6.0.8 was outside [6.0.7,6.1.0) and advised a downgrade)."""
    report = dependency_report(state.get("task", ""))
    if report.startswith("["):
        return {"pack": report, "history": ["pack_engineer: checked (no mod files)"]}
    crash = crash_report()
    if "no load errors." in crash and not _CRASHY.search(state.get("task", "")):
        crash = ""                                   # nothing broke: keep the report short
    full = report + ("\n\n" + crash if crash else "")
    return {"pack": full + "\n\n" + _pack_summary(full), "history": ["pack_engineer: checked"]}


def _pack_summary(report: str) -> str:
    """Plain-language summary of a dependency check, written from the check itself."""
    missing = re.findall(r"needs ([\w.-]+) (\S*) -> NOT INSTALLED", report)
    wrong = re.findall(r"needs ([\w.-]+) (\S*) -> installed (\S+) \(wrong version\)", report)
    unchecked = len(re.findall(r"\(range not checked\)", report))
    if not missing and not wrong:
        out = ["- No problems: every required mod is installed at a version the mods accept."
               + ("" if re.search(r"failed to load|newest crash report", report) else " Nothing to change.")]
    else:
        out = [f"- Missing: install {d} (version {r or 'any'})." for d, r in missing]
        out += [f"- Wrong version: {d} {v} is installed, but {r} is required. Change {d} to a version in {r}."
                for d, r, v in wrong]
        out = list(dict.fromkeys(out))               # three mods needing the same fix: say it once
    if unchecked:
        out.append(f"- {unchecked} version range(s) couldn't be checked automatically: compare them by hand.")
    failed = re.findall(r"- mod '([\w-]+)' failed to load", report)
    owners = re.findall(r"belongs to '([\w-]+)'", report)
    for mod in failed:
        if owners:
            out.append(f"- Launch crash: '{mod}' failed to load because it needs code that your installed "
                       f"'{owners[0]}' doesn't have: it was built for a different '{owners[0]}' version. Change "
                       f"'{mod}' to the version made for your '{owners[0]}' (or the other way round).")
        else:
            out.append(f"- Launch crash: '{mod}' failed to load; see LAST LAUNCH above.")
    if "## LAST LAUNCH" in report and not failed and "no load errors" not in report:
        out.append("- The last launch crashed: see LAST LAUNCH above for what the files say.")
    planned = [r["Mod"] for r in sheet_rows()
               if r.get("Status") in ("Core", "Recommended") and (r.get("In Cinema") or "").strip() == "No"]
    if planned:
        out.append(f"- From the mod sheet: {len(planned)} Core/Recommended mods aren't in the pack yet: "
                   + ", ".join(planned[:12]) + (" ..." if len(planned) > 12 else "") + ".")
    return "\n".join(out)


_MATH = re.compile(r"(?<![\d.])(\d+)\s*[x×*]\s*(\d+)(?:\s*[x×*]\s*(\d+))?\s*=\s*(\d{1,3}(?:,\d{3})+|\d+)(?![\d.])")


def _math_check(out: str) -> str:
    """Correct wrong 'A x B (x C) = N' sums (25 Nov 2026: small models get multiplication wrong)."""
    def fix(m):
        a, b, c = int(m.group(1)), int(m.group(2)), int(m.group(3) or 1)
        said, right = int(m.group(4).replace(",", "")), a * b * c
        if said == right or out[m.end():m.end() + 13] == " (correction:":
            return m.group(0)
        return m.group(0) + f" (correction: = {right:,})"
    return _MATH.sub(fix, out)


# A size-table row: "| Part | 9 x 9 x 8 | ..." or, as the model sometimes writes it, "- Part | 9 x 9 x 8 | ...".
_SIZE_ROW = re.compile(r"^\s*(?:\||[-*]\s)?\s*\**([^|*\n]+?)\**\s*\|[^\n]*?(\d+)\s*[x×]\s*(\d+)\s*[x×]\s*(\d+)", re.M)
_THIN = re.compile(r"\b(one|1)[- ]block[- ]thick\b|\bhollow\b", re.I)


def _hollow_check(spec: str, task: str) -> str:
    """'One block thick' means a ring, not a solid floor on every layer (25 Nov 2026: a 5x5 tower laid 25 blocks
    a layer; the ring is 16). Flags step lines that use the solid count for a footprint in the task."""
    if not _THIN.search(task):
        return spec
    sizes = {(int(a), int(b)) for a, b in re.findall(r"(\d+) blocks? wide,? (\d+) (?:blocks? )?deep", task)}
    sizes |= {(int(a), int(b)) for a, b, _ in re.findall(r"(\d+)\s*[x×]\s*(\d+)\s*[x×]\s*(\d+)", task)}
    notes = []
    for w, d in sizes:
        solid, ring = w * d, 2 * (w + d) - 4
        steps = "\n".join(ln for ln in spec.splitlines() if "|" not in ln)      # not the size table
        said_solid = re.search(rf"\b{solid}\b[^\n]*?\b(blocks?|bricks?)\b", steps, re.I)
        said_grid = re.search(rf"\b{w}\s*[x×]\s*{d}\b(?!\s*[x×])[^\n]*?\b(blocks?|bricks?|layer)", steps, re.I)
        if w > 2 and d > 2 and (said_solid or said_grid):
            notes.append(f"a one-block-thick {w} x {d} wall is {ring} blocks per layer (a ring), not {solid} "
                         "(a solid floor); only the floor and roof are solid")
    return spec + ("\n\n" + "\n".join(f"(math check: {n})" for n in notes) if notes else "")


_NOT_A_BUILDING = re.compile(r"terrain|ground|drift|snow|ice field|shore|cliff|water|path|area|site|footprint|"
                             r"ladder|stair|door|window|banner|lantern", re.I)


def _block_estimates(spec: str) -> str:
    """Block counts worked out in code for every 'W x D x H' row of the spec's size table: walls one block
    thick first, then floor and roof for parts that have them. Terrain rows are skipped (not a building)."""
    rows = []
    for m in _SIZE_ROW.finditer(spec):
        name, w, d, h = m.group(1).strip(), int(m.group(2)), int(m.group(3)), int(m.group(4))
        if (not (1 <= w <= 256 and 1 <= d <= 256 and 1 <= h <= 384) or name.lower().startswith(("part", "room", "-"))
                or _NOT_A_BUILDING.search(name)):
            continue
        walls = 2 * h * (w + d - 2) if w > 1 and d > 1 else w * d * h
        floor = w * d
        stacks = lambda n: f"{-(-n // 64)} stack{'s' if n > 64 else ''}"
        rows.append(f"- {name} ({w} x {d} x {h}): walls {walls:,} blocks ({stacks(walls)}); "
                    f"floor {floor:,} and roof {floor:,} more if it has them")
    if not rows:
        return ""
    return ("## BLOCK ESTIMATES (worked out in code for 1-block walls; openings, ruins and pitched roofs change it)\n"
            + "\n".join(rows))


def master_builder(state: TeamState) -> dict:
    """Build specs, including terrain shaping (Terraformer folded in, 25 Nov 2026)."""
    out = _ask(
        "You are the Master Builder; you also plan terrain. Turn the plan or design into a spec someone can "
        "build from: 1) a size table with columns Part | Size (W x D x H blocks) | Main blocks, one row per "
        "part, sizes written exactly like '9 x 9 x 8'; 2) numbered layer-by-layer build steps, from the "
        "ground up (walls are a ring one block thick unless the task says solid: only floors and roofs fill the whole "
        "footprint; put doors, ladders and windows in the step where they go); 3) terrain shaping steps if the site needs them (cliffs, shoreline, ice). Use real block "
        "names from vanilla or the installed mods, and keep to the project's look rules. Don't count blocks "
        "yourself: block estimates are added by code from your size table. Write only those three sections: "
        "no notes for other agents, no extra tips or warnings, no claims about mods. Worldgen mods (terrain, "
        "biome, structure mods) only act when the world generates; hand terrain is shaped by hand or WorldEdit."
        + _GROUNDED,
        _context(state), model=design_llm,
    )
    out = _hollow_check(_math_check(_worker_check(out, state)), state.get("task", ""))
    est = _block_estimates(out)
    return {"build": out + ("\n\n" + est if est else ""), "history": ["master_builder: spec written"]}


_FALL_WORDS = re.compile(r"\b(fell|fall|destroy\w*|ended|doom\w*|ruin\w*|perish\w*|lost|collapse\w*)\b", re.I)
_WRONG_CAUSE = re.compile(r"\b(fire|flames?|burn\w*|war|plague|siege|invasion|invaders|flood\w*|earthquake|dragon)\b", re.I)
_FROST = re.compile(r"\b(frost|froze|frozen|freez\w*|ice|icy|winter|cold|snow)\b", re.I)


BOOK_PAGE_CHARS = 260  # roughly what one written-book page shows (14 short lines)


def _look_check(out: str) -> str:
    """Words from the other project's look (config 'avoid'), flagged once each (25 Nov 2026: an RPG trophy
    hall framed in obsidian)."""
    notes = [why for word, why in config.AVOID.items()
             if re.search(r"\b" + re.escape(word), out, re.I) and f"(look check: {why})" not in out]
    return out + ("\n\n" + "\n".join(f"(look check: {n})" for n in notes) if notes else "")


def _lore_check(out: str) -> str:
    """Lore rules checked in code (25 Nov 2026): canon names spelled exactly, and the kingdom fell to the
    frost, not to something else. Notes go after the paragraph they're about."""
    import difflib
    names = config.CANON_NAMES
    if not names or "(lore check:" in out:
        return out
    singles = [n for n in names if " " not in n]
    game_words = {w for n in known_block_names() for w in n.split()}   # 25 Nov: 'Deepslate' isn't 'Deepgate'
    notes = []
    for word in sorted({re.sub(r"'s?$", "", w) for w in re.findall(r"\b[A-Z][a-z']{3,}\b", out)}):
        if word in singles or word.lower() in game_words:
            continue
        close = difflib.get_close_matches(word, singles, n=1, cutoff=0.85)
        if close:
            notes.append(f"'{word}' looks like a misspelling of '{close[0]}'")
    pages = re.split(r"\**Page \d+\**", out)[1:]
    for i, page in enumerate(pages, 1):
        page = re.split(r"\n\s*\(|\n#", page)[0].strip()
        if len(page) > BOOK_PAGE_CHARS:
            notes.append(f"Page {i} is {len(page)} characters; about {BOOK_PAGE_CHARS} fit on one book page in-game, "
                         "so split or shorten it")
    for para in re.split(r"\n\s*\n", out):
        if re.search(r"\bDahaad\b|kingdom", para) and _FALL_WORDS.search(para) and _WRONG_CAUSE.search(para) \
                and not _FROST.search(para):
            notes.append("this says the kingdom fell to something other than the frost; canon: the frost took it")
            break
    return out + ("\n\n" + "\n".join(f"(lore check: {n})" for n in notes) if notes else "")


def lore_writer(state: TeamState) -> dict:
    out = _ask(
        "You are the Lore Writer. Write the in-world text the task asks for (books, journals, signs, "
        "inscriptions, legends) in the voice of the world, not as notes about it. Follow the Lore section of "
        "the project notes exactly: never contradict it, never rename places, and spell every name exactly as "
        "the notes do. Keep each book page under 250 characters (2-4 short sentences) and label them 'Page 1', 'Page 2'; "
        "use more pages rather than longer ones. "
        "No modern words, no game terms (blocks, mods, players), no out-of-world commentary.",
        _context(state), model=design_llm,
    )
    out = _math_check(_worker_check(out, state))
    return {"lore": out, "history": ["lore_writer: written"]}


_MATERIAL_RANK = [(re.compile(r"\bnetherite\b", re.I), 4, "netherite"), (re.compile(r"\bdiamonds?\b", re.I), 3, "diamond"),
                  (re.compile(r"\biron\b", re.I), 2, "iron"), (re.compile(r"\b(stone|copper|chainmail)\b", re.I), 1, "stone/copper")]


_LIBRARY = re.compile(r"\b(lib|library|api|animator|config|core|framework)\b|lib$", re.I)
_ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5}


def _mods_used_check(out: str) -> str:
    """Every mod in the 'Mods used' section must be installed. Names are matched against display names AND jar
    file names (25 Nov 2026: Soulscraft's display name is 'Tarnished Legacy'; its jar says soulscraft)."""
    import difflib
    mods = installed_mods()
    if not mods or "(mod check:" in out:
        return out
    low = [n.lower() for n, _, _ in mods] + [re.sub(r"[-_]+", " ", j.lower()) for _, _, j in mods]
    sec = re.search(r"(?:mods used|mods needed|required mods|mods? it uses)[^\n]*\n(.*?)(?=\n#|\n\s*\n\S[^-*\n]|\Z)",
                    out, re.I | re.S)
    if not sec:
        return out
    notes = []
    for line in sec.group(1).splitlines():
        m = re.match(r"\s*[-*\d.]+\s*\**([^*:\n(]+?)\**\s*[:(\-–]", line)
        if not m:
            continue
        mod = m.group(1).strip().strip("*").strip()
        ml = mod.lower().replace("'", "")
        if _LIBRARY.search(mod):
            notes.append(f"'{mod}' is a library; it adds no gameplay of its own")
            continue
        status = sheet_status(mod)
        if status == "Dropped":
            notes.append(f"'{mod}' is Dropped on the mod sheet; don't build around it")
            continue
        if any(ml in n.replace("'", "") or n in ml for n in low if len(n) >= 4) or difflib.get_close_matches(ml, low, 1, 0.85):
            continue
        if status and "(planned" in line.lower():
            continue
        if status:
            notes.append(f"'{mod}' is on the mod sheet ({status}) but not installed yet; mark it '(planned)'")
        else:
            notes.append(f"'{mod}' is not an installed mod; use one from the pack or add it to the sheet first")
    return out + ("\n\n" + "\n".join(f"(mod check: {n})" for n in notes) if notes else "")


def _tier_check(out: str) -> str:
    """Tiers must not go backwards (25 Nov 2026): an early tier can't use a better vanilla material than every
    later tier, e.g. Tier 1 gear needing netherite when Tier 3 tops out at diamond."""
    tiers = []                                       # (tier number, best rank, material name)
    num = r"(\d+|I{1,3}|IV|V)"
    for m in re.finditer(rf"\bTier\s*{num}\b(.*?)(?=\bTier\s*{num}\b|\n#|\Z)", out, re.S):
        best = max(((r, n) for rx, r, n in _MATERIAL_RANK if rx.search(m.group(2))), default=(0, ""))
        t = m.group(1)
        tiers.append((int(t) if t.isdigit() else _ROMAN[t], *best))
    seen, notes = {}, []
    for num, rank, name in tiers:                     # a tier may be mentioned again in prose: keep the first
        seen.setdefault(num, (rank, name))
    order = sorted(seen.items())
    for i, (num, (rank, name)) in enumerate(order):
        later = [r for _, (r, _) in order[i + 1:]]
        if later and rank > max(later):
            notes.append(f"Tier {num} uses {name}, better than anything in the later tiers; "
                         "move it later or raise the later tiers")
    return out + ("\n\n" + "\n".join(f"(tier check: {n})" for n in notes) if notes else "")


def _sheet_context() -> str:
    """What the mod sheet says about mods that aren't plain 'installed': planned ones and dropped ones."""
    rows = sheet_rows()
    planned = [r["Mod"] for r in rows if r.get("Status") in ("Core", "Recommended")
               and (r.get("In Cinema") or "").strip() == "No"]
    dropped = [r["Mod"] for r in rows if r.get("Status") == "Dropped"]
    out = ""
    if planned:
        out += "\n\nPLANNED, NOT INSTALLED YET (mention only as '(planned)'):\n" + ", ".join(planned)
    if dropped:
        out += "\n\nDROPPED (never use):\n" + ", ".join(dropped)
    return out


def gameplay_designer(state: TeamState) -> dict:
    out = _ask(
        "You are the Gameplay Designer. Design how the pack plays for the task: the hunting loop (track, fight, "
        "carve, craft, upgrade), progression tiers, quests or bounties, loot and rewards, or the start of the "
        "game, whichever the task asks. Write: 1) the loop as short numbered steps; 2) a tier table with columns "
        "Tier | What you hunt | Gear you craft | Materials, rows labelled 'Tier 1', 'Tier 2', ...; 3) 'Mods used': "
        "only mods in the PACK CHECK list, one line each saying what it does in the loop. Bold real item names "
        "exactly as the game or the mod names them. An item the pack would have to add itself (with KubeJS) "
        "goes in bold followed by '(custom)'. Follow the Lore section; no build specs, no code, no tips or "
        "warnings section, no XP numbers the mods don't give. Library mods (names with Lib, API, Animator, "
        "Config, Core) add no gameplay: never use them. In the loop, name only mods you list in 'Mods used'."
        + _GROUNDED,
        _context(state) + "\n\nINSTALLED MODS (the only mods you may use):\n" + ", ".join(sorted({n for n in installed_mod_names() if not _LIBRARY.search(n)}))
        + _sheet_context(),
        model=design_llm,
    )
    out = _tier_check(_math_check(_worker_check(out, state)))
    return {"gameplay": out, "history": ["gameplay_designer: designed"]}


def _research_calls(state: TeamState) -> int:
    return sum(1 for h in state.get("history", []) if h.startswith("researcher:"))


def _past_queries(state: TeamState) -> list[str]:
    return [ln[len("### Query: "):].strip().lower()
            for ln in (state.get("research") or "").splitlines() if ln.startswith("### Query: ")]


_TASK_FILLER = set("review only no code answer yes say whether should stay check quote exact file names "
                   "find suggest other new not do does please tell me if it isn't installed look they each have has is are "
                   "build builds version versions the a an and or of in for".split())


def _json_to_bullets(out: str) -> str:
    """The model sometimes answers in JSON instead of bullets (25 Nov 2026): turn it into '- key: value'."""
    text = out.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    if not text.startswith("{"):
        return out
    try:
        data = json.loads(text)
    except ValueError:
        return out
    if not isinstance(data, dict):
        return out
    fmt = lambda v: "yes" if v is True else "no" if v is False else str(v)
    return "\n".join(f"- {str(k).replace('_', ' ')}: {fmt(v)}" for k, v in data.items())


def _valid_query(q: str) -> bool:
    """A search query is a few plain words, not an answer, JSON or a sentence."""
    return bool(q) and not re.search(r"[{}\[\]:=]", q) and len(q.split()) <= 8


def _task_query(task: str) -> str:
    """Fallback query: the task's own key words (mod names come first in most tasks)."""
    words = [w for w in re.findall(r"[A-Za-z0-9_'-]+(?:\.\d+)*", task) if w.lower() not in _TASK_FILLER and len(w) > 1]
    return " ".join(words[:6])[:80]


def researcher(state: TeamState) -> dict:
    past = _past_queries(state)
    avoid = f"\nAlready searched (pick something different): {', '.join(past)}" if past else ""
    exact = re.match(r"Search for exactly this mod: (.+)", state.get("instructions", ""))
    if exact:                                    # the orchestrator named the missing mod: no model guess
        query = exact.group(1).strip()[:80]
    else:
        query = _ask(
            "Write ONE short search query (2-6 words, no quotes) for the Minecraft fact "
            f"most needed right now. Output only the query.{avoid}",
            _context(state), model=query_llm,
        )
        query = (query.splitlines() or [""])[0].strip().strip('"')[:80]
        if not _valid_query(query):              # 25 Nov: it wrote its JSON answer as the query
            query = _task_query(state.get("task", ""))
    if not query or query.lower() in past:
        return {"history": [f"researcher: skipped repeat query '{query}'"]}

    if _research_calls(state) == 0:
        prune_web_cache()                        # once per run: drop cached web answers older than 2 weeks
    sources, used = _gather_sources(query, state)
    calc = stress_calculator(f"{state.get('task', '')} {query}", notes=load_project_notes())
    if calc and not calc.startswith("["):
        sources = calc + "\n\n" + sources          # the Researcher sees the exact maths first
    # Don't feed the Researcher its own earlier research: that is how it looped before.
    context = _context({**state, "research": ""})
    out = _ask(
        f"You are the Minecraft Researcher. Game context: {config.MODPACK_CONTEXT}\n"
        f"Installed mods in this modpack: {MOD_LIST}\n"
        "Summarize only the facts relevant to the task from the sources below, in at most "
        "8 short bullet points. Trust them in this order: INSTALLED MODPACK FILES (exact versions, "
        "dependencies and current config values) > MOD DOCS > WIKI > GITHUB ISSUES > YOUTUBE. "
        "If a STRESS CALCULATOR is given, quote its totals and verdicts exactly; never redo its maths. "
        "YouTube transcripts are practical tips from players: say which video (and timestamp) a tip "
        "comes from, and warn if the video is old" + (", because Create changed a lot before 6.0. " if config.MOD_DOCS
                                                    else ", since mods change between versions. ") +
        "Only recommend blocks/items from vanilla or the installed mods; say so if something needs a "
        "mod that isn't installed. Keep source links. Only give numbers (SU, RPM, ranges, counts) that "
        "appear in the sources; if a number isn't there, write 'not in sources'. Only cite links that "
        "appear in the sources. If the sources don't answer the question, say so in one line. Never "
        "repeat the task text back.",
        f"{context}\n\nSEARCH QUERY: {query}\n\n{sources}",
        model=research_llm,
    )
    # evidence = this lookup's sources + facts verified in earlier lookups (minus anything flagged)
    out = _json_to_bullets(out)
    out = _fact_check(out, sources + "\n" + _FLAGGED.sub("", state.get("research") or ""), check_dims=True)
    out = _range_check(out)
    out = _check_blocks(out)
    out = _installed_check(out)
    out = _check_timestamps(out, sources)
    if calc and not calc.startswith("["):
        out = _calc_check(out, calc)
    lookups = _CALC_BLOCK.sub("", state.get("research", "")).strip()
    combined = (lookups + f"\n\n### Query: {query}\n{out}").strip()
    if len(combined) > config.MAX_RESEARCH_CHARS:
        # keep the newest lookups, cut at a query heading
        tail = combined[-config.MAX_RESEARCH_CHARS:]
        cut = tail.find("### Query: ")
        combined = tail[cut:] if cut > 0 else tail
    # Exact stress maths, done by code, copied in word for word and kept at the TOP so it is never trimmed
    # (10 Nov: correct stats but wrong arithmetic in every run).
    if calc and not calc.startswith("["):
        combined = calc + "\n\n" + combined
        used = used + ["stress_calculator"]
    return {"research": combined, "history": [f"researcher: looked up '{query}' ({', '.join(used)})"]}


_CALC_BLOCK = re.compile(r"## STRESS CALCULATOR.*?(?=\n### Query: |\Z)", re.S)


_URL = re.compile(r"https?://[^\s)\]>\"']+")
_UNIT_NUMBER = re.compile(r"(\d[\d,.]*)\s*(?:x\s*)?(SU|RPM|su|rpm)\b")


_FLAG = " (unverified - not in sources)"
_JAR = re.compile(r"[\w.+-]+\.jar\b", re.I)
_JAR_FLAG = " (file name not in sources - check)"
_FLAGGED = re.compile(r"\d[\d,.]*\s*(?:x\s*)?(?:SU|RPM|su|rpm)\b" + re.escape(_FLAG) + r"|" + re.escape(_FLAG))


_DIMS = re.compile(r"\b\d+\s*[x×]\s*\d+(?:\s*[x×]\s*\d+)?\b")


def _fact_check(out: str, sources: str, flag: str = _FLAG, allow_calculated: bool = False,
                check_dims: bool = False) -> str:
    """Remove links and flag SU/RPM numbers the model wrote that are not in the sources it was given.
    (On 9 Nov the 8B model invented a YouTube video and water-wheel numbers.)
    allow_calculated: a number right after '=' is a worked result ("8 x 32 RPM = 256 SU") and is kept."""
    src = sources.lower()
    src_nums = re.sub(r"(?<=\d),(?=\d{3})", "", src)          # "1,440" -> "1440"

    def link(m):
        url = m.group(0).rstrip(".,;:")
        trail = m.group(0)[len(url):]
        return (url if url.lower() in src else "[link not in sources - removed]") + trail

    def number(m):
        if out_text[m.end():m.end() + len(flag)] == flag or out_text[m.end():m.end() + len(_FLAG)] == _FLAG:
            return m.group(0)                                   # already flagged: don't double up
        if allow_calculated and (re.search(r"=\s*\**\s*$", out_text[max(0, m.start() - 4):m.start()])
                                 or re.match(r"\s*\**\s*=", out_text[m.end():m.end() + 4])):
            return m.group(0)                                   # result or input of a shown sum
        n = m.group(1).rstrip(".,").replace(",", "")
        unit = m.group(2).lower()
        # the same number WITH the same unit must be in the sources ("5 SU" is not backed by a stray "5")
        found = re.search(r"(?<![\d.])" + re.escape(n) + r"\s*(?:x\s*)?" + unit, src_nums)
        # "4 SU per RPM" / "4 SU/RPM" is the same fact as the wiki's "4x RPM"
        if not found and unit == "su" and re.match(r"\s*(?:/|per)\s*rpm", out_text[m.end():m.end() + 9], re.I):
            found = re.search(r"(?<![\d.])" + re.escape(n) + r"\s*x\s*rpm", src_nums)
        return m.group(0) if found else m.group(0) + flag

    def jar(m):
        name = m.group(0)
        if name.lower() in src or out_text[m.end():m.end() + len(_JAR_FLAG)] == _JAR_FLAG:
            return name
        return name + _JAR_FLAG

    def dims(m):
        key = re.sub(r"\s+", "", m.group(0)).replace("×", "x").lower()
        if key in re.sub(r"\s+", "", src).replace("×", "x") or out_text[m.end():m.end() + len(flag)] == flag:
            return m.group(0)
        return m.group(0) + flag

    out_text = _URL.sub(link, out)
    if check_dims:     # 25 Nov: the Researcher invented room sizes and the Architect trusted them as research
        out_text = _DIMS.sub(dims, out_text)
    # file names must be copied exactly (25 Nov 2026: 'relicts-1.21.1-0.12.11.jar' for relics-...)
    out_text = _JAR.sub(jar, out_text)
    return _UNIT_NUMBER.sub(number, out_text)


def _worker_check(out: str, state: TeamState) -> str:
    """Fact-check the Architect/Designer/Developer the same way. Evidence = the task, project notes,
    installed mod list and the research, minus anything the Researcher already flagged as unverified
    (otherwise a copied '5 SU (unverified)' would count as backed)."""
    research = _FLAGGED.sub("", state.get("research") or "")
    evidence = "\n".join([state.get("task", ""), PROJECT_NOTES or "", MOD_LIST or "", research])
    out = _fact_check(out, evidence, flag=" (not in research - check)", allow_calculated=True)
    out = _check_timestamps(out, research)
    calc = _CALC_BLOCK.search(research)
    if calc:
        out = _calc_check(out, calc.group(0))
    out = _known_mistakes(out)
    out = _installed_check(out)
    out = _range_check(out)
    out = _math_check(out)
    out = _check_blocks(out)
    out = _check_items(out)                       # 25 Nov: shared by every agent, not just the new ones
    out = _mods_used_check(out)
    out = _lore_check(out)                        # no-op on projects without canon names
    out = _look_check(out)
    out = _label_new_parts(out, evidence)
    return _label_tips(out, evidence)


# ---------- Extra checks (10 Nov 2026) ----------
_CALC_ROW = re.compile(r"^- (?P<name>[A-Z][\w ]+?)(?: x(?P<count>\d+), all \d+ together)? "
                       r"\(uses (?P<per>[\d.,]+) SU per RPM each", re.M)
_MACHINE_WORDS = {"Mechanical Press": "press", "Mechanical Mixer": "mixer", "Encased Fan": "fan",
                  "Mechanical Saw": "saw", "Mechanical Drill": "drill", "Deployer": "deployer",
                  "Crushing Wheel": "crushing wheel", "Millstone": "millstone", "Mechanical Pump": "pump",
                  "Mechanical Crafter": "crafter", "Mechanical Piston": "piston", "Mechanical Bearing": "bearing",
                  "Rope Pulley": "pulley", "Mechanical Harvester": "harvester", "Mechanical Arm": "arm"}
_SU_AMOUNT = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*SU\b(?!\s*(?:/|per)\s*RPM)", re.I)


def _calc_check(out: str, calc: str) -> str:
    """Catch a machine given the wrong SU for its speed (10 Nov: 'Encased Fans x2 at 32 RPM = 256 SU';
    the calculator said 128 SU for both). Only checks a clause that names exactly one machine type,
    one speed and one SU amount; anything ambiguous is left alone."""
    rows = {}
    for m in _CALC_ROW.finditer(calc):
        word = _MACHINE_WORDS.get(m.group("name").strip())
        if word:
            rows[word] = (float(m.group("per").replace(",", "")), int(m.group("count") or 1))
    if not rows:
        return out

    def fix(clause: str) -> str:
        low = clause.lower()
        named = [w for w in rows if re.search(r"\b" + re.escape(w) + r"(e?s)?\b", low)]
        rpms = re.findall(r"(\d+)\s*RPM\b", clause, re.I)
        sus = list(_SU_AMOUNT.finditer(clause))
        if (len(named) != 1 or len(set(rpms)) != 1 or len(sus) != 1 or "(calculator:" in clause
                or re.search(r"capacity|network|wheel|spare|supply|total|engine", low)):
            return clause                  # ambiguous, or the SU is about power supply, not this machine
        per, count = rows[named[0]]
        rpm = float(rpms[0])
        said = float(sus[0].group(1).replace(",", ""))
        ok = {per * rpm * k for k in range(1, count + 1)}
        if said in ok:
            return clause
        each = per * rpm
        note = (f" (calculator: {_num(each)} SU each" + (f", {_num(each * count)} SU for all {count}" if count > 1 else "")
                + f" at {_num(rpm)} RPM)")
        return clause[:sus[0].end()] + note + clause[sus[0].end():]

    pieces = re.split(r"(\n|;|\.\s)", out)
    return "".join(p if i % 2 else fix(p) for i, p in enumerate(pieces))


def _num(n: float) -> str:
    return f"{n:,.0f}" if float(n).is_integer() else f"{n:,.1f}"


# Mistakes the 8B model has made before, with the correction shown next to them (10 Nov 2026).
# Add a line here whenever a report gets a Create fact wrong. Each pattern is checked per clause.
KNOWN_MISTAKES = [
    (r"\biron bars?\b[^.\n]{0,40}\b(ladder|climb\w*)|\bladder\b[^.\n]{0,30}\biron bars?\b",
     "iron bars can't be climbed; use the Ladder block (or scaffolding, or vines)"),     # 25 Nov 2026
    (r"\b(blue ice|packed ice|ice|snow(?: blocks?)?)\b[^.\n]{0,60}?\b(emit|emits|emitting|give off|gives off|glow|glows|"
     r"glowing|cast|casts|produce|produces|radiat\w*)\b[^.\n]{0,30}\blight",
     "ice and snow give off no light; for cold light use soul lanterns, sea lanterns, or glowstone behind blue "
     "stained glass"),
    (r"(mechanical arms?|funnels?|chutes?|hoppers?)\s+(?:to |that |which )?(?:carr|transmit|deliver|distribut|send|"
     r"driv|pass)\w*\s+(?:the\s+)?(power|rotation|SU|speed)\b"
     r"|\b(power|rotation)\w*\s+(?:is |are )?(?:[\w',\u2019]+\s+){0,3}?(?:through|via|by|using)\s+"
     r"(?:[\w',\u2019]+\s+){0,7}?(mechanical arms?|funnels?|chutes?|hoppers?)\b",
     "Mechanical Arms, funnels, chutes and hoppers move items; power goes through shafts, cogwheels, "
     "gearboxes, belts or chain drives"),
    (r"^\s*\|\s*(?:large )?water ?wheels?\s*\|[^|]*\b(deepslate|stone|obsidian|tuff|calcite|brick|iron|steel|concrete)\b"
     r"|water ?wheels?\s+(?:are |is )?(?:made (?:of|from)|built (?:of|from)|in|from)\s+(?:\w+\s+)?(deepslate|stone|obsidian|tuff|calcite|brick|iron|steel|concrete)\b",
     "water wheels are wooden (Create 6 lets you pick the plank type); stone blocks can't be used"),
    (r"^\s*\|\s*(cogwheels?|shafts?|large cogwheels?)\s*\|[^|]*\b(iron|steel)\b"
     r"|\b(cogwheels?|shafts?)\s+(?:are |is )?(?:made (?:of|from)|built (?:of|from)|from)\s+(?:\w+\s+)?(iron|steel)\b",
     "shafts and cogwheels are crafted from andesite alloy and planks, not iron or steel"),
    (r"\b(press|mixer|fans?|water ?wheels?|cogwheels?|belts?|shafts?|deployers?|saws?|drills?)\b[^\n]{0,80}"
     r"(vscarmor:|armou?r plating|steel plating)",
     "Create machines can't be clad or re-skinned with other blocks; armor blocks go on the walls around them"),
    (r"\bbelts?\b[^.\n]{0,40}\b(?:made of|made from|of)\s+(?:\w+\s+)?(steel|iron|metal)\b",
     "Mechanical Belts are made from dried kelp, not metal"),
]


def _known_mistakes(out: str) -> str:
    pieces = re.split(r"(\n|;|\.\s)", out)
    for i in range(0, len(pieces), 2):
        for pattern, fix in KNOWN_MISTAKES:
            if re.search(pattern, pieces[i], re.I) and "(correction:" not in pieces[i]:
                pieces[i] = pieces[i].rstrip() + f" (correction: {fix})"
                break
    return "".join(pieces)


# Create parts that are just plumbing: proposing them is normal design work, not an invention.
_PLUMBING = {"Shaft", "Cogwheel", "Gearbox", "Mechanical Belt", "Encased Chain Drive"}


def _label_new_parts(out: str, evidence: str) -> str:
    """Mark the first mention of a Create machine that isn't in the task, notes or research, so new
    ideas are visible instead of looking like facts (10 Nov: Mechanical Arms 'distributing power')."""
    from research_tools import CREATE_COMPONENTS
    ev = (evidence or "").lower()
    titles = [t for _, pages in CREATE_COMPONENTS for t in pages if t not in _PLUMBING]
    for title in dict.fromkeys(titles):
        noun = _MACHINE_WORDS.get(title, title.lower())
        if title.lower() in ev or re.search(r"\b" + re.escape(noun) + r"(e?s)?\b", ev):
            continue
        m = re.search(r"\b" + re.escape(title) + r"s?\b", out, re.I)
        if m and "(new idea" not in out[m.end():m.end() + 12]:
            out = out[:m.end()] + " (new idea - not in the task, notes or research)" + out[m.end():]
    return out


_STAMP = re.compile(r"(?<![\d:/=.])(\d{1,2}):([0-5]\d)(?![\d:])")
_STAMP_WINDOW = 50          # a transcript chunk covers about 40 s after its start time


def _check_timestamps(out: str, evidence: str) -> str:
    """Video timestamps must exist in the transcript the Researcher was given (10 Nov: unchecked
    timestamps). A time is kept if it falls inside a transcript chunk ('[3:40] ...' starts it) or is
    quoted exactly in the evidence; otherwise it is marked."""
    if not re.search(r"youtu|video", out, re.I):
        return out
    starts = [int(a) * 60 + int(b) for a, b in re.findall(r"\[(\d{1,2}):([0-5]\d)\]", evidence or "")]
    quoted = {f"{int(a)}:{b}" for a, b in _STAMP.findall(evidence or "")}

    def check(m):
        t = int(m.group(1)) * 60 + int(m.group(2))
        if f"{int(m.group(1))}:{m.group(2)}" in quoted or any(s <= t <= s + _STAMP_WINDOW for s in starts):
            return m.group(0)
        if out[m.end():m.end() + 14] == " (timestamp no":
            return m.group(0)
        return m.group(0) + " (timestamp not in the transcript - check)"

    return _STAMP.sub(check, out)


_TIP_HEADING = re.compile(r"(tips?\b|gotcha|warning|pitfall|caution|watch out|risks?\b|avoid|mistake)", re.I)
_TAG = re.compile(r"\[(research|notes|rules|game knowledge)\]", re.I)   # [rules] = the notes' Rules section
_UNSOURCED = " [unverified - check in-game]"
def _label_tips(out: str, evidence: str = "") -> str:
    """In tips / warnings / gotchas sections, every bullet must say where it came from:
    [research], [notes], [rules] or [game knowledge]; untagged bullets are labelled unverified
    (10 Nov: an invented 'radar interference' tip). A word-by-word support check was tried the same
    day and dropped: it flagged harmless tips as often as real problems."""
    lines, in_tips = out.split("\n"), False
    for i, line in enumerate(lines):
        bare = line.strip()
        if not re.match(r"([-*+]|\d+[.)])\s+", bare):
            if re.match(r"(#{1,6}\s|\*\*.+\*\*:?$|[A-Z][^.!?]{0,60}:$)", bare):
                in_tips = bool(_TIP_HEADING.search(bare))
            continue
        flat = re.sub(r"[\u2013\u2014]", "-", bare).lower()       # the model copies labels with other dashes
        if in_tips and "[unverified" not in flat and not _TAG.search(bare):
            lines[i] = line.rstrip() + _UNSOURCED
    return "\n".join(lines)


# Shared rules for the three workers (9 Nov: they copied flagged numbers and invented machines).
_GROUNDED = ("\nRules for facts: if the research has a STRESS CALCULATOR, copy its totals and 'enough / short "
             "by' verdicts exactly and base your answer on them; never redo that arithmetic yourself. "
             "Only use numbers (SU, RPM, sizes, counts) that appear in the MINECRAFT "
             "RESEARCH or PROJECT NOTES. If you work one out, show the sum, e.g. '8 x 32 RPM = 256 SU'. "
             "Never invent blocks, machines or mods: use only vanilla and the installed mods. If the "
             "research marks something unverified or doesn't cover it, say 'needs checking' instead of "
             "guessing. In any tips, warnings or gotchas section, end every bullet with where it came from: "
             "[research], [notes] or [game knowledge]. Leave a tip out rather than make one up; never "
             "invent interactions between mods. Only give a video timestamp the research gives.")


# Which extra sources a lookup uses (local files, docs and the wiki are always checked).
_GITHUB_HINTS = re.compile(r"\b(bug|crash\w*|error|issue|broken|not working|compat\w*|version|update\w*|"
                           r"changelog|fix\w*|incompatible|requires)\b")
_YOUTUBE_HINTS = re.compile(r"\b(create|contraption|train|track|build|design|farm|automat\w*|tutorial|"
                            r"how to|layout|mechanic\w*|kinetic|stress|belt|press|mixer|deployer|"
                            r"youtube|video)\b")


def _gather_sources(query: str, state: TeamState) -> tuple[str, list[str]]:
    """Run the lookups for one query. Returns (text for the model, names of sources used)."""
    why = f"{query} {state.get('instructions', '')} {state.get('task', '')}".lower()
    parts = [
        ("local_mods", "INSTALLED MODPACK FILES", lambda: search_local_mods(query)),
        ("mod_docs", "MOD DOCS", lambda: search_mod_docs(query, task=state.get("task", ""))),
        ("wiki", "MINECRAFT WIKI (vanilla)", lambda: search_minecraft_wiki(query, pages=1, chars_per_page=1500)),
        ("modrinth", "MODRINTH", lambda: search_modrinth(query, limit=2)),
        ("curseforge", "CURSEFORGE", lambda: search_curseforge(query, limit=2)),
    ]
    if not config.MOD_DOCS:                       # Create / CC / AP docs are Fort Redux only
        parts = [p for p in parts if p[0] != "mod_docs"]
    if _GITHUB_HINTS.search(why):
        parts.append(("github", "GITHUB ISSUES", lambda: search_github(query)))
    if _YOUTUBE_HINTS.search(why):
        parts.append(("youtube", "YOUTUBE (Create tutorials)", lambda: search_youtube(query)))
    texts, used = [], []
    for name, title, fn in parts:
        text = fn()
        texts.append(f"{title}:\n{text}")
        if not text.startswith("["):          # "[source] ..." means nothing found / failed
            used.append(name)
    return "\n\n".join(texts), used or ["no hits"]
