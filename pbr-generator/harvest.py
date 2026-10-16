r"""Offline texture catalog: every texture in the game's .rbz archives with its RTX Remix hash.

Remix keys materials (and texture tags) by XXH3_64 of a texture's raw mip-0 bytes (dxvk-remix
d3d9_common_texture.cpp SetupForRtxFrom), so a material can be made for a texture without the game ever drawing it.
This reads the ZIP-format .rbz archives under <game>\Maps (encrypted RSF archives are listed, not read; the proxy's
harvest mode covers those), the per-texture flags in the track .ini files (IsGroundTexture, OpacityMap, ...) and RSF's
TextureFilenameMap remaps (a stage reusing another stage's identical texture), and writes them to SQLite.

Usage: python harvest.py [--game <RBR folder>]
                                                                  [--out work\catalog\catalog.sqlite] [--stats]
  --stats  also decode each unique texture once (Pillow) for mean colour and alpha coverage (slower).
  --runtime [folder]  also import the proxy's in-game harvest (RBR-RTX harvest mode; default work\harvest):
           harvest.tsv + <HASH>.dds, one archive row per stage id (kind 'runtime'); no names (encrypted stages).
Tables: archives, textures (one row per hash), occurrences (archive member -> hash), flags (ini sections), remaps.
Needs xxhash (and Pillow for --stats) in the tools venv.
"""
import argparse
import re
import sqlite3
import struct
import sys
import zipfile
from concurrent.futures import ProcessPoolExecutor
from io import BytesIO
from pathlib import Path

import xxhash

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import CATALOG, GAME, HARVEST  # noqa: E402
FOURCC_BLOCK = {b'DXT1': 8, b'DXT2': 16, b'DXT3': 16, b'DXT4': 16, b'DXT5': 16, b'ATI1': 8, b'ATI2': 16,
                b'BC4U': 8, b'BC5U': 16}
DXGI_BLOCK = {70: 8, 71: 8, 72: 8, 73: 16, 74: 16, 75: 16, 76: 16, 77: 16, 78: 16, 79: 8, 80: 8, 81: 8, 82: 16,
              83: 16, 84: 16, 94: 16, 95: 16, 96: 16, 97: 16, 98: 16, 99: 16}
DXGI_BPP = {2: 128, 10: 64, 24: 32, 28: 32, 29: 32, 61: 8, 85: 16, 86: 16, 87: 32, 88: 32, 91: 32}
FOLDERS = {'dry', 'damp', 'wet'}


