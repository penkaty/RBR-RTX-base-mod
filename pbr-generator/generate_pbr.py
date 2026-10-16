r"""Overnight batch: normal + roughness maps for stage textures with the PBRify_Remix models (CC0), headless.

Usage (in this folder; see README.md):
  python generate_pbr.py            # Chirdonhead, Warthog stages, then the rest
  python generate_pbr.py --limit 5  # quick test
  Options: --only warthog|all  --max 2048  --maps normal,rough  --game <RBR folder>
Stop any time with Ctrl+C (or close the window); a rerun skips finished textures. Progress: work\pbr\progress.tsv,
errors: work\pbr\errors.tsv. Runs at below-normal CPU priority; cap GPU power in MSI Afterburner if it is loud.

Input: work\catalog\catalog.sqlite (harvest.py + classify.py): every hash whose class has generate=1, read from the
first ZIP .rbz that holds it. Output per texture hash (Remix hash = XXH3 of the game's mip 0):
  work\pbr\normal\<HASH>.dds  BC5: R,G = tangent-space normal x,y in 0..1 (z rebuilt); convention (OpenGL or DirectX
                               green) and Remix's octahedral packing are applied when the mod is assembled
  work\pbr\rough\<HASH>.dds   BC4: perceptual roughness 0..1
Maps are generated at the texture's size, at most --max px per side (bigger ones are downscaled first), full mip chain.
Maps are derived from RBR/RSF textures: keep them local (rights note, docs/research/asset-pipeline.md).
"""
import argparse
import ctypes
import io
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import CATALOG, GAME, MAPS, MODELS, TEXCONV  # noqa: E402,F401
WARTHOG = set(range(10, 16)) | set(range(20, 26)) | set(range(31, 37)) | set(range(41, 47)) | set(range(51, 57)) | set(range(61, 67))
FIRST = {22}                                    # Chirdonhead: the user's first stage
CLASS_ORDER = ['road_gravel', 'road_tarmac', 'road_generic', 'road_snow', 'ground_dirt', 'ground_grass', 'ground_generic',
               'ground_far', 'rock', 'road_line', 'wood', 'building', 'metal', 'fence_cutout', 'foliage_solid',
               'foliage_card', 'cutout_generic', 'prop', 'vehicle_prop', 'sign_banner']


def track_id(path: str):
    m = re.search(r'track-(\d+)_\w_textures\.rbz$', path.replace(chr(92), '/'))
    return int(m.group(1)) if m else None


def work_list(db, only: str):
    """[(hash, class, archive_path, member)] in processing order."""
    gen = [c for (c,) in db.execute('SELECT class FROM class_params WHERE generate = 1')]
    rank = {c: i for i, c in enumerate(CLASS_ORDER)}
    rows = db.execute(f'SELECT c.hash, c.class, c.archives FROM classes c WHERE c.class IN ({",".join("?" * len(gen))})', gen).fetchall()
    where = {}
    for h, aid, member, path in db.execute('SELECT o.hash, o.archive_id, o.member, a.path FROM occurrences o '
                                           'JOIN archives a ON a.id = o.archive_id WHERE a.kind = "zip" ORDER BY o.archive_id'):
        t = track_id(path)
        best = where.get(h)
        tier = 0 if t in FIRST else 1 if t in WARTHOG else 2
        if best is None or tier < best[0]:
            where[h] = (tier, path, member)
    out = []
    for h, cls, n_arch in rows:
        if h not in where:
            continue
        tier, path, member = where[h]
        if only == 'warthog' and tier > 1:
            continue
        out.append((tier, rank.get(cls, 99), -(n_arch or 0), h, cls, path, member))
    out.sort()
    return [(h, cls, path, member) for _, _, _, h, cls, path, member in out]


def load_model(name, device):
    import torch
    from spandrel import ModelLoader
    m = ModelLoader().load_from_file(str(MODELS / name)).eval().to(device)
    half = device.type == 'cuda' and m.supports_half
    if half:
        m.model.half()
    return m, half


