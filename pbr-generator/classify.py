r"""Material class for every texture in the catalog (harvest.py), by rules, without looking at images.

Signals, in order: name tokens (multilingual: RBR stages come from many countries), the track .ini flags
(IsGroundTexture, OpacityMap), alpha coverage from the --stats pass, and the dry/damp/wet folder. Each class carries
starting PBR parameters for the generator (roughness dry/wet, metallic, normal strength, whether to generate maps).
Writes tables `classes` and `class_params` into the catalog and work\catalog\classes.csv, prints counts and the most
shared textures still unclassified (the short list to review by hand).

Usage: python classify.py [--catalog work\catalog\catalog.sqlite] [--unknown 40]
"""
import argparse
import csv
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import CATALOG  # noqa: E402

# class: (roughness dry, roughness wet, metallic, normal strength, generate maps, note)
PARAMS = {
    'aux_spec':      (None, None, None, None, 0, 'specular/mask helper of another texture; not an albedo'),
    'shadow_decal':  (None, None, None, None, 0, 'baked shadow; candidate for rtx.ignoreTextures'),
    'emissive_light': (0.4, 0.3, 0.0, 0.3, 0, 'start clock / lamp lights; emissive in the Toolkit'),
    'sky':           (None, None, None, None, 0, 'sky/cloud textures; Remix sky'),
    'horizon':       (0.95, 0.9, 0.0, 0.0, 0, 'superbowl/panorama/far scenery planes; keep flat'),
    'people':        (0.8, 0.6, 0.0, 0.3, 0, 'spectators/marshals cards'),
    'sign_banner':   (0.5, 0.3, 0.0, 0.3, 1, 'banners, signs, logos, boards'),
    'road_line':     (0.55, 0.25, 0.0, 0.5, 1, 'road markings, arrows, crossings'),
    'water':         (0.05, 0.05, 0.0, 0.5, 0, 'water surfaces; Toolkit water material'),
    'road_tarmac':   (0.65, 0.2, 0.0, 1.0, 1, 'asphalt'),
    'road_gravel':   (0.85, 0.45, 0.0, 1.2, 1, 'gravel road'),
    'road_snow':     (0.5, 0.3, 0.0, 0.8, 1, 'snow/ice road'),
    'road_generic':  (0.75, 0.3, 0.0, 1.0, 1, 'road of unknown surface'),
    'foliage_card':  (0.7, 0.5, 0.0, 0.6, 1, 'tree/bush/grass cards with cut-out alpha'),
    'foliage_solid': (0.8, 0.6, 0.0, 0.8, 1, 'bark, solid vegetation, tree walls without alpha'),
    'rock':          (0.8, 0.45, 0.0, 1.4, 1, 'rock, cliff, stone walls'),
    'building':      (0.8, 0.5, 0.0, 1.0, 1, 'houses, walls, roofs, concrete, brick'),
    'metal':         (0.4, 0.25, 1.0, 0.6, 1, 'rails, barriers, poles, metal sheets'),
    'wood':          (0.75, 0.45, 0.0, 1.0, 1, 'planks, wooden fences, poles'),
    'fence_cutout':  (0.6, 0.4, 0.0, 0.5, 1, 'fences/nets with cut-out alpha'),
    'vehicle_prop':  (0.5, 0.3, 0.0, 0.4, 1, 'parked cars, tents, service vehicles'),
    'prop':          (0.6, 0.4, 0.0, 0.5, 1, 'small props: cones, chairs, cans, bales, boxes'),
    'ground_grass':  (0.9, 0.5, 0.0, 1.0, 1, 'grass ground'),
    'ground_dirt':   (0.9, 0.45, 0.0, 1.2, 1, 'dirt, mud, sand, soil'),
    'ground_far':    (0.95, 0.6, 0.0, 0.5, 1, 'satellite/far terrain'),
    'ground_generic': (0.9, 0.5, 0.0, 1.0, 1, 'ini says ground, name unknown'),
    'cutout_generic': (0.75, 0.5, 0.0, 0.5, 1, 'unknown object with cut-out alpha'),
    'placeholder':   (0.8, 0.5, 0.0, 0.5, 0, 'RBR template/blank textures'),
    'unknown':       (0.8, 0.5, 0.0, 0.8, 0, 'needs a rule'),
}

