#!/usr/bin/env python3
"""Generate browser, launcher, themed-icon and splash assets from resources/logo.svg."""
import argparse
from pathlib import Path
from xml.etree import ElementTree as ET

import fitz

ROOT = Path(__file__).resolve().parents[1]
RES = Path('android/app/src/main/res')
SVG = '{http://www.w3.org/2000/svg}'
ANDROID = 'http://schemas.android.com/apk/res/android'
DENSITIES = {'ldpi': .75, 'mdpi': 1, 'hdpi': 1.5, 'xhdpi': 2, 'xxhdpi': 3, 'xxxhdpi': 4}
SPLASH_SIZES = {
    'ldpi': (240, 320), 'mdpi': (320, 480), 'hdpi': (480, 800),
    'xhdpi': (720, 1280), 'xxhdpi': (960, 1600), 'xxxhdpi': (1280, 1920),
}


def source_art():
    source = ROOT / 'resources/logo.svg'
    logo = ET.fromstring(source.read_bytes())
    background = logo.find(SVG + 'rect')
    paths = logo.findall(SVG + 'path')
    if logo.get('viewBox') != '0 0 72 72' or background is None or len(paths) != 4:
        raise ValueError('The brand source must contain a 72-unit tile and four tone contours')
    for path in paths:
        if (path.get('fill') != 'none' or path.get('stroke-linecap') != 'round'
                or path.get('stroke-linejoin') != 'round'):
            raise ValueError('Tone contours require unfilled, rounded strokes')
    ET.register_namespace('', SVG[1:-1])
    tile = ''.join(ET.tostring(element, encoding='unicode') for element in logo)
    marks = ''.join(ET.tostring(element, encoding='unicode') for element in paths)
    return source.read_bytes(), background.get('fill'), paths, tile, marks


def svg_document(width, height, content, viewbox=None):
    return (
        f'<svg xmlns="{SVG[1:-1]}" width="{width}" height="{height}" '
        f'viewBox="{viewbox or f"0 0 {width} {height}"}">{content}</svg>'
    ).encode()


def rasterize(svg):
    with fitz.open(stream=svg, filetype='svg') as document:
        return document[0].get_pixmap(alpha=True).tobytes('png')


def vector(paths, monochrome=False):
    rows = [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<!-- Generated from resources/logo.svg; run npm run assets:icons. -->',
        f'<vector xmlns:android="{ANDROID}" android:width="108dp" android:height="108dp"',
        '    android:viewportWidth="108" android:viewportHeight="108">',
        '    <group android:translateX="18" android:translateY="18">',
    ]
    for path in paths:
        color = '#FFFFFF' if monochrome else path.get('stroke')
        rows += [
            f'        <path android:pathData="{path.get("d")}" android:fillColor="#00000000"',
            f'            android:strokeColor="{color}" android:strokeWidth="{path.get("stroke-width")}"',
            '            android:strokeLineCap="round" android:strokeLineJoin="round" />',
        ]
    return ('\n'.join([*rows, '    </group>', '</vector>', ''])).encode()


def generated_assets():
    source, background, paths, tile, marks = source_art()
    adaptive = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<!-- Generated from resources/logo.svg; run npm run assets:icons. -->\n'
        f'<adaptive-icon xmlns:android="{ANDROID}">\n'
        '    <background android:drawable="@color/ic_launcher_background" />\n'
        '    <foreground android:drawable="@drawable/ic_launcher_foreground" />\n'
        '    <monochrome android:drawable="@drawable/ic_launcher_monochrome" />\n'
        '</adaptive-icon>\n'
    ).encode()
    yield Path('app/logo.svg'), source
    yield RES / 'drawable-v24/ic_launcher_foreground.xml', vector(paths)
    yield RES / 'drawable-v24/ic_launcher_monochrome.xml', vector(paths, monochrome=True)
    yield RES / 'values/ic_launcher_background.xml', (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<!-- Generated from resources/logo.svg; run npm run assets:icons. -->\n'
        f'<resources>\n    <color name="ic_launcher_background">{background}</color>\n</resources>\n'
    ).encode()
    for name in ('ic_launcher', 'ic_launcher_round'):
        yield RES / f'mipmap-anydpi-v26/{name}.xml', adaptive
    round_tile = f'<circle cx="36" cy="36" r="36" fill="{background}"/>{marks}'
    for density, scale in DENSITIES.items():
        size = round(48 * scale)
        for name, content in [('ic_launcher', tile), ('ic_launcher_round', round_tile)]:
            yield RES / f'mipmap-{density}/{name}.png', rasterize(
                svg_document(size, size, content, '0 0 72 72')
            )
    for night in (False, True):
        qualifier = '-night' if night else ''
        for orientation in ('port', 'land'):
            for density, (short, long) in SPLASH_SIZES.items():
                width, height = (short, long) if orientation == 'port' else (long, short)
                size = round(96 * DENSITIES[density])
                scale = size / 72
                backdrop = '#14251f' if night else '#f4f6f5'
                content = (
                    f'<rect width="{width}" height="{height}" fill="{backdrop}"/>'
                    f'<g transform="translate({(width-size)/2} {(height-size)/2}) scale({scale})">{tile}</g>'
                )
                png = rasterize(svg_document(width, height, content))
                yield RES / f'drawable-{orientation}{qualifier}-{density}/splash.png', png
                if orientation == 'port' and density == 'mdpi':
                    yield RES / f'drawable{qualifier}/splash.png', png


def obsolete_assets():
    yield RES / 'drawable/ic_launcher_background.xml'
    for density in DENSITIES:
        for layer in ('background', 'foreground'):
            yield RES / f'mipmap-{density}/ic_launcher_{layer}.png'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='fail if committed assets differ from the source')
    args = parser.parse_args()
    if fitz.VersionBind != '1.26.5':
        parser.error('Install requirements.txt for the pinned PyMuPDF 1.26.5 asset renderer')
    changed = []
    for relative, payload in generated_assets():
        target = ROOT / relative
        if not target.is_file() or target.read_bytes() != payload:
            changed.append(relative.as_posix())
            if not args.check:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(payload)
    for relative in obsolete_assets():
        target = ROOT / relative
        if target.exists():
            changed.append(relative.as_posix())
            if not args.check:
                target.unlink()
    if args.check and changed:
        parser.exit(1, 'Stale brand assets; run npm run assets:icons:\n' + '\n'.join(changed) + '\n')
    print(f'Brand assets {"verified" if args.check else "generated"}: {len(changed)} changes.')


if __name__ == '__main__':
    main()
