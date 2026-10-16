r"""Summary of the texture catalog (harvest.py) and how much of a Remix capture it covers.

Usage: python catalog_report.py [--catalog work\catalog\catalog.sqlite]
                                                                        [--captures <RBR folder>\rtx-remix\captures]
Prints: archive/texture counts, the most widely shared textures, ground-texture counts, and for the capture folder the
share of captured texture hashes found in the catalog (the rest come from encrypted stages, cars or the menu).
"""
import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import CAPTURES, CATALOG  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--catalog', default=str(CATALOG))
    ap.add_argument('--captures', default=str(CAPTURES))
    a = ap.parse_args()
    db = sqlite3.connect(a.catalog)
    one = lambda s, *p: db.execute(s, p).fetchone()[0]
    print(f'archives {one("SELECT COUNT(*) FROM archives")}: '
          f'zip {one("SELECT COUNT(*) FROM archives WHERE kind=?", "zip")}, '
          f'encrypted {one("SELECT COUNT(*) FROM archives WHERE kind=?", "encrypted")} '
          f'({one("SELECT COALESCE(SUM(size),0) FROM archives WHERE kind=?", "encrypted") / 1e9:.1f} GB)')
    print(f'unique textures {one("SELECT COUNT(*) FROM textures")}, occurrences {one("SELECT COUNT(*) FROM occurrences")}')
    print('formats:', ', '.join(f'{f} {n}' for f, n in db.execute(
        'SELECT format, COUNT(*) FROM textures GROUP BY format ORDER BY 2 DESC LIMIT 8')))
    print('conditions:', ', '.join(f'{c or "(root)"} {n}' for c, n in db.execute(
        'SELECT condition, COUNT(DISTINCT hash) FROM occurrences GROUP BY condition ORDER BY 2 DESC')))
    g = one('SELECT COUNT(DISTINCT o.hash) FROM occurrences o JOIN flags f ON f.key = o.key WHERE f.ground = 1')
    print(f'textures flagged IsGroundTexture in some ini: {g}')
    print('\nmost shared textures (archives using the same hash):')
    for hx, n, name in db.execute('SELECT o.hash, COUNT(DISTINCT o.archive_id) n, MIN(o.key) FROM occurrences o '
                                  'GROUP BY o.hash ORDER BY n DESC LIMIT 12'):
        print(f'  {hx} {n:4} archives  {name}')
    tex = Path(a.captures) / 'textures'
    if tex.is_dir():
        cap = {p.stem for p in tex.glob('*.dds') if len(p.stem) == 16}
        known = {r[0] for r in db.execute('SELECT hash FROM textures')}
        hit = cap & known
        print(f'\ncapture textures {len(cap)}: in catalog {len(hit)} ({100 * len(hit) / max(1, len(cap)):.0f}%)')


if __name__ == '__main__':
    main()
