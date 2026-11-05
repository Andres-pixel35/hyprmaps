"""Terminal multiplexers: tmux and zellij."""

import re
import shlex

from ..model import SEQ, Keymap, block_comments, combo, humanize, merge, split_combo
from ..sources import CONFIG, getenv, read_text, read_tree, rel, run
from .base import App, Ctx

# ----------------------------------------------------------------- tmux --

_TMUX_NAMES = {"BSpace": "Backspace", "DC": "Delete", "IC": "Insert", "PPage": "PageUp",
               "NPage": "PageDown", "BTab": "Shift + Tab", "Escape": "Esc", "Space": "Space"}
_TMUX_SOURCE = re.compile(r"^\s*source(?:-file)?\s+(?:-[qnvF]+\s+)*(?P<path>\S+)")
_TMUX_LIST = re.compile(r"^bind-key\s+(?:-r\s+)?-T\s+(\S+)\s+(\S+)\s+(.*)$")


def tmux_key(key: str) -> str:
    """'C-M-x' -> 'Ctrl + Alt + X', 'BTab' -> 'Shift + Tab', '\\#' -> '#'."""
    key = _unescape(key)
    if not key:  # 'bind ""': no key, a row finalize drops (not a crash that hides every tmux keymap)
        return ""
    m = re.match(r"^((?:[CMS]-)*)(.+)$", key)
    mods = {"C": "Ctrl", "M": "Alt", "S": "Shift"}
    names = [mods[c] for c in m.group(1).replace("-", "")]
    base = _TMUX_NAMES.get(m.group(2), m.group(2))
    if " + " in base:  # BTab already carries its modifier
        return " + ".join(names + [base]) if names else base
    return combo(names, base)


def _tmux_section(table: str, prefix_key: str):
    """(display prefix, section) for a key table."""
    if table == "prefix":
        return prefix_key + SEQ, ""
    if table == "root":
        return "", "root (no prefix)"
    return "", table


def _tmux_desc(action: str) -> str:
    m = re.match(r"^send-keys\s+-X\s+([\w-]+)", action)
    return humanize(m.group(1)) if m else ""


def _tmux_list(text: str, notes: dict, prefix_key: str, tables, source: str, default: bool):
    out = []
    for line in text.splitlines():
        m = _TMUX_LIST.match(line)
        if not m or m.group(1) not in tables:
            continue
        table, key, action = m.group(1), _unescape(m.group(2)), m.group(3)
        if table == "root" and ("Mouse" in key or "Wheel" in key):
            pre, section = "", "mouse"
        else:
            pre, section = _tmux_section(table, prefix_key)
        out.append(Keymap(pre + tmux_key(key), _short(action),
                          notes.get((table, key), "") or _tmux_desc(action), section, source,
                          default=default))
    return out


def _unescape(key: str) -> str:
    return key[1:] if len(key) > 1 and key[0] == "\\" else key


def _short(action: str, limit: int = 160) -> str:
    return action if len(action) <= limit else action[:limit - 1] + "…"


def _tmux_notes(text: str, prefix_raw: str) -> dict:
    """Parse 'list-keys -N': '<prefix> <key>   note' lines (root keys have no prefix)."""
    notes = {}
    for line in text.splitlines():
        parts = re.split(r"\s{2,}", line.strip(), maxsplit=1)
        if len(parts) != 2:
            continue
        left, note = parts
        if left.startswith(prefix_raw + " "):
            notes[("prefix", left[len(prefix_raw) + 1:])] = note
        else:
            notes[("root", left)] = note
    return notes


