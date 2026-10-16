"""AutoPBR v0 gate test: write a Remix mod that overrides normal + roughness of chosen legacy materials.

Usage (in this folder):
  python make_test_mod.py <game>\\rtx-remix\\mods\\rbr_autopbr_test  # empty placeholder
  python make_test_mod.py <game>\\rtx-remix\\mods\\rbr_autopbr_test 0123ABCD...  # override hashes

Remix 1.5.2 facts this relies on (tools/ref/dxvk-remix, re/kb/systems/remix-source.md):
- mods are found in DEFAULT_MODS_DIR/<mod>/mod.usda; a changed mod.usda is reloaded while the game runs
- material prim /RootNode/Looks/mat_<16 hex> = legacy material hash (XXH3 of colour texture mip 0);
  the replacement is merged with the game's material, so albedo and sampler stay the game's
- normalmap_texture is read as unsigned hemispherical octahedral RG (packing.slangh); only .dds is loaded
Test pattern: normal map = diagonal ridges + round bumps, roughness = checker 0.05 (glossy) / 0.85 (matte).
"""
import struct
import sys
from pathlib import Path

import numpy as np

SIZE = 512
DXGI_R8G8B8A8_UNORM = 28


def write_dds_rgba8(path: Path, img: np.ndarray) -> None:
    """img: HxWx4 uint8. Writes a DX10-header DDS with a full mip chain (box filter)."""
    mips = [img]
    while mips[-1].shape[0] > 1 and mips[-1].shape[1] > 1:
        m = mips[-1].astype(np.float32)
        m = (m[0::2, 0::2] + m[1::2, 0::2] + m[0::2, 1::2] + m[1::2, 1::2]) / 4.0
        mips.append(np.clip(m + 0.5, 0, 255).astype(np.uint8))
    h, w = img.shape[:2]
    flags = 0x1 | 0x2 | 0x4 | 0x1000 | 0x20000 | 0x8  # CAPS|HEIGHT|WIDTH|PIXELFORMAT|MIPMAPCOUNT|PITCH
    pf = struct.pack('<II4sIIIII', 32, 0x4, b'DX10', 0, 0, 0, 0, 0)
    hdr = struct.pack('<4sIIIIII', b'DDS ', 124, flags, h, w, w * 4, 0) + struct.pack('<I', len(mips)) \
        + b'\0' * 44 + pf + struct.pack('<IIIII', 0x1000 | 0x400000 | 0x8, 0, 0, 0, 0)
    dx10 = struct.pack('<IIIII', DXGI_R8G8B8A8_UNORM, 3, 0, 1, 0)  # TEXTURE2D, arraySize 1
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'wb') as f:
        f.write(hdr + dx10)
        for m in mips:
            f.write(np.ascontiguousarray(m).tobytes())


def octa_encode(n: np.ndarray) -> np.ndarray:
    """Tangent-space normals (z >= 0) -> unsigned hemispherical octahedral RG in [0,1] (Remix format)."""
    p = n[..., :2] / (np.abs(n[..., 0]) + np.abs(n[..., 1]) + n[..., 2])[..., None]
    s = np.stack([p[..., 0] + p[..., 1], p[..., 0] - p[..., 1]], axis=-1)
    return s * 0.5 + 0.5


def test_textures(out: Path) -> None:
    y, x = np.mgrid[0:SIZE, 0:SIZE].astype(np.float32) / SIZE
    # height: diagonal ridges + bumps -> normal from gradient (strong, to be obvious)
    hgt = 0.5 * np.sin((x + y) * 2 * np.pi * 8) + 0.5 * np.cos(x * 2 * np.pi * 4) * np.cos(y * 2 * np.pi * 4)
    dy, dx = np.gradient(hgt, 1.0 / SIZE)
    strength = 0.02
    n = np.stack([-dx * strength, -dy * strength, np.ones_like(hgt)], axis=-1)
    n /= np.linalg.norm(n, axis=-1, keepdims=True)
    rg = octa_encode(n)
    normal = np.zeros((SIZE, SIZE, 4), np.uint8)
    normal[..., 0:2] = np.clip(rg * 255 + 0.5, 0, 255).astype(np.uint8)
    normal[..., 3] = 255
    write_dds_rgba8(out / 'textures' / 'rbrtest_normal.dds', normal)

    checker = ((np.floor(x * 8) + np.floor(y * 8)) % 2).astype(np.float32)
    r = np.where(checker > 0, 0.85, 0.05)
    rough = np.zeros((SIZE, SIZE, 4), np.uint8)
    rough[..., 0:3] = np.clip(r * 255 + 0.5, 0, 255).astype(np.uint8)[..., None]
    rough[..., 3] = 255
    write_dds_rgba8(out / 'textures' / 'rbrtest_rough.dds', rough)


def glow_material(h: str) -> str:
    """Unmistakable check that an override applies at all: the surface glows red (no mask -> whole surface)."""
    return f'''        def Material "mat_{h}"
        {{
            def Shader "Shader"
            {{
                uniform token info:implementationSource = "sourceAsset"
                uniform asset info:mdl:sourceAsset = @AperturePBR_Opacity.mdl@
                uniform token info:mdl:sourceAsset:subIdentifier = "AperturePBR_Opacity"
                bool inputs:enable_emission = 1
                color3f inputs:emissive_color_constant = (1, 0, 0)
                float inputs:emissive_intensity = 5
                token outputs:out
            }}
        }}
'''


def material(h: str) -> str:
    if h.startswith('GLOW:'):
        return glow_material(h[5:].zfill(16))
    return f'''        def Material "mat_{h.zfill(16)}"
        {{
            def Shader "Shader"
            {{
                uniform token info:implementationSource = "sourceAsset"
                uniform asset info:mdl:sourceAsset = @AperturePBR_Opacity.mdl@
                uniform token info:mdl:sourceAsset:subIdentifier = "AperturePBR_Opacity"
                asset inputs:normalmap_texture = @./textures/rbrtest_normal.dds@ (
                    colorSpace = "raw"
                )
                asset inputs:reflectionroughness_texture = @./textures/rbrtest_rough.dds@ (
                    colorSpace = "raw"
                )
                token outputs:out
            }}
        }}
'''


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    out = Path(sys.argv[1])
    # hash = normal+roughness test pattern; glow:hash = red emissive (does any override apply?)
    hashes = [h.upper().replace('0X', '').replace('MAT_', '') for h in sys.argv[2:]]
    for h in hashes:
        int(h.removeprefix('GLOW:'), 16)  # validate
    out.mkdir(parents=True, exist_ok=True)
    if hashes:
        test_textures(out)  # textures first: Remix reloads as soon as mod.usda changes
    body = ''.join(material(h) for h in hashes)
    usda = f'#usda 1.0\n(\n    upAxis = "Y"\n)\n\ndef Xform "RootNode"\n{{\n    def Scope "Looks"\n    {{\n{body}    }}\n}}\n'
    (out / 'mod.usda').write_text(usda, encoding='utf-8')
    print(f'{out / "mod.usda"}: {len(hashes)} material override(s)')


if __name__ == '__main__':
    main()
