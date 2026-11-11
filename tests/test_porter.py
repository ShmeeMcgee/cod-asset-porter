"""End-to-end tests on synthetic assets (no game files needed).

Run:  python -m unittest discover -s tests
"""
import json
import math
import os
import shutil
import struct
import sys
import tempfile
import unittest
import wave

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from porter.engine import convert_batch  # noqa: E402
from porter.gdt import read_gdt  # noqa: E402
from porter.xanim import read_xanim  # noqa: E402
from porter.xbin import read_xanim_bin, read_xmodel_bin  # noqa: E402
from porter.xmodel import read_xmodel  # noqa: E402

S45 = math.sqrt(0.5)
ROT90Z = (0.0, 0.0, S45, S45)
IDQ = (0.0, 0.0, 0.0, 1.0)

BONES = [
    dict(name="tag_origin", parent=-1, wpos=(0, 0, 0), wrot=IDQ, lpos=(0, 0, 0), lrot=IDQ),
    dict(name="j_arm", parent=0, wpos=(10, 0, 0), wrot=ROT90Z, lpos=(10, 0, 0), lrot=ROT90Z),
    dict(name="j_hand", parent=1, wpos=(10, 5, 0), wrot=ROT90Z, lpos=(5, 0, 0), lrot=IDQ),
]
CUBE = [(x, y, z) for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)]
CUBE_FACES = [(0, 1, 3), (0, 3, 2), (4, 6, 7), (4, 7, 5), (0, 4, 5), (0, 5, 1),
              (2, 3, 7), (2, 7, 6), (0, 2, 6), (0, 6, 4), (1, 5, 7), (1, 7, 3)]


def _weights():
    out = []
    for i, p in enumerate(CUBE):
        if i == 7:   # 10 influences: tests trimming
            out.append([(0, .1), (1, .1), (2, .1), (1, .1), (0, .1), (2, .1), (1, .1), (0, .1), (2, .1), (1, .1)])
        elif p[0] < 0:
            out.append([(1, 1.0)])
        else:
            out.append([(1, .5), (2, .5)])
    return out


def _fmt(n):
    return "B" if n <= 0xFF else ("H" if n <= 0xFFFF else "I")


def write_semodel(path):
    meshes = [
        dict(pos=CUBE, uv=[((p[0] + 1) / 2, (p[1] + 1) / 2) for p in CUBE],
             n=[tuple(c / math.sqrt(3) for c in p) for p in CUBE],
             c=[(255, 255, 255, 255)] * 8, w=_weights(), faces=CUBE_FACES, mat=0),
        dict(pos=[(0, 0, 2), (1, 0, 2), (0, 1, 2)], uv=[(0, 0), (1, 0), (0, 1)],
             n=[(0, 0, 1)] * 3, c=[(255, 0, 0, 255)] * 3, w=[[(2, 1.0)]] * 3,
             faces=[(0, 1, 2), (0, 0, 1)], mat=1),
    ]
    mats = [("Mtl Arm Skin", "_images/Arm Color.png"), ("mtl_glow", "glow_c.png")]
    buf = bytearray(b"SEModel") + struct.pack("<H", 1)
    buf += struct.pack("<H3BIII3B", 0x14, 7, 3, 15, len(BONES), len(meshes), len(mats), 0, 0, 0)
    for b in BONES:
        buf += b["name"].encode() + b"\0"
    for b in BONES:
        buf += struct.pack("<Bi", 0, b["parent"])
        buf += struct.pack("<7f", *b["wpos"], *b["wrot"])
        buf += struct.pack("<7f", *b["lpos"], *b["lrot"])
    bt = _fmt(len(BONES))
    for m in meshes:
        v = len(m["pos"])
        mi = max(len(w) for w in m["w"])
        buf += struct.pack("<3BII", 0, 1, mi, v, len(m["faces"]))
        for p in m["pos"]:
            buf += struct.pack("<3f", *p)
        for uv in m["uv"]:
            buf += struct.pack("<2f", *uv)
        for n in m["n"]:
            buf += struct.pack("<3f", *n)
        for c in m["c"]:
            buf += struct.pack("<4B", *c)
        for w in m["w"]:
            w = list(w) + [(0, 0.0)] * (mi - len(w))
            for bi, wt in w:
                buf += struct.pack("<" + bt + "f", bi, wt)
        for f in m["faces"]:
            buf += struct.pack("<3" + _fmt(v), *f)
        buf += struct.pack("<i", m["mat"])
    for name, diff in mats:
        buf += name.encode() + b"\0\x01" + diff.encode() + b"\0n.png\0s.png\0"
    with open(path, "wb") as f:
        f.write(buf)


