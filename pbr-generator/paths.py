r"""Where the PBR generator reads and writes. The defaults suit a fresh download; environment variables override them.

  RBR_GAME           your RBR folder: textures are READ from its .rbz archives and track .ini files, never written
                     (or pass --game to harvest.py / generate_pbr.py)
  RBRRTX_PBR_WORK    working folder for catalog\, pbr\ (generated maps) and harvest\ (default: work\ next to these
                     scripts; the generated maps for all stages can take tens of GB)
  RBRRTX_CAPTURES    Remix captures for catalog_report.py (default: <RBR_GAME>\rtx-remix\captures)
"""
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
GAME = os.environ.get('RBR_GAME') or None
WORK = Path(os.environ.get('RBRRTX_PBR_WORK') or HERE / 'work')
CATALOG = WORK / 'catalog' / 'catalog.sqlite'
MAPS = WORK / 'pbr'
HARVEST = WORK / 'harvest'
CAPTURES = Path(os.environ.get('RBRRTX_CAPTURES') or
                (Path(GAME) / 'rtx-remix' / 'captures' if GAME else WORK / 'captures'))
MOD_OUT = HERE.parent / 'rtx-remix' / 'mods' / 'z_rbrrtx_pbr_warthog'
MODELS = HERE / 'models'
TEXCONV = HERE / 'bin' / 'texconv.exe'
