"""Pure fixture tests for bounded project-local SVG/icon materialization."""
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET

from PIL import Image
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tl_svg_resources as resources
from tl_guard import ContractError, validate_svg

NS = 'http://www.w3.org/2000/svg'


def svg(body, *, extra=''):
    """Build a namespaced synthetic SVG, never a user file."""
    return (f'<svg xmlns="{NS}" viewBox="0 0 100 100" {extra}>{body}</svg>').encode()


def roster(uses=1):
    """Match extract_svg_assets' preserve-color project-local icon protocol."""
    return {
        'templates/page.svg': svg('<use data-icon="imported/item" x="5" y="8" width="20" height="30"/>' * uses),
        'icons/imported/item.svg': svg('<g id="shape"><rect width="10" height="10" fill="#123456"/></g>',
                                       extra='data-icon-style="preserve-color"'),
    }


class SvgResourceTests(unittest.TestCase):
    """Exercise the helper without bypassing any template integrity gate."""

    def test_imported_icon_preserves_color_geometry_and_ids(self):
        """Imported multicolor artwork becomes editable in-document groups."""
        entries = roster(2)
        original = dict(entries)
        data = resources.materialize_svg_resources('templates/page.svg', entries)
        root = ET.fromstring(data)
        rects = root.findall(f'.//{{{NS}}}rect')
        self.assertEqual(len(rects), 2)
        self.assertTrue(all(rect.get('fill') == '#123456' for rect in rects))
        ids = [element.get('id') for element in root.iter() if element.get('id')]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertFalse(any(element.get('data-icon') for element in root.iter()))
        self.assertTrue(any('translate(5, 8)' in element.get('transform', '') for element in root.iter()))
        self.assertEqual(entries, original)
        validate_svg(data)

    def test_stroke_library_color_and_transform(self):
        """Upstream stroke overrides and authoritative placement are retained."""
        entries = {'page.svg': svg('<use data-icon="outline/item" fill="none" stroke="#FF0000" '
                                   'stroke-width="3" transform="translate(7,9)"/>'),
                   'icons/outline/item.svg': svg('<path d="M0 0 L10 10" fill="none" stroke="currentColor"/>')}
        root = ET.fromstring(resources.materialize_svg_resources('page.svg', entries))
        groups = root.findall(f'.//{{{NS}}}g')
        self.assertTrue(any(g.get('stroke') == '#FF0000' and g.get('stroke-width') == '3' for g in groups))
        self.assertTrue(any(g.get('transform') == 'translate(7,9)' for g in groups))

    def test_repeated_large_icon_rejected_before_next_generation(self):
        """Reference work is charged per occurrence, not by distinct asset."""
        entries = roster(4)
        entries['icons/imported/item.svg'] = svg('<rect width="10" height="10" data-padding="' + 'x' * 100000 + '"/>',
                                                 extra='data-icon-style="preserve-color"')
        original = resources.icons.generate_icon_group
        with patch.object(resources, 'MAX_TOTAL_BYTES', 500000):
            with patch.object(resources.icons, 'generate_icon_group', wraps=original) as generate:
                with self.assertRaisesRegex(ContractError, 'allocation budget'):
                    resources.materialize_svg_resources('templates/page.svg', entries)
                self.assertEqual(generate.call_count, 1)

    def test_relative_raster_and_recursive_svg_images(self):
        """Images resolve relative to their own roster member, without files."""
        buffer = io.BytesIO()
        Image.new('RGB', (2, 2), 'red').save(buffer, format='PNG')
        entries = {'templates/page.svg': svg('<image href="../images/wrapper.svg" width="20" height="20"/>'),
                   'images/wrapper.svg': svg('<image href="pixel.png" width="10" height="10"/>'),
                   'images/pixel.png': buffer.getvalue()}
        output = resources.materialize_svg_resources('templates/page.svg', entries)
        root = ET.fromstring(output)
        self.assertTrue(root.find(f'{{{NS}}}image').get('href').startswith('data:image/svg+xml;base64,'))
        validate_svg(output)

    def test_local_use_inside_icon_is_expanded(self):
        """The same-document use algorithm still works within imported art."""
        entries = roster()
        entries['icons/imported/item.svg'] = svg('<defs><g id="base"><rect width="3" height="4"/></g></defs>'
                                                 '<use href="#base" x="5"/>', extra='data-icon-style="preserve-color"')
        root = ET.fromstring(resources.materialize_svg_resources('templates/page.svg', entries))
        self.assertFalse(root.findall(f'.//{{{NS}}}use'))
        self.assertGreaterEqual(len(root.findall(f'.//{{{NS}}}rect')), 1)

    def test_escape_cycle_and_external_resources_rejected(self):
        """Canonical icon identifiers and relative resource closure fail closed."""
        for value in ('../../outside.png', 'https://example.com/icon.svg', '../images/%2e%2e/x.png'):
            with self.subTest(value=value):
                with self.assertRaises(ContractError):
                    resources.materialize_svg_resources('page.svg', {'page.svg': svg(f'<image href="{value}"/>')})
        entries = {'a.svg': svg('<image href="b.svg"/>'), 'b.svg': svg('<image href="a.svg"/>')}
        with self.assertRaisesRegex(ContractError, 'Cyclic'):
            resources.materialize_svg_resources('a.svg', entries)
        with self.assertRaises(ContractError):
            resources.materialize_svg_resources('page.svg', {'page.svg': svg('<use data-icon="imported/../../outside"/>')})

    def test_active_icon_or_placeholder_is_not_silently_dropped(self):
        """Validation precedes upstream extraction's optional attribute removal."""
        entries = roster()
        entries['templates/page.svg'] = svg('<use data-icon="imported/item" onclick="evil()"/>')
        with self.assertRaises(ContractError):
            resources.materialize_svg_resources('templates/page.svg', entries)
        entries = roster()
        entries['icons/imported/item.svg'] = svg('<script>evil()</script><rect width="1" height="1"/>')
        with self.assertRaises(ContractError):
            resources.materialize_svg_resources('templates/page.svg', entries)

    def test_xlink_and_gradient_ids_survive_normalization(self):
        """Prefixed SVG serialization retains declared xlink and local paint IDs."""
        entries = roster()
        entries['icons/imported/item.svg'] = (
            '<s:svg xmlns:s="http://www.w3.org/2000/svg" '
            'xmlns:x="http://www.w3.org/1999/xlink" viewBox="0 0 24 24" '
            'data-icon-style="preserve-color"><s:defs><s:linearGradient id="paint">'
            '<s:stop offset="0" stop-color="#000000"/><s:stop offset="1" stop-color="#FFFFFF"/>'
            '</s:linearGradient></s:defs><s:a x:href="https://example.com">'
            '<s:rect width="10" height="10" fill="url(#paint)"/></s:a></s:svg>').encode()
        output = resources.materialize_svg_resources('templates/page.svg', entries)
        root = ET.fromstring(output)
        anchor = root.find(f'.//{{{NS}}}a')
        self.assertEqual(anchor.get('{http://www.w3.org/1999/xlink}href'), 'https://example.com')
        paint = root.find(f'.//{{{NS}}}linearGradient').get('id')
        self.assertEqual(root.find(f'.//{{{NS}}}rect').get('fill'), f'url(#{paint})')

    def test_nonfinite_icon_geometry_is_rejected(self):
        """Invalid icon extents cannot silently become zero-sized artwork."""
        entries = roster()
        entries['icons/imported/item.svg'] = (
            f'<svg xmlns="{NS}" viewBox="0 0 inf inf" data-icon-style="preserve-color">'
            '<rect width="1" height="1"/></svg>').encode()
        with self.assertRaisesRegex(ContractError, 'finite'):
            resources.materialize_svg_resources('templates/page.svg', entries)

    def test_budget_is_shared_across_pages(self):
        """A caller-provided dictionary accumulates costs across the bundle."""
        entries = roster()
        budget = {}
        resources.materialize_svg_resources('templates/page.svg', entries, budget)
        previous = dict(budget)
        resources.materialize_svg_resources('templates/page.svg', entries, budget)
        self.assertGreater(budget['bytes'], previous['bytes'])
        self.assertGreater(budget['nodes'], previous['nodes'])


if __name__ == '__main__':
    unittest.main()
