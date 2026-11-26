"""Settings for the agent graph. Edit these or override with environment variables."""
import os

# Local Ollama model (run `ollama pull qwen3:8b` once first).
# Switched from llama3.1:8b on 9 Nov 2026: better instruction following, same 8 GB VRAM fit.
# To go back: set this to "llama3.1:8b" (still installed).
MODEL = os.getenv("GRAPH_MODEL", "qwen3:8b")
# Qwen3's "thinking" mode writes long hidden reasoning that would eat the output caps below.
THINKING = os.getenv("GRAPH_THINKING", "0") == "1"
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
# Context window (tokens). 12288 since 9 Nov 2026: the Researcher now reads up to 6 sources, and
# project.md outgrew the old 6000-character cut. Qwen3 8B at 12k still fits the 8 GB RTX 3070 Ti.
NUM_CTX = int(os.getenv("GRAPH_NUM_CTX", "9216"))   # 9k (10 Nov): 12k fit 87% on the GPU, 10k 92%
# Longest one model answer may take (seconds). On 9 Nov a request hung for 8+ minutes while
# Minecraft shared the graphics card; now that agent gives up and the run carries on.
REQUEST_TIMEOUT = int(os.getenv("GRAPH_REQUEST_TIMEOUT", "240"))
# Most characters of project.md every agent sees (the "Rules / lessons" section is at the end).
NOTES_CHARS = int(os.getenv("GRAPH_NOTES_CHARS", "10000"))

# Hard cap on how many times the Orchestrator can hand off work.
MAX_STEPS = int(os.getenv("GRAPH_MAX_STEPS", "10"))   # 10 (10 Nov): room for revisions after new research

# --- Anti-loop guards (added 9 Nov 2026 after the 430k-character runaway report) ---
# Most tokens one agent may write in a single answer.
MAX_OUTPUT_TOKENS = int(os.getenv("GRAPH_MAX_OUTPUT_TOKENS", "1200"))
# The Designer writes the longest answers; on 10 Nov it was cut off mid-sentence at 1200.
DESIGN_OUTPUT_TOKENS = int(os.getenv("GRAPH_DESIGN_OUTPUT_TOKENS", "1600"))
# The Researcher's summaries should be short.
RESEARCH_OUTPUT_TOKENS = int(os.getenv("GRAPH_RESEARCH_OUTPUT_TOKENS", "600"))
# Penalise repeating recent text (1.0 = off). 1.15 stops paragraph loops without hurting code.
REPEAT_PENALTY = float(os.getenv("GRAPH_REPEAT_PENALTY", "1.15"))
REPEAT_LAST_N = int(os.getenv("GRAPH_REPEAT_LAST_N", "256"))
# Most times the Researcher can run in one task.
MAX_RESEARCH_CALLS = int(os.getenv("GRAPH_MAX_RESEARCH_CALLS", "3"))
# Most characters of research kept in the shared state (older lookups are dropped).
MAX_RESEARCH_CHARS = int(os.getenv("GRAPH_MAX_RESEARCH_CHARS", "6000"))

_HERE = os.path.dirname(os.path.abspath(__file__))
_INSTANCES = os.path.join(os.path.expanduser("~"), "curseforge", "minecraft", "Instances")

# --- Projects (added 25 Nov 2026) ---
# Each project has its own notes file, modpack folder and game version, so one project's notes and
# installed mods never leak into another's runs (the RPG pack's 25 Nov run got Fort Redux's
# obsidian/armour look and 1.20.1 Zombies mods). Pick one with GRAPH_PROJECT, run_team(project=...)
# or `run_graph.bat --project rpg "task"`.
PROJECTS = {
    "fort_redux": {
        "name": "Fort Redux (Zombies modpack)",
        "notes": os.path.join(_HERE, "project.md"),
        "modpack_dir": os.path.join(_INSTANCES, "Zombies"),
        "loader": "forge", "game_version": "1.20.1", "mod_docs": True,
        "context": "Minecraft 1.20.1 Forge modpack from CurseForge, using the Create mod and TACZ.",
    },
    "rpg": {
        "name": "Cinematic RPG modpack (Dahaad)",
        "notes": os.path.join(_HERE, "projects", "rpg.md"),
        "modpack_dir": os.path.join(_INSTANCES, "Cinema"),
        "loader": "neoforge", "game_version": "1.21.1", "mod_docs": False,
        "context": "Minecraft 1.21.1 NeoForge modpack from CurseForge (instance 'Cinema'): a cinematic "
                   "dark-fantasy winter RPG with Monster Hunter-style hunting. Only NeoForge 1.21.1 builds count; "
                   "Forge 1.20.1-only mods are 'Pinned', not usable.",
    },
}
PROJECT = os.getenv("GRAPH_PROJECT", "fort_redux").strip().lower()
if PROJECT not in PROJECTS:
    raise ValueError(f"Unknown GRAPH_PROJECT '{PROJECT}'. Use one of: {', '.join(PROJECTS)}")
_P = PROJECTS[PROJECT]
PROJECT_NAME = _P["name"]
LOADER = _P["loader"]
GAME_VERSION = _P["game_version"]
# The Create / CC:Tweaked / Advanced Peripherals docs only fit Fort Redux (25 Nov: they leaked "Copycat Block"
# into an RPG build spec).
MOD_DOCS = _P.get("mod_docs", False)
# The vanilla game jar: its English names are the list of real blocks the checks compare against.
MINECRAFT_JAR = os.getenv("GRAPH_MINECRAFT_JAR", os.path.join(
    os.path.expanduser("~"), "curseforge", "minecraft", "Install", "versions", GAME_VERSION, f"{GAME_VERSION}.jar"))

# Background the Minecraft Researcher always keeps in mind.
MODPACK_CONTEXT = os.getenv("GRAPH_MODPACK_CONTEXT", _P["context"])

# Project notes every agent reads (edit the project's notes file as the project changes).
PROJECT_FILE = os.getenv("GRAPH_PROJECT_FILE", _P["notes"])

# Modpack instance folder. Read-only: the Researcher lists and searches the mods (jars) and
# config files here; nothing in it is ever changed.
MODPACK_DIR = os.getenv("GRAPH_MODPACK_DIR", _P["modpack_dir"])

# Where final reports are saved.
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
