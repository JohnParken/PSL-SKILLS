"""Integration of independent creation verification with the publication boundary."""

import base64
from copy import deepcopy
from io import BytesIO
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tl_create
import tl_docx
from tl_create_verify import CreationMismatch

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j5WQAAAAASUVORK5CYII=')


class CreationPublicationGates(unittest.TestCase):
    """Faults in the generator cannot corrupt the independent input snapshot."""

    def test_input_mutation_and_omitted_list_do_not_publish(self):
        original = tl_create.build_document
        spec = {'blocks': [{'type': 'paragraph', 'text': 'kept'}, {'type': 'list', 'items': ['must remain']}]}
        def broken(value, reader):
            value['blocks'].pop()
            return original(value, reader)
        with tempfile.TemporaryDirectory(prefix='tl-create-publication-') as directory:
            root = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                stdin = SimpleNamespace(buffer=BytesIO(json.dumps(spec).encode()))
                args = tl_docx.arguments(['create', 'result.docx'])
                with patch.object(tl_docx.sys, 'stdin', stdin), patch.object(tl_create, 'build_document', broken), patch.object(tl_docx, 'finish') as finish:
                    with self.assertRaises(CreationMismatch):
                        tl_docx.write_document(root, args, None)
                    finish.assert_not_called()
                self.assertEqual(list(Path(directory).iterdir()), [])
            finally:
                os.close(root)

    def test_omitted_image_still_has_independent_input_snapshot(self):
        original = tl_create.build_document
        spec = {'blocks': [{'type': 'image', 'path': 'pixel.png', 'alt': 'required'}]}
        before = deepcopy(spec)
        reads = []
        def reader(path):
            reads.append(path)
            return PNG
        def broken(value, read_image):
            value['blocks'] = []
            return original(value, read_image)
        with patch.object(tl_create, 'build_document', broken), self.assertRaises(CreationMismatch):
            tl_docx.create_verified_bytes(spec, reader)
        self.assertEqual(reads, ['pixel.png'])
        self.assertEqual(spec, before)

    def test_repeated_images_share_immutable_bytes_not_reopened_files(self):
        spec = {'blocks': [{'type': 'image', 'path': 'pixel.png'},
                           {'type': 'table', 'rows': [[{'image': {'path': 'pixel.png', 'width_mm': 5, 'height_mm': 5, 'alt': 'cell image'}}]]}]}
        reader = unittest.mock.Mock(return_value=PNG)
        data, details = tl_docx.create_verified_bytes(spec, reader)
        self.assertTrue(data.startswith(b'PK'))
        reader.assert_called_once_with('pixel.png')
        self.assertEqual(details['content_verification']['status'], 'verified')

    def test_valid_input_is_verified_before_publication(self):
        spec = {'blocks': [{'type': 'title', 'text': 'Synthetic report'}]}
        with tempfile.TemporaryDirectory(prefix='tl-create-publication-') as directory:
            root = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                stdin = SimpleNamespace(buffer=BytesIO(json.dumps(spec).encode()))
                with patch.object(tl_docx.sys, 'stdin', stdin), patch.object(tl_docx, 'finish', return_value=('published', 0)) as finish:
                    self.assertEqual(tl_docx.write_document(root, tl_docx.arguments(['create', 'result.docx']), None), ('published', 0))
                    self.assertEqual(finish.call_args.args[2]['content_verification']['status'], 'verified')
            finally:
                os.close(root)


if __name__ == '__main__':
    unittest.main()
