# PBR generator

Tools that make **PBR materials for Richard Burns Rally stages**: normal, roughness and height (POM) maps generated
from the game's own textures, packed as an RTX Remix mod for [RBR-RTX](https://github.com/penkaty/RBR-RTX).
The Warthog materials in this repo were made with them. Stage creators can use them to make materials for their own
stages.

**Players don't need this folder.** It is only for making materials.

## What it does

| Step | Script | Result |
|---|---|---|
| 1 | `harvest.py` | Lists every texture in your RBR folder (stage `.rbz` archives, track `.ini` flags) with its Remix texture hash, in `work\catalog\catalog.sqlite`. Reads the game, never writes to it. |
| 2 | `classify.py` | Sorts the textures into material classes (gravel road, tarmac, rock, grass, wood, ...) by name and flags. Each class has its own roughness and depth settings. |
| 3 | `generate_pbr.py` | Runs the PBRify_Remix AI models on the GPU and writes the maps to `work\pbr\`. Can run overnight; stop with Ctrl+C, a rerun continues where it stopped. |
| 4 | `make_mod.py` | Converts the maps into Remix's formats and writes the mod (`mod.usda` + `textures\`). |

Helpers: `paths.py` (all folders in one place), `make_test_mod.py` (DDS writing, normal encoding),
`catalog_report.py` (what the catalog covers).

## What you need
- Windows, an NVIDIA RTX GPU, Python 3.12 (python.org).
- Python packages: `pip install -r requirements.txt` (PyTorch with CUDA, see the comment in that file).
- The **PBRify_Remix** models (CC0) from https://github.com/Kim2091/PBRify_Remix/releases, copied into `models\`:
  `1x-PBRify_NormalV3.pth`, `1x-PBRify_RoughnessV2.pth`, `1x-PBRify_Height.pth`.
- **texconv.exe** (Microsoft DirectXTex, MIT) from https://github.com/microsoft/DirectXTex/releases, copied into `bin\`.

## Run it
In this folder (PowerShell):

```
$env:RBR_GAME = "C:\Games\Richard Burns Rally"   # your RBR folder
python harvest.py
python classify.py
python generate_pbr.py --limit 5                  # quick test first
python generate_pbr.py --only all --maps normal,rough,height
python make_mod.py --only all --out "$env:RBR_GAME\rbr-rtx\rtx-remix\mods\z_my_stage_pbr"
```

Start the game with RBR-RTX: Remix loads every folder in `rbr-rtx\rtx-remix\mods`. Delete the mod folder to go back
to the original look.

Folders: `work\` next to these scripts holds the catalog and the generated maps (tens of GB for all stages). Set
`RBRRTX_PBR_WORK` to put it on another drive. All settings are in `paths.py`.

## Limits (proof of concept)
- `--only` knows `warthog` (the original stages) and `all`. A filter for a single stage is planned.
- Encrypted stages: their textures can't be read from the files; RBR-RTX's in-game harvest mode
  (`harvest.py --runtime`) covers them, but it is not part of the player release yet.
- The maps are derived from the game's textures: share them only where you have the rights to.