def write_seanim(path, atype, fps, frames, names, tracks, notes):
    buf = bytearray(b"SEAnim") + struct.pack("<H", 1)
    buf += struct.pack("<HBBBBHfIIB3sI", 0x1C, atype, 0, 1 | 2 | (64 if notes else 0), 0, 0,
                       fps, frames, len(names), 0, b"\0\0\0", len(notes))
    ff = _fmt(frames)
    for n in names:
        buf += n.encode() + b"\0"
    for t in tracks:
        buf += b"\0"
        for key, n in (("loc", 3), ("rot", 4)):
            keys = t.get(key, [])
            buf += struct.pack("<" + ff, len(keys))
            for fr, val in keys:
                buf += struct.pack("<" + ff + "%df" % n, fr, *val)
    for fr, name in notes:
        buf += struct.pack("<" + ff, fr) + name.encode() + b"\0"
    with open(path, "wb") as f:
        f.write(buf)


# ---- minimal Cast writer (spec: github.com/dtzxporter/cast) ----
def _cprop(kind, name, values):
    codes = {"b": "B", "h": "H", "i": "I", "l": "Q", "f": "f", "s": None, "2v": "2f", "3v": "3f", "4v": "4f"}
    raw = kind.encode().ljust(2, b"\0")
    if kind == "s":
        body = values.encode() + b"\0"
        count = 1
    else:
        flat = [c for v in values for c in (v if isinstance(v, tuple) else (v,))]
        body = struct.pack("<" + codes[kind] * len(values), *flat)
        count = len(values)
    return struct.pack("<2sHI", raw, len(name), count) + name.encode() + body


def _cnode(nid, nhash, props, children):
    body = b"".join(props) + b"".join(children)
    return struct.pack("<IIQII", nid, 24 + len(body), nhash, len(props), len(children)) + body


def write_cast_model(path):
    bones = [_cnode(0x656E6F62, 100 + i, [
        _cprop("s", "n", b["name"]), _cprop("i", "p", [b["parent"] & 0xFFFFFFFF]),
        _cprop("3v", "lp", [b["lpos"]]), _cprop("4v", "lr", [b["lrot"]]),
        _cprop("3v", "wp", [b["wpos"]]), _cprop("4v", "wr", [b["wrot"]])], []) for i, b in enumerate(BONES)]
    skel = _cnode(0x6C656B73, 1, [], bones)
    ccw = [(a, c, b) for a, b, c in CUBE_FACES]          # Cast stores CCW triangles
    w = [ws + [(0, 0.0)] * (3 - len(ws)) for ws in ([(1, 1.0)] if p[0] < 0 else [(1, .5), (2, .5)] for p in CUBE)]
    mesh = _cnode(0x6873656D, 2, [
        _cprop("3v", "vp", CUBE), _cprop("3v", "vn", [tuple(c / math.sqrt(3) for c in p) for p in CUBE]),
        _cprop("2v", "u0", [((p[0] + 1) / 2, (p[1] + 1) / 2) for p in CUBE]), _cprop("b", "ul", [1]),
        _cprop("b", "mi", [3]), _cprop("b", "wb", [b for ws in w for b, _ in ws]),
        _cprop("f", "wv", [x for ws in w for _, x in ws]),
        _cprop("b", "f", [i for tri in ccw for i in tri]), _cprop("l", "m", [77])], [])
    filen = _cnode(0x656C6966, 88, [_cprop("s", "p", "Arm Color.png")], [])
    mat = _cnode(0x6C74616D, 77, [_cprop("s", "n", "cast_mat"), _cprop("s", "t", "pbr"),
                                   _cprop("l", "albedo", [88])], [filen])
    model = _cnode(0x6C646F6D, 3, [], [skel, mesh, mat])
    root = _cnode(0x746F6F72, 4, [], [model])
    with open(path, "wb") as f:
        f.write(struct.pack("<IIII", 0x74736163, 1, 1, 0) + root)


def write_cast_anim(path):
    curves = [_cnode(0x76727563, 10, [
        _cprop("s", "nn", "j_arm"), _cprop("s", "kp", "rq"), _cprop("b", "kb", [0, 10]),
        _cprop("4v", "kv", [IDQ, ROT90Z]), _cprop("s", "m", "absolute")], [])]
    note = _cnode(0x6669746E, 11, [_cprop("s", "n", "fire"), _cprop("b", "kb", [5])], [])
    anim = _cnode(0x6D696E61, 12, [_cprop("f", "fr", [30.0]), _cprop("b", "lo", [1])], curves + [note])
    root = _cnode(0x746F6F72, 13, [], [anim])
    with open(path, "wb") as f:
        f.write(struct.pack("<IIII", 0x74736163, 1, 1, 0) + root)