def load_tmux(ctx: Ctx) -> list[Keymap]:
    static = _tmux_static(ctx)
    prefix_raw = static["prefix"]
    prefix_key = tmux_key(prefix_raw)
    mode_keys = static["mode_keys"] or ("vi" if "vi" in (getenv("VISUAL") or getenv("EDITOR") or "") else "emacs")
    tables = {"prefix", "root", "copy-mode-vi" if mode_keys == "vi" else "copy-mode"}
    ctx.notes.append(f"Prefix: {prefix_key} · copy mode: {mode_keys}")

    sandbox = ["tmux", "-f", "/dev/null", "-S", "{tmp}/s", "start-server", ";"]
    defaults = []
    if ctx.defaults or not static["binds"]:
        dkeys = run(sandbox + ["list-keys", ";", "kill-server"], sandbox=True) or ""
        dnotes = _tmux_notes(run(sandbox + ["list-keys", "-N", ";", "kill-server"], sandbox=True) or "", "C-b")
        defaults = _tmux_list(dkeys, dnotes, prefix_key, tables, "tmux defaults", True)

    live = run(["tmux", "-N", "list-keys"])  # -N: never start a server (it would run the config)
    if live:
        lnotes = _tmux_notes(run(["tmux", "-N", "list-keys", "-N"]) or "", prefix_raw)
        current = _tmux_list(live, lnotes, prefix_key, tables, "tmux (running server)", False)
        known = {(d.section, d.key, d.action) for d in defaults}
        for k in current:
            if (k.section, k.key, k.action) in known:
                k.default, k.source = True, "tmux defaults"
        ctx.notes.append("Read from the running tmux server.")
        return current if ctx.defaults else [k for k in current if not k.default]
    if static["unbind_all"]:
        defaults = []
    return merge(defaults, static["binds"])


def _tmux_static(ctx: Ctx) -> dict:
    res = {"prefix": "C-b", "mode_keys": "", "binds": [], "unbind_all": False}
    main = ctx.first()
    if not main:
        return res
    for path, lines in read_tree(main, _TMUX_SOURCE):
        comments = block_comments(lines, "#", lambda t: t.startswith(("bind", "unbind", "set")))
        for i, line in _tmux_logical_lines(lines):
            try:
                t = shlex.split(line, comments=True)
            except ValueError:
                continue
            if not t:
                continue
            cmd = t[0]
            if cmd in ("set", "set-option", "setw", "set-window-option"):
                args = [a for a in t[1:] if not a.startswith("-")]
                if len(args) >= 2 and args[0] == "prefix":
                    res["prefix"] = args[1]
                elif len(args) >= 2 and args[0] == "mode-keys":
                    res["mode_keys"] = args[1]
            elif cmd in ("bind", "bind-key", "unbind", "unbind-key"):
                k = _tmux_bind(t, res, f"{rel(path)}:{i + 1}")
                if k:
                    k.desc = k.desc or comments.get(i, "")
                    res["binds"].append(k)
    prefix_key = tmux_key(res["prefix"])
    for k in res["binds"]:  # the prefix is only known after the whole file is read
        k.key = k.key.replace("\0", prefix_key + SEQ)
    return res


def _tmux_logical_lines(lines):
    """Join '\\' continuations and '{ ... }' blocks into one command line."""
    i = 0
    while i < len(lines):
        start, buf = i, lines[i]
        while (buf.rstrip().endswith("\\") or buf.count("{") > buf.count("}")) and i + 1 < len(lines):
            i += 1
            buf = buf.rstrip().rstrip("\\") + " " + lines[i].strip()
        yield start, buf
        i += 1


def _tmux_bind(t: list[str], res: dict, source: str) -> Keymap | None:
    unbind = t[0].startswith("unbind")
    t, table, note = t[1:], "prefix", ""
    while t and t[0].startswith("-") and len(t[0]) > 1:
        opt = t.pop(0)
        if "n" in opt:
            table = "root"
        if "a" in opt and unbind:
            res["unbind_all"] = True
            return None
        if opt.endswith("T") and t:
            table = t.pop(0)
        elif opt.endswith("N") and t:
            note = t.pop(0)
    if not t:
        return None
    _, section = _tmux_section(table, "")
    key = ("\0" if table == "prefix" else "") + tmux_key(t[0])  # \0 = prefix, filled in later
    action = " ".join(t[1:])
    return Keymap(key, _short(action), note or _tmux_desc(action), section, source, removed=unbind)


# --------------------------------------------------------------- zellij --

