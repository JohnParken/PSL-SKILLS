"""Isolated publication failures, competition, and owned-child SIGKILL tests."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]

import errno
import hashlib
import json
import os
import selectors
import signal
import stat
import subprocess
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch

from pptx_core import publication


SCRIPTS = Path(__file__).resolve().parents[1]
PAYLOAD = b"publication-payload:" + bytes(range(256)) * 1024


class PublicationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="psl-publication-test-")
        self.root = Path(self.temporary.name)
        self.parent = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        self.events = []

    def tearDown(self) -> None:
        os.close(self.parent)
        self.temporary.cleanup()

    def publish(self, data: bytes = PAYLOAD) -> publication.PublicationReceipt:
        return publication.publish_bytes(self.parent, "deck.pptx", data, register=self.events.append)

    def test_registration_precedes_creation_and_success_has_only_complete_final(self) -> None:
        def register(event: dict) -> None:
            self.assertEqual(list(self.root.iterdir()), [])
            self.assertEqual(event["event"], "planned")
            self.events.append(event)

        receipt = publication.publish_bytes(self.parent, "deck.pptx", PAYLOAD, register=register)
        self.assertEqual(len(self.events), 1)
        self.assertEqual((self.root / "deck.pptx").read_bytes(), PAYLOAD)
        self.assertEqual({item.name for item in self.root.iterdir()}, {"deck.pptx"})
        self.assertTrue(receipt.published)
        self.assertTrue(receipt.cleanup_complete)
        self.assertEqual(receipt.temporary_files, 0)
        self.assertIsNone(receipt.journal)
        info = (self.root / "deck.pptx").stat()
        self.assertEqual(receipt.identity, (info.st_dev, info.st_ino))
        self.assertEqual(stat.S_IMODE(info.st_mode), 0o600)
        self.assertEqual(receipt.as_dict()["sha256"], hashlib.sha256(PAYLOAD).hexdigest())
        self.assertEqual(receipt.as_dict()["expected_bytes"], len(PAYLOAD))

    def test_registration_failure_creates_nothing(self) -> None:
        def refused(_event: dict) -> None:
            raise OSError("registration unavailable")

        with self.assertRaises(publication.PublicationError) as caught:
            publication.publish_bytes(self.parent, "deck.pptx", PAYLOAD, register=refused)
        self.assertFalse(caught.exception.receipt.published)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_existing_target_is_untouched_without_temporary_creation(self) -> None:
        (self.root / "deck.pptx").write_bytes(b"original")
        with self.assertRaises(publication.PublicationError) as caught:
            self.publish()
        self.assertEqual(caught.exception.code, "output_exists")
        self.assertEqual((self.root / "deck.pptx").read_bytes(), b"original")
        self.assertEqual(self.events, [])
        self.assertEqual({item.name for item in self.root.iterdir()}, {"deck.pptx"})

    def test_existing_target_symlink_is_not_followed(self) -> None:
        (self.root / "original").write_bytes(b"protected")
        (self.root / "deck.pptx").symlink_to("original")
        with self.assertRaises(publication.PublicationError) as caught:
            self.publish()
        self.assertEqual(caught.exception.code, "output_exists")
        self.assertTrue((self.root / "deck.pptx").is_symlink())
        self.assertEqual((self.root / "original").read_bytes(), b"protected")

    def test_concurrent_publishers_have_one_complete_winner(self) -> None:
        barrier = threading.Barrier(2)
        alternatives = (PAYLOAD + b"A", PAYLOAD + b"B")

        def attempt(data: bytes):
            try:
                return publication.publish_bytes(self.parent, "deck.pptx", data, register=lambda _event: barrier.wait(timeout=5))
            except publication.PublicationError as error:
                return error

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(attempt, alternatives))
        self.assertEqual(sum(isinstance(item, publication.PublicationReceipt) for item in results), 1)
        failed = next(item for item in results if isinstance(item, publication.PublicationError))
        self.assertEqual(failed.code, "output_exists")
        self.assertTrue(failed.receipt.cleanup_complete)
        self.assertIn((self.root / "deck.pptx").read_bytes(), alternatives)
        self.assertEqual({item.name for item in self.root.iterdir()}, {"deck.pptx"})

    def test_partial_write_exception_cleans_only_this_attempt(self) -> None:
        original = publication._write_all
        (self.root / "unrelated.tmp").write_bytes(b"keep")

        def broken(descriptor: int, data: bytes) -> None:
            if data == PAYLOAD:
                os.write(descriptor, data[:512])
                raise OSError("injected partial write")
            original(descriptor, data)

        with patch.object(publication, "_write_all", broken):
            with self.assertRaises(publication.PublicationError) as caught:
                self.publish()
        self.assertTrue(caught.exception.receipt.cleanup_complete)
        self.assertFalse(caught.exception.receipt.published)
        self.assertEqual({item.name for item in self.root.iterdir()}, {"unrelated.tmp"})
        self.assertEqual((self.root / "unrelated.tmp").read_bytes(), b"keep")

    def test_short_writes_are_completed(self) -> None:
        original = os.write

        def short(descriptor: int, data) -> int:
            return original(descriptor, data[:max(1, len(data) // 2)])

        with patch.object(publication.os, "write", short):
            receipt = self.publish()
        self.assertTrue(receipt.cleanup_complete)
        self.assertEqual((self.root / "deck.pptx").read_bytes(), PAYLOAD)

    def test_staging_identity_change_is_retained_and_reported(self) -> None:
        original = publication._verify_contents

        def replace_stage(owned, data: bytes) -> None:
            original(owned, data)
            path = self.root / owned.name
            path.unlink()
            path.write_bytes(b"foreign replacement")

        with patch.object(publication, "_verify_contents", replace_stage):
            with self.assertRaises(publication.PublicationError) as caught:
                self.publish()
        receipt = caught.exception.receipt
        event = self.events[0]
        self.assertEqual(caught.exception.code, "publication_identity_changed")
        self.assertFalse(receipt.cleanup_complete)
        self.assertEqual(set(receipt.retained), {event["staging"], event["journal"]})
        self.assertEqual((self.root / event["staging"]).read_bytes(), b"foreign replacement")
        self.assertFalse((self.root / "deck.pptx").exists())

    def test_journal_identity_change_is_not_removed(self) -> None:
        original = publication._verify_contents

        def replace_journal(owned, data: bytes) -> None:
            original(owned, data)
            path = self.root / self.events[0]["journal"]
            path.unlink()
            path.write_bytes(b"foreign control file")

        with patch.object(publication, "_verify_contents", replace_journal):
            with self.assertRaises(publication.PublicationError) as caught:
                self.publish()
        receipt = caught.exception.receipt
        self.assertFalse(receipt.cleanup_complete)
        self.assertEqual(receipt.retained, (self.events[0]["journal"],))
        self.assertEqual((self.root / receipt.journal).read_bytes(), b"foreign control file")
        self.assertFalse((self.root / "deck.pptx").exists())

    def test_unregistered_historical_name_is_not_adopted(self) -> None:
        identifier = "a" * 32
        name = "_pptx_stage_" + identifier + ".tmp"
        (self.root / name).write_bytes(b"historical")
        with patch.object(publication.uuid, "uuid4", return_value=SimpleNamespace(hex=identifier)):
            with self.assertRaises(publication.PublicationError):
                self.publish()
        self.assertEqual((self.root / name).read_bytes(), b"historical")
        self.assertEqual({item.name for item in self.root.iterdir()}, {name})
        self.assertEqual(self.events, [])

    def test_staging_sync_failure_cleans_owned_partial_items(self) -> None:
        original = os.fsync

        def fail_staging(descriptor: int) -> None:
            if os.fstat(descriptor).st_size == len(PAYLOAD):
                raise OSError("injected staging sync failure")
            original(descriptor)

        with patch.object(publication.os, "fsync", fail_staging):
            with self.assertRaises(publication.PublicationError) as caught:
                self.publish()
        self.assertTrue(caught.exception.receipt.cleanup_complete)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_post_commit_failure_reports_and_preserves_complete_final(self) -> None:
        original = os.fsync
        failed = False

        def fail_after_commit(descriptor: int) -> None:
            nonlocal failed
            if descriptor == self.parent and (self.root / "deck.pptx").exists() and not failed:
                failed = True
                raise OSError("injected post-commit directory sync failure")
            original(descriptor)

        with patch.object(publication.os, "fsync", fail_after_commit):
            with self.assertRaises(publication.PublicationError) as caught:
                self.publish()
        receipt = caught.exception.receipt
        self.assertTrue(receipt.published)
        self.assertTrue(receipt.cleanup_complete)
        self.assertEqual(receipt.final, "deck.pptx")
        self.assertEqual((self.root / "deck.pptx").read_bytes(), PAYLOAD)
        self.assertEqual(caught.exception.details["publication"]["final_identity"], list(receipt.identity))
        self.assertEqual({item.name for item in self.root.iterdir()}, {"deck.pptx"})

    def test_cleanup_failure_does_not_turn_complete_final_into_failure(self) -> None:
        original = os.unlink

        def refuse_stage(name, *args, **kwargs):
            if str(name).startswith("_pptx_stage_"):
                raise PermissionError("injected cleanup failure")
            return original(name, *args, **kwargs)

        with patch.object(publication.os, "unlink", refuse_stage):
            receipt = self.publish()
        self.assertTrue(receipt.published)
        self.assertFalse(receipt.cleanup_complete)
        self.assertEqual(receipt.temporary_files, 2)
        self.assertEqual((self.root / "deck.pptx").read_bytes(), PAYLOAD)
        records = [json.loads(line) for line in (self.root / receipt.journal).read_text(encoding="ascii").splitlines()]
        self.assertEqual(records[-1]["event"], "published")

    def test_journal_cleanup_failure_preserves_only_journal_and_final(self) -> None:
        original = os.unlink

        def refuse_journal(name, *args, **kwargs):
            if str(name).startswith("_pptx_publish_"):
                raise PermissionError("injected journal cleanup failure")
            return original(name, *args, **kwargs)

        with patch.object(publication.os, "unlink", refuse_journal):
            receipt = self.publish()
        self.assertTrue(receipt.published)
        self.assertFalse(receipt.cleanup_complete)
        self.assertEqual(receipt.retained, (self.events[0]["journal"],))
        self.assertEqual((self.root / "deck.pptx").read_bytes(), PAYLOAD)

    def test_link_unsupported_fails_without_direct_final_fallback(self) -> None:
        with patch.object(publication.os, "link", side_effect=OSError(errno.EOPNOTSUPP, "hard links unavailable")):
            with self.assertRaises(publication.PublicationError) as caught:
                self.publish()
        self.assertFalse(caught.exception.receipt.published)
        self.assertTrue(caught.exception.receipt.cleanup_complete)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_missing_descriptor_capability_creates_nothing(self) -> None:
        with patch.object(publication.os, "supports_dir_fd", set()):
            with self.assertRaises(publication.PublicationError) as caught:
                self.publish()
        self.assertEqual(caught.exception.code, "publication_unsupported")
        self.assertEqual(list(self.root.iterdir()), [])

    def test_invalid_names_cannot_escape_parent(self) -> None:
        for name in ("../outside.pptx", "a/b.pptx", "a\\b.pptx", ".", "..", "", "/absolute", "bad\x00name"):
            with self.subTest(name=name):
                with self.assertRaises(publication.PublicationError):
                    publication.publish_bytes(self.parent, name, PAYLOAD)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_every_created_item_uses_nofollow_and_the_given_directory(self) -> None:
        original = os.open
        calls = []

        def inspect_open(name, flags, mode=0o777, *, dir_fd=None):
            if flags & os.O_CREAT:
                calls.append((name, flags, dir_fd))
                self.assertEqual(dir_fd, self.parent)
                self.assertTrue(flags & os.O_NOFOLLOW)
                self.assertTrue(flags & os.O_EXCL)
                self.assertNotIn("/", name)
            return original(name, flags, mode, dir_fd=dir_fd)

        with patch.object(publication.os, "open", inspect_open):
            self.publish()
        self.assertEqual(len(calls), 2)
        self.assertNotIn("deck.pptx", {call[0] for call in calls})

    def kill_at(self, phase: str) -> tuple[dict, list[dict]]:
        child = (
            "import json, os, signal, sys\n"
            "sys.path.insert(0, sys.argv[1])\n"
            "from pptx_core import publication\n"
            "phase = sys.argv[2]\n"
            "payload = b'publication-payload:' + bytes(range(256)) * 1024\n"
            "write_all = publication._write_all\n"
            "link = os.link\n"
            "def pause():\n"
            "    print('ready', flush=True)\n"
            "    signal.pause()\n"
            "def interrupted_write(descriptor, data):\n"
            "    if phase == 'journal' or (phase == 'staging' and data == payload):\n"
            "        os.write(descriptor, data[:12 if phase == 'journal' else 512])\n"
            "        pause()\n"
            "    write_all(descriptor, data)\n"
            "def interrupted_link(*args, **kwargs):\n"
            "    link(*args, **kwargs)\n"
            "    if phase == 'published':\n"
            "        pause()\n"
            "publication._write_all = interrupted_write\n"
            "publication.os.link = interrupted_link\n"
            "parent = os.open('.', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)\n"
            "publication.publish_bytes(parent, 'deck.pptx', payload, "
            "register=lambda event: print(json.dumps(event), file=sys.stderr, flush=True))\n"
        )
        process = subprocess.Popen(
            [sys.executable, "-I", "-S", "-B", "-c", child, str(SCRIPTS), phase],
            cwd=self.root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
        )
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                self.assertTrue(selector.select(timeout=10), "owned child did not reach requested interruption point")
            self.assertEqual(process.stdout.readline().strip(), "ready")
            process.kill()
            _stdout, stderr = process.communicate(timeout=10)
            self.assertEqual(process.returncode, -signal.SIGKILL)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)
            process.stdout.close()
            process.stderr.close()
        event = json.loads(stderr)
        journal = self.root / event["journal"]
        records = [] if phase == "journal" else [json.loads(line) for line in journal.read_text(encoding="ascii").splitlines()]
        return event, records

    def test_sigkill_mid_staging_never_exposes_partial_final(self) -> None:
        event, records = self.kill_at("staging")
        self.assertFalse((self.root / "deck.pptx").exists())
        staged = self.root / event["staging"]
        self.assertEqual(staged.read_bytes(), PAYLOAD[:512])
        self.assertEqual(records[0]["event"], "planned")
        self.assertEqual(records[0]["journal"], event["journal"])
        self.assertEqual(records[1]["event"], "staging_created")
        self.assertEqual(records[1]["staging_identity"], [staged.stat().st_dev, staged.stat().st_ino])
        self.assertEqual(event["sha256"], hashlib.sha256(PAYLOAD).hexdigest())
        self.assertEqual({item.name for item in self.root.iterdir()}, {event["staging"], event["journal"]})

    def test_sigkill_during_journal_bootstrap_has_registration_and_no_staging(self) -> None:
        event, _records = self.kill_at("journal")
        self.assertEqual((self.root / event["journal"]).stat().st_size, 12)
        self.assertFalse((self.root / event["staging"]).exists())
        self.assertFalse((self.root / "deck.pptx").exists())
        self.assertEqual({item.name for item in self.root.iterdir()}, {event["journal"]})

    def test_sigkill_at_commit_preserves_complete_final_and_original_registration(self) -> None:
        event, records = self.kill_at("published")
        final = self.root / "deck.pptx"
        staged = self.root / event["staging"]
        self.assertEqual(final.read_bytes(), PAYLOAD)
        self.assertEqual(staged.read_bytes(), PAYLOAD)
        self.assertEqual(final.stat().st_ino, staged.stat().st_ino)
        self.assertEqual(records[-1]["event"], "verified")
        self.assertEqual(records[1]["staging_identity"], [final.stat().st_dev, final.stat().st_ino])


if __name__ == "__main__":
    unittest.main()
