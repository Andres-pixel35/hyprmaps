"""Opt-in cache of the loaded keymaps (``cache = true`` in config.toml).

These are the only files hyprmaps ever writes: $XDG_CACHE_HOME/hyprmaps/keymaps.json,
readable by the user only, and the compiled code of hyprmaps in .../hyprmaps/pycache.
Without ``cache = true`` this module is not imported.

An app's cached keymaps are used while every input of its last load is
unchanged. The inputs are recorded while the reader runs, without the readers
knowing about it:
- every path it stats or opens, found or not, with inode, size, mtime and
  ctime: an edited, replaced, created or deleted file shows up, and so does a
  program installed or upgraded in PATH (``which`` stats every candidate);
- the names in every folder it lists, so a new file matching a glob shows up;
- the environment variables it reads and the programs ``which`` finds, so
  launching from a terminal or from a Hyprland bind (other PATH) both hit;
- the output of every command that reads live state (``hyprctl binds``,
  ``tmux -N list-keys``, live Neovim): checking runs it again. Commands that
  print built-in defaults are sandboxed, so they depend only on their program.
A check is some stat calls per app (about a millisecond) plus those commands,
and apps are checked in parallel.
"""

import hashlib
import importlib.util
import json
import os
import stat
import sys
import tempfile
import time
from dataclasses import fields
from pathlib import Path

from . import apps as registry
from .model import Keymap
from .sources import MAX_DATA_BYTES, TRACK, read_text, run, which, xdg

FILE = xdg("XDG_CACHE_HOME", ".cache") / "hyprmaps" / "keymaps.json"
# Environment that changes what every reader sees or what commands print; other variables are
# recorded where a reader reads them (sources.getenv, sources.which).
_ENV = ("HOME", "LANG", "LC_ALL")
# A file changed this recently could change again within one timestamp tick unseen: cache it next time.
_RACY_NS = 2 * 10**9
_FIELDS = [f.name for f in fields(Keymap)]
_real_stat = os.stat


def _digest(text: str | None) -> str | None:
    return None if text is None else hashlib.sha1(text.encode("utf-8", "surrogateescape")).hexdigest()


def _sig(st) -> list | str | None:
    """What tells a file's content changed. A folder only has to exist: its names are checked by _listing."""
    if st is None:
        return None
    if stat.S_ISDIR(st.st_mode):
        return "dir"
    return [st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns]


def _stat_sig(path):
    try:
        return _sig(_real_stat(path))
    except (OSError, ValueError):
        return None


def _listing(path):
    try:
        return _digest("\0".join(sorted(os.listdir(path))))
    except (OSError, ValueError):
        return None


class _Recorder:
    """The inputs of one reader run. It is sources.TRACK.rec in the thread running the reader."""

    def __init__(self):
        self.files, self.dirs, self.cmds, self.ok = {}, {}, [], True
        self.vars, self.found = {}, {}

    def file(self, path, sig):
        self.files.setdefault(os.path.abspath(os.fsdecode(path)), sig)  # the first look is the one the reader used

    def listing(self, path):
        p = os.path.abspath(os.fsdecode(path))
        if p not in self.dirs:
            self.dirs[p] = _listing(p)

    def env(self, name, value):
        self.vars.setdefault(name, value)

    def which(self, names, found):
        self.found.setdefault(names, found)

    def command(self, argv, sandbox, out):
        if sandbox is True:
            self.ok = self.ok and out is not None  # a failed or timed-out defaults dump must not stick
        else:
            self.cmds.append([argv, sandbox, _digest(out)])

    def deps(self, start_ns: int) -> dict | None:
        """The recorded inputs, or None when the result must not be cached."""
        racy = any(isinstance(s, list) and s[2] >= start_ns - _RACY_NS for s in self.files.values())
        if racy or not self.ok:
            return None
        return {"vars": self.vars, "which": [[list(n), f] for n, f in self.found.items()],
                "files": self.files, "dirs": self.dirs, "cmds": self.cmds}


def _stat(path, *args, **kwargs):
    """os.stat that records the path while a reader runs (pathlib's exists/is_file, shutil.which, ...)."""
    rec = getattr(TRACK, "rec", None)
    if (rec is None or args or isinstance(path, int) or kwargs.get("dir_fd") is not None
            or not kwargs.get("follow_symlinks", True)):
        return _real_stat(path, *args, **kwargs)
    try:
        st = _real_stat(path, **kwargs)
    except OSError:
        rec.file(path, None)
        raise
    rec.file(path, _sig(st))
    return st


def _audit(event, args):
    """Opened files and listed folders of a running reader (zip and .pak files are opened without a stat)."""
    if event not in ("open", "os.scandir", "os.listdir"):
        return
    rec = getattr(TRACK, "rec", None)
    path = args[0]
    if rec is None or path is None or isinstance(path, int):
        return
    TRACK.rec = None  # _listing below raises an audit event too
    try:
        if event == "open":
            rec.file(path, _stat_sig(path))
        else:
            rec.listing(path)
    except Exception:  # an audit hook must never break the read it watches; just do not cache
        rec.ok = False
    finally:
        TRACK.rec = rec


