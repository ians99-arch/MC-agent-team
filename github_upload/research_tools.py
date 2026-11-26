"""Free, no-key lookups for the Minecraft Researcher. Standard library only (no installs).

Sources
  search_minecraft_wiki  vanilla facts from minecraft.wiki
  search_modrinth        mod listings from Modrinth
  search_curseforge      mod listings from CurseForge (needs CURSEFORGE_API_KEY)
  search_mod_docs        CC:Tweaked API index, Advanced Peripherals 0.7 docs, Create wiki
  search_local_mods      the modpack's own jars + config files (exact installed versions)
  search_github          issues / repos for the main mods (bugs, changelogs, compatibility)
  search_youtube         Create tutorials: finds videos and pulls the relevant parts of the transcript

Every function returns plain text and never raises; on failure it returns a one-line "[source] ..." note.
Added 9 Nov 2026: docs, local mods, GitHub and YouTube.
"""
import html
import json
import math
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

import config

HEADERS = {"User-Agent": "agent-graph-minecraft-researcher/1.1 (personal project)"}
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/130.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Cookie": "CONSENT=YES+1",
}
_HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(_HERE, "cache")
STOP = set("the a an and or of to in on for with how what is are does do can why when which from into "
           "minecraft mod mods using use about this that it its be by at as".split())
# Words that appear in almost every mod file / config: they may rank results but never match on their own.
GENERIC = set("version versions require requires required dependency dependencies config configs setting "
              "settings file files value values option options enable enabled".split())


# ---------------------------------------------------------------- helpers
def _secret(name: str) -> str:
    """An API key from the environment, or else from the Windows user variables in the registry
    (25 Nov 2026: the Claude app starts this server with a limited environment, so user variables set in
    Windows never arrived). Read-only; the value is never printed or saved."""
    value = os.getenv(name, "").strip()
    if value or os.name != "nt":
        return value
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
            return str(winreg.QueryValueEx(k, name)[0]).strip()
    except OSError:
        return ""



# Web answers saved on disk so repeat runs are fast and don't burn GitHub's 60-an-hour limit
# (added 10 Nov 2026). Hours each site's answers are kept; sites not listed are never cached
# (YouTube watch pages and caption links carry short-lived keys).
WEB_CACHE_DIR = os.path.join(CACHE_DIR, "web")
WEB_CACHE_HOURS = {"api.curseforge.com": 24, "api.github.com": 12, "create.fandom.com": 72, "minecraft.wiki": 72,
                   "api.modrinth.com": 24, "www.youtube.com/results": 24}
WEB_CACHE_MAX_DAYS = 14          # files older than this are deleted


def _cache_hours(url: str) -> float:
    rest = url.split("://", 1)[-1]
    return next((h for prefix, h in WEB_CACHE_HOURS.items() if rest.startswith(prefix)), 0)


def _cache_path(key: str) -> str:
    import hashlib
    return os.path.join(WEB_CACHE_DIR, hashlib.sha1(key.encode("utf-8")).hexdigest() + ".bin")


def _cache_read(key: str, hours: float) -> bytes | None:
    path = _cache_path(key)
    try:
        if time.time() - os.path.getmtime(path) < hours * 3600:
            with open(path, "rb") as f:
                return f.read()
    except OSError:
        pass
    return None


def _cache_write(key: str, data: bytes) -> None:
    try:
        os.makedirs(WEB_CACHE_DIR, exist_ok=True)
        with open(_cache_path(key), "wb") as f:
            f.write(data)
    except OSError:
        pass                                     # caching is a convenience, never a failure


def prune_web_cache(max_days: float = WEB_CACHE_MAX_DAYS) -> int:
    """Delete cached answers older than max_days. Returns how many files were removed."""
    removed = 0
    try:
        for name in os.listdir(WEB_CACHE_DIR):
            path = os.path.join(WEB_CACHE_DIR, name)
            if time.time() - os.path.getmtime(path) > max_days * 86400:
                os.remove(path)
                removed += 1
    except OSError:
        pass
    return removed


def _get(url: str, headers=None, data: bytes | None = None, timeout: int = 15) -> bytes:
    hours = _cache_hours(url) if data is None else 0         # only plain GETs are cached
    if hours:
        hit = _cache_read(url, hours)
        if hit is not None:
            return hit
    req = urllib.request.Request(url, headers=headers or HEADERS, data=data)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
    if hours:                                    # errors raise above, so they are never cached
        _cache_write(url, body)
    return body


def _get_json(url: str, headers=None, data=None, timeout: int = 15):
    return json.loads(_get(url, headers, data, timeout).decode("utf-8"))


