"""Find where each game's mod tools are installed (Steam libraries on Windows)."""
from __future__ import annotations

import os
import re
import sys

from .games import GAMES

# folder names Steam uses, most specific first; the folder must look like a mod tools root
CANDIDATES = {
    "cod4": ["Call of Duty 4"],
    "waw": ["Call of Duty World at War"],
    "bo1": ["Call of Duty Black Ops 42740", "Call of Duty Black Ops Mod Tools", "Call of Duty Black Ops"],
    "bo2": ["Call of Duty Black Ops II"],
    "bo3": ["Call of Duty Black Ops III", "Call of Duty Black Ops III 455130"],
}


def _steam_roots():
    roots = []
    if sys.platform == "win32":
        try:
            import winreg
            for hive, key in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
                              (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam")):
                try:
                    with winreg.OpenKey(hive, key) as k:
                        for name in ("SteamPath", "InstallPath"):
                            try:
                                roots.append(winreg.QueryValueEx(k, name)[0])
                            except OSError:
                                pass
                except OSError:
                    pass
        except ImportError:
            pass
        roots += [r"C:\Program Files (x86)\Steam", r"C:\Program Files\Steam"]
    else:
        roots += [os.path.expanduser("~/.steam/steam"), os.path.expanduser("~/.local/share/Steam")]
    libraries = []
    for r in roots:
        r = os.path.normpath(r)
        if r not in libraries and os.path.isdir(r):
            libraries.append(r)
        vdf = os.path.join(r, "steamapps", "libraryfolders.vdf")
        try:
            with open(vdf, encoding="utf-8", errors="replace") as f:
                for path in re.findall(r'"path"\s+"([^"]+)"', f.read()):
                    path = os.path.normpath(path.replace("\\\\", "\\"))
                    if path not in libraries and os.path.isdir(path):
                        libraries.append(path)
        except OSError:
            pass
    return libraries


def _is_tools_root(path, key):
    if key == "bo2":
        return os.path.isdir(path)
    return os.path.isdir(os.path.join(path, "model_export")) or \
        os.path.isdir(os.path.join(path, "source_data")) or \
        os.path.isfile(os.path.join(path, "bin", "linker_modtools.exe"))


def find_game_folders():
    """-> {game key: mod tools folder} for every game that's installed."""
    found = {}
    for lib in _steam_roots():
        common = os.path.join(lib, "steamapps", "common")
        for key, names in CANDIDATES.items():
            if key in found:
                continue
            for name in names:
                path = os.path.join(common, name)
                if _is_tools_root(path, key):
                    found[key] = path
                    break
    return {k: v for k, v in found.items() if k in GAMES}


NEXT_STEPS = {
    "legacy": "Next: open Asset Manager, File > Open > source_data\\{pack}.gdt, select everything "
              "and press Convert. Then add the assets to your mod's mod.csv.",
    "bo3": "Next: open APE (the {pack}.gdt shows up under source_data), check the "
           "entries, then add them to your zone file and link with the Launcher.",
    "bo2": "Next: point OpenAssetTools' Linker at this folder (--search-path) and list the "
           "xmodels and images in your zone file.",
}


def next_steps(game, pack):
    style = game.gdt or ("bo2" if game.key == "bo2" else "legacy")
    return NEXT_STEPS.get(style, "").format(pack=pack)