# (class, patterns). Plain patterns match as substrings; patterns starting with '^' are regexes on the name.
RULES = [
    ('aux_spec',      [r'^.*(_spec|spec\d*$|_specular|-specular|waterspec|_mask$|_alpha$|_nrm$|_normal$)']),
    ('shadow_decal',  ['shadow', 'sombra', 'stin', 'schatten', 'ombre']),
    ('emissive_light', ['start_clock', 'redlight', 'greenlight', 'light_on', 'lamp', 'streetlight', 'neon']),
    ('sky',           [r'^(sky|cloud|us_clear|us_overcast|us_clouded|us_precip)', 'skybox', 'clouds']),
    ('horizon',       ['superbowl', 'horizont', 'horizon', 'panorama', 'farscenery', 'mountain_tree_plane',
                       r'^(pan\d|horr_)', 'background', r'^bg_']),
    ('people',        ['spect', 'ludzie', 'people', 'crowd', 'marshal', 'publik', 'divaci', 'person', 'gente',
                       'zuschauer', 'spectateur', 'fans', 'photographer', '_head', 'female', 'male_', 'girl']),
    ('sign_banner',   ['banner', 'baner', 'sign', 'znak', 'panneau', 'zenkl', 'logo', 'advert', 'reklam', 'board',
                       'flag', 'poster', 'autoshow', 'senyal', 'cartel', 'tabla', 'sponsor', 'arrow_sign', 'km_',
                       'rallytriangle', 'placa', 'schild']),
    ('road_line',     ['line', 'marking', 'arrow', 'prechod', 'zebra', 'crosswalk', 'stripe', 'lane']),
    ('water',         ['water', 'river', 'lake', 'puddle', 'voda', 'agua', 'kaluz', 'eau']),
    ('road_snow',     ['snow', 'ice', 'sneh', 'snieg', 'neige', 'nieve', 'lumi']),
    ('road_tarmac',   ['tarmac', 'asphalt', 'asfalt', r'^asf', 'bitum', 'asfalto']),
    ('road_gravel',   ['gravel', r'^grav', 'gravier', 'sterrato', 'sora', 'sotr', 'grava', 'kies', 'macadam',
                       'stones_road', 'sora']),
    ('road_generic',  ['road', 'track_', 'cesta', 'droga', 'strada', 'route', 'camino', 'carretera']),
    ('foliage',       ['tree', 'bush', 'veg', 'leaf', 'leaves', 'birch', 'pine', 'fagus', 'spruce', 'fir', 'oak',
                       'derev', 'strom', 'drzew', 'arbol', 'arbre', 'baum', 'otpt', 'otpo', 'plant', 'fern', 'reed',
                       'hedge', 'shrub', 'palm', 'branch', 'conifer', 'forest', 'wood_plane', 'plum', 'kmen', 'bark',
                       'grasst', 'veg-grass', 'flower', 'kriak', 'keri']),
    ('rock',          ['rock', 'cliff', 'stone', 'kamen', 'roca', 'pierre', 'skal', 'boulder', 'gravelwall',
                       'wall_grassrock', 'wall_rock']),
    ('building',      ['house', 'domy', r'^dom', 'hisa', 'build', 'roof', 'brick', 'concrete', 'beton', 'casa',
                       'torre', 'church', 'barn', 'shed', 'garage', 'window', 'door', 'facade', 'office', 'ristor',
                       'hotel', 'bridge', 'most', 'tunnel', 'cot5', 'cot57', 'hoc_', 'wall', 'zid', 'mur']),
    ('metal',         ['metal', 'metail', 'rail', 'armco', 'guard', 'barrier', 'pole', 'steel', 'iron', 'pipe',
                       'zabradl', 'svodid', 'kov']),
    ('wood',          ['wood', 'plank', 'timber', 'drev', 'madera', 'bois', 'holz', 'log']),
    ('fence',         ['fence', 'plot', r'^net', 'netting', 'fil$', r'^fil\b', 'mesh', 'ograd']),
    ('vehicle_prop',  [r'^cars?[_\d]', 'supportcars', 'truck', 'tent', 'van', 'bus', 'tractor', 'ambulance',
                       'vehicle', 'auto_', 'pompier', 'police']),
    ('prop',          ['cone', 'chair', 'spraycan', 'bale', 'barrel', 'crate', 'table', 'bench', 'tyre', 'tire',
                       'pallet', 'curve', 'rozhlas', 'speaker', 'umbrella', 'kuzel']),
    ('ground_grass',  ['grass', 'trav', 'herbe', 'hierba', 'gras', 'meadow', 'lawn', 'trawa']),
    ('ground_far',    ['google', 'googl', r'^sat', 'farsat', 'satel', 'location', 'locati', 'terrain_far', '_far',
                       'bing', r'^sas_', 'teren', 'fondo', 'scenery', 'mountain', 'ortho', 'aerial']),
    ('ground_dirt',   ['dirt', 'mud', 'sand', 'earth', 'soil', 'ground', 'terrain', 'zem', 'blato', 'tierra',
                       'terre', 'pisek', 'field', 'polje']),
    ('placeholder',   ['blank', 'placeholder', 'tracktextureplate', 'objecttextureplate', 'default', 'dummy']),
]
COMPILED = [(cls, [re.compile(p[1:]) if p.startswith('^') else p for p in pats]) for cls, pats in RULES]
CUTOUT = 0.05   # alpha_cut share above which a texture counts as cut-out