def _cached_json(name: str, url: str, max_age_h: float = 72, headers=None):
    """Download a JSON file at most every few days (doc indexes are big and rarely change)."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, name)
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < max_age_h * 3600:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    data = _get_json(url, headers)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    return data


def _keywords(query: str) -> list[str]:
    words = re.findall(r"[a-z0-9_:.]+", query.lower())
    return [w for w in words if len(w) >= 3 and w not in STOP] or words


def _score(text: str, kws: list[str]) -> int:
    """0 unless at least one specific (non-generic) keyword appears; more distinct keywords rank higher."""
    t = text.lower()
    specific = [k for k in kws if k not in GENERIC] or kws
    if not any(k in t for k in specific):
        return 0
    hits = sum(1 for k in kws if k in t)
    return hits * 10 + sum(t.count(k) for k in kws)


def _coverage(text: str, kws: list[str]) -> float:
    """Share of the specific keywords that appear in the text (0..1)."""
    specific = [k for k in kws if k not in GENERIC] or kws
    t = text.lower()
    return sum(1 for k in specific if k in t) / max(len(specific), 1)


def _html_to_text(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style|nav|header|footer)[^>]*>.*?</\1>", " ", raw)
    raw = re.sub(r"(?i)<br\s*/?>|</(p|div|li|h\d|tr|pre)>", "\n", raw)
    raw = re.sub(r"<[^>]+>", " ", raw)
    raw = html.unescape(raw)
    raw = re.sub(r"[ \t]+", " ", raw)
    return re.sub(r"\n\s*\n+", "\n\n", raw).strip()


_INFOBOXES = ("block", "item", "entity", "kinetic", "component", "infobox")


def _infobox_stats(text: str) -> str:
    """Pull 'key = value' fields out of the page's infobox ({{Block|stress_impact=8x RPM|...}}).
    Wikis keep the key numbers there (stress, speed, hardness), and plain template-stripping loses them."""
    m = re.search(r"\{\{\s*(" + "|".join(_INFOBOXES) + r")\b", text, re.I)
    if not m:
        return ""
    depth, i, start = 0, m.start(), m.start()
    while i < len(text) - 1:                         # find the matching }} (templates can nest)
        pair = text[i:i + 2]
        if pair == "{{":
            depth += 1
            i += 2
            continue
        if pair == "}}":
            depth -= 1
            i += 2
            if depth == 0:
                break
            continue
        i += 1
    body = text[start + 2:i - 2]
    fields, depth, cur = [], 0, ""
    for ch in body:                                  # split on | only at the top level
        depth += ch in "{[" and 1 or 0
        depth -= ch in "}]" and 1 or 0
        if ch == "|" and depth == 0:
            fields.append(cur)
            cur = ""
        else:
            cur += ch
    fields.append(cur)
    stats = []
    for f in fields[1:]:
        if "=" not in f:
            continue
        k, v = (s.strip() for s in f.split("=", 1))
        v = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]+)\]\]", r"\1", v).strip()   # "512 [[Kinetic Stress|SU]]" -> "512 SU"
        if v and len(v) <= 60 and "<" not in v and k.lower() not in ("image", "image2", "gallery", "caption"):
            stats.append(f"{k.replace('_', ' ')}: {v}")
    return ("Stats: " + "; ".join(stats)) if stats else ""


def _clean_wikitext(text: str) -> str:
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    stats = _infobox_stats(text)
    text = re.sub(r"<ref[^>]*>.*?</ref>", "", text, flags=re.S)
    text = re.sub(r"<gallery>.*?</gallery>", "", text, flags=re.S)
    for _ in range(3):                                               # nested templates, inside out
        text = re.sub(r"\{\{[^{}]*\}\}", "", text)
    text = stats + "\n" + text if stats else text
    text = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]+)\]\]", r"\1", text)  # [[link|label]]
    text = re.sub(r"'{2,}", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _mediawiki(base: str, label: str, query: str, pages: int, chars_per_page: int) -> str:
    api = base + "/api.php"
    q = urllib.parse.urlencode({"action": "query", "list": "search", "srsearch": query,
                                "srlimit": pages, "format": "json"})
    hits = _get_json(f"{api}?{q}").get("query", {}).get("search", [])
    if not hits:
        return f"[{label}] No results for '{query}'."
    out = []
    for hit in hits:
        title = hit["title"]
        p = urllib.parse.urlencode({"action": "query", "prop": "revisions", "rvprop": "content",
                                    "rvslots": "main", "titles": title, "format": "json", "formatversion": 2})
        page = _get_json(f"{api}?{p}")["query"]["pages"][0]
        content = page.get("revisions", [{}])[0].get("slots", {}).get("main", {}).get("content", "")
        url = base + "/wiki/" + urllib.parse.quote(title.replace(" ", "_"))
        out.append(f"## {title} ({label})\n{url}\n{_clean_wikitext(content)[:chars_per_page]}")
    return "\n\n".join(out)


# ---------------------------------------------------------------- vanilla + Modrinth (existing)
def search_minecraft_wiki(query: str, pages: int = 2, chars_per_page: int = 2500) -> str:
    """Vanilla Minecraft facts from minecraft.wiki."""
    try:
        text = _mediawiki("https://minecraft.wiki", "minecraft.wiki", query, pages, chars_per_page)
        # wiki search always returns *something*: drop pages that cover under half the question
        # (25 Nov: 'Snow Real Magic' returned ModJam and an April Fools snapshot)
        if not text.startswith("[") and _coverage(text, _keywords(query)) < 0.5:
            return f"[minecraft.wiki] No relevant page for '{query}' (vanilla wiki; mods aren't covered)."
        return text
    except Exception as e:
        return f"[minecraft.wiki] Lookup failed: {e}"


_LOADER_WORDS = re.compile(r"\b(neo\s?forge|forge|fabric|quilt|mods?|minecraft|mc|builds?|versions?|ports?|"
                           r"compatib\w*|supports?|available|exists?|does|have|has|is|there|any|"
                           r"\d+\.\d+(?:\.\d+)?)\b|[?!]", re.I)


def _modrinth_query(query: str) -> str:
    """Drop loader / version / 'mod' words: Modrinth matches them against mod names and finds nothing
    (25 Nov: 'neoforge 1.21.1 monster hunter mods' got no hits). The loader and version go in facets."""
    return " ".join(_LOADER_WORDS.sub(" ", query).split()) or query


def search_modrinth(query: str, limit: int = 3) -> str:
    """Mod info from Modrinth's public API, filtered to the project's loader and game version.
    If nothing has a build for them, says so and lists what versions do exist (= a Pinned mod)."""
    try:
        facets = json.dumps([[f"categories:{config.LOADER}"], [f"versions:{config.GAME_VERSION}"],
                             ["project_type:mod"]])
        # A stray extra word ('Pinned', 'boss') makes Modrinth find nothing: retry with fewer words
        # (25 Nov 2026). At most 3 tries; the mod name is usually the first words.
        words = _modrinth_query(query).split()
        lowest = max(len(words) - 3, min(len(words), 2) - 1)        # never below 2 words (1 if that's all)
        tries = [" ".join(words[:n]) for n in range(len(words), lowest, -1)] or [query]
        hits, text = [], tries[0]
        for text in tries:
            q = urllib.parse.urlencode({"query": text, "limit": limit, "facets": facets})
            hits = _get_json(f"https://api.modrinth.com/v2/search?{q}").get("hits", [])
            if hits:
                break
        header = f"Builds for {config.LOADER} {config.GAME_VERSION} (filtered):"
        if not hits:
            for text in tries:
                q = urllib.parse.urlencode({"query": text, "limit": limit})
                hits = _get_json(f"https://api.modrinth.com/v2/search?{q}").get("hits", [])
                if hits:
                    break
            if not hits:
                return f"[Modrinth] No mods found for '{tries[0]}'."
            header = (f"NO {config.LOADER} {config.GAME_VERSION} build found for '{text}'. "
                      "Closest matches on other loaders/versions (not usable as-is):")
        lines = [header]
        for h in hits:
            versions = ", ".join(h.get("versions", [])[-5:])
            lines.append(
                f"- {h['title']} ({h.get('project_type', 'mod')}): {h.get('description', '')}\n"
                f"  Loaders/categories: {', '.join(h.get('categories', []))}\n"
                f"  Recent MC versions: {versions}\n"
                f"  https://modrinth.com/{h.get('project_type', 'mod')}/{h['slug']}"
            )
        return "\n".join(lines)
    except Exception as e:
        return f"[Modrinth] Lookup failed: {e}"


# ---------------------------------------------------------------- CurseForge (added 25 Nov 2026)
_CF_LOADERS = {"forge": 1, "fabric": 4, "quilt": 5, "neoforge": 6}   # CurseForge's modLoaderType numbers


def _key_shape(key: str) -> str:
    """Describe a key without revealing it, to spot copy-paste mistakes."""
    return (f"(Key shape: {len(key)} characters; starts like a CurseForge key ('$2a$'): "
            f"{'yes' if key.startswith('$2a$') else 'no'}; quotes in it: {'yes' if any(q in key for q in chr(34) + chr(39)) else 'no'}; "
            f"spaces in it: {'yes' if ' ' in key else 'no'}.)")


def search_curseforge(query: str, limit: int = 3) -> str:
    """Mods on CurseForge (where most of Ian's mods live), filtered to the project's loader and version.
    Needs the CURSEFORGE_API_KEY environment variable; the key is sent in a header and never printed."""
    key = _secret("CURSEFORGE_API_KEY")
    if not key:
        return "[CurseForge] No API key set (CURSEFORGE_API_KEY), so CurseForge wasn't searched."
    headers = {"x-api-key": key, "Accept": "application/json", "User-Agent": HEADERS["User-Agent"]}
    words = _modrinth_query(query).split()
    lowest = max(len(words) - 3, min(len(words), 2) - 1)
    tries = [" ".join(words[:n]) for n in range(len(words), lowest, -1)] or [query]

    def search(text, filtered):
        params = {"gameId": 432, "classId": 6, "searchFilter": text, "pageSize": limit,
                  "sortField": 2, "sortOrder": "desc"}             # 432 = Minecraft, 6 = mods, 2 = popularity
        if filtered:
            params.update({"gameVersion": config.GAME_VERSION, "modLoaderType": _CF_LOADERS.get(config.LOADER, 0)})
        url = "https://api.curseforge.com/v1/mods/search?" + urllib.parse.urlencode(params)
        return _get_json(url, headers).get("data", [])

    try:
        hits, text = [], tries[0]
        for text in tries:
            hits = search(text, True)
            if hits:
                break
        header = f"Builds for {config.LOADER} {config.GAME_VERSION} on CurseForge (filtered):"
        if not hits:
            for text in tries:
                hits = search(text, False)
                if hits:
                    break
            if not hits:
                return f"[CurseForge] No mods found for '{tries[0]}'."
            header = (f"NO {config.LOADER} {config.GAME_VERSION} build on CurseForge for '{text}'. "
                      "Closest matches on other loaders/versions (not usable as-is):")
        lines = [header]
        for m in hits:
            files = [f for f in m.get("latestFilesIndexes", []) if f.get("gameVersion") == config.GAME_VERSION]
            names = ", ".join(dict.fromkeys(f.get("filename", "") for f in files[:2] if f.get("filename")))
            versions = sorted({f.get("gameVersion", "") for f in m.get("latestFilesIndexes", [])}, reverse=True)[:5]
            lines.append(f"- {m.get('name')}: {m.get('summary', '')}\n"
                         f"  Latest files for {config.GAME_VERSION}: {names or 'none'}\n"
                         f"  Recent MC versions: {', '.join(versions)}\n"
                         f"  {(m.get('links') or {}).get('websiteUrl', '')}")
        return "\n".join(lines)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            try:
                why = re.sub(r"\s+", " ", e.read().decode("utf-8", "ignore"))[:200].replace(key, "[key]")
            except Exception:
                why = ""
            return (f"[CurseForge] The API key was refused (HTTP {e.code}): check CURSEFORGE_API_KEY. {_key_shape(key)}"
                    + (f" Server said: {why}" if why else ""))
        return f"[CurseForge] Lookup failed: HTTP {e.code}"
    except Exception as e:
        return f"[CurseForge] Lookup failed: {type(e).__name__}"


# ---------------------------------------------------------------- 1. mod documentation
def _cc_tweaked(kws: list[str], limit: int) -> str:
    idx = _cached_json("cc_tweaked_index.json", "https://tweaked.cc/index.json")
    scored = []
    for key, e in idx.items():
        blob = key + " " + e.get("name", "") + " " + e.get("summary", "")
        if _coverage(blob, kws) < 0.66:         # narrow API docs: must match 2/3 of the specific words
            continue
        s = _score(key, kws) * 3 + _score(e.get("summary", ""), kws)
        if s:
            scored.append((s, key, e))
    scored.sort(key=lambda x: -x[0])
    if not scored:
        return ""
    lines = [f"- {e.get('name', k)}: {e.get('summary', '')} (https://tweaked.cc/{e.get('url', '')})"
             for _, k, e in scored[:6]]
    # full text of the best match's page, around that function
    best = scored[0][2]
    try:
        page = _html_to_text(_get("https://tweaked.cc/" + best["url"].split("#")[0]).decode("utf-8", "ignore"))
        name = best.get("name", "").split("(")[0].split(".")[-1]
        at = max(page.find(name + "("), 0)
        lines.append(f"From the {best['url'].split('#')[0]} page:\n" + page[at:at + limit])
    except Exception:
        pass
    return "## CC:Tweaked docs (tweaked.cc)\n" + "\n".join(lines)


def _advanced_peripherals(kws: list[str], limit: int) -> str:
    base = "https://docs.advanced-peripherals.de/0.7/"          # 0.7 = the installed 0.7.48r
    idx = _cached_json("advanced_peripherals_0.7_index.json", base + "search/search_index.json")
    # The index has one entry per page plus one per section ("page/#anchor"). Pick the page that covers
    # the question best, then show that page's best-matching sections (e.g. scanEntities), not its intro.
    pages: dict[str, list[dict]] = {}
    for d in idx.get("docs", []):
        pages.setdefault(d.get("location", "").split("#")[0], []).append(d)
    best_page, best_key = None, (0.0, 0)
    for loc, entries in pages.items():
        blob = " ".join(e.get("title", "") + " " + e.get("text", "") for e in entries)
        key = (_coverage(blob, kws), _score(blob, kws))
        if key > best_key:
            best_page, best_key = loc, key
    if not best_page or best_key[0] < 0.66:    # 2/3 of the specific words
        return ""
    entries = pages[best_page]
    intro = next((e for e in entries if "#" not in e.get("location", "")), entries[0])
    sections = sorted((e for e in entries if "#" in e.get("location", "")),
                      key=lambda e: -(_score(e.get("title", ""), kws) * 3 + _score(e.get("text", ""), kws)))
    sections = [e for e in sections if _score(e.get("title", "") + " " + e.get("text", ""), kws)]
    out = [f"- Page: {intro.get('title', '')} ({base}{best_page})"]
    used = 0
    for e in sections[:4] or [intro]:
        text = _html_to_text(e.get("text", ""))[:limit - used]
        if not text:
            break
        out.append(f"  ### {e.get('title', '')} ({base}{e.get('location', '')})\n  {text}")
        used += len(text)
    return "## Advanced Peripherals 0.7 docs\n" + "\n".join(out)


# Create machines a question may name -> Create wiki page(s). Titles checked against the wiki on 10 Nov 2026.
CREATE_COMPONENTS = [
    (r"\bpress", ["Mechanical Press"]), (r"\bmixer", ["Mechanical Mixer"]),
    (r"\bfans?\b|encased fan", ["Encased Fan"]), (r"water ?wheels?", ["Water Wheel", "Large Water Wheel"]),
    (r"windmill", ["Windmill Bearing"]), (r"steam engine", ["Steam Engine"]), (r"\bbelts?\b", ["Mechanical Belt"]),
    (r"\bshafts?\b", ["Shaft"]), (r"\bcog", ["Cogwheel"]), (r"gearbox", ["Gearbox"]), (r"deployer", ["Deployer"]),
    (r"\bsaws?\b", ["Mechanical Saw"]), (r"\bdrills?\b", ["Mechanical Drill"]), (r"crush", ["Crushing Wheel"]),
    (r"millstone", ["Millstone"]), (r"harvester", ["Mechanical Harvester"]), (r"mechanical arms?|\barms?\b", ["Mechanical Arm"]),
    (r"hand crank", ["Hand Crank"]), (r"blaze burner", ["Blaze Burner"]), (r"\bpumps?\b", ["Mechanical Pump"]),
    (r"bearing", ["Mechanical Bearing"]), (r"crafter", ["Mechanical Crafter"]), (r"piston", ["Mechanical Piston"]),
    (r"pulley", ["Rope Pulley"]), (r"chain drive", ["Encased Chain Drive"]), (r"creative motor", ["Creative Motor"]),
]
_STAT_KEYS = ("stress", "speed", "rpm", "capacity")


_COMPONENT_CACHE: dict[str, list[str] | None] = {}      # title -> stat strings (None = page missing)


def _component_titles(text: str, limit: int = 8) -> list[str]:
    q = text.lower()
    titles = []
    for pattern, pages in CREATE_COMPONENTS:
        if re.search(pattern, q):
            titles += [t for t in pages if t not in titles]
    return titles[:limit]


def _component_stats(text: str) -> list[tuple[str, list[str], str]]:
    """[(title, ['stress impact: 8x RPM', ...], url)] for the Create machines named in text.
    One wiki request for all of them; results cached for the rest of the run."""
    titles = _component_titles(text)
    todo = [t for t in titles if t not in _COMPONENT_CACHE]
    if todo:
        p = urllib.parse.urlencode({"action": "query", "prop": "revisions", "rvprop": "content", "rvslots": "main",
                                    "titles": "|".join(todo), "redirects": 1, "format": "json", "formatversion": 2})
        pages = _get_json(f"https://create.fandom.com/api.php?{p}").get("query", {}).get("pages", [])
        for title in todo:
            page = next((pg for pg in pages if pg.get("title") == title), None)
            if not page or page.get("missing"):
                _COMPONENT_CACHE[title] = None
                continue
            content = page.get("revisions", [{}])[0].get("slots", {}).get("main", {}).get("content", "")
            stats = [s for s in _infobox_stats(content).removeprefix("Stats: ").split("; ")
                     if any(k in s.split(":")[0].lower() for k in _STAT_KEYS)]
            if any(s.startswith("stress impact") for s in stats):   # a machine: hide generator-only stats
                stats = [s for s in stats if not s.startswith(("stress capacity", "speed generated"))]  # (fan's
                # 16x RPM capacity was read as its load on 9-10 Nov)
            _COMPONENT_CACHE[title] = stats
    return [(t, _COMPONENT_CACHE[t], "https://create.fandom.com/wiki/" + urllib.parse.quote(t.replace(" ", "_")))
            for t in titles if _COMPONENT_CACHE.get(t) is not None]


def _create_components(query: str) -> str:
    """Stress/speed stats for every Create machine the question names, in ONE wiki request.
    (10 Nov: a long multi-machine query found no single wiki page, so the model invented every number.)"""
    lines = [f"- {title}: {'; '.join(stats) or 'no stress/speed stats listed'} ({url})"
             for title, stats, url in _component_stats(query)]
    if not lines:
        return ""
    return ("## Create machine stats (Create wiki)\n" + "\n".join(lines) +
            "\nHow to read these: 'stress impact: 8x RPM' means the machine uses 8 SU for every RPM it runs at "
            "(8 x 16 RPM = 128 SU). A source's 'stress capacity' is the SU it adds to its network.")


# ---------------------------------------------------------------- 1b. Create stress calculator (exact)
_WORDS = {"one": 1, "a": 1, "an": 1, "single": 1, "two": 2, "both": 2, "pair": 2, "double": 2, "twin": 2,
          "three": 3, "four": 4, "five": 5, "six": 6, "eight": 8}
_NOUN = {"Mechanical Press": "press", "Mechanical Mixer": "mixer", "Encased Fan": "fan", "Mechanical Saw": "saw",
         "Mechanical Drill": "drill", "Deployer": "deployer", "Crushing Wheel": "crushing wheel",
         "Millstone": "millstone", "Mechanical Pump": "pump", "Mechanical Crafter": "crafter",
         "Mechanical Piston": "piston", "Mechanical Bearing": "bearing", "Rope Pulley": "pulley",
         "Mechanical Harvester": "harvester", "Water Wheel": "water wheel", "Large Water Wheel": "large water wheel"}
_DEFAULT_COUNT = {"Crushing Wheel": 2}          # crushing wheels always work in pairs
_STEPS = (8, 16, 32, 64)                         # water-wheel speed doubled by large->small cogwheel steps


def _first_number(s: str) -> float | None:
    m = re.search(r"\d[\d,]*(?:\.\d+)?", s or "")
    return float(m.group(0).replace(",", "")) if m else None


def _count_in(text: str, title: str) -> tuple[int, bool]:
    """How many of a machine the task asks for. Returns (count, stated) - stated=False means assumed."""
    noun = _NOUN.get(title, title.split()[-1].lower())
    m = re.search(r"\b(\d+|" + "|".join(_WORDS) + r")\s+(?:of\s+)?(?:the\s+)?(?:[\w-]+\s+){0,2}?" +
                  re.escape(noun) + r"s?\b", text.lower())
    if m:
        w = m.group(1)
        return (int(w) if w.isdigit() else _WORDS[w]), True
    if re.search(r"\bthe\s+" + re.escape(noun) + r"\b(?!s)", text.lower()):
        return 1, True                                   # "the press" = one press
    return _DEFAULT_COUNT.get(title, 1), False


def _fmt(n: float) -> str:
    return f"{n:,.0f}" if float(n).is_integer() else f"{n:,.1f}"


_NUM = r"(\d+|" + "|".join(w for w in _WORDS if w not in ("a", "an")) + r")"
_HAVE = re.compile(r"\b(has|have|had|built|currently|existing|already|running|installed|there are|got|"
                   r"powered by|driven by)\b", re.I)
_BUILT_PATTERNS = {   # power sources the notes may say are already built
    "Water Wheel": re.compile(r"\b" + _NUM + r"\s+(?:(?!large\b)[a-z-]+\s+){0,2}?water[\s-]?wheels?\b", re.I),
    "Large Water Wheel": re.compile(r"\b" + _NUM + r"\s+(?:[a-z-]+\s+)?large\s+water[\s-]?wheels?\b", re.I),
}


def built_sources(text: str) -> dict[str, tuple[int, str]]:
    """Power sources the text says already exist: {'Water Wheel': (4, 'the line it came from')}.
    Only lines that say something is built / has / currently ... count, and the LAST such line wins,
    because project notes are appended in date order (10 Nov: Ian's mill has 4 water wheels)."""
    found = {}
    for line in (text or "").splitlines():
        if not _HAVE.search(line):
            continue
        for title, pat in _BUILT_PATTERNS.items():
            m = pat.search(line)
            if m:
                w = m.group(1).lower()
                found[title] = (int(w) if w.isdigit() else _WORDS[w], line.strip(" -*\t"))
    return found


def stress_calculator(text: str, notes: str = "") -> str:
    """Exact stress maths for the Create machines named in text, from the Create wiki stats.
    Done in code because the 8B model got the arithmetic wrong in every run on 9-10 Nov.
    notes: the project notes; power sources they say are already built get a plain yes/no verdict."""
    built = built_sources(text + "\n" + (notes or ""))
    try:
        comps = _component_stats(text + " " + " ".join(built))
    except Exception as e:
        return f"[stress calculator] Could not load machine stats: {e}"
    machines, sources = [], []
    for title, stats, _ in comps:
        d = {k.strip(): v.strip() for k, v in (s.split(":", 1) for s in stats if ":" in s)}
        impact, need = d.get("stress impact", ""), d.get("speed requirement", "")
        if impact:
            per_rpm = 0.0 if impact.lower().startswith(("none", "0")) else _first_number(impact)
            if per_rpm is None or "rpm" not in impact.lower() and per_rpm:
                continue                                  # not a per-RPM figure: can't compute safely
            count, stated = _count_in(text, title)
            min_rpm = _first_number(need) if need and need.lower() != "none" else None
            machines.append({"name": title, "per_rpm": per_rpm, "count": count, "stated": stated, "min": min_rpm})
        elif d.get("stress capacity"):
            cap = _first_number(d["stress capacity"])
            if cap and "x" not in d["stress capacity"].lower().split("su")[0]:
                rng = "-" in d["stress capacity"]
                sources.append({"name": title, "su": cap, "rpm": _first_number(d.get("speed generated", "")),
                                "range": rng})
    load = [m for m in machines if m["per_rpm"]]
    if not load:
        return ""
    lines = ["## STRESS CALCULATOR (worked out by code from the Create wiki stats - these numbers are exact)",
             "Machine SU = stress impact x RPM x how many. Shafts, cogwheels and belts use 0 SU. A source's "
             "capacity stays the same when you gear the speed up or down.", "",
             "SU per machine type at each speed:"]
    for m in load:
        many = f" x{m['count']}, all {m['count']} together" if m["count"] > 1 else ""
        note = "" if m["stated"] else " (count assumed)"
        minr = f", needs at least {_fmt(m['min'])} RPM" if m["min"] else ""
        cells = ", ".join(f"{s} RPM = {_fmt(m['per_rpm'] * s * m['count'])} SU" for s in _STEPS[:3])
        lines.append(f"- {m['name']}{many} (uses {_fmt(m['per_rpm'])} SU per RPM each{minr}){note}: {cells}")
    for m in machines:
        if not m["per_rpm"]:
            lines.append(f"- {m['name']}: 0 SU at any speed")

    def speed_for(m, base):
        need = max(m["min"] or 0, base)
        return next((s for s in _STEPS if s >= need), _STEPS[-1])

    floor = max([m["min"] or 0 for m in load] + [8])
    same = next((s for s in _STEPS if s >= floor), _STEPS[-1])
    plans = [(f"Everything at {same} RPM", {m["name"]: same for m in load})]
    split = {m["name"]: speed_for(m, 16) if m["min"] else 16 for m in load}
    if split != plans[0][1]:
        slow = [m["name"] for m in load if not m["min"]]
        if len(slow) == len(load):                # nothing has a minimum speed: it's just a faster plan
            plans.append(("Everything at 16 RPM", split))
        else:
            plans.append(("Only the machines that need it run faster; " + ", ".join(slow) + " at 16 RPM", split))
    lines += ["", "Total demand:"]
    totals, each_plan = [], []
    for label, speeds in plans:
        each_plan.append((label, "; ".join(
            f"{_noun_count(m)} at {speeds[m['name']]} RPM = {_fmt(m['per_rpm'] * speeds[m['name']] * m['count'])} SU"
            + (f" ({_fmt(m['per_rpm'] * speeds[m['name']])} SU each)" if m["count"] > 1 else "")
            for m in load)))
        parts = [f"{_fmt(m['per_rpm'] * speeds[m['name']] * m['count'])} SU "
                 f"({m['name']}{' x' + str(m['count']) + ' together' if m['count'] > 1 else ''} at {speeds[m['name']]} RPM)"
                 for m in load]
        total = sum(m["per_rpm"] * speeds[m["name"]] * m["count"] for m in load)
        totals.append((label, total))
        lines.append(f"- {label}: " + " + ".join(parts) + f" = {_fmt(total)} SU")
    if sources:
        bottom = []
        for label, total in totals:
            picks = []
            for src in sources:
                if src["range"]:
                    continue
                n = max(1, math.ceil(total / src["su"]))
                spare = src["su"] * n - total
                safe = f", {n + 1} for some margin" if spare == 0 else ""
                picks.append(f"{n} {src['name']}{'s' if n > 1 else ''} ({_fmt(spare)} SU spare{safe})")
            if picks:
                bottom.append(f"- {label.split(';')[0]} = {_fmt(total)} SU -> fewest sources: " + " or ".join(picks))
        have = [(s, built[s["name"]]) for s in sources if s["name"] in built and not s["range"]]
        mine = []
        for s, (n, said) in have:
            cap = s["su"] * n
            verdicts = [f"{label.split(';')[0]}: " + ("ENOUGH, " + (f"{_fmt(cap - total)} SU spare" if cap > total
                                                                   else "no margin") if cap >= total
                                                    else f"SHORT by {_fmt(total - cap)} SU")
                        for label, total in totals]
            mine.append(f"- YOUR BUILD: {n} {s['name']}{'s' if n > 1 else ''} = {n} x {_fmt(s['su'])} = {_fmt(cap)} SU "
                        f"-> " + " | ".join(verdicts) + f'  (from the notes: "{said[:120]}")')
        if mine:
            mine.append("- That assumes nothing else already runs on the same network: anything else on those "
                        "shafts takes from it (Engineer's Goggles show what's free).")
        else:
            mine = ["- How many wheels are already built isn't known here: count them in-game (Engineer's Goggles "
                    "show SU), or add the count to the project notes."]
        per_machine = [f"- {label.split(';')[0]}, per machine: {each}" for label, each in each_plan]
        if bottom:
            lines[1:1] = ["BOTTOM LINE (copy this):"] + mine + bottom + per_machine
        lines += ["", "Supply vs demand:"]
        for s in sources:
            if built and s["name"] not in built:
                continue                         # the build is known: other sources are in the bottom line
            if s["range"]:
                lines.append(f"- {s['name']}: {s['su']:,.0f}+ SU each (varies with size), not compared.")
                continue
            # known build: just that count (the bottom line already gives the fewest needed)
            for n in ((built[s["name"]][0],) if s["name"] in built else (1, 2, 3, 4)):
                cap = s["su"] * n
                verdicts = []
                for label, total in totals:
                    diff = cap - total
                    verdicts.append(f"{label.split(';')[0]}: " +
                                    (("exactly enough, no margin" if diff == 0 else f"enough, {_fmt(diff)} SU spare")
                                     if diff >= 0 else f"short by {_fmt(-diff)} SU"))
                lines.append(f"- {n} x {s['name']} = {_fmt(cap)} SU -> " + " | ".join(verdicts))
            if s["rpm"]:
                top = max(sp for _, sp in plans[0][1].items())
                doublings = math.ceil(math.log2(top / s["rpm"])) if top > s["rpm"] else 0
                if doublings:
                    lines.append(f"  ({s['name']} turns at {_fmt(s['rpm'])} RPM: reaching {top} RPM takes "
                                 f"{doublings} large-to-small cogwheel step{'s' if doublings > 1 else ''}, each doubles the speed.)")
    else:
        lines[1:1] = ["BOTTOM LINE (copy this):"] + [f"- {label.split(';')[0]}, per machine: {each}"
                                                     for label, each in each_plan]
        lines += ["", "No power source named, so supply isn't compared."]
    return "\n".join(lines)


def _noun_count(m: dict) -> str:
    return f"{m['name']} x{m['count']} together" if m["count"] > 1 else m["name"]


def search_mod_docs(query: str, chars: int = 2400, task: str = "") -> str:
    """Official docs: CC:Tweaked (Lua API), Advanced Peripherals 0.7, and the Create wiki.
    task: the full task text; Create machines named there get their stats too, even if the short
    search query left them out (10 Nov: the query dropped 'water wheels')."""
    kws = _keywords(query)
    parts = []
    try:                                       # exact stats for any Create machines named: always kept
        comp = _create_components(query + " " + task)
    except Exception as e:
        comp = f"[Create wiki stats] Lookup failed: {e}"
    for name, fn in (("CC:Tweaked", lambda: _cc_tweaked(kws, 900)),
                     ("Advanced Peripherals", lambda: _advanced_peripherals(kws, 900)),
                     ("Create wiki", lambda: _mediawiki("https://create.fandom.com", "Create wiki", query, 3, 480))):
        try:
            text = fn()
            # wiki search always returns *something*; drop pages that cover under half the question
            if text and "No results" not in text and _coverage(text, kws) >= 0.5:
                parts.append(text)
        except Exception as e:
            parts.append(f"[{name} docs] Lookup failed: {e}")
    if not parts and not comp:
        return f"[mod docs] Nothing found for '{query}'."
    # machine stats first (never cut), then the best-matching pages within the budget
    parts.sort(key=lambda p: -_score(p, kws))
    rest = "\n\n".join(parts)[:max(chars - len(comp), 600)]
    return (comp + "\n\n" + rest).strip() if comp else rest


# ---------------------------------------------------------------- 2. local modpack files
_MOD_INDEX = None
_INDEX_FORMAT = 2        # bump when _read_mod_meta changes, so old cached indexes are rebuilt


def _mods_dir() -> str:
    return os.path.join(config.MODPACK_DIR, "mods")


def _read_mod_meta(z: zipfile.ZipFile, fname: str) -> list[tuple[str, str]]:
    """(kind, text) entries from a jar: mod id/version/dependencies and English lang strings."""
    entries = []
    names = z.namelist()
    # NeoForge 1.21 jars use neoforge.mods.toml (25 Nov 2026: without this, no RPG mod had a
    # version or dependency entry, and installed mods were reported "not installed")
    toml_name = next((n for n in ("META-INF/neoforge.mods.toml", "META-INF/mods.toml") if n in names), "")
    toml = z.read(toml_name).decode("utf-8", "ignore") if toml_name else ""
    manifest = z.read("META-INF/MANIFEST.MF").decode("utf-8", "ignore") if "META-INF/MANIFEST.MF" in names else ""
    impl = (re.search(r"Implementation-Version:\s*(\S+)", manifest) or [None, ""])[1]
    if toml:
        for block in re.split(r"\[\[mods\]\]", toml)[1:]:
            mid = (re.search(r'modId\s*=\s*"([^"]+)"', block) or [None, "?"])[1]
            ver = (re.search(r'version\s*=\s*"([^"]+)"', block) or [None, "?"])[1]
            if "${" in ver:
                ver = impl or ver
            disp = (re.search(r'displayName\s*=\s*"([^"]+)"', block) or [None, mid])[1]
            entries.append(("mod", f"{disp} (id {mid}) version {ver}, file {fname}"))
        for dep in re.finditer(r"\[\[dependencies\.([\w-]+)\]\](.*?)(?=\[\[|\Z)", toml, re.S):
            owner, body = dep.group(1), dep.group(2)
            did = (re.search(r'modId\s*=\s*"([^"]+)"', body) or [None, "?"])[1]
            rng = (re.search(r'versionRange\s*=\s*"([^"]*)"', body) or [None, ""])[1]
            mand = re.search(r"mandatory\s*=\s*true|type\s*=\s*\"required\"", body) is not None
            if did not in ("forge", "neoforge", "minecraft"):
                entries.append(("dependency", f"{owner} {'requires' if mand else 'optionally uses'} {did} {rng}"))
    elif "fabric.mod.json" in names:                 # Fabric mods running through Connector
        try:
            fj = json.loads(z.read("fabric.mod.json").decode("utf-8", "ignore"))
            entries.append(("mod", f"{fj.get('name', fj.get('id'))} (id {fj.get('id')}) version {fj.get('version')}, file {fname}"))
            for did, rng in (fj.get("depends") or {}).items():
                if did not in ("fabricloader", "minecraft", "java"):
                    entries.append(("dependency", f"{fj.get('id')} requires {did} {rng}"))
        except Exception:
            pass
    for n in names:
        if n.endswith("/lang/en_us.json") and n.startswith("assets/"):
            try:
                lang = json.loads(z.read(n).decode("utf-8", "ignore"))
            except Exception:
                continue
            for k, v in lang.items():
                if isinstance(v, str) and v.strip():
                    entries.append(("lang", f"{k} = {v[:300]}"))
    return entries


def _build_mod_index() -> list[dict]:
    mods_dir = _mods_dir()
    jars = sorted(f for f in os.listdir(mods_dir) if f.lower().endswith(".jar"))
    sig = [[f, os.path.getsize(os.path.join(mods_dir, f)), int(os.path.getmtime(os.path.join(mods_dir, f)))] for f in jars]
    os.makedirs(CACHE_DIR, exist_ok=True)
    # one index per project (25 Nov 2026), so switching projects doesn't rebuild or mix them
    name = "mod_index.json" if config.PROJECT == "fort_redux" else f"mod_index_{config.PROJECT}.json"
    cache = os.path.join(CACHE_DIR, name)
    if os.path.exists(cache):
        try:
            with open(cache, encoding="utf-8") as f:
                data = json.load(f)
            if data.get("sig") == sig and data.get("format") == _INDEX_FORMAT:
                return data["entries"]
        except Exception:
            pass
    entries = []
    for f, _, _ in sig:
        try:
            with zipfile.ZipFile(os.path.join(mods_dir, f)) as z:
                for kind, text in _read_mod_meta(z, f):
                    entries.append({"jar": f, "kind": kind, "text": text})
        except Exception:
            continue
    with open(cache, "w", encoding="utf-8") as fh:
        json.dump({"sig": sig, "format": _INDEX_FORMAT, "entries": entries}, fh)
    return entries


def _config_lines(kws: list[str], limit: int) -> list[str]:
    cfg = os.path.join(config.MODPACK_DIR, "config")
    hits = []
    if not os.path.isdir(cfg):
        return hits
    for root, _, files in os.walk(cfg):
        for fn in files:
            if not fn.endswith((".toml", ".json", ".json5", ".cfg", ".properties")):
                continue
            path = os.path.join(root, fn)
            try:
                if os.path.getsize(path) > 600_000:
                    continue
                with open(path, encoding="utf-8", errors="ignore") as f:
                    lines = f.read().splitlines()
            except OSError:
                continue
            rel = os.path.relpath(path, cfg)
            for i, line in enumerate(lines):
                s = _score(rel + " " + line, kws)
                if s and "=" in line or s and ":" in line:
                    ctx = " | ".join(l.strip() for l in lines[max(i - 1, 0):i + 1] if l.strip())
                    hits.append((s, f"[config/{rel}:{i + 1}] {ctx[:220]}"))
    hits.sort(key=lambda x: -x[0])
    return [h for _, h in hits[:limit]]


_BLOCK_NAMES = None


def known_block_names() -> set[str]:
    """English names of every vanilla and installed-mod block and item (lowercase), from the game jar's
    and the mods' en_us lang files. Empty set if the game jar can't be read (then nothing is flagged)."""
    global _BLOCK_NAMES, _MOD_INDEX
    if _BLOCK_NAMES is not None:
        return _BLOCK_NAMES
    names = set()
    try:
        with zipfile.ZipFile(config.MINECRAFT_JAR) as z:
            lang = json.loads(z.read("assets/minecraft/lang/en_us.json").decode("utf-8", "ignore"))
        names |= {v.lower() for k, v in lang.items() if k.startswith(("block.minecraft.", "item.minecraft."))
                  and k.count(".") == 2 and isinstance(v, str)}
    except Exception:
        _BLOCK_NAMES = set()
        return _BLOCK_NAMES                     # no vanilla list: don't guess from mods alone
    try:
        if _MOD_INDEX is None and os.path.isdir(_mods_dir()):
            _MOD_INDEX = _build_mod_index()
        for e in _MOD_INDEX or []:
            m = re.match(r"(block|item)\.[\w-]+\.[\w/-]+ = (.+)$", e["text"]) if e["kind"] == "lang" else None
            if m:
                names.add(m.group(2).strip().lower())
    except Exception:
        pass
    _BLOCK_NAMES = names
    return names


def search_blocks(query: str) -> str:
    """For checking the block list works: how many names are known, and which contain the query."""
    names = known_block_names()
    if not names:
        return f"[blocks] Could not read the game jar: {config.MINECRAFT_JAR}"
    hits = sorted(n for n in names if query.lower() in n)[:15]
    return f"{len(names)} known block/item names. Containing '{query}': " + (", ".join(hits) or "none")


def installed_mods() -> list[tuple[str, str, str]]:
    """(display name, version, jar file) for every installed mod; [] if the folder is missing."""
    global _MOD_INDEX
    try:
        if _MOD_INDEX is None:
            if not os.path.isdir(_mods_dir()):
                return []
            _MOD_INDEX = _build_mod_index()
    except Exception:
        return []
    out = []
    for e in _MOD_INDEX:
        m = re.match(r"(.+?) \(id [\w.-]+\) version (\S+), file (.+)$", e["text"]) if e["kind"] == "mod" else None
        if m:
            name = m.group(1).strip()
            if re.fullmatch(r"[a-z0-9_]+", name):    # no displayName in the jar: 'reliquified_artifacts'
                name = name.replace("_", " ").title()
            out.append((name, m.group(2), m.group(3).strip()))
    return out


def installed_mod_names() -> list[str]:
    """Display names of the installed mods."""
    return [name for name, _, _ in installed_mods()]


def _installed_versions() -> dict[str, str]:
    """mod id -> installed version, from the jars' own metadata."""
    out = {}
    for e in _MOD_INDEX or []:
        m = re.match(r".*\(id ([\w.-]+)\) version (\S+),", e["text"]) if e["kind"] == "mod" else None
        if m:
            out[m.group(1)] = m.group(2)
    return out


def _dependency_status(text: str, installed: dict[str, str]) -> str:
    """Say whether a required mod is installed, so the model never has to guess (25 Nov 2026: it called
    Curios and OctoLib 'not listed' because they weren't among the search hits)."""
    m = re.match(r"\S+ (?:requires|optionally uses) ([\w.-]+)", text)
    if not m or not installed:
        return ""
    ver = installed.get(m.group(1))
    return f" -> installed: {ver}" if ver else " -> NOT INSTALLED"


def search_local_mods(query: str, max_hits: int = 14, chars: int = 1600) -> str:
    """Search the installed jars (versions, dependencies, English names/descriptions) and config files.
    This is ground truth for exactly what is installed. Read-only."""
    global _MOD_INDEX
    try:
        if not os.path.isdir(_mods_dir()):
            return "[local mods] Mods folder not found: check MODPACK_DIR in config.py."
        if _MOD_INDEX is None:
            _MOD_INDEX = _build_mod_index()
        kws = _keywords(query)
        weight = {"mod": 4, "dependency": 3, "lang": 1}
        scored = []
        for e in _MOD_INDEX:
            s = _score(e["text"] + " " + e["jar"], kws) * weight[e["kind"]]
            if s:
                scored.append((s, e))
        # Mods first, then a few dependency lines, then item names (25 Nov 2026: item names from one
        # mod filled all the slots and the other mods asked about looked "not installed").
        order = {"mod": 0, "dependency": 1, "lang": 2}
        scored.sort(key=lambda x: (order[x[1]["kind"]], -x[0]))
        installed = _installed_versions()
        lines, seen, deps = [], set(), 0
        for _, e in scored:
            if e["kind"] == "dependency":
                if deps >= 6:
                    continue
                deps += 1
            line = f"- [{e['kind']}] {e['text']}" + ("" if e["kind"] == "mod" else f"  ({e['jar']})")
            if e["kind"] == "dependency":
                line += _dependency_status(e["text"], installed)
            if line not in seen:
                seen.add(line)
                lines.append(line)
            if len(lines) >= max_hits:
                break
        cfg = _config_lines(kws, 5)
        if not lines and not cfg:
            return f"[local mods] Nothing in the installed mods or configs matches '{query}'."
        cfg_text = ("\n## Config files (current settings)\n" + "\n".join(cfg))[:chars // 3] if cfg else ""
        text = ("## Installed modpack files (exact versions)\n" + "\n".join(lines))[:chars - len(cfg_text)]
        return text + cfg_text      # config values always get room: they are often the real answer
    except Exception as e:
        return f"[local mods] Lookup failed: {e}"


# ---------------------------------------------------------------- 3. GitHub
GITHUB_REPOS = [   # (words that point at the mod, repo)
    (("cc:tweaked", "computercraft", "cc tweaked", "rednet", "turtle", "lua", "computer"), "cc-tweaked/CC-Tweaked"),
    (("advanced peripherals", "advancedperipherals", "environment detector", "player detector", "chat box"),
     "IntelligenceModding/AdvancedPeripherals"),
    (("big cannons", "createbigcannons", "cbc", "cannon"), "Cannoneers-of-Create/CreateBigCannons"),
    (("create",), "Creators-of-Create/Create"),
]


def search_github(query: str, chars: int = 1300) -> str:
    """GitHub issues for the main mods (bugs, crashes, version notes); falls back to a general search."""
    gh = {"User-Agent": HEADERS["User-Agent"], "Accept": "application/vnd.github+json"}
    token = _secret("GITHUB_TOKEN")
    if token:                     # 25 Nov 2026: a read-only token lifts the 10-searches-a-minute limit
        gh["Authorization"] = f"Bearer {token}"
    ql = query.lower()
    match = next(((words, r) for words, r in GITHUB_REPOS if any(w in ql for w in words)), None)
    repo = match[1] if match else None
    try:
        kws = _keywords(query)
        if match:   # the repo filter already names the mod; searching its name too only hides issues
            name_words = {p for w in match[0] for p in w.split()}
            kws = [k for k in kws if k not in name_words] or kws
        terms = " ".join(kws[:6])
        q = f"{terms} repo:{repo}" if repo else f"{terms} minecraft in:title,body"
        data = _get_json("https://api.github.com/search/issues?" + urllib.parse.urlencode(
            {"q": q, "per_page": 5, "sort": "updated"}), gh)
        items = data.get("items", [])
        out = [f"## GitHub issues ({repo or 'all repos'}) for '{terms}'"]
        for i in items:
            body = re.sub(r"\s+", " ", i.get("body") or "")[:220]
            out.append(f"- [{i['state']}] {i['title']} ({i['updated_at'][:10]})\n  {i['html_url']}\n  {body}")
        if not items:
            out.append("(no matching issues)")
        if not repo:
            r = _get_json("https://api.github.com/search/repositories?" + urllib.parse.urlencode(
                {"q": f"{terms} minecraft", "per_page": 3}), gh)
            for it in r.get("items", []):
                out.append(f"- repo {it['full_name']} ({it.get('stargazers_count', 0)} stars): {it.get('description') or ''}")
        return "\n".join(out)[:chars]
    except urllib.error.HTTPError as e:
        if e.code in (403, 429):
            if _secret("GITHUB_TOKEN"):
                return "[GitHub] Rate limit reached or token refused (check GITHUB_TOKEN). Try again shortly."
            return "[GitHub] Rate limit reached (10 searches a minute without a token; set GITHUB_TOKEN). Try again shortly."
        return f"[GitHub] Lookup failed: HTTP {e.code}"
    except Exception as e:
        return f"[GitHub] Lookup failed: {e}"


# ---------------------------------------------------------------- 4. YouTube (Create tutorials)
_YT_ANDROID = {"clientName": "ANDROID", "clientVersion": "20.10.38", "androidSdkVersion": 34, "hl": "en", "gl": "US"}
_YT_IOS = {"clientName": "IOS", "clientVersion": "20.10.4", "hl": "en", "gl": "US"}


def _yt_search(query: str, limit: int = 8) -> list[dict]:
    page = _get("https://www.youtube.com/results?" + urllib.parse.urlencode({"search_query": query}),
                BROWSER_HEADERS).decode("utf-8", "ignore")
    m = re.search(r"var ytInitialData = (\{.*?\});</script>", page, re.S)
    if not m:
        return []
    found = []

    def walk(o):
        if isinstance(o, dict):
            v = o.get("videoRenderer")
            if isinstance(v, dict) and v.get("videoId"):
                found.append({
                    "id": v["videoId"],
                    "title": "".join(r.get("text", "") for r in v.get("title", {}).get("runs", [])),
                    "channel": "".join(r.get("text", "") for r in v.get("ownerText", {}).get("runs", [])),
                    "length": v.get("lengthText", {}).get("simpleText", ""),
                    "views": v.get("viewCountText", {}).get("simpleText", ""),
                    "age": v.get("publishedTimeText", {}).get("simpleText", ""),
                })
            for val in o.values():
                walk(val)
        elif isinstance(o, list):
            for val in o:
                walk(val)

    walk(json.loads(m.group(1)))
    return found[:limit]


def _seconds(length: str) -> int:
    total = 0
    for part in length.split(":"):
        total = total * 60 + int(part) if part.isdigit() else total
    return total


TRANSCRIPT_CACHE_HOURS = 24 * 14     # a video's captions don't change


def _yt_transcript(video_id: str) -> list[tuple[float, str]]:
    """[(start_seconds, text)] English captions, saved on disk for two weeks."""
    key = "yt-transcript:" + video_id
    hit = _cache_read(key, TRANSCRIPT_CACHE_HOURS)
    if hit is not None:
        try:
            return [(float(s), t) for s, t in json.loads(hit.decode("utf-8"))]
        except ValueError:
            pass
    lines = _yt_transcript_live(video_id)
    if lines:                                    # don't remember "no captions": it may be a hiccup
        _cache_write(key, json.dumps(lines).encode("utf-8"))
    return lines


def _yt_transcript_live(video_id: str) -> list[tuple[float, str]]:
    """[(start_seconds, text)] English captions (manual preferred, auto-generated otherwise)."""
    watch =_get(f"https://www.youtube.com/watch?v={video_id}", BROWSER_HEADERS).decode("utf-8", "ignore")
    key = (re.search(r'"INNERTUBE_API_KEY":"([^"]+)"', watch) or [None, None])[1]
    if not key:
        return []
    for client, ua in ((_YT_ANDROID, "com.google.android.youtube/20.10.38 (Linux; U; Android 14) gzip"),
                       (_YT_IOS, "com.google.ios.youtube/20.10.4 (iPhone16,2; U; CPU iOS 18_1 like Mac OS X)")):
        try:
            body = json.dumps({"context": {"client": client}, "videoId": video_id}).encode()
            player = _get_json(f"https://www.youtube.com/youtubei/v1/player?key={key}",
                               {"User-Agent": ua, "Content-Type": "application/json"}, body)
        except Exception:
            continue
        tracks = player.get("captions", {}).get("playerCaptionsTracklistRenderer", {}).get("captionTracks", [])
        english = [t for t in tracks if t.get("languageCode", "").startswith("en")]
        if not english:
            continue
        english.sort(key=lambda t: t.get("kind") == "asr")          # manual captions first
        xml = _get(english[0]["baseUrl"].replace("&fmt=srv3", ""), {"User-Agent": ua}).decode("utf-8", "ignore")
        lines = _parse_transcript_xml(xml)
        if lines:
            return lines
    return []


def _parse_transcript_xml(xml: str) -> list[tuple[float, str]]:
    out = []
    for start, text in re.findall(r'<text start="([\d.]+)"[^>]*>(.*?)</text>', xml, re.S):
        t = html.unescape(html.unescape(text)).replace("\n", " ").strip()   # captions are double-escaped
        if t:
            out.append((float(start), t))
    return out


def _best_parts(lines: list[tuple[float, str]], kws: list[str], chars: int, window: float = 40.0) -> str:
    """Split the transcript into ~40 s chunks, keep the chunks that mention the query most, in time order."""
    chunks, cur, cur_start = [], [], None
    for start, text in lines:
        if cur_start is None:
            cur_start = start
        if start - cur_start > window and cur:
            chunks.append((cur_start, " ".join(cur)))
            cur, cur_start = [], start
        cur.append(text)
    if cur:
        chunks.append((cur_start, " ".join(cur)))
    ranked = sorted(range(len(chunks)), key=lambda i: -_score(chunks[i][1], kws))
    keep, used = [], 0
    for i in ranked:
        if used + len(chunks[i][1]) > chars and keep:
            break
        keep.append(i)
        used += len(chunks[i][1])
    return "\n".join(f"[{int(chunks[i][0] // 60)}:{int(chunks[i][0] % 60):02d}] {chunks[i][1]}" for i in sorted(keep))


def search_youtube(query: str, max_videos: int = 2, chars: int = 2400) -> str:
    """Find Create mod tutorial videos and return the transcript parts that match the question.
    Videos can be old: Create changed a lot between 0.5 and 6.0, so treat them as practical tips."""
    try:
        q = query if "create" in query.lower() else f"create mod {query}"
        videos = [v for v in _yt_search(q)
                  if 120 <= _seconds(v["length"]) <= 3600                       # skip Shorts and streams
                  and re.search(r"create|minecraft", (v["title"] + " " + v["channel"]).lower())]  # not OpenTTD etc.
        if not videos:
            return f"[YouTube] No tutorial videos found for '{q}'."
        kws = _keywords(query)
        videos.sort(key=lambda v: -_score(v["title"], kws))
        out, per_video = [], chars // max_videos
        for v in videos[:max_videos + 2]:                        # a couple of spares if captions are missing
            if len(out) >= max_videos:
                break
            lines = _yt_transcript(v["id"])
            if not lines:
                continue
            out.append(f"## {v['title']} - {v['channel']} ({v['length']}, {v['views']}, {v['age']})\n"
                       f"https://www.youtube.com/watch?v={v['id']}\n" + _best_parts(lines, kws, per_video))
        if not out:
            titles = "; ".join(f"{v['title']} (https://youtu.be/{v['id']})" for v in videos[:3])
            return f"[YouTube] Found videos but none had English captions: {titles}"
        return ("Video transcripts (spoken, auto-captioned; check the upload date - older videos may show "
                "pre-6.0 Create behaviour):\n\n" + "\n\n".join(out))[:chars + 400]
    except Exception as e:
        return f"[YouTube] Lookup failed: {e}"
