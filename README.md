<h1 align="center">Richard Burns Rally - RTX Remix Base Mod</h1>

<div align="center" markdown="1">

The content part of [RBR-RTX](https://github.com/penkaty/RBR-RTX), made for NVIDIA's
[RTX Remix](https://github.com/NVIDIAGameWorks/rtx-remix).
Runs on **Richard Burns Rally + RallySimFans (RSF)**; online functionality has not been tested yet.

### This is not a remaster.
RBR-RTX makes the game run with RTX Remix. This mod adds materials on top: the stages keep their original
textures, which now get surface detail, roughness and depth under path tracing.

**Proof of concept:** original Warthog stages only, more to come.

</div>

## Base mod features

- PBR materials for the original Warthog stages: normal, roughness and height (POM) maps for every stage texture
  (about 2,100 textures).
- Every map is generated from the game's own texture, so the look stays RBR's. Gravel, tarmac, rock, grass, wood
  and other material classes each get their own roughness and depth.
- A plain Remix mod layer. Textures are matched by their Remix hash, and no game files are touched. RBR-RTX works
  the same with or without it.
- The tools that made it are included in [`pbr-generator/`](pbr-generator/README.md). Stage creators can make
  materials for their own stages with them.

## Installation / Usage

1. Install [RBR-RTX](https://github.com/penkaty/RBR-RTX) first.
2. Download the latest [release](https://github.com/penkaty/RBR-RTX-base-mod/releases) and unpack it **into your
   RBR folder** (next to `RichardBurnsRally_SSE.exe`). The mod lands in
   `rbr-rtx\rtx-remix\mods\z_rbrrtx_pbr_warthog\`. `.7z` opens with [7-Zip](https://www.7-zip.org/).
3. Start the game as usual and drive a Warthog stage.
4. Uninstall: delete `rbr-rtx\rtx-remix\mods\z_rbrrtx_pbr_warthog\`. Uninstalling RBR-RTX removes it too.

## Troubleshooting

- Surfaces look wrong (too shiny, wrong bumps, shimmering)? Run the **Alt+K sweep** from RBR-RTX on that spot and
  send me `rbr-rtx\logs` through [GitHub issues](https://github.com/penkaty/RBR-RTX-base-mod/issues) or the
  Discord (link coming soon). Say whether it also happens without this mod (move the mod folder out and back).
- No change on a stage: only the original Warthog stages are covered so far.

## Make materials yourself

The textures aren't in git (about 1.5 GB). They're built from the game's textures by the tools in
[`pbr-generator/`](pbr-generator/README.md): `harvest.py` lists the textures with their Remix hashes, `classify.py`
sorts them into material classes, `generate_pbr.py` runs the PBRify_Remix AI models, and `make_mod.py` writes the
Remix mod. Settings used for this release: `rtx-remix/mods/z_rbrrtx_pbr_warthog/settings.json`.

## Credits

- [NVIDIA RTX Remix](https://github.com/NVIDIAGameWorks/rtx-remix) for the runtime and mod format.
- [Kim2091 / PBRify_Remix](https://github.com/Kim2091/PBRify_Remix) (CC0): the AI models that generate the maps.
- [spandrel](https://github.com/chaiNNer-org/spandrel) (chaiNNer-org) and [PyTorch](https://pytorch.org/): running the models.
- [Microsoft DirectXTex](https://github.com/microsoft/DirectXTex) (texconv): texture conversion.
- [xxHash](https://github.com/Cyan4973/xxHash): Remix texture hashes.
- [xoxor4d](https://github.com/xoxor4d): the compatibility-mod + base-mod split, and the AutoPBR idea
  ([gta4-rtx](https://github.com/xoxor4d/gta4-rtx), [gta4-rtx-autopbr-mod](https://github.com/xoxor4d/gta4-rtx-autopbr-mod)).
- [RallySimFans](https://www.rallysimfans.hu/) for keeping RBR alive. Richard Burns Rally and its textures belong to
  their owners; the maps here are derived from them and are shared for use with the game only.
