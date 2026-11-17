"""Hostile-package budgets and ordinary native-package preflight."""
import io
from pathlib import Path
import sys
import unittest
import warnings
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pptx import Presentation
from tl_guard import ContractError
from tl_package_guard import inspect_package


def package(changes=None):
    """Produce a real empty PPTX, optionally replacing specified members."""
    stream = io.BytesIO()
    Presentation().save(stream)
    with zipfile.ZipFile(stream) as source:
        entries = {name: source.read(name) for name in source.namelist()}
    entries.update(changes or {})
    return archive(entries.items())


def archive(items):
    """Write arbitrary fixture members without filesystem extraction."""
    stream = io.BytesIO()
    with warnings.catch_warnings(), zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as target:
        warnings.simplefilter('ignore', UserWarning)
        for name, content in items:
            target.writestr(name, content)
    return stream.getvalue()


class PackageGuardTests(unittest.TestCase):
    """Package bytes are bounded and relationships cannot escape to files."""

    def test_real_package(self):
        result = inspect_package(package())
        self.assertEqual(result['structural_validation'], 'passed')
        self.assertGreater(result['part_count'], 10)
        self.assertEqual(result['visual_validation'], 'not_run')

    def test_unsafe_names_and_duplicates(self):
        for name in ('../outside', '/outside', 'ppt/%2foutside', 'ppt/../outside'):
            with self.subTest(name=name), self.assertRaises(ContractError):
                inspect_package(package({name: b'bad'}))
        with self.assertRaisesRegex(ContractError, 'Duplicate'):
            inspect_package(archive([('x', b'1'), ('x', b'2')]))

    def test_utf16_entity_rejected(self):
        payload = '<!DOCTYPE a [<!ENTITY x "secret">]><a>&x;</a>'.encode('utf-16')
        with self.assertRaises(ContractError):
            inspect_package(package({'extra.xml': payload}))

    def test_external_resource_rejected_but_passive_hyperlink_preserved(self):
        namespace = 'http://schemas.openxmlformats.org/package/2006/relationships'
        office = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
        for kind, target, accepted in (('hyperlink', 'https://example.com', True),
                                        ('image', 'https://example.com/image.png', False),
                                        ('hyperlink', 'file:///secret', False)):
            xml = (f'<Relationships xmlns="{namespace}"><Relationship Id="rId1" '
                   f'Type="{office}/{kind}" Target="{target}" TargetMode="External"/></Relationships>').encode()
            with self.subTest(kind=kind, target=target):
                if accepted:
                    self.assertEqual(inspect_package(package({'extra/data.xml': b'<data/>', 'extra/_rels/data.xml.rels': xml}))['structural_validation'], 'passed')
                else:
                    with self.assertRaises(ValueError):
                        inspect_package(package({'extra/data.xml': b'<data/>', 'extra/_rels/data.xml.rels': xml}))

    def test_nested_budget_and_opaque_zip(self):
        payload = archive([('plain.txt', b'attachment')])
        self.assertEqual(inspect_package(package({'ppt/embeddings/attachment.zip': payload}))['nested_archives'], 1)
        for _ in range(4):
            payload = archive([('nested.zip', payload)])
        with self.assertRaisesRegex(ContractError, 'nesting'):
            inspect_package(package({'ppt/embeddings/attachment.zip': payload}))


if __name__ == '__main__':
    unittest.main()
