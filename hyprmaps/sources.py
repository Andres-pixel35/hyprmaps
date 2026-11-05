"""Read-only access to the outside world: config files and dump commands.

Everything that touches the disk or runs a program goes through here, so the
read-only guarantee is easy to audit:
- files are opened for reading only, with a size cap and a binary check;
- commands run without a shell, with a timeout, and (for "default" dumps) in
  an environment whose HOME/XDG dirs point at a throw-away temp directory so
  a shell or app cannot write history, caches or state into the user's home.
"""

import contextlib
import fnmatch
import glob
import itertools
import json
import os
import re
import shutil
import signal
import struct
import subprocess
import tempfile
import threading
import zlib
from pathlib import Path

MAX_FILE_BYTES = 2 * 1024 * 1024  # a keymap file larger than this is not a config
MAX_DATA_BYTES = 32 * 1024 * 1024  # browser prefs and jar members can be bigger
MAX_FILES = 300                   # cap for include chains / recursive globs
TIMEOUT = 4                       # seconds per dump command

HOME = Path.home()

# While the opt-in cache records a reader (cache.py), TRACK.rec is its recorder in that thread.
TRACK = threading.local()


def xdg(name: str, fallback: str) -> Path:
    v = os.environ.get(name)
    return Path(v) if v else HOME / fallback


CONFIG = xdg("XDG_CONFIG_HOME", ".config")


def expand(path: str, base: Path | None = None, extra_vars=None) -> list[Path]:
    """Expand ~, $VARS and globs; relative paths resolve against ``base``.
    Unknown variables make the path unusable, so it expands to nothing."""
    s = path.strip().strip("'\"")
    if not s:
        return []
    env = dict(os.environ)
    env.setdefault("XDG_CONFIG_HOME", str(CONFIG))
    env.setdefault("ZDOTDIR", str(HOME))
    env.update(extra_vars or {})

    def var(m):
        name = m.group(1) or m.group(2)
        if name not in (extra_vars or {}):
            getenv(name)  # recorded for the cache
        return env.get(name, "\0")

    s = re.sub(r"\$\{(\w+)(?::-[^}]*)?\}|\$(\w+)", var, s)
    if "\0" in s:
        return []
    s = os.path.expanduser(s)
    p = Path(s)
    if not p.is_absolute():
        if base is None:
            return []
        p = base / p
    if any(c in s for c in "*?["):
        return [Path(x) for x in sorted(itertools.islice(_glob(str(p)), MAX_FILES))]  # lazy: the cap ends the walk
    return [p]


def _glob(pattern: str):
    """glob.iglob(pattern, recursive=True), except that a '**' walks each real folder once:
    a symlink loop ends (glob follows it until the path is too long, 2^depth paths for two
    loops). The only other difference: a folder linked twice in the tree is listed once."""
    parts = pattern.split(os.sep)
    if "**" not in parts:
        yield from glob.iglob(pattern)
        return
    i = parts.index("**")
    head, rest = os.sep.join(parts[:i]) or os.sep, os.sep.join(parts[i + 1:])
    for base in glob.iglob(head) if any(c in head for c in "*?[") else [head]:
        seen = set()
        for d, dirs, files in os.walk(base, followlinks=True):
            try:
                st = os.stat(d)
            except OSError:
                dirs.clear()
                continue
            if (st.st_dev, st.st_ino) in seen:
                dirs.clear()
                continue
            seen.add((st.st_dev, st.st_ino))
            names = dirs + files
            dirs[:] = [x for x in dirs if not x.startswith(".")]  # like glob: ** skips hidden names
            if rest and os.sep not in rest and rest != "**":  # '**/*.conf': match this listing instead of listing d again
                if not any(c in rest for c in "*?["):
                    yield from [os.path.join(d, rest)] if rest in names else []
                else:
                    shown = names if rest.startswith(".") else [n for n in names if not n.startswith(".")]
                    yield from (os.path.join(d, n) for n in fnmatch.filter(shown, rest))
            elif rest:
                yield from _glob(os.path.join(glob.escape(d), rest))
            elif parts[i + 1:] == [""]:  # '**/': folders only, each with a trailing slash
                yield os.path.join(d, "")
            else:
                yield os.path.join(d, "") if d == base else d
                yield from (os.path.join(d, f) for f in files if not f.startswith("."))