_installed = False


def _install():
    global _installed
    if not _installed:
        os.stat = _stat
        sys.addaudithook(_audit)
        _installed = True


def _stamp() -> str:
    """Changes when hyprmaps itself, Python or the environment of the readers changes: then nothing is reused."""
    code = sorted((str(p), _stat_sig(p)) for p in Path(__file__).parent.rglob("*.py"))
    return _digest(json.dumps([sys.version, code, [os.environ.get(v) for v in _ENV]]))


def _inputs(app, cfg: dict) -> str:
    """The settings the reader gets (see registry.load)."""
    return json.dumps([app.paths, app.bins, app.options, cfg.get("apps", {}).get(app.id, {}),
                       cfg.get("defaults", True)], sort_keys=True, default=str)


def _fresh(deps: dict) -> bool:
    """Every recorded input unchanged. Cheap checks first: live commands run only if the files match."""
    return (all(os.environ.get(n) == v for n, v in deps["vars"].items())
            and all(which(*names) == f for names, f in deps["which"])
            and all(_stat_sig(p) == s for p, s in deps["files"].items())
            and all(_listing(p) == d for p, d in deps["dirs"].items())
            and all(_digest(run(argv, sandbox)) == d for argv, sandbox, d in deps["cmds"]))


def _compile():
    """Bytecode of every loaded module, and of all of hyprmaps, into sys.pycache_prefix: bin/hyprmaps
    looks there once the cache dir exists, and then for the stdlib too. Compiling at each start
    costs more than all the readers. Up-to-date files are skipped; errors only cost speed."""
    import compileall
    files = {getattr(m, "__file__", None) for m in list(sys.modules.values())}
    files.update(str(f) for f in Path(__file__).parent.rglob("*.py"))  # modules this run did not import (ui.py)
    for f in files:
        if isinstance(f, str) and f.endswith(".py"):
            compileall.compile_file(f, quiet=2)


def _private_dir() -> bool:
    """The cache dir is the user's and no one else can write in it (bin/hyprmaps checks the same)."""
    try:
        st = _real_stat(FILE.parent)
    except OSError:
        return False
    return stat.S_ISDIR(st.st_mode) and st.st_uid == os.getuid() and not st.st_mode & 0o022


def _read() -> dict:
    """The saved cache, only if it is the user's own and private: it names commands that a check runs."""
    if not _private_dir():
        return {}
    try:
        st = _real_stat(FILE)
        if st.st_uid != os.getuid() or st.st_mode & 0o077:
            return {}
        data = json.loads(read_text(FILE, MAX_DATA_BYTES) or "{}")
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


class Cache:
    def __init__(self):
        _install()
        self.stamp = _stamp()
        data = _read()
        ok = data.get("stamp") == self.stamp and isinstance(data.get("apps"), dict)
        self.apps = data["apps"] if ok else {}
        self.changed = False

    def load(self, app, cfg: dict):
        """registry.load(app, cfg), from the cache while the app's inputs are unchanged."""
        if app.id.startswith("file:"):  # --add paths can be relative to the current folder
            return registry.load(app, cfg)
        inputs = _inputs(app, cfg)
        try:
            entry = self.apps[app.id]
            if entry["inputs"] == inputs and _fresh(entry["deps"]):
                return [Keymap(*k) for k in entry["keymaps"]], list(entry["notes"])
        except (KeyError, TypeError, ValueError):  # not cached yet, or a damaged entry
            pass
        rec, start = _Recorder(), time.time_ns()
        TRACK.rec = rec
        try:
            keymaps, notes = registry.load(app, cfg)
        finally:
            TRACK.rec = None
        deps = rec.deps(start)
        if deps is not None:
            # stored now: the caller changes the Keymap objects afterwards (descriptions.toml)
            self.apps[app.id] = {"inputs": inputs, "deps": deps, "notes": list(notes),
                                 "keymaps": [[getattr(k, f) for f in _FIELDS] for k in keymaps]}
            self.changed = True
        return keymaps, notes

    def save(self):
        """Write the cache if a reader ran, and the compiled code if it is missing or may be old."""
        if self.changed:
            self._write()
        if sys.pycache_prefix and (self.changed or not os.path.exists(importlib.util.cache_from_source(__file__))):
            _compile()

    def _write(self):
        """Atomic, so a parallel run never reads half a file."""
        tmp = None
        try:
            FILE.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            if not _private_dir():
                raise OSError(f"{FILE.parent} is not a private dir of this user")
            fd, tmp = tempfile.mkstemp(dir=FILE.parent, prefix=".keymaps-", suffix=".json")  # mode 600
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"stamp": self.stamp, "apps": self.apps}, f, separators=(",", ":"), default=str)
            os.replace(tmp, FILE)
        except (OSError, ValueError) as e:  # a cache that cannot be written only costs speed
            print(f"hyprmaps: cache not saved: {e}", file=sys.stderr)
            if tmp:
                Path(tmp).unlink(missing_ok=True)
