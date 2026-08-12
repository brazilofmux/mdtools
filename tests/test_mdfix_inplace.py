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


def _require_fresh_binary() -> None:
    """
    Fail loudly if mdfix is older than its source.

    These tests shell out to whatever binary sits at that path. `make test`
    rebuilds first, but running unittest directly does not — and switching
    branches in this repo leaves a stale binary, since mdfix.c is generated
    and committed. A stale binary gives false greens *and* false reds; the
    latter cost real time chasing a phantom bug during review.
    """
    if not MDFIX.is_file():
        raise unittest.SkipTest(f"{MDFIX} not built; run `make -C mdfix`")
    source = ROOT / "mdfix" / "mdfix.c"
    if source.is_file() and source.stat().st_mtime > MDFIX.stat().st_mtime:
        raise AssertionError(
            f"{MDFIX} is older than {source} — rebuild with `make -C mdfix` "
            "before running these tests; results against a stale binary are "
            "meaningless in both directions."
        )


def _run(args: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(MDFIX), *args],
        capture_output=True,
        text=True,
        **kwargs,
    )


class AtomicInplaceTests(unittest.TestCase):
    def setUp(self) -> None:
        _require_fresh_binary()
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

    def test_bak_is_always_the_previous_version(self) -> None:
        # `-i` documents "creates .bak backup", and the undo everyone reaches
        # for is `mv doc.md.bak doc.md`. Hunting for a free name (.bak.1,
        # .bak.2, …) kept .bak as the *oldest* preimage, so that undo silently
        # restored a version several edits stale. A backup that is not the
        # previous version is worse than none, because it looks like one.
        path = self.dir / "doc.md"
        bak = Path(str(path) + ".bak")

        for i in range(1, 5):
            path.write_text(f"* item {i}\n", encoding="utf-8")
            result = _run(["-i", "-q", str(path)])
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            self.assertEqual(path.read_text(encoding="utf-8"), f"- item {i}\n")
            # .bak always holds the immediately preceding content.
            self.assertEqual(bak.read_text(encoding="utf-8"), f"* item {i}\n")

        # No numbered ladder accumulating in the tree.
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()),
                         ["doc.md", "doc.md.bak"])

    def test_dangling_symlink_backup_is_replaced_not_followed(self) -> None:
        # lstat, not stat: a dangling symlink reports ENOENT under stat, so the
        # name looked free and the link was destroyed by the rename.
        path = self.dir / "doc.md"
        bak = Path(str(path) + ".bak")
        path.write_text("* one\n", encoding="utf-8")
        bak.symlink_to("/nonexistent/target")

        result = _run(["-i", "-q", str(path)])
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertFalse(bak.is_symlink())
        self.assertEqual(bak.read_text(encoding="utf-8"), "* one\n")

    def test_input_path_never_disappears_during_install(self) -> None:
        # The backup is hard-linked aside, not renamed, so the original inode
        # is reachable by both names until a single atomic rename swaps in the
        # new content. Rename-aside leaves a window with no file at the path.
        path = self.dir / "doc.md"
        path.write_text("* one\n", encoding="utf-8")
        result = _run(["-i", "-q", str(path)])
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertTrue(path.is_file())
        self.assertEqual(path.read_text(encoding="utf-8"), "- one\n")
        self.assertEqual(
            Path(str(path) + ".bak").read_text(encoding="utf-8"), "* one\n"
        )

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
