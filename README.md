# RBR-RTX base mod

The RTX Remix content mod for [RBR-RTX](https://github.com/penkaty/RBR-RTX): materials (and later more content) for
Richard Burns Rally. RBR-RTX is the engine part that makes the game run with Remix; this mod is the content on top,
installed separately (the same split as xoxor4d's GTA IV Compatibility Mod + base mod).

For now it holds generated normal, roughness and height (POM) maps for the original Warthog stages: a demo of what
RBR-RTX's PBR tools make, and a starting point for stage creators who want to make materials for their own stages.

It only adds a Remix mod layer. The game's own textures and stages are not changed, and the textures are matched by
the Remix texture hash, so RBR-RTX works the same with or without this mod.

## Install
1. Install RBR-RTX first.
2. Download the release zip and unpack it into the RBR folder, so the mod lands in
   `rbr-rtx\rtx-remix\mods\z_rbrrtx_pbr_warthog\`.
3. Remove that folder to uninstall.

## Build it yourself
The textures are not in git: they are built from the game's own textures by the RBR-RTX PBR tools
(PBRify_Remix models for the maps, `make_mod.py` to assemble the mod). Settings used for the release:
`rtx-remix/mods/z_rbrrtx_pbr_warthog/settings.json`.

## Status
Proof of concept, original Warthog stages only. Rights for the derived maps: to be settled before the first release.