def match(name):
    for cls, pats in COMPILED:
        for p in pats:
            if (p.search(name) if hasattr(p, 'search') else p in name):
                return cls, p.pattern if hasattr(p, 'pattern') else p
    return None, None


def classify(name, ground, opacity, alpha_cut):
    cutout = (alpha_cut or 0) > CUTOUT
    cls, rule = match(name)
    if cls == 'foliage':
        cls = 'foliage_card' if cutout else 'foliage_solid'
    elif cls == 'fence':
        cls = 'fence_cutout' if cutout else 'wood'
    if cls and cls != 'placeholder':
        return cls, f'name:{rule}', 'high' if not (ground and cls in ('building', 'sign_banner')) else 'medium'
    if ground:
        return ('ground_generic' if cls != 'placeholder' else 'ground_generic'), 'ini:IsGroundTexture', 'medium'
    if cls == 'placeholder':
        return cls, f'name:{rule}', 'medium'
    if cutout or opacity:
        return 'cutout_generic', 'alpha', 'low'
    return 'unknown', '', 'none'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--catalog', default=str(CATALOG))
    ap.add_argument('--unknown', type=int, default=40)
    a = ap.parse_args()
    db = sqlite3.connect(a.catalog)
    rows = db.execute('''
      SELECT t.hash, MIN(o.key), GROUP_CONCAT(DISTINCT o.key), MAX(COALESCE(f.ground, 0)), MAX(COALESCE(f.opacity, 0)),
             t.alpha_cut, COUNT(DISTINCT o.archive_id), GROUP_CONCAT(DISTINCT o.condition), t.format, t.width, t.height
      FROM textures t JOIN occurrences o ON o.hash = t.hash LEFT JOIN flags f ON f.key = o.key
      GROUP BY t.hash''').fetchall()
    db.executescript('''DROP TABLE IF EXISTS classes; DROP TABLE IF EXISTS class_params;
      CREATE TABLE classes(hash TEXT PRIMARY KEY, class TEXT, rule TEXT, confidence TEXT, name TEXT, names TEXT,
                           ground INTEGER, cutout INTEGER, wet INTEGER, archives INTEGER, format TEXT, width INTEGER,
                           height INTEGER);
      CREATE TABLE class_params(class TEXT PRIMARY KEY, roughness REAL, roughness_wet REAL, metallic REAL,
                                normal_strength REAL, generate INTEGER, note TEXT);''')
    db.executemany('INSERT INTO class_params VALUES (?,?,?,?,?,?,?)', [(k, *v) for k, v in PARAMS.items()])
    out = []
    for hx, name, names, ground, opacity, alpha_cut, n_arch, conds, fmt, w, h in rows:
        # try every name the texture goes by; the first that matches a rule wins
        cls, rule, conf = 'unknown', '', 'none'
        for nm in [name] + [x for x in (names or '').split(',') if x != name]:
            c = classify(nm, ground, opacity, alpha_cut)
            if c[0] != 'unknown':
                cls, rule, conf = c
                name = nm
                break
        wet = 1 if conds and 'wet' in conds.split(',') else 0
        out.append((hx, cls, rule, conf, name, names, ground, int((alpha_cut or 0) > CUTOUT), wet, n_arch, fmt, w, h))
    db.executemany('INSERT INTO classes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)', out)
    db.commit()
    csv_path = Path(a.catalog).with_name('classes.csv')
    with open(csv_path, 'w', newline='', encoding='utf-8') as f:
        wr = csv.writer(f)
        wr.writerow(['hash', 'class', 'rule', 'confidence', 'name', 'ground', 'cutout', 'wet', 'archives', 'format', 'w', 'h'])
        for r in sorted(out, key=lambda r: (r[1], -r[9])):
            wr.writerow([r[0], r[1], r[2], r[3], r[4], r[6], r[7], r[8], r[9], r[10], r[11], r[12]])
    print(f'{len(out)} textures classified -> {csv_path}')
    for cls, n, arch in db.execute('SELECT class, COUNT(*), SUM(archives) FROM classes GROUP BY class ORDER BY 2 DESC'):
        print(f'  {cls:15} {n:6}   (archive uses {arch})')
    print(f'\nmost shared unclassified (review):')
    for name, n, w, h, fmt in db.execute('SELECT name, archives, width, height, format FROM classes WHERE class=? '
                                         'ORDER BY archives DESC, width*height DESC LIMIT ?', ('unknown', a.unknown)):
        print(f'  {n:3}  {name}  {w}x{h} {fmt}')


if __name__ == '__main__':
    main()
