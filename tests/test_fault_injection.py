"""
What happens when the write fails (issue #10, "fault-injected filesystem tests").

The other half of #10's coverage list. A tool that rewrites manuscripts in
place has one obligation above every other: when it cannot finish, the file
it was given must still be there, whole. These tests make writes fail and
check exactly that.

**How the fault is injected.** `RLIMIT_FSIZE` caps how large a file the
process may write, so a write past it fails with `EFBIG` — the same shape as a
full disk or an exceeded quota, without needing a real one, root, or a
platform-specific mount.

Two variants, and both are worth having:

  * **SIGXFSZ ignored** — `write` returns an error and mdfix gets to see it.
    This is "the disk filled up while we were writing".
  * **SIGXFSZ default** — the kernel kills the process mid-write. This is the
    power-failure analogue: no handler runs, nothing gets cleaned up, and the
    only thing that can protect the file is the order the writes were done in.

**A caveat that cost me an hour.** `RLIMIT_FSIZE` applies to regular files,
not to pipes. Capturing stdout with a pipe means the limit never bites and
every stdout test passes while testing nothing. The stdout cases below
redirect to a real file for exactly that reason.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

try:
    import resource
    import signal
    HAVE_RLIMIT = hasattr(resource, "RLIMIT_FSIZE")
except ImportError:                                  # pragma: no cover
    HAVE_RLIMIT = False

ROOT = Path(__file__).resolve().parents[1]
MDFIX = ROOT / "mdfix" / "mdfix"

# Comfortably larger than the cap, so the failure lands mid-document rather
# than on the first or last write.
BODY = "#Heading\n\n" + ("word " * 200 + "\n\n") * 40
CAP = 4096

needs_rlimit = unittest.skipUnless(HAVE_RLIMIT, "RLIMIT_FSIZE not available")


class FaultTestCase(unittest.TestCase):
    def setUp(self) -> None:
        if not MDFIX.is_file():
            raise unittest.SkipTest(f"{MDFIX} not built; run `make -C mdfix`")
        source = ROOT / "mdfix" / "mdfix.c"
        if source.is_file() and source.stat().st_mtime > MDFIX.stat().st_mtime:
            raise AssertionError(
                f"{MDFIX} is older than {source} — rebuild with `make -C mdfix`"
            )
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def _write(self, name: str = "a.md", text: str = BODY) -> Path:
        path = self.dir / name
        path.write_text(text, encoding="utf-8")
        return path

    def _capped(self, *args: str, stdin: bytes = None, survive: bool = True,
                stdout_to: Path = None):
        """Run mdfix with a file-size cap in force."""
        def preexec():
            resource.setrlimit(resource.RLIMIT_FSIZE, (CAP, CAP))
            if survive:
                # Let the write fail and be reported, rather than having the
                # kernel kill us before mdfix can react.
                signal.signal(signal.SIGXFSZ, signal.SIG_IGN)

        out = open(stdout_to, "wb") if stdout_to else subprocess.PIPE
        try:
            return subprocess.run([str(MDFIX), *args], stdout=out,
                                  stderr=subprocess.PIPE, input=stdin,
                                  preexec_fn=preexec)
        finally:
            if stdout_to:
                out.close()

    def _strays(self, *keep: str) -> list:
        return sorted(p.name for p in self.dir.iterdir() if p.name not in keep)


@needs_rlimit
class InPlaceTests(FaultTestCase):
    """`-i` is the path where a failure could destroy the input."""

    def test_a_failed_write_leaves_the_original_whole(self) -> None:
        path = self._write()
        before = path.read_bytes()
        result = self._capped("-q", "-i", str(path))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(path.read_bytes(), before)

    def test_a_failed_write_says_so(self) -> None:
        # The bug this file was written to find: mdfix reported success. A
        # 40090-byte manuscript came back 4096 bytes, cut mid-word, exit 0.
        # `fwrite` had failed and drained the buffer, so `fflush`, `fsync` and
        # `fclose` all succeeded and nothing consulted `ferror`.
        path = self._write()
        result = self._capped("-q", "-i", str(path))
        self.assertEqual(result.returncode, 1)
        message = result.stderr.decode().lower()
        self.assertIn("write", message)         # what failed
        self.assertIn("too large", message)     # and why, from errno

    def test_a_failed_write_leaves_no_temp_file(self) -> None:
        path = self._write()
        self._capped("-q", "-i", str(path))
        self.assertEqual(self._strays("a.md"), [])

    def test_being_killed_mid_write_still_leaves_the_original(self) -> None:
        # The power-failure case: SIGXFSZ kills the process, no handler runs,
        # no cleanup happens. Writing to a temp and renaming is what makes the
        # original survivable, and this is the test that says so.
        path = self._write()
        before = path.read_bytes()
        result = self._capped("-q", "-i", str(path), survive=False)
        self.assertLess(result.returncode, 0, "expected death by signal")
        self.assertEqual(path.read_bytes(), before)
        # A temp file may be orphaned — nothing can prevent that — but it must
        # never be at the input's own path.
        for stray in self._strays("a.md"):
            self.assertNotEqual(stray, "a.md")


@needs_rlimit
class OutputFileTests(FaultTestCase):
    def test_a_partial_output_file_is_removed(self) -> None:
        # Leaving one behind is doubly bad: mdfix refuses to overwrite an
        # existing output, so the retry fails too and the user is stuck with a
        # truncated file until they delete it by hand.
        path = self._write()
        result = self._capped("-q", str(path), str(self.dir / "out.md"))
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.dir / "out.md").exists())

    def test_the_input_is_untouched(self) -> None:
        path = self._write()
        before = path.read_bytes()
        self._capped("-q", str(path), str(self.dir / "out.md"))
        self.assertEqual(path.read_bytes(), before)


@needs_rlimit
class StdoutTests(FaultTestCase):
    """
    stdout is quieter than a file and worse in a pipeline.

    A truncated `--emit-ir` stream is still well-formed JSONL — just with
    records missing — so a consumer sees no error at all and silently believes
    a document ended early. I4.1 says the records tile the file; a short stream
    breaks that without breaking any parser.
    """

    def test_a_truncated_ir_stream_is_reported(self) -> None:
        path = self._write()
        result = self._capped("--emit-ir", str(path),
                              stdout_to=self.dir / "cap.jsonl")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"IR", result.stderr)

    def test_a_truncated_applied_document_is_reported(self) -> None:
        path = self._write()
        result = self._capped("-q", "--apply-edits", str(path), stdin=b"",
                              stdout_to=self.dir / "cap.md")
        self.assertNotEqual(result.returncode, 0)

    def test_a_truncated_diff_is_reported(self) -> None:
        # The hunk has to be larger than CAP so RLIMIT_FSIZE bites, but
        # each result line must stay under MAX_LINE or I4.3 refuses the
        # splice before print_edit_diff / finish_stdout ever run.
        path = self._write("a.md", "# T\n\nThe quick fox.\n")
        data = path.read_bytes()
        i = data.index(b"quick")
        edits = ('{"kind":"edits","schema":"mdtools-edits-1","source":"%s",'
                 '"bytes":%d}\n{"start":%d,"end":%d,"replacement":"%s"}\n'
                 % (path, len(data), i, i + 5, "slow " * 1000))
        result = self._capped("-q", "--apply-edits", "--diff", str(path),
                              stdin=edits.encode(),
                              stdout_to=self.dir / "cap.diff")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"diff", result.stderr)
        self.assertNotIn(b"cannot validate the spliced result", result.stderr)

    def test_the_pipe_caveat_is_real(self) -> None:
        # Guards the comment at the top of this file. If RLIMIT_FSIZE ever did
        # apply to pipes, the tests above could be simplified — and if someone
        # "simplifies" them without checking, this says why not.
        path = self._write()
        result = self._capped("--emit-ir", str(path))     # captured by a pipe
        self.assertEqual(result.returncode, 0,
                         "RLIMIT_FSIZE now applies to pipes; the stdout tests "
                         "no longer need a real file")


class PermissionTests(FaultTestCase):
    """Failures that need no size limit."""

    def test_a_read_only_file_is_still_rewritten(self) -> None:
        # Deliberate, and pinned so it is not mistaken for a bug. Replacement
        # is governed by write permission on the *directory*, which is how
        # rename works and how every other in-place editor behaves. The mode
        # is carried onto the new file.
        path = self._write("ro.md", "#Title\n\nbody\n")
        os.chmod(path, 0o444)
        self.addCleanup(os.chmod, path, 0o644)
        result = subprocess.run([str(MDFIX), "-q", "-i", str(path)],
                                capture_output=True)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("# Title", path.read_text(encoding="utf-8"))
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o444)

    def test_a_directory_as_output_is_refused(self) -> None:
        path = self._write("a.md", "#Title\n\nbody\n")
        (self.dir / "adir").mkdir()
        result = subprocess.run(
            [str(MDFIX), "-q", str(path), str(self.dir / "adir")],
            capture_output=True)
        self.assertNotEqual(result.returncode, 0)

    def test_a_directory_as_input_is_refused(self) -> None:
        (self.dir / "bdir").mkdir()
        result = subprocess.run([str(MDFIX), "-q", "-n", str(self.dir / "bdir")],
                                capture_output=True)
        self.assertNotEqual(result.returncode, 0)

    def test_an_empty_input_is_not_an_error(self) -> None:
        # The boundary on the other side: nothing to do is not a failure.
        result = subprocess.run([str(MDFIX), "-q", "-n", "/dev/null"],
                                capture_output=True)
        self.assertEqual(result.returncode, 0, msg=result.stderr)


if __name__ == "__main__":
    unittest.main()
