"""In-memory model / animation shared by all readers and writers.

The model layout mirrors xmodel_export: one global vertex list (position +
bone weights) and faces whose three corners each carry a normal, colour and UV.
"""
from __future__ import annotations

from .mathutil import (IDENTITY_Q, q_conj, q_mul, q_normalize, q_rotate,
                       v_add, v_sub)


class ConversionError(Exception):
    """A problem the user can act on (shown in the log as-is)."""


class Bone:
    __slots__ = ("name", "parent", "world_pos", "world_rot",
                 "local_pos", "local_rot")

    def __init__(self, name, parent=-1):
        self.name = name
        self.parent = parent
        self.world_pos = None
        self.world_rot = None
        self.local_pos = None
        self.local_rot = None


#: texture slots a material can carry, in the order GDTs list them
MAP_SLOTS = ("color", "normal", "spec", "gloss", "ao", "mask")


class Material:
    """`maps` holds slot -> image reference (file path or bare image name).

    After the engine resolves images, `files` holds slot -> source file on
    disk and `outputs` slot -> converted file name (no folder).
    """
    __slots__ = ("name", "maps", "files", "outputs")

    def __init__(self, name, color_map="", normal_map="", spec_map=""):
        self.name = name
        self.maps = {}
        self.files = {}
        self.outputs = {}
        for slot, ref in (("color", color_map), ("normal", normal_map), ("spec", spec_map)):
            if ref:
                self.maps[slot] = ref

    @property
    def color_map(self):
        return self.outputs.get("color") or self.maps.get("color", "")


class Face:
    # corners: three (vertex_index, normal, rgba, uv) tuples
    __slots__ = ("obj", "mat", "corners")

    def __init__(self, obj, mat, corners):
        self.obj = obj
        self.mat = mat
        self.corners = corners


class Model:
    def __init__(self, name):
        self.name = name
        self.bones: list[Bone] = []
        self.verts: list = []        # (position, [(bone, weight), ...])
        self.faces: list[Face] = []
        self.objects: list[str] = []
        self.materials: list[Material] = []

    # -- skeleton helpers ---------------------------------------------------
    def parents_first_order(self):
        """Bone indices ordered so every parent comes before its children."""
        n = len(self.bones)
        children = [[] for _ in range(n)]
        roots = []
        for i, b in enumerate(self.bones):
            if 0 <= b.parent < n and b.parent != i:
                children[b.parent].append(i)
            else:
                roots.append(i)
        order, seen = [], set()
        stack = list(reversed(roots))
        while stack:
            i = stack.pop()
            if i in seen:
                continue
            seen.add(i)
            order.append(i)
            stack.extend(reversed(children[i]))
        # Bones caught in a parent loop: treat as roots.
        for i in range(n):
            if i not in seen:
                self.bones[i].parent = -1
                order.append(i)
        return order

    def fill_transforms(self):
        """Compute whichever of world/local transforms is missing."""
        for i in self.parents_first_order():
            b = self.bones[i]
            p = self.bones[b.parent] if b.parent >= 0 else None
            if b.world_pos is None and b.local_pos is not None:
                if p is None:
                    b.world_pos, b.world_rot = b.local_pos, b.local_rot
                else:
                    b.world_rot = q_normalize(q_mul(p.world_rot, b.local_rot))
                    b.world_pos = v_add(p.world_pos,
                                        q_rotate(p.world_rot, b.local_pos))
            if b.world_pos is None:
                b.world_pos, b.world_rot = (0.0, 0.0, 0.0), IDENTITY_Q
            if b.local_pos is None:
                if p is None:
                    b.local_pos, b.local_rot = b.world_pos, b.world_rot
                else:
                    inv = q_conj(p.world_rot)
                    b.local_rot = q_normalize(q_mul(inv, b.world_rot))
                    b.local_pos = q_rotate(inv, v_sub(b.world_pos, p.world_pos))

    def reorder_bones_parents_first(self):
        order = self.parents_first_order()
        if order == list(range(len(self.bones))):
            return
        remap = {old: new for new, old in enumerate(order)}
        new_bones = []
        for old in order:
            b = self.bones[old]
            b.parent = remap.get(b.parent, -1) if b.parent >= 0 else -1
            new_bones.append(b)
        self.bones = new_bones
        self.verts = [(pos, [(remap[bi], w) for bi, w in weights if bi in remap])
                      for pos, weights in self.verts]


class Anim:
    """Fully baked animation: every part has a model-space transform per frame."""

    def __init__(self, name):
        self.name = name
        self.framerate = 30.0
        self.parts: list[str] = []
        # frames[f][p] = (offset, X, Y, Z)
        self.frames: list[list] = []
        self.notes: list = []          # (frame, name)
        self.kind = None               # relative / absolute / additive / delta
        self.looping = False