def _kdl_strip(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", lambda m: re.sub(r"[^\n]", " ", m.group(0)), text, flags=re.S)
    return re.sub(r'("(?:[^"\\]|\\.)*")|//[^\n]*', lambda m: m.group(1) or "", text)


def _kdl_blocks(text: str, start: int = 0):
    """Yield (head, body_start, body_end) of the '{...}' nodes directly inside
    text[start:]; heads are the text before '{' on the same node."""
    i, depth, head_start, n = start, 0, start, len(text)
    while i < n:
        c = text[i]
        if c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            i = j + 1
            continue
        if c == "{":
            if depth == 0:
                head, body = text[head_start:i].strip(), i + 1
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                yield head, body, i
                head_start = i + 1
            if depth < 0:
                return
        elif depth == 0 and c in ";\n":
            head_start = i + 1
        i += 1


def _zellij_section(head: str) -> str:
    words = re.findall(r'"([^"]*)"|([\w-]+)', head)
    name = words[0][1] if words else ""
    args = [a for a, w in words[1:] if a]
    if name == "normal":
        return ""
    if name == "shared":
        return "all modes"
    if name == "shared_except":
        return "all except " + ", ".join(args)
    if name == "shared_among":
        return ", ".join(args)
    return name


def zellij_parse(text: str, source: str, default: bool):
    """-> (keymaps, clear_all, cleared_sections). Unbinds come back with removed=True."""
    text = _kdl_strip(text)
    out, clear_all, cleared = [], False, set()
    for head, b0, b1 in _kdl_blocks(text):
        if not head.startswith("keybinds"):
            continue
        clear_all = clear_all or "clear-defaults=true" in head
        body = text[b0:b1]
        for m in re.finditer(r'(?m)^\s*unbind\s+((?:"[^"]*"\s*)+)', body):  # top-level unbinds
            for key in re.findall(r'"([^"]*)"', m.group(1)):
                out.append(Keymap(split_combo(key, " "), "", "", "*", source, removed=True))
        for mhead, m0, m1 in _kdl_blocks(body):
            section = _zellij_section(mhead)
            if "clear-defaults=true" in mhead:
                cleared.add(section)
            mbody = body[m0:m1]
            for um in re.finditer(r'(?m)^\s*unbind\s+((?:"[^"]*"\s*)+)', mbody):
                for key in re.findall(r'"([^"]*)"', um.group(1)):
                    out.append(Keymap(split_combo(key, " "), "", "", section, source, removed=True))
            for bhead, a0, a1 in _kdl_blocks(mbody):
                if not bhead.startswith("bind"):
                    continue
                actions = [" ".join(a.split()).replace('"', "") for a in re.split(r"[;\n]", mbody[a0:a1]) if a.strip()]
                action = "; ".join(actions)
                line = text.count("\n", 0, b0 + m0 + a0) + 1
                for key in re.findall(r'"([^"]*)"', bhead):
                    out.append(Keymap(split_combo(key, " "), action,
                                      humanize(action) if len(actions) == 1 else "",
                                      section, source if default else f"{source}:{line}", default=default))
    return out, clear_all, cleared


def load_zellij(ctx: Ctx) -> list[Keymap]:
    user, clear_all, cleared = [], False, set()
    main = ctx.first()
    if main:
        user, clear_all, cleared = zellij_parse(read_text(main) or "", rel(main), False)
    defaults = []
    if ctx.defaults and not clear_all:
        text = run(["zellij", "setup", "--dump-config"], sandbox=True) or ""
        defaults = [d for d in zellij_parse(text, "zellij defaults", True)[0] if d.section not in cleared]
    global_unbinds = {k.key for k in user if k.removed and k.section == "*"}
    defaults = [d for d in defaults if d.key not in global_unbinds]
    return merge(defaults, [k for k in user if k.section != "*"])


APPS = [
    App("tmux", "tmux", "multiplexer", load_tmux,
        paths=("~/.tmux.conf", f"{CONFIG}/tmux/tmux.conf"), bins=("tmux",), icon="utilities-terminal"),
    App("zellij", "Zellij", "multiplexer", load_zellij,
        paths=(f"{CONFIG}/zellij/config.kdl",), bins=("zellij",), icon="utilities-terminal"),
]
