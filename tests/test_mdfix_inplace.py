"""Atomic, metadata-preserving mdfix -i writes (issue #7)."""

from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MDFIX = ROOT / "mdfix" / "mdfix"


def _run(args: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(MDFIX), *args],
        capture_output=True,
        text=True,
        **kwargs,
    )


class AtomicInplaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def test_mode_bits_are_preserved(self) -> None:
        # Old path: rename aside + fopen("w") created a 0644 file from a 0600.
        path = self.dir / "secret.md"
        path.write_text("* item\n", encoding="utf-8")  # * → - is always-on fix
        os.chmod(path, 0o600)
        before_mode = stat.S_IMODE(path.stat().st_mode)

        result = _run(["-i", "-q", str(path)])
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        after_mode = stat.S_IMODE(path.stat().st_mode)
        self.assertEqual(after_mode, before_mode)
        self.assertEqual(after_mode, 0o600)
        self.assertEqual(path.read_text(encoding="utf-8"), "- item\n")

    def test_unchanged_file_leaves_no_backup(self) -> None:
        path = self.dir / "clean.md"
        path.write_text("- already fine\n", encoding="utf-8")
        result = _run(["-i", "-q", str(path)])
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(path.read_text(encoding="utf-8"), "- already fine\n")
        self.assertFalse((self.dir / "clean.md.bak").exists())
        # No leftover temps either.
        leftovers = list(self.dir.glob("*.mdfix.*"))
        self.assertEqual(leftovers, [])

    def test_backup_holds_preimage_when_content_changes(self) -> None:
        path = self.dir / "doc.md"
        original = "* bullet\n"
        path.write_text(original, encoding="utf-8")
        result = _run(["-i", "-q", str(path)])
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(path.read_text(encoding="utf-8"), "- bullet\n")
        bak = Path(str(path) + ".bak")
        self.assertTrue(bak.is_file())
        self.assertEqual(bak.read_text(encoding="utf-8"), original)

    def test_existing_bak_is_not_clobbered(self) -> None:
        path = self.dir / "doc.md"
        bak = Path(str(path) + ".bak")
        path.write_text("* one\n", encoding="utf-8")
        bak.write_text("PREEXISTING BACKUP\n", encoding="utf-8")

        result = _run(["-i", "-q", str(path)])
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        # Old .bak untouched.
        self.assertEqual(bak.read_text(encoding="utf-8"), "PREEXISTING BACKUP\n")
        # New backup under a free name.
        bak1 = Path(str(path) + ".bak.1")
        self.assertTrue(bak1.is_file())
        self.assertEqual(bak1.read_text(encoding="utf-8"), "* one\n")
        self.assertEqual(path.read_text(encoding="utf-8"), "- one\n")

    def test_original_survives_when_directory_not_writable(self) -> None:
        # mkstemp cannot create a temp beside the file → fail before touching
        # the primary path. The original bytes and mode must remain.
        path = self.dir / "doc.md"
        original = "* keep me\n"
        path.write_text(original, encoding="utf-8")
        os.chmod(path, 0o640)
        os.chmod(self.dir, 0o555)
        try:
            result = _run(["-i", "-q", str(path)])
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(path.read_text(encoding="utf-8"), original)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o640)
            self.assertFalse(Path(str(path) + ".bak").exists())
        finally:
            os.chmod(self.dir, 0o755)

    def test_failed_open_does_not_leave_tmp_for_missing_input(self) -> None:
        missing = self.dir / "gone.md"
        _run(["-i", "-q", str(missing)])
        self.assertEqual(list(self.dir.glob("*")), [])


if __name__ == "__main__":
    unittest.main()
