"""Per-game settings for the mod tools each target game ships with.

If a game's tools reject something, this is the first place to look:
every limit, folder and output format the converters use comes from here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Game:
    key: str
    name: str
    short: str
    model_format: str            # "xmodel6" | "xmodel_bin" | "glb"
    anim_format: Optional[str]   # "xanim_export" | "xanim_bin" | None (not supported)
    max_bones: Optional[int]     # warn above this
    max_influences: int          # bone weights kept per vertex
    max_verts: Optional[int]     # hard limit of the model format
    texture_ext: str             # image format the asset tools read
    power_of_two: bool           # resize textures to power-of-two sizes
    max_texture: Optional[int]   # largest texture side
    sound_rate: int              # output sample rate (16-bit PCM WAV)
    sound_dir: str               # where sounds go
    gdt: Optional[str]           # "legacy" (Asset Manager) | "bo3" (APE) | None
    model_dir: str = "model_export"
    anim_dir: str = "xanim_export"
    texture_dir: str = "texture_assets"
    steam_folder: str = ""       # folder name under steamapps/common
    notes: str = ""

    @property
    def model_ext(self):
        return {"xmodel6": ".xmodel_export", "xmodel_bin": ".xmodel_bin", "glb": ".glb"}[self.model_format]

    @property
    def anim_ext(self):
        return {"xanim_export": ".xanim_export", "xanim_bin": ".xanim_bin"}.get(self.anim_format or "", "")


GAMES = {
    "cod4": Game("cod4", "Call of Duty 4: Modern Warfare", "CoD4",
                 "xmodel6", "xanim_export", 128, 9, 0xFFFF, ".tga", True, 2048, 44100,
                 "raw/sound", "legacy", steam_folder="Call of Duty 4"),
    "waw": Game("waw", "Call of Duty: World at War", "World at War",
                "xmodel6", "xanim_export", 128, 9, 0xFFFF, ".tga", True, 2048, 44100,
                "raw/sound", "legacy", steam_folder="Call of Duty World at War"),
    "bo1": Game("bo1", "Call of Duty: Black Ops", "Black Ops 1",
                "xmodel6", "xanim_export", 128, 9, 0xFFFF, ".tga", True, 2048, 44100,
                "raw/sound", "legacy", steam_folder="Call of Duty Black Ops"),
    "bo2": Game("bo2", "Call of Duty: Black Ops II (Plutonium + OpenAssetTools)", "Black Ops 2",
                "glb", None, 128, 4, None, ".dds", True, 2048, 48000,
                "sound", None, model_dir="model_export", texture_dir="images",
                steam_folder="Call of Duty Black Ops II",
                notes="BO2 models are built with OpenAssetTools. Their materials must already "
                      "exist in BO2 (or be made with OAT), and OAT can't load source animations yet."),
    "bo3": Game("bo3", "Call of Duty: Black Ops III", "Black Ops 3",
                "xmodel_bin", "xanim_bin", None, 8, None, ".tif", False, None, 48000,
                "sound_assets", "bo3", steam_folder="Call of Duty Black Ops III"),
}

ORDER = ("cod4", "waw", "bo1", "bo2", "bo3")
