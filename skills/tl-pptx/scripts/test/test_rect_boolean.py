"""Exact native rectangle subset, including holes and strict refusal cases."""
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from xml.etree import ElementTree as ET
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tl_geometry import boolean_document
from tl_guard import ContractError
from tl_build import prepare_build, build_package
from tl_rect_boolean import rectangle_fragment

NS = 'http://www.w3.org/2000/svg'

def svg(first='', second='', extra=''):
    return ('<svg xmlns="'+NS+'" viewBox="0 0 100 100">'
            '<rect id="a" x="0" y="0" width="20" height="20" fill="#112233" '+first+'/>'
            '<rect id="b" x="10" y="10" width="20" height="20" fill="#112233" '+second+'/>'+extra+'</svg>')

def run_xml(xml, op='union', style=None):
    return ET.fromstring(rectangle_fragment(ET.fromstring(xml),op,['a','b'],'result',style or {},[]))

def area(path):
    # Signed shoelace area: clockwise outer contours, counter-clockwise holes.
    total = 0
    for contour in path.get('d').split(' Z'):
        if not contour.strip():
            continue
        points=[tuple(map(float,p.split())) for p in contour.strip()[2:].split(' L ')]
        total += sum(x*v-u*y for (x,y),(u,v) in zip(points,points[1:]+points[:1])) / 2
    return total

class RectangleBooleanTests(unittest.TestCase):
    def test_exact_operations(self):
        for op, expected in [('union',700),('intersect',100),('subtract',300),('combine',600)]:
            # XOR of overlapping rectangles meets at corner vertices and is
            # deliberately refused, rather than emitting ambiguous boundaries.
            if op == 'combine':
                with self.assertRaisesRegex(ContractError,'point-touching'):
                    run_xml(svg(),op)
            else:
                self.assertEqual(area(run_xml(svg(),op)), expected)

    def test_hole_and_xor(self):
        xml=svg().replace('x="10" y="10" width="20" height="20"','x="5" y="5" width="10" height="10"')
        for op in ['subtract','combine']:
            path=run_xml(xml,op)
            self.assertEqual(area(path),300)
            self.assertEqual(path.get('d').count('M '),2)
            self.assertEqual(path.get('fill-rule'),'nonzero')

    def test_decimal_and_disconnected(self):
        xml=svg().replace('x="10" y="10"','x="30.000001" y="10"')
        for op in ['union','combine']:
            path=run_xml(xml,op)
            self.assertIn('30.000001',path.get('d'))
            self.assertEqual(path.get('d').count('M '),2)
            self.assertAlmostEqual(area(path),800)
        with self.assertRaisesRegex(ContractError,'empty'):
            run_xml(xml,'intersect')

    def test_grid_matches_integer_pixel_area(self):
        import random
        rng = random.Random(42)
        checked = 0
        for _ in range(50):
            rects = [(rng.randrange(-3, 4), rng.randrange(-3, 4), rng.randrange(1, 5), rng.randrange(1, 5)) for _ in range(3)]
            xml = '<svg xmlns="'+NS+'">' + ''.join(
                f'<rect id="r{i}" x="{x}" y="{y}" width="{w}" height="{h}"/>'
                for i,(x,y,w,h) in enumerate(rects)) + '</svg>'
            for op in ['union','intersect','subtract','combine']:
                expected = 0
                for x in range(-3, 8):
                    for y in range(-3, 8):
                        hits = [a <= x < a+w and b <= y < b+h for a,b,w,h in rects]
                        expected += {'union':any(hits),'intersect':all(hits),
                                     'subtract':hits[0] and not any(hits[1:]),'combine':sum(hits)%2 == 1}[op]
                try:
                    result = ET.fromstring(rectangle_fragment(ET.fromstring(xml),op,['r0','r1','r2'],'out',{},[]))
                except ContractError as error:
                    self.assertTrue('point-touching' in str(error) or ('empty' in str(error) and expected == 0))
                else:
                    self.assertEqual(area(result),expected)
                    checked += 1
        self.assertGreater(checked,100)

    def test_rejections(self):
        for bad in [svg('transform="translate(1 0)"'), svg('rx="1"'),svg('style="fill:red"'),
                    svg('mask="url(#m)"'),svg('opacity="0.5"'),svg('class="x"'),
                    svg().replace('width="20"','width="20px"',1),
                    svg().replace('width="20"','width="0"',1),
                    svg().replace('<rect id="a"','<circle id="a"'),
                    svg(extra='<style>rect { fill:red }</style>'),
                    svg().replace('viewBox=', 'transform="scale(2)" viewBox='),
                    svg().replace('fill="#112233"','fill="url(#gradient)"',1)]:
            with self.subTest(bad=bad),self.assertRaises(ContractError): run_xml(bad)
        for style in [{'stroke':'#123456'},{'fill':'none'},{'opacity':'0.5'}]:
            with self.assertRaises(ContractError):run_xml(svg(),style=style)
        with self.assertRaises(ContractError):run_xml(svg(),'fragment')

    def test_real_adapter_native_hole_and_unchanged_source(self):
        original=os.getcwd()
        with tempfile.TemporaryDirectory() as folder, patch.dict(ET._namespace_map):
            try:
                os.chdir(folder)
                content=svg().replace('x="10" y="10" width="20" height="20"','x="5" y="5" width="10" height="10"')
                Path('input.svg').write_text(content)
                ET.register_namespace('', NS)
                result,evidence=boolean_document('input.svg',{'operation':'subtract','source_ids':['a','b'],'output_id':'result'})
                self.assertEqual(Path('input.svg').read_text(),content)
                self.assertEqual(evidence['engine'],'exact-axis-aligned-rectangles')
                self.assertEqual(sorted(os.listdir()),['input.svg'])
                Path('result.svg').write_bytes(result)
                pptx,_=build_package(prepare_build({'slides':[{'svg':'result.svg'}]}))
                with zipfile.ZipFile(io.BytesIO(pptx)) as archive:
                    root=ET.fromstring(archive.read('ppt/slides/slide1.xml'))
                ns={'p':'http://schemas.openxmlformats.org/presentationml/2006/main','a':'http://schemas.openxmlformats.org/drawingml/2006/main'}
                self.assertEqual(len(root.findall('p:cSld/p:spTree/p:sp',ns)),1)
                self.assertEqual(len(root.findall('.//a:custGeom',ns)),1)
                self.assertEqual(len(root.findall('.//a:moveTo',ns)),2)
                self.assertEqual(len(root.findall('.//a:close',ns)),2)
                native = root.find('.//a:custGeom/a:pathLst/a:path',ns)
                contours, current = [], []
                for command in native:
                    if command.tag.endswith('}close'):
                        contours.append(current)
                        current = []
                    else:
                        point = command.find('a:pt',ns)
                        if point is not None:
                            current.append((int(point.get('x')),int(point.get('y'))))
                signs = [sum(x*v-u*y for (x,y),(u,v) in zip(points,points[1:]+points[:1])) for points in contours]
                self.assertLess(signs[0]*signs[1],0, 'Hole winding must survive native conversion')
                self.assertFalse(root.findall('.//p:pic',ns))
            finally:os.chdir(original)

if __name__=='__main__':unittest.main()
