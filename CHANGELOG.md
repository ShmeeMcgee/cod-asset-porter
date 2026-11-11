# Changelog

## 1.0.0

First public release.

* Targets: CoD4, World at War, Black Ops 1, Black Ops 2 (via OpenAssetTools) and Black Ops 3.
* Reads `.xmodel_export`, `.xanim_export` (+ `.NT_EXPORT`), BO3 `.xmodel_bin`/`.xanim_bin`,
  `.semodel`, `.seanim`, `.cast`, textures and sounds.
* Writes a GDT per batch with models, materials (textures filled in), BO3 images and animations.
* Uses GDTs and `*_images.txt` that come with port packs to wire up textures and rigs.
* Finds your Steam mod tools folders and can write straight into them.
