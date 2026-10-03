import importlib.util
import struct
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
AVAILABLE = importlib.util.find_spec('fitz') is not None
if AVAILABLE:
    spec = importlib.util.spec_from_file_location('generate_brand_assets', ROOT / 'scripts/generate_brand_assets.py')
    branding = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(branding)

ANDROID = '{http://schemas.android.com/apk/res/android}'
SVG = '{http://www.w3.org/2000/svg}'


@unittest.skipUnless(AVAILABLE, 'install requirements.txt for the pinned brand renderer')
class BrandAssetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.generated = dict(branding.generated_assets())

    def test_every_generated_asset_matches_the_committed_source(self):
        self.assertEqual(len(self.generated), 44)
        for relative, expected in self.generated.items():
            with self.subTest(path=relative):
                self.assertEqual((ROOT / relative).read_bytes(), expected)
        self.assertTrue(all(not (ROOT / relative).exists() for relative in branding.obsolete_assets()))

    def test_legacy_icons_cover_all_densities_and_round_launchers(self):
        for density, scale in branding.DENSITIES.items():
            for name in ('ic_launcher', 'ic_launcher_round'):
                payload = self.generated[branding.RES / f'mipmap-{density}/{name}.png']
                self.assertEqual(payload[:8], b'\x89PNG\r\n\x1a\n')
                size = round(48 * scale)
                self.assertEqual(struct.unpack('>II', payload[16:24]), (size, size))
                image = branding.fitz.Pixmap(payload)
                self.assertEqual(image.pixel(size // 2, size // 2), (23, 107, 80, 255))
                self.assertEqual(image.pixel(0, 0)[3], 0)

    def test_adaptive_layers_have_full_background_and_matching_themed_geometry(self):
        _, color, source_paths, _, _ = branding.source_art()
        for name in ('ic_launcher', 'ic_launcher_round'):
            adaptive = ET.fromstring(self.generated[branding.RES / f'mipmap-anydpi-v26/{name}.xml'])
            self.assertEqual(adaptive.find('background').get(ANDROID + 'drawable'), '@color/ic_launcher_background')
            self.assertEqual(adaptive.find('foreground').get(ANDROID + 'drawable'), '@drawable/ic_launcher_foreground')
            self.assertEqual(adaptive.find('monochrome').get(ANDROID + 'drawable'), '@drawable/ic_launcher_monochrome')
            self.assertEqual(len(adaptive.findall('.//inset')), 0)
        background = ET.fromstring(self.generated[branding.RES / 'values/ic_launcher_background.xml'])
        self.assertEqual(background.find('color').text, color)
        for variant in ('foreground', 'monochrome'):
            vector = ET.fromstring(self.generated[branding.RES / f'drawable-v24/ic_launcher_{variant}.xml'])
            self.assertEqual(vector.get(ANDROID + 'viewportWidth'), '108')
            self.assertEqual(vector.get(ANDROID + 'viewportHeight'), '108')
            group = vector.find('group')
            self.assertEqual(group.get(ANDROID + 'translateX'), '18')
            self.assertEqual(group.get(ANDROID + 'translateY'), '18')
            self.assertEqual(len(group.findall('path')), 4)
            for original, path in zip(source_paths, group.findall('path')):
                self.assertEqual(path.get(ANDROID + 'pathData'), original.get('d'))
                self.assertEqual(path.get(ANDROID + 'strokeWidth'), original.get('stroke-width'))
                expected = '#FFFFFF' if variant == 'monochrome' else original.get('stroke')
                self.assertEqual(path.get(ANDROID + 'strokeColor'), expected)
                self.assertEqual(path.get(ANDROID + 'strokeLineCap'), 'round')

    def test_all_foreground_pixels_fit_the_adaptive_circular_safe_zone(self):
        _, _, _, _, marks = branding.source_art()
        svg = branding.svg_document(432, 432, f'<g transform="translate(18 18)">{marks}</g>', '0 0 108 108')
        image = branding.fitz.Pixmap(branding.rasterize(svg))
        colored = 0
        for y in range(image.height):
            for x in range(image.width):
                if image.pixel(x, y)[3] > 8:
                    colored += 1
                    self.assertLessEqual(((x + .5) / 4 - 54) ** 2 + ((y + .5) / 4 - 54) ** 2, 33 ** 2)
        self.assertGreater(colored, 3000)

    def test_splash_backdrops_and_browser_branding_are_consistent(self):
        for night, background in [(False, (244, 246, 245, 255)), (True, (20, 37, 31, 255))]:
            qualifier = '-night' if night else ''
            image = branding.fitz.Pixmap(self.generated[branding.RES / f'drawable{qualifier}/splash.png'])
            self.assertEqual((image.width, image.height), (320, 480))
            self.assertEqual(image.pixel(0, 0), background)
            self.assertEqual(image.pixel(160, 240), (23, 107, 80, 255))
        self.assertEqual(self.generated[Path('app/logo.svg')], (ROOT / 'resources/logo.svg').read_bytes())
        html = (ROOT / 'app/index.html').read_text(encoding='utf-8')
        self.assertIn('<link rel="icon" type="image/svg+xml" href="logo.svg">', html)
        self.assertIn('<img class="brand-mark" src="logo.svg" alt="" width="42" height="42">', html)


if __name__ == '__main__':
    unittest.main()
