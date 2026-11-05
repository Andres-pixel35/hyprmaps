"""The opt-in cache: an app is reloaded exactly when one of its inputs changed."""

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hyprmaps import cache, sources  # noqa: E402
from hyprmaps.apps.base import App  # noqa: E402
from hyprmaps.model import Keymap  # noqa: E402

OLD = time.time_ns() - 10 * 10**9  # outside the window where a just-saved file is not cached yet


class CacheTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        patch = mock.patch.object(cache, "FILE", self.dir / "cache" / "keymaps.json")
        patch.start()
        self.addCleanup(patch.stop)
        (self.dir / "inc").mkdir()
        self.conf = self.write("app.conf", "a")
        self.cfg = {"apps": {}, "defaults": True}
        self.calls = 0

    def write(self, name, text, recent=False):
        p = self.dir / name
        p.write_text(text)
        if not recent:
            os.utime(p, ns=(OLD, OLD))
        return p

    def reader(self, ctx):
        """Reads its file, probes a file that may not exist, globs a folder, reads a variable, finds a program."""
        self.calls += 1
        extra = sources.read_text(self.dir / "extra.conf")
        incs = list((self.dir / "inc").glob("*.conf"))
        found = sources.which("sh"), sources.getenv("HYPRMAPS_TEST")
        return [Keymap(sources.read_text(ctx.paths[0]), f"{len(incs)} {extra} {found}")]

    def run_app(self, reader=None):
        """One hyprmaps run: open the cache, load the app, save."""
        c = cache.Cache()
        keymaps, _ = c.load(App("t", "T", "other", reader or self.reader, paths=(str(self.conf),)), self.cfg)
        c.save()
        return keymaps

    def test_unchanged_inputs_are_not_read_again(self):
        first = self.run_app()
        self.assertEqual(self.run_app(), first)
        self.assertEqual(self.calls, 1)
        self.assertEqual(cache.FILE.stat().st_mode & 0o777, 0o600)

    def test_each_kind_of_change_reloads(self):
        changes = {
            # same size and mtime: only the ctime tells
            "edited file": lambda: self.write("app.conf", "b"),
            "file created where the reader looked": lambda: self.write("extra.conf", "x"),
            "file added to a globbed folder": lambda: self.write("inc/new.conf", ""),
            "setting changed": lambda: self.cfg.update(defaults=False),
            "variable it reads changed": lambda: os.environ.update(HYPRMAPS_TEST="1"),
            "PATH finds another program": lambda: os.environ.update(PATH=str(self.dir)),
        }
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        self.write("sh", "")
        (self.dir / "sh").chmod(0o755)
        for name, change in changes.items():
            with self.subTest(name):
                self.run_app()
                calls = self.calls
                change()
                self.run_app()
                self.assertEqual(self.calls, calls + 1)
        self.assertEqual(self.run_app()[0].key, "b")

    def test_other_launch_environment_still_hits(self):
        """A Hyprland bind and a terminal differ in PATH and in variables no reader reads."""
        self.run_app()
        with mock.patch.dict(os.environ, PATH=os.environ["PATH"] + ":" + str(self.dir), EDITOR="other"):
            self.run_app()
        self.assertEqual(self.calls, 1)

    def test_live_command_output_is_checked(self):
        state = self.write("state", "1")

        def reader(ctx):
            self.calls += 1
            return [Keymap(sources.run(["cat", str(state)]) or "")]

        self.run_app(reader)
        self.run_app(reader)
        self.assertEqual(self.calls, 1)
        self.write("state", "2")  # only `cat` reads it: the cache sees it by running the command again
        self.assertEqual(self.run_app(reader)[0].key, "2")
        self.assertNotIn(str(state), cache._read()["apps"]["t"]["deps"]["files"])

    def test_not_cached(self):
        def failing_defaults(ctx):
            self.calls += 1
            return [Keymap(sources.run(["false"], sandbox=True) or "")]

        cases = {
            "failed defaults dump": (failing_defaults, lambda: None),
            "file saved just now": (None, lambda: self.write("app.conf", "c", recent=True)),
        }
        for name, (reader, setup) in cases.items():
            with self.subTest(name):
                setup()
                calls = self.calls
                self.run_app(reader)
                self.run_app(reader)
                self.assertEqual(self.calls, calls + 2)

    def test_cache_of_someone_else_is_ignored(self):
        self.run_app()
        cache.FILE.chmod(0o644)
        self.run_app()
        self.assertEqual(self.calls, 2)

    def test_dir_others_can_write_in_is_not_used(self):
        self.run_app()
        cache.FILE.parent.chmod(0o770)
        before = cache.FILE.stat().st_mtime_ns
        with mock.patch("sys.stderr"):
            self.run_app()
            self.run_app()
        self.assertEqual(self.calls, 3)  # not read
        self.assertEqual(cache.FILE.stat().st_mtime_ns, before)  # not written


if __name__ == "__main__":
    unittest.main()
