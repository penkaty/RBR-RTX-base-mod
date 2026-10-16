r"""Assemble generate_pbr.py output into a Remix mod: normal + roughness overrides keyed by texture hash.

Usage (in this folder; see README.md):
  python make_mod.py                 # Warthog stages (Chirdonhead included)
  python make_mod.py --only all      # every generated texture
  Options: --out ..\rtx-remix\mods\z_rbrrtx_pbr_warthog  --raw-rough  --strength 1.5  --rough-contrast 1.5  --flip-g  --jobs 8
           (strength/contrast default to the stage group's PROFILES entry; a settings change reconverts everything)
           --glow  (test: overridden surfaces glow red; rerun without it to restore, only mod.usda is rewritten)
Remix loads every folder in <RBR>\rbr-rtx\rtx-remix\mods, so the next game start (or a mod.usda change while it runs)
picks the mod up. Delete the mod folder to compare with the original look. Resumable: finished textures are skipped.

Input: work\pbr\normal|rough\<HASH>.dds (BC5 x,y tangent space / BC4 roughness) and the catalog's classes.
Per texture:
  normal  DirectX green (measured 2026-10-16: PBRify n.y follows -dH/drow on 20 of 20 textures; Remix's MDL flips G
          only for OpenGL input) -> xy * class normal_strength * --strength, z rebuilt -> unsigned hemispherical
          octahedral RG, BC5 (the only normal format the runtime reads, make_test_mod.py)
  rough   PBRify roughness shifted so its mean is the class roughness (roughness_wet for wet-folder textures), detail
          kept; --raw-rough keeps the model's values. BC4.
  metallic  class constant when > 0 (metal class).
  height  (when work\pbr\height\<HASH>.dds exists: generate_pbr.py --maps height) PBRify height stretched to 0..1
          (1st-99th percentile), R8 with a MAX-filtered mip chain: Remix's default QuadtreePOM uses the mips as a
          quadtree of maximum heights (runtime option text); texconv's average mips would not do. Remix's POM
          then shows it; depth = class value (DISPLACE) split into displace_in/out around the texture's mean height,
          times Alt+X's global / in / out displacement factors.
The game's albedo and alpha state stay (an override merges with the game's material). Maps are derived from the
game's textures: keep this mod local (rights note, docs/research/asset-pipeline.md).
"""
import argparse
import json
import shutil
import sqlite3
import struct
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from generate_pbr import TEXCONV, work_list  # noqa: E402
from paths import CATALOG, MAPS, MOD_OUT  # noqa: E402
from make_test_mod import octa_encode  # noqa: E402

DXGI_R8_UNORM = 61
# POM depth per class (AperturePBR displace_in, default 0.05); classes not listed get no height map (flat in POM terms).
DISPLACE = {'road_gravel': 0.05, 'road_generic': 0.04, 'road_tarmac': 0.02, 'road_snow': 0.03, 'ground_dirt': 0.05,
            'ground_grass': 0.04, 'ground_generic': 0.04, 'rock': 0.08, 'building': 0.04, 'wood': 0.03}


def write_dds_r8_maxmips(path: Path, img: np.ndarray) -> None:
    """img: HxW uint8 -> R8_UNORM DDS (DX10 header) whose mips are 2x2 MAXIMA (QuadtreePOM)."""
    mips = [img]
    while mips[-1].shape[0] > 1 or mips[-1].shape[1] > 1:
        m = mips[-1]
        if m.shape[0] % 2 and m.shape[0] > 1:
            m = np.concatenate([m, m[-1:]], 0)
        if m.shape[1] % 2 and m.shape[1] > 1:
            m = np.concatenate([m, m[:, -1:]], 1)
        if m.shape[0] > 1:
            m = np.maximum(m[0::2], m[1::2])
        if m.shape[1] > 1:
            m = np.maximum(m[:, 0::2], m[:, 1::2])
        mips.append(m)
    h, w = img.shape
    flags = 0x1 | 0x2 | 0x4 | 0x1000 | 0x20000 | 0x8  # CAPS|HEIGHT|WIDTH|PIXELFORMAT|MIPMAPCOUNT|PITCH
    pf = struct.pack('<II4sIIIII', 32, 0x4, b'DX10', 0, 0, 0, 0, 0)
    hdr = struct.pack('<4sIIIIII', b'DDS ', 124, flags, h, w, w, 0) + struct.pack('<I', len(mips)) \
        + b'\0' * 44 + pf + struct.pack('<IIIII', 0x1000 | 0x400000 | 0x8, 0, 0, 0, 0)
    dx10 = struct.pack('<IIIII', DXGI_R8_UNORM, 3, 0, 1, 0)  # TEXTURE2D, arraySize 1
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'wb') as f:
        f.write(hdr + dx10)
        for m in mips:
            f.write(np.ascontiguousarray(m).tobytes())


def texconv(args: list) -> None:
    r = subprocess.run([str(TEXCONV), '-nologo', '-y', *map(str, args)], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f'texconv {args[:4]}: {r.stdout[-300:]} {r.stderr[-300:]}')