def dds_info(b):
    """(format, width, height, mips, mip0 bytes) of a DDS file, or None if unsupported."""
    if len(b) < 128 or b[:4] != b'DDS ':
        return None
    h, w = struct.unpack_from('<II', b, 12)
    mips = max(1, struct.unpack_from('<I', b, 28)[0])
    pf_flags, fourcc, bits = struct.unpack_from('<I4sI', b, 80)
    off, bw, bh = 128, max(1, (w + 3) // 4), max(1, (h + 3) // 4)
    if fourcc == b'DX10':
        dxgi = struct.unpack_from('<I', b, 128)[0]
        off += 20
        if dxgi in DXGI_BLOCK:
            size, fmt = bw * bh * DXGI_BLOCK[dxgi], f'DXGI{dxgi}'
        elif dxgi in DXGI_BPP:
            size, fmt = w * h * DXGI_BPP[dxgi] // 8, f'DXGI{dxgi}'
        else:
            return None
    elif pf_flags & 0x4 and fourcc in FOURCC_BLOCK:
        size, fmt = bw * bh * FOURCC_BLOCK[fourcc], fourcc.decode()
    elif pf_flags & 0x4:
        return None
    elif bits:
        size, fmt = w * h * bits // 8, f'RGB{bits}' + ('A' if pf_flags & 0x1 else '')
    else:
        return None
    data = b[off:off + size]
    return (fmt, w, h, mips, data) if len(data) == size else None


def tex_key(name):
    """Name used to join archive members with ini sections: lower-case base name without extensions."""
    n = name.replace('\\', '/').rsplit('/', 1)[-1].lower()
    return re.sub(r'(\.tga_\d+)?(\.dds|\.tga|\.bmp)+$', '', n)


def archive_meta(path, maps):
    """stage id, stage folder name, time-of-day letter from an archive path."""
    rel = path.relative_to(maps)
    m = re.match(r'track-(\d+)_([A-Z])_textures', path.stem, re.I)
    stage_id = int(m.group(1)) if m else None
    tod = m.group(2).upper() if m else None
    folder = rel.parts[0] if len(rel.parts) > 1 else ''
    return stage_id, folder, tod


def parse_ini_flags(path):
    rows, section = [], None
    cur = {}
    for line in path.read_text(errors='replace').splitlines():
        line = line.strip()
        if line.startswith('[') and line.endswith(']'):
            if section and cur:
                rows.append((section, cur))
            section, cur = line[1:-1], {}
        elif '=' in line and section:
            k, v = line.split('=', 1)
            cur[k.strip()] = v.strip()
    if section and cur:
        rows.append((section, cur))
    return [(s, c) for s, c in rows if 'TextureFormat' in c or 'IsGroundTexture' in c or 'OpacityMap' in c]


def stats(job):
    """Mean RGB and alpha coverage of one texture (decoded mip 0 via Pillow)."""
    h, (zpath, member) = job
    try:
        from PIL import Image
        if member is None:                      # a loose DDS from the runtime harvest
            im = Image.open(zpath)
        else:
            with zipfile.ZipFile(zpath) as z:
                im = Image.open(BytesIO(z.read(member)))

        rgba = im.convert('RGBA').resize((64, 64))
        px = list(rgba.getdata())
        n = len(px)
        mean = [sum(p[i] for p in px) / n / 255 for i in range(3)]
        cut = sum(1 for p in px if p[3] < 128) / n
        partial = sum(1 for p in px if 16 < p[3] < 240) / n
        return h, mean, cut, partial
    except Exception:
        return h, None, None, None


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--game', default=GAME, required=GAME is None, help='RBR folder (or set RBR_GAME)')
    ap.add_argument('--out', default=str(CATALOG))
    ap.add_argument('--stats', action='store_true')
    ap.add_argument('--runtime', nargs='?', const=str(HARVEST), default=None)
    a = ap.parse_args()
    maps = Path(a.game) / 'Maps'
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()
    db = sqlite3.connect(out)
    db.executescript('''
      CREATE TABLE archives(id INTEGER PRIMARY KEY, path TEXT, kind TEXT, size INTEGER, stage_id INTEGER,
                            stage_folder TEXT, tod TEXT, n_textures INTEGER);
      CREATE TABLE textures(hash TEXT PRIMARY KEY, format TEXT, width INTEGER, height INTEGER, mips INTEGER,
                            mean_r REAL, mean_g REAL, mean_b REAL, alpha_cut REAL, alpha_partial REAL);
      CREATE TABLE occurrences(hash TEXT, archive_id INTEGER, member TEXT, key TEXT, condition TEXT);
      CREATE TABLE flags(ini TEXT, stage_id INTEGER, section TEXT, key TEXT, ground INTEGER, opacity INTEGER,
                         onebit INTEGER, format TEXT, mip_levels INTEGER, raw TEXT);
      CREATE TABLE remaps(map_ini TEXT, original TEXT, target TEXT, original_key TEXT);
    ''')
    archives = sorted(p for p in maps.rglob('*.rbz') if p.stem.lower().endswith('_textures') or 'textures' in p.stem.lower())
    unsupported = 0
    blobs = {}
    for i, path in enumerate(archives, 1):
        stage_id, folder, tod = archive_meta(path, maps)
        kind = 'zip' if zipfile.is_zipfile(path) else 'encrypted'
        cur = db.execute('INSERT INTO archives(path, kind, size, stage_id, stage_folder, tod, n_textures) '
                         'VALUES (?,?,?,?,?,?,0)', (str(path.relative_to(maps)), kind, path.stat().st_size,
                                                    stage_id, folder, tod))
        aid, n = cur.lastrowid, 0
        if kind == 'zip':
            with zipfile.ZipFile(path) as z:
                for m in z.infolist():
                    if m.is_dir() or not m.filename.lower().endswith('.dds'):
                        continue
                    b = z.read(m)
                    info = dds_info(b)
                    if not info:
                        unsupported += 1
                        continue
                    fmt, w, h, mips, data = info
                    hx = '%016X' % xxhash.xxh3_64_intdigest(data)
                    parts = [p.lower() for p in m.filename.replace('\\', '/').split('/')]
                    cond = next((p for p in parts if p in FOLDERS), '')
                    db.execute('INSERT OR IGNORE INTO textures(hash, format, width, height, mips) VALUES (?,?,?,?,?)',
                               (hx, fmt, w, h, mips))
                    db.execute('INSERT INTO occurrences VALUES (?,?,?,?,?)', (hx, aid, m.filename, tex_key(m.filename), cond))
                    if a.stats and hx not in blobs:
                        blobs[hx] = (str(path), m.filename)
                    n += 1
            db.execute('UPDATE archives SET n_textures=? WHERE id=?', (n, aid))
        if i % 25 == 0 or i == len(archives):
            db.commit()
            print(f'  {i}/{len(archives)} archives', file=sys.stderr)
    for ini in sorted(maps.rglob('*.ini')):
        name = ini.name.lower()
        if name.startswith('texturefilenamemap'):
            for line in ini.read_text(errors='replace').splitlines():
                if '\t' in line and not line.startswith(';'):
                    o, t = line.split('\t', 1)
                    db.execute('INSERT INTO remaps VALUES (?,?,?,?)', (str(ini.relative_to(maps)), o.strip(), t.strip(), tex_key(o)))
        elif name.startswith('track-'):
            m = re.match(r'track-(\d+)', name)
            for section, c in parse_ini_flags(ini):
                tf = lambda k: 1 if c.get(k, '').lower() == 'true' else 0
                ml = c.get('MipLevels')
                db.execute('INSERT INTO flags VALUES (?,?,?,?,?,?,?,?,?,?)',
                           (str(ini.relative_to(maps)), int(m.group(1)) if m else None, section, tex_key(section),
                            tf('IsGroundTexture'), tf('OpacityMap'), tf('OneBitOpacity'), c.get('TextureFormat'),
                            int(ml) if ml and ml.isdigit() else None, repr(c)))
    db.commit()
    if a.runtime:
        rt = Path(a.runtime)
        tsv = rt / 'harvest.tsv'
        if tsv.exists():
            stage_rows = {}
            lines = tsv.read_text(errors='replace').splitlines()
            for line in lines[1:]:
                f = line.split('	')
                if len(f) < 11:
                    continue
                hx, fmt, w, h, levels, stage = f[0].upper(), f[1], int(f[2]), int(f[3]), int(f[4]), int(f[7])
                if stage not in stage_rows:
                    cur = db.execute('INSERT INTO archives(path, kind, size, stage_id, stage_folder, tod, n_textures) '
                                     'VALUES (?,?,?,?,?,?,0)', (f'runtime/stage-{stage}', 'runtime', 0, stage, '', None))
                    stage_rows[stage] = cur.lastrowid
                db.execute('INSERT OR IGNORE INTO textures(hash, format, width, height, mips) VALUES (?,?,?,?,?)',
                           (hx, fmt, w, h, levels))
                db.execute('INSERT INTO occurrences VALUES (?,?,?,?,?)', (hx, stage_rows[stage], f'{hx}.dds', '', ''))
                dds = rt / f'{hx}.dds'
                if a.stats and hx not in blobs and dds.exists():
                    blobs[hx] = (str(dds), None)
            db.execute("UPDATE archives SET n_textures=(SELECT COUNT(*) FROM occurrences o WHERE o.archive_id=archives.id) "
                       "WHERE kind='runtime'")
            db.commit()
            print(f'  runtime harvest: {len(lines) - 1} textures from {len(stage_rows)} stage ids', file=sys.stderr)
        else:
            print(f'  runtime harvest: {tsv} not found', file=sys.stderr)
    if a.stats and blobs:
        print(f'  decoding {len(blobs)} unique textures for stats...', file=sys.stderr)
        with ProcessPoolExecutor() as ex:
            for hx, mean, cut, partial in ex.map(stats, blobs.items(), chunksize=32):
                if mean:
                    db.execute('UPDATE textures SET mean_r=?, mean_g=?, mean_b=?, alpha_cut=?, alpha_partial=? WHERE hash=?',
                               (*mean, cut, partial, hx))
        db.commit()
    db.executescript('CREATE INDEX occ_hash ON occurrences(hash); CREATE INDEX occ_key ON occurrences(key); '
                     'CREATE INDEX flags_key ON flags(key);')
    q = lambda s: db.execute(s).fetchone()[0]
    print(f'archives: {q("SELECT COUNT(*) FROM archives")} '
          f'(zip {q("SELECT COUNT(*) FROM archives WHERE kind=\'zip\'")}, '
          f'encrypted {q("SELECT COUNT(*) FROM archives WHERE kind=\'encrypted\'")})')
    print(f'texture occurrences: {q("SELECT COUNT(*) FROM occurrences")}, unique hashes: {q("SELECT COUNT(*) FROM textures")}, '
          f'unsupported DDS: {unsupported}')
    print(f'ini texture sections: {q("SELECT COUNT(*) FROM flags")} (ground {q("SELECT COUNT(*) FROM flags WHERE ground=1")}), '
          f'remaps: {q("SELECT COUNT(*) FROM remaps")}')
    print(f'catalog: {out}')


if __name__ == '__main__':
    main()
