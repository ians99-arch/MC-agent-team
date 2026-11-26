"""Loads the project notes and the modpack's installed mod list (read-only)."""
import os
import re

import config


def load_project_notes() -> str:
    try:
        with open(config.PROJECT_FILE, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def _pretty_mod_name(filename: str) -> str:
    name = filename[:-4]  # drop .jar
    # Strip loader tags and version numbers: "create-1.20.1-0.5.1.f" -> "create"
    name = re.sub(r"[-_+ ](mc)?1\.\d+(\.\d+)?.*$", "", name, flags=re.I)
    name = re.sub(r"[-_ ]v?\d+(\.\d+)+.*$", "", name)
    name = re.sub(r"[-_ ](forge|neoforge|fabric)$", "", name, flags=re.I)
    return name.strip("-_ ") or filename


def load_mod_list() -> list[str]:
    mods_dir = os.path.join(config.MODPACK_DIR, "mods") if config.MODPACK_DIR else ""
    if not mods_dir or not os.path.isdir(mods_dir):
        return []
    names = {
        _pretty_mod_name(f)
        for f in os.listdir(mods_dir)
        if f.lower().endswith(".jar")  # skips .jar.disabled
    }
    return sorted(names, key=str.lower)


def mod_list_text(max_chars: int = 4000) -> str:
    mods = load_mod_list()
    if not mods:
        return "(mod list unavailable: check MODPACK_DIR in config.py)"
    text = f"{len(mods)} mods installed: " + ", ".join(mods)
    return text if len(text) <= max_chars else text[:max_chars] + " ...[more]"