def read_dds(path: Path, tmp: Path) -> np.ndarray:
    """Mip 0 as float RGB in [0,1] (texconv decodes BC4/BC5 to PNG)."""
    tmp.mkdir(parents=True, exist_ok=True)
    texconv(['-ft', 'png', '-f', 'R8G8B8A8_UNORM', '-m', '1', '-o', tmp, path])
    png = tmp / (path.stem + '.png')
    a = np.asarray(Image.open(png).convert('RGB'), np.float32) / 255.0
    png.unlink()
    return a


def write_dds(px: np.ndarray, out: Path, fmt: str, tmp: Path) -> None:
    """px: HxWx3 uint8 -> <out> as fmt with a full mip chain."""
    tmp.mkdir(parents=True, exist_ok=True)
    png = tmp / (out.stem + '.png')
    Image.fromarray(px).save(png)
    texconv(['-f', fmt, '-m', '0', '-o', tmp, png])
    png.unlink()
    shutil.move(tmp / (out.stem + '.dds'), out)  # temp dir may be on another drive


# Per stage group, on top of the class values. The original Warthog stages are softer and more pastel than later
# RSF stages, so each group gets its own tuning instead of one game-wide setting (user, 2026-10-16).
# strength: normal xy multiplier; rough_contrast: roughness detail around the class mean (1 = model's own spread).
PROFILES = {
    'warthog': {'strength': 1.5, 'rough_contrast': 1.5},  # glow test 2026-10-16: applied but too subtle at 1.0/1.0
    'all': {'strength': 1.0, 'rough_contrast': 1.0},      # untuned
}


def convert(h: str, src: Path, tex: Path, tmp: Path, strength: float, rough_target, rough_contrast: float,
            flip_g: bool) -> None:
    t = tmp / h
    n_out, r_out = tex / f'{h}_n.dds', tex / f'{h}_r.dds'
    if not n_out.exists():
        xy = read_dds(src / 'normal' / f'{h}.dds', t)[..., :2] * 2.0 - 1.0
        z = np.sqrt(np.clip(1.0 - (xy ** 2).sum(-1), 0.0, 1.0))
        if flip_g:
            xy[..., 1] = -xy[..., 1]
        n = np.concatenate([xy * strength, z[..., None]], axis=-1)
        n /= np.maximum(np.linalg.norm(n, axis=-1, keepdims=True), 1e-6)
        px = np.zeros(n.shape[:2] + (3,), np.uint8)
        px[..., :2] = np.clip(octa_encode(n) * 255 + 0.5, 0, 255).astype(np.uint8)
        write_dds(px, n_out, 'BC5_UNORM', t)
    if not r_out.exists():
        r = read_dds(src / 'rough' / f'{h}.dds', t)[..., 0]
        if rough_target is not None:
            r = np.clip((r - r.mean()) * rough_contrast + rough_target, 0.02, 1.0)
        g = np.clip(r * 255 + 0.5, 0, 255).astype(np.uint8)
        write_dds(np.repeat(g[..., None], 3, axis=-1), r_out, 'BC4_UNORM', t)
    h_src, h_out = src / 'height' / f'{h}.dds', tex / f'{h}_h.dds'
    if h_src.exists() and not h_out.exists():
        z = read_dds(h_src, t)[..., 0]
        lo, hi = np.percentile(z, 1), np.percentile(z, 99)
        z = np.clip((z - lo) / max(hi - lo, 1e-3), 0.0, 1.0)
        write_dds_r8_maxmips(h_out, np.clip(z * 255 + 0.5, 0, 255).astype(np.uint8))


GLOW = '''                bool inputs:enable_emission = 1
                color3f inputs:emissive_color_constant = (1, 0, 0)
                float inputs:emissive_intensity = 2
'''


def height_mean(path: Path) -> float:
    """Mean of mip 0 of a write_dds_r8_maxmips file (148-byte DX10 header, R8)."""
    raw = path.read_bytes()
    h, w = int.from_bytes(raw[12:16], 'little'), int.from_bytes(raw[16:20], 'little')
    return float(np.frombuffer(raw, np.uint8, h * w, 148).mean()) / 255.0