def run(model, half, rgb: np.ndarray, device, pad: int = 32) -> np.ndarray:
    """Stage textures tile: the input is wrap-padded so the model sees across the tile border (without it, maps
    break at every tile edge = visible lines across roads, playtest 2026-10-16 evening); the pad is cropped off."""
    import torch
    h, w = rgb.shape[:2]
    py, px = min(pad, h // 2), min(pad, w // 2)
    rgb = np.pad(rgb, ((py, py), (px, px), (0, 0)), mode='wrap')
    out = _run(model, half, rgb, device)
    return out[py:py + h, px:px + w]


def _run(model, half, rgb: np.ndarray, device) -> np.ndarray:
    import torch
    t = torch.from_numpy(rgb).permute(2, 0, 1).unsqueeze(0).to(device)
    t = t.half() if half else t.float()
    with torch.inference_mode():
        o = model(t)
    return o.squeeze(0).permute(1, 2, 0).float().clamp(0, 1).cpu().numpy()


def texconv(png: Path, out_dir: Path, fmt: str, name: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    r = subprocess.run([str(TEXCONV), '-nologo', '-y', '-f', fmt, '-m', '0', '-o', str(out_dir), str(png)],
                       capture_output=True, text=True)
    produced = out_dir / (png.stem + '.dds')
    if r.returncode != 0 or not produced.exists():
        raise RuntimeError(f'texconv {fmt} failed: {r.stdout[-300:]} {r.stderr[-300:]}')
    produced.replace(out_dir / f'{name}.dds')


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--game', default=GAME, required=GAME is None, help='RBR folder (or set RBR_GAME)')
    ap.add_argument('--catalog', default=str(CATALOG))
    ap.add_argument('--out', default=str(MAPS))
    ap.add_argument('--only', choices=['warthog', 'all'], default='all')
    ap.add_argument('--max', type=int, default=2048)
    ap.add_argument('--maps', default='normal,rough')
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--redo', action='store_true', help='regenerate maps that already exist (after a method change)')
    a = ap.parse_args()
    try:
        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)  # BELOW_NORMAL
    except Exception:
        pass

    import torch
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    maps = [m for m in a.maps.split(',') if m]
    files = {'normal': '1x-PBRify_NormalV3.pth', 'rough': '1x-PBRify_RoughnessV2.pth', 'height': '1x-PBRify_Height.pth'}
    fmt = {'normal': 'BC5_UNORM', 'rough': 'BC4_UNORM', 'height': 'BC4_UNORM'}
    models = {m: load_model(files[m], device) for m in maps}
    out = Path(a.out)
    for m in maps:
        (out / m).mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(a.catalog)
    todo = [w for w in work_list(db, a.only) if a.redo or not all((out / m / f'{w[0]}.dds').exists() for m in maps)]
    if a.limit:
        todo = todo[:a.limit]
    print(f'{len(todo)} textures to do on {device} ({torch.cuda.get_device_name(0) if device.type == "cuda" else "CPU"}), '
          f'maps {maps}, max {a.max}px -> {out}', flush=True)
    prog = open(out / 'progress.tsv', 'a', encoding='utf-8')
    errs = open(out / 'errors.tsv', 'a', encoding='utf-8')
    maps_dir = Path(a.game) / 'Maps'
    zips = {}
    t0 = time.time()
    px_done = 0
    tmp = Path(tempfile.mkdtemp(prefix='rbr_pbr_'))
    for i, (h, cls, apath, member) in enumerate(todo, 1):
        ts = time.time()
        try:
            z = zips.get(apath)
            if z is None:
                if len(zips) > 8:
                    for old in zips.values():
                        old.close()
                    zips.clear()
                z = zips[apath] = zipfile.ZipFile(maps_dir / apath)
            img = Image.open(io.BytesIO(z.read(member))).convert('RGB')
            if max(img.size) > a.max:
                s = a.max / max(img.size)
                img = img.resize((max(4, int(img.width * s)), max(4, int(img.height * s))), Image.LANCZOS)
            rgb = np.asarray(img, dtype=np.float32) / 255.0
            for m in maps:
                if (out / m / f'{h}.dds').exists() and not a.redo:
                    continue
                res = run(*models[m], rgb, device)
                if m == 'normal':
                    n = res * 2.0 - 1.0
                    n /= np.maximum(np.linalg.norm(n, axis=-1, keepdims=True), 1e-6)
                    px = np.zeros(n.shape[:2] + (3,), np.uint8)
                    px[..., 0:2] = np.clip((n[..., 0:2] * 0.5 + 0.5) * 255 + 0.5, 0, 255).astype(np.uint8)
                else:
                    g = np.clip(res.mean(axis=-1) * 255 + 0.5, 0, 255).astype(np.uint8)
                    px = np.repeat(g[..., None], 3, axis=-1)
                png = tmp / f'{h}_{m}.png'
                Image.fromarray(px).save(png)
                texconv(png, out / m, fmt[m], h)
                png.unlink(missing_ok=True)
            px_done += rgb.shape[0] * rgb.shape[1]
            prog.write(f'{h}\t{cls}\t{rgb.shape[1]}x{rgb.shape[0]}\t{time.time() - ts:.2f}\t{apath}\t{member}\n')
        except KeyboardInterrupt:
            print('stopped; rerun to continue', flush=True)
            break
        except Exception as e:  # keep going overnight; the error list is reviewed later
            errs.write(f'{h}\t{apath}\t{member}\t{type(e).__name__}: {e}\n')
            errs.flush()
        if i % 25 == 0 or i == len(todo):
            prog.flush()
            el = time.time() - t0
            eta = el / i * (len(todo) - i)
            print(f'{i}/{len(todo)}  {px_done / 1e6:.0f} Mpx  {el / 60:.1f} min  ETA {eta / 3600:.1f} h', flush=True)
    prog.close()
    errs.close()


if __name__ == '__main__':
    main()