def write_wav(path, rate, channels, bits, fmt_tag, seconds):
    import numpy as np
    t = np.arange(int(rate * seconds)) / rate
    data = np.stack([0.4 * np.sin(2 * np.pi * (220 + 110 * c) * t) for c in range(channels)], 1)
    if fmt_tag == 3:
        raw = data.astype("<f4").tobytes()
    else:
        v = np.round(data * (2 ** (bits - 1) - 1)).astype(np.int32)
        raw = b"".join(int(s).to_bytes(bits // 8, "little", signed=True) for s in v.ravel())
    blk = channels * bits // 8
    hdr = struct.pack("<4sI4s4sIHHIIHH4sI", b"RIFF", 36 + len(raw), b"WAVE", b"fmt ", 16, fmt_tag,
                      channels, rate, rate * blk, blk, bits, b"data", len(raw))
    with open(path, "wb") as f:
        f.write(hdr + raw)


def make_samples(folder):
    import numpy as np
    from PIL import Image
    write_semodel(os.path.join(folder, "Test Arm.semodel"))
    write_seanim(os.path.join(folder, "arm_swing_abs.seanim"), 0, 30.0, 11, ["j_arm", "j_not_in_rig"],
                 [{"rot": [(0, IDQ), (10, ROT90Z)]}, {"rot": [(0, IDQ)]}], [(0, "start"), (10, "end")])
    write_seanim(os.path.join(folder, "arm_lift_rel.seanim"), 2, 24.0, 3, ["j_arm"],
                 [{"loc": [(0, (0, 0, 2))], "rot": [(0, IDQ)]}], [])
    yy, xx = np.mgrid[0:200, 0:300]
    rgba = np.dstack([xx % 256, yy % 256, (xx + yy) % 256, (xx % 2) * 255]).astype(np.uint8)
    Image.fromarray(rgba, "RGBA").save(os.path.join(folder, "Arm Color.png"))
    # two-channel normal map (BC5 style: blue empty)
    nrm = np.dstack([128 + 60 * np.sin(xx / 9), 128 + 60 * np.cos(yy / 7), np.zeros_like(xx)]).astype(np.uint8)
    Image.fromarray(nrm, "RGB").save(os.path.join(folder, "arm_n.png"))
    write_wav(os.path.join(folder, "shot_24bit.wav"), 22050, 2, 24, 1, 1.0)
    write_wav(os.path.join(folder, "reload_float.wav"), 48000, 1, 32, 3, 0.5)
    os.makedirs(os.path.join(folder, "cast"))
    write_cast_model(os.path.join(folder, "cast", "cast_arm.cast"))
    write_cast_anim(os.path.join(folder, "cast", "cast_swing.cast"))


def close(a, b, tol=1e-3):
    return all(abs(x - y) < tol for x, y in zip(a, b))


class PorterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="porter_test_")
        cls.src = os.path.join(cls.tmp, "src")
        os.makedirs(cls.src)
        make_samples(cls.src)
        cls.out = {}
        cls.summary = {}
        for game in ("cod4", "waw", "bo1", "bo2", "bo3"):
            out = os.path.join(cls.tmp, game)
            cls.summary[game] = convert_batch([cls.src], game, out, pack="test_pack")
            cls.out[game] = out

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def p(self, game, *parts):
        return os.path.join(self.out[game], *parts)

    def load_model(self, game, name):
        if game == "bo3":
            return read_xmodel_bin(self.p(game, "model_export", "test_pack", name + ".xmodel_bin"))
        return read_xmodel(self.p(game, "model_export", "test_pack", name + ".xmodel_export"))

    def load_anim(self, game, name):
        if game == "bo3":
            return read_xanim_bin(self.p(game, "xanim_export", "test_pack", name + ".xanim_bin"))
        return read_xanim(self.p(game, "xanim_export", "test_pack", name + ".xanim_export"))

    def test_nothing_failed(self):
        for game, s in self.summary.items():
            self.assertEqual(s["failed"], 0, game)

    def test_models(self):
        for game in ("cod4", "waw", "bo1", "bo3"):
            m = self.load_model(game, "test_arm")
            self.assertEqual([b.name for b in m.bones], ["tag_origin", "j_arm", "j_hand"])
            self.assertTrue(close(m.bones[2].world_pos, (10, 5, 0)), game)
            self.assertEqual((len(m.verts), len(m.faces)), (11, 13), game)   # degenerate tri dropped
            limit = 8 if game == "bo3" else 9
            self.assertLessEqual(max(len(w) for _, w in m.verts), limit)
            for _, ws in m.verts:
                self.assertAlmostEqual(sum(w for _, w in ws), 1.0, places=4)
            self.assertEqual([x.name for x in m.materials], ["mtl_arm_skin", "mtl_glow"])

    def test_cast_model_matches_semodel(self):
        for game in ("waw", "bo3"):
            c = self.load_model(game, "cast_arm")
            s = self.load_model(game, "test_arm")
            self.assertTrue(close(c.bones[2].world_pos, s.bones[2].world_pos))
            # same winding as the SEModel cube once Cast's CCW order is flipped
            self.assertEqual([tuple(x[0] for x in f.corners) for f in c.faces[:12]],
                             [tuple(x[0] for x in f.corners) for f in s.faces[:12]])

    def test_anims(self):
        for game in ("cod4", "waw", "bo1", "bo3"):
            a = self.load_anim(game, "arm_swing_abs")
            self.assertEqual([p for p in a.parts], ["tag_origin", "j_arm", "j_hand"])
            self.assertEqual(len(a.frames), 11)
            self.assertTrue(close(a.frames[0][2][0], (15, 0, 0)), game)
            self.assertTrue(close(a.frames[5][2][0], (10 + 5 * S45, 5 * S45, 0)), game)
            self.assertTrue(close(a.frames[10][2][0], (10, 5, 0)), game)
            self.assertEqual(sorted(a.notes), [(0, "start"), (10, "end")])
            r = self.load_anim(game, "arm_lift_rel")
            self.assertTrue(close(r.frames[0][2][0], (10, 5, 2)), game)
            c = self.load_anim(game, "cast_swing")
            self.assertTrue(close(c.frames[10][2][0], (10, 5, 0)), game)
            self.assertEqual(c.notes, [(5, "fire")])

    def test_gdt(self):
        for game in ("waw", "bo3"):
            ents = {(n, g): kv for n, g, kv in read_gdt(self.p(game, "source_data", "test_pack.gdt"))}
            self.assertIn(("test_arm", "xmodel.gdf"), ents)
            anim = ents[("arm_swing_abs", "xanim.gdf")]
            self.assertIn("test_arm", anim["model"])
            mat = ents[("mtl_arm_skin", "material.gdf")]
            if game == "bo3":
                self.assertEqual(mat["colorMap"], "i_arm_color")
                self.assertEqual(ents[("i_arm_color", "image.gdf")]["semantic"], "diffuseMap")
                self.assertEqual(ents[("i_arm_n", "image.gdf")]["semantic"], "normalMap")
            else:
                self.assertTrue(mat["colorMap"].endswith("arm_color.tga"))

    def test_textures(self):
        from PIL import Image
        with Image.open(self.p("waw", "texture_assets", "test_pack", "arm_color.tga")) as t:
            self.assertEqual((t.size, t.mode), ((256, 256), "RGBA"))
        with Image.open(self.p("bo3", "texture_assets", "test_pack", "arm_color.tif")) as t:
            self.assertEqual(t.size, (300, 200))
        with Image.open(self.p("bo3", "texture_assets", "test_pack", "arm_n.tif")) as n:
            self.assertGreater(n.convert("RGB").getextrema()[2][0], 100)   # blue rebuilt from X/Y
        with open(self.p("bo2", "images", "arm_color.dds"), "rb") as f:
            head = f.read(128)
        self.assertEqual(head[:4], b"DDS ")
        self.assertEqual(struct.unpack_from("<II", head, 12), (256, 256))

    def test_sounds(self):
        for game, folder, rate in (("waw", ("raw", "sound"), 44100), ("bo3", ("sound_assets",), 48000)):
            with wave.open(self.p(game, *folder, "test_pack", "shot_24bit.wav")) as w:
                self.assertEqual((w.getframerate(), w.getsampwidth(), w.getnchannels()), (rate, 2, 2))
                self.assertAlmostEqual(w.getnframes() / rate, 1.0, places=2)

    def test_bo2_glb(self):
        path = self.p("bo2", "model_export", "test_pack", "test_arm.glb")
        with open(path, "rb") as f:
            data = f.read()
        magic, version, length = struct.unpack_from("<4sII", data, 0)
        self.assertEqual((magic, version, length), (b"glTF", 2, len(data)))
        jlen, = struct.unpack_from("<I", data, 12)
        gltf = json.loads(data[20:20 + jlen])
        self.assertEqual(len(gltf["skins"][0]["joints"]), 3)
        prim = gltf["meshes"][0]["primitives"][0]
        self.assertEqual(gltf["accessors"][prim["attributes"]["POSITION"]]["type"], "VEC3")
        hand = next(n for n in gltf["nodes"] if n["name"] == "j_hand")
        self.assertTrue(close(hand["translation"], (5, 0, 0)))   # local to j_arm, unaffected by axis turn
        with open(self.p("bo2", "xmodel", "test_arm.json"), encoding="utf-8") as f:
            meta = json.load(f)
        self.assertEqual((meta["_type"], meta["_version"]), ("xmodel", 2))
        self.assertEqual(self.summary["bo2"].get("skipped"), 3)   # anims skipped for BO2


class RealToolFixes(unittest.TestCase):
    """Regressions found by running output through the real WaW converter."""

    def test_missing_rig_bones_are_filled(self):
        from porter.anims import complete_with_rig
        from porter.data import Anim, Bone, Model
        rig = Model("rig")
        for name, parent, pos in (("tag_view", -1, (0, 0, 0)), ("tag_ads", 0, (1, 0, 0)), ("j_arm", 1, (2, 0, 0))):
            b = Bone(name, parent)
            b.world_pos, b.world_rot = pos, IDQ
            rig.bones.append(b)
        rig.fill_transforms()
        anim = Anim("a")
        anim.parts = ["j_arm"]
        ident = ((1.0, 0, 0), (0, 1.0, 0), (0, 0, 1.0))
        anim.frames = [[((5.0, 0, 0), *ident)], [((6.0, 0, 0), *ident)]]
        added = complete_with_rig(anim, rig)
        self.assertEqual(sorted(added), ["tag_ads", "tag_view"])
        self.assertEqual(len(anim.frames[1]), 3)
        self.assertTrue(close(anim.frames[1][anim.parts.index("tag_ads")][0], (1, 0, 0)))

    def test_viewmodel_recentred_for_old_games(self):
        from porter.data import Bone, Model
        from porter.models import recenter_viewmodel
        m = Model("vm")
        b = Bone("tag_view", -1)
        b.world_pos, b.world_rot = (0.0, 0.0, 60.0), IDQ
        m.bones.append(b)
        m.verts = [((1.0, 0.0, 61.0), [(0, 1.0)])]
        m.fill_transforms()
        self.assertEqual(recenter_viewmodel(m), (-0.0, -0.0, -60.0))
        self.assertTrue(close(m.verts[0][0], (1, 0, 1)))

    def test_taken_names_and_placeholder(self):
        tmp = tempfile.mkdtemp(prefix="porter_taken_")
        try:
            src = os.path.join(tmp, "src")
            os.makedirs(src)
            write_semodel(os.path.join(src, "Test Arm.semodel"))       # no textures supplied
            game_root = os.path.join(tmp, "game")
            os.makedirs(os.path.join(game_root, "model_export"))
            with open(os.path.join(game_root, "model_export", "stock.gdt"), "w") as f:
                f.write('{\n\t"mtl_arm_skin" ( "material.gdf" )\n\t{\n\t}\n\t"test_arm" ( "xmodel.gdf" )\n\t{\n\t}\n}\n')
            s = convert_batch([src], "waw", game_root, pack="pk")
            self.assertEqual(s["failed"], 0)
            ents = {(n, g): kv for n, g, kv in read_gdt(os.path.join(game_root, "source_data", "pk.gdt"))}
            self.assertIn(("test_arm_pk", "xmodel.gdf"), ents)
            self.assertIn(("mtl_arm_skin_pk", "material.gdf"), ents)
            self.assertTrue(ents[("mtl_arm_skin_pk", "material.gdf")]["colorMap"].endswith("pk_missing_c.tga"))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_bo1_far_from_root_warning(self):
        """BO1's converter prompts for small meshes far from the origin (found in the real BO1 tools)."""
        from porter.data import Model
        from porter.models import far_from_root
        box = [(x, y, z) for x in (0.0, 3.5) for y in (0.0, 4.7) for z in (0.0, 4.6)]
        far = Model("far")
        far.verts = [((x + 9.7, y + 6.4, z - 16.0), [(0, 1.0)]) for x, y, z in box]
        near = Model("near")
        near.verts = [((x - 1.7, y - 2.3, z - 2.3), [(0, 1.0)]) for x, y, z in box]
        radius, dist = far_from_root(far)
        self.assertAlmostEqual(radius, 3.73, delta=0.02)  # same measure BO1's converter prints
        self.assertGreater(dist, 20)
        self.assertIsNone(far_from_root(near))


if __name__ == "__main__":
    unittest.main()