def material(h: str, metallic: float, note: str, glow: bool = False, displace: float = 0.0, mean: float = 1.0) -> str:
    met = f'                float inputs:metallic_constant = {metallic:g}\n' if metallic else ''
    if displace:
        # Depth split so the texture's mean height is the original surface (Remix: a pixel of displace_in /
        # (displace_in + displace_out) keeps the triangle's height): half the step at mesh edges compared with
        # inwards-only, and Alt+X's Displacement Out Factor gets something to scale (user, 2026-10-16 evening).
        met += f'''                asset inputs:height_texture = @./textures/{h}_h.dds@ (
                    colorSpace = "raw"
                )
                float inputs:displace_in = {displace * mean:.4f}
                float inputs:displace_out = {displace * (1.0 - mean):.4f}
'''
    met += GLOW if glow else ''
    return f'''        def Material "mat_{h}"  # {note}
        {{
            def Shader "Shader"
            {{
                uniform token info:implementationSource = "sourceAsset"
                uniform asset info:mdl:sourceAsset = @AperturePBR_Opacity.mdl@
                uniform token info:mdl:sourceAsset:subIdentifier = "AperturePBR_Opacity"
                asset inputs:normalmap_texture = @./textures/{h}_n.dds@ (
                    colorSpace = "raw"
                )
                asset inputs:reflectionroughness_texture = @./textures/{h}_r.dds@ (
                    colorSpace = "raw"
                )
{met}                token outputs:out
            }}
        }}
'''


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--catalog', default=str(CATALOG))
    ap.add_argument('--src', default=str(MAPS))
    ap.add_argument('--out', default=str(MOD_OUT))
    ap.add_argument('--only', choices=['warthog', 'all'], default='warthog')
    ap.add_argument('--strength', type=float, help='normal strength on top of the class value (default: PROFILES)')
    ap.add_argument('--rough-contrast', type=float, help='roughness detail multiplier (default: PROFILES)')
    ap.add_argument('--raw-rough', action='store_true')
    ap.add_argument('--flip-g', action='store_true', help='treat the maps as OpenGL green')
    ap.add_argument('--jobs', type=int, default=8)
    ap.add_argument('--glow', action='store_true', help='test: every overridden surface glows red (does the mod apply?)')
    a = ap.parse_args()

    prof = PROFILES[a.only]
    strength = prof['strength'] if a.strength is None else a.strength
    contrast = prof['rough_contrast'] if a.rough_contrast is None else a.rough_contrast

    src, out = Path(a.src), Path(a.out)
    tex = out / 'textures'
    tex.mkdir(parents=True, exist_ok=True)
    # Converted textures are reused only if they were made with the same settings.
    stamp = out / 'settings.json'
    settings = {'strength': strength, 'rough_contrast': contrast, 'raw_rough': a.raw_rough, 'flip_g': a.flip_g,
                'maps': 'wrap-padded'}  # bump when generate_pbr.py's output changes, so everything is reconverted
    if not stamp.exists() or json.loads(stamp.read_text()) != settings:
        for p in tex.glob('*.dds'):
            p.unlink()
    stamp.write_text(json.dumps(settings))
    print(f'settings {settings}', flush=True)
    db = sqlite3.connect(a.catalog)
    params = {c: (r, rw, m, ns) for c, r, rw, m, ns in
              db.execute('SELECT class, roughness, roughness_wet, metallic, normal_strength FROM class_params')}
    info = {h: (wet, name) for h, wet, name in db.execute('SELECT hash, wet, name FROM classes')}
    items = []
    for h, cls, _, member in work_list(db, a.only):
        if not ((src / 'normal' / f'{h}.dds').exists() and (src / 'rough' / f'{h}.dds').exists()):
            continue
        r, rw, m, ns = params[cls]
        wet, name = info.get(h, (0, ''))
        target = None if a.raw_rough else (rw if wet else r)
        items.append((h, cls, ns * strength, target, m or 0.0, f'{cls} {name or member}'.replace('\n', ' ')))
    todo = [i for i in items if not ((tex / f'{i[0]}_n.dds').exists() and (tex / f'{i[0]}_r.dds').exists()
                                     and ((tex / f'{i[0]}_h.dds').exists() or not (src / 'height' / f'{i[0]}.dds').exists()))]
    print(f'{len(items)} materials ({len(todo)} to convert) -> {out}', flush=True)

    tmp = Path(tempfile.mkdtemp(prefix='rbr_mod_'))
    errors = []

    def job(i):
        h, cls, strength, target, _, _ = i
        try:
            convert(h, src, tex, tmp, strength, target, contrast, a.flip_g)
        except Exception as e:
            errors.append(f'{h}\t{type(e).__name__}: {e}')

    with ThreadPoolExecutor(a.jobs) as pool:
        for k, _ in enumerate(pool.map(job, todo), 1):
            if k % 100 == 0 or k == len(todo):
                print(f'{k}/{len(todo)}', flush=True)
    bad = {e.split('\t')[0] for e in errors}
    def mat(h, cls, m, note):
        hp = tex / f'{h}_h.dds'
        d = DISPLACE.get(cls, 0.0) if hp.exists() else 0.0
        return material(h, m, note, a.glow, d, height_mean(hp) if d else 1.0)
    body = ''.join(mat(h, cls, m, note) for h, cls, _, _, m, note in items if h not in bad)
    print(f'height maps (POM) on {body.count("height_texture")} materials', flush=True)
    usda = f'#usda 1.0\n(\n    upAxis = "Y"\n)\n\ndef Xform "RootNode"\n{{\n    def Scope "Looks"\n    {{\n{body}    }}\n}}\n'
    (out / 'mod.usda').write_text(usda, encoding='utf-8')  # last: Remix reloads when mod.usda changes
    print(f'{out / "mod.usda"}: {len(items) - len(bad)} materials, {len(errors)} errors')
    for e in errors[:10]:
        print(e)


if __name__ == '__main__':
    main()