def read_text(path: Path, limit: int = MAX_FILE_BYTES) -> str | None:
    """Return the file text, or None for missing, huge, binary or unreadable files.
    ``limit`` is raised only for app state files (browser prefs) that are bigger."""
    try:
        if not path.is_file():  # first: opening a FIFO would block
            return None
        with open(path, "rb") as f:
            if os.fstat(f.fileno()).st_size > limit:  # the opened file: it may have been swapped since is_file
                return None
            data = f.read()
    except OSError:
        return None
    if b"\0" in data[:4096]:
        return None
    return data.decode("utf-8", errors="replace")


def read_json(path: Path, limit: int = MAX_DATA_BYTES):
    """Parsed JSON of a file, or None when missing, unreadable or invalid."""
    text = read_text(path, limit)
    try:
        return json.loads(text) if text else None
    except ValueError:
        return None


class Jar:
    """Read-only access to the members of a zip file: extensions (.xpi) and
    Mozilla's omni.ja. omni.ja keeps its central directory at the start of the
    file, which Python's zipfile rejects, so the directory is read from the
    offset the end record gives. Members are decompressed one at a time, with
    a size cap. Use as a context manager."""

    def __init__(self, path: Path):
        self.members: dict[str, tuple[int, int, int, int]] = {}  # name -> method, csize, usize, offset
        self._f = None
        try:
            self._f = open(path, "rb")  # noqa: SIM115 - closed in __exit__
            self._index()
        except (OSError, ValueError, struct.error):
            self.members = {}

    def _index(self):
        f = self._f
        size = f.seek(0, 2)
        tail = min(size, 22 + 0xFFFF)  # end record + longest possible comment
        f.seek(size - tail)
        buf = f.read(tail)
        i = buf.rfind(b"PK\x05\x06")
        if i < 0 or len(buf) - i < 22:
            raise ValueError("no zip end record")
        _, _, _, _, count, cd_size, cd_off, _ = struct.unpack("<4sHHHHIIH", buf[i:i + 22])
        if cd_off + cd_size > size:
            raise ValueError("bad central directory")
        f.seek(cd_off)
        cd = f.read(cd_size)
        pos = 0
        for _ in range(count):
            if cd[pos:pos + 4] != b"PK\x01\x02":
                raise ValueError("bad central directory entry")
            method, csize, usize, nl, xl, cl, off = struct.unpack("<10xH8xIIHHH8xI", cd[pos:pos + 46])
            name = cd[pos + 46:pos + 46 + nl].decode("utf-8", "replace")
            self.members[name] = (method, csize, usize, off)
            pos += 46 + nl + xl + cl

    def read(self, name: str) -> str | None:
        """Text of one member, or None if absent, too big, or not deflate/stored."""
        info = self.members.get(name)
        if not info or info[2] > MAX_DATA_BYTES or info[0] not in (0, 8):
            return None
        method, csize, _, off = info
        try:
            self._f.seek(off)
            head = self._f.read(30)
            if head[:4] != b"PK\x03\x04":
                return None
            nl, xl = struct.unpack("<HH", head[26:30])
            self._f.seek(off + 30 + nl + xl)
            data = self._f.read(csize)
            if method == 8:
                data = zlib.decompressobj(-15).decompress(data, MAX_DATA_BYTES)
        except (OSError, zlib.error, struct.error):
            return None
        return data.decode("utf-8", "replace")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        if self._f:
            self._f.close()


def pak_find(path: Path, marker: bytes) -> str | None:
    """Text of the first resource of a Chromium .pak file whose first bytes
    contain ``marker``. Resources are stored plain or gzipped; only the first
    bytes of each are inflated until the one that matches."""
    try:
        with open(path, "rb") as f:
            head = f.read(12)
            version = struct.unpack("<I", head[:4])[0]
            if version == 5:
                count, pos = struct.unpack("<8xH", head[:10])[0], 12
            elif version == 4:
                count, pos = struct.unpack("<4xI", head[:8])[0], 9
            else:
                return None
            f.seek(pos)
            table = f.read(6 * (count + 1))
            offsets = [struct.unpack("<2xI", table[i:i + 6])[0] for i in range(0, len(table), 6)]
            for start, end in zip(offsets, offsets[1:], strict=False):
                if end <= start:  # a damaged entry: read(-1) reads to EOF, below -1 it raises and ends the lookup
                    continue
                f.seek(start)
                first = f.read(min(end - start, 512))
                gz = first[:2] == b"\x1f\x8b"
                try:
                    if marker not in (zlib.decompressobj(31).decompress(first, 256) if gz else first[:256]):
                        continue
                except zlib.error:  # a damaged resource is skipped, not fatal
                    continue
                f.seek(start)
                data = f.read(end - start)
                data = zlib.decompressobj(31).decompress(data, MAX_DATA_BYTES) if gz else data
                return data.decode("utf-8", "replace")
    except (OSError, ValueError, struct.error, zlib.error):
        return None
    return None



def read_tree(start: Path, include_re: re.Pattern | None, extra_vars=None):
    """Yield (path, lines) for a config file and every file it includes.

    ``include_re`` captures the included path in group 'path'; relative paths
    resolve against the including file's directory. Cycles and runaway chains
    are cut by MAX_FILES.
    """
    seen, todo = set(), [start]
    while todo and len(seen) < MAX_FILES:
        path = todo.pop(0)
        try:
            key = path.resolve()
        except OSError:
            continue
        if key in seen:
            continue
        seen.add(key)
        text = read_text(path)
        if text is None:
            continue
        lines = text.splitlines()
        yield path, lines
        if include_re is None:
            continue
        for line in lines:
            m = include_re.match(line)
            if m:
                todo.extend(expand(m.group("path"), path.parent, extra_vars))


def which(*names: str) -> str | None:
    found = next((p for p in map(shutil.which, names) if p), None)
    rec = getattr(TRACK, "rec", None)
    if rec is not None:
        rec.which(names, found)
    return found


def getenv(name: str) -> str | None:
    """os.environ.get for readers: the opt-in cache records the variables a reader depends on."""
    value = os.environ.get(name)
    rec = getattr(TRACK, "rec", None)
    if rec is not None:
        rec.env(name, value)
    return value


_SANDBOX_VARS = {
    # everything: the command sees an empty home (used to print an app's defaults)
    True: ("HOME", "ZDOTDIR", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"),
    # state only: the user config and plugins load, but state/cache writes land in the temp dir
    "state": ("XDG_STATE_HOME", "XDG_CACHE_HOME"),
}


def run(argv: list[str], sandbox: bool | str = False) -> str | None:
    """Run a dump command and return stdout, or None on any failure.

    ``sandbox=True`` points HOME and the XDG dirs at a temp dir that is deleted
    afterwards, for commands whose job is to print an app's *defaults*: they
    then cannot read the user's config nor write history/state anywhere real.
    ``sandbox="state"`` redirects only the state and cache dirs.
    ``{tmp}`` in an argument is replaced by that temp dir (e.g. a tmux socket).
    """
    if not which(argv[0]):
        return None
    rec = getattr(TRACK, "rec", None)
    TRACK.rec = None  # the command's temp dir and pipes are not inputs: the cache records the command itself
    try:
        out = _run(argv, sandbox)
    finally:
        TRACK.rec = rec
    if rec is not None:
        rec.command(argv, sandbox, out)
    return out


def _run(argv: list[str], sandbox: bool | str) -> str | None:
    env = dict(os.environ)
    env["HISTFILE"] = "/dev/null"
    tmp = tempfile.TemporaryDirectory(prefix="hyprmaps-") if sandbox else None
    try:
        if tmp:
            for var in _SANDBOX_VARS[sandbox]:
                env[var] = tmp.name
            argv = [a.replace("{tmp}", tmp.name) for a in argv]
        # Own process group: a timeout (or Ctrl+C) kills the command and every child it started,
        # not only the command as subprocess.run does (a plugin's jobs would go on writing).
        with subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
                              stdin=subprocess.DEVNULL, errors="replace", start_new_session=True,
                              cwd=tmp.name if tmp else None) as proc:
            try:
                out, _ = proc.communicate(timeout=TIMEOUT)
            finally:
                if proc.poll() is None:  # still running, not reaped: the group id is surely ours
                    with contextlib.suppress(OSError):
                        os.killpg(proc.pid, signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        return None
    finally:
        if tmp:
            tmp.cleanup()
    return out if proc.returncode == 0 else None


def rel(path: Path) -> str:
    """Path shown to the user: '~/…' instead of the full home path."""
    s = str(path)
    h = str(HOME)
    return "~" + s[len(h):] if s == h or s.startswith(h + "/") else s
