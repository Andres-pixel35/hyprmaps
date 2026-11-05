"""Terminal emulators: kitty, Ghostty, Alacritty, foot, WezTerm."""

import re
import tomllib
from pathlib import Path

from .. import luaparse as lua
from ..model import Keymap, block_comments, combo, merge, split_combo
from ..sources import CONFIG, expand, read_text, read_tree, rel, run
from .base import App, Ctx

# ---------------------------------------------------------------- kitty --

_KITTY_INCLUDE = re.compile(r"^\s*(?:include|globinclude)\s+(?P<path>.+?)\s*$")
_KITTY_MAP = re.compile(r"^\s*map\s+(.*)$")
_KITTY_VALUE_OPTS = {"--when-focus-on", "--mode", "--new-mode", "--on-unknown", "--on-action",
                     "--allow-fallback"}
_KITTY_DOCS = ("/usr/share/doc/kitty/kitty.conf", "/usr/share/doc/kitty-*/kitty.conf",
               "~/.local/kitty.app/share/doc/kitty/kitty.conf", "/usr/lib/kitty/kitty.conf")


def _kitty_map(rest: str):
    """'--mode=x ctrl+a new_tab' -> (key, action, mode). action None = unmap."""
    tokens, mode = rest.split(), ""
    while tokens and tokens[0].startswith("--"):
        opt = tokens.pop(0)
        name, _, val = opt.partition("=")
        if not val and name in _KITTY_VALUE_OPTS and tokens:
            val = tokens.pop(0)
        if name == "--mode":
            mode = val
    if not tokens:
        return None
    key = tokens[0]
    action = " ".join(tokens[1:]) or None
    return key, action, mode


def _kitty_key(key: str, kitty_mod: str) -> str:
    return split_combo(key.replace("kitty_mod", kitty_mod))


def load_kitty(ctx: Ctx) -> list[Keymap]:
    main = ctx.first()
    files = list(read_tree(main, _KITTY_INCLUDE)) if main else []
    kitty_mod, clear = "ctrl+shift", False
    for _, lines in files:
        for line in lines:
            m = re.match(r"^\s*kitty_mod\s+(\S+)", line)
            if m:
                kitty_mod = m.group(1)
            if re.match(r"^\s*clear_all_shortcuts\s+yes\b", line):
                clear = True
    defaults = [] if clear or not ctx.defaults else _kitty_defaults(kitty_mod)
    doc_desc = {d.action.split()[0]: d.desc for d in defaults if d.action}
    user = []
    for path, lines in files:
        comments = block_comments(lines, "#", lambda t: t.startswith(("map ", "mouse_map ")))
        for i, line in enumerate(lines):
            m = _KITTY_MAP.match(line)
            parsed = _kitty_map(m.group(1)) if m else None
            if not parsed:
                continue
            key, action, mode = parsed
            # kitty's own wording beats a comment, which is often a group header
            desc = doc_desc.get((action or "").split(" ")[0], "") or comments.get(i, "")
            user.append(Keymap(_kitty_key(key, kitty_mod), action or "", desc, mode,
                               f"{rel(path)}:{i + 1}", removed=action is None))
    return merge(defaults, user)


def _kitty_defaults(kitty_mod: str) -> list[Keymap]:
    doc = next((p for g in _KITTY_DOCS for p in expand(g) if p.is_file()), None)
    text = read_text(doc) if doc else None
    if not text:
        return []
    out, desc, active = [], "", False
    for line in text.splitlines():
        if line == "#: Keyboard shortcuts {{{":
            active = True
            continue
        if not active:
            continue
        if line.startswith("#: ") and not line.startswith("#::"):
            desc = line[3:].strip()
            continue
        m = re.match(r"^# map\s+(.*)$", line)
        if m:
            parsed = _kitty_map(m.group(1))
            if parsed and parsed[1] and not re.search(r"\b(cmd|opt)\+", parsed[0]):
                out.append(Keymap(_kitty_key(parsed[0], kitty_mod), parsed[1], desc, "",
                                  "kitty defaults", default=True))
    return out


# -------------------------------------------------------------- Ghostty --

_GHOSTTY_INCLUDE = re.compile(r"^\s*config-file\s*=\s*\??(?P<path>.+?)\s*$")
_GHOSTTY_BIND = re.compile(r"^\s*keybind\s*=\s*(.+?)\s*$")


def _ghostty_split(value: str):
    """'ctrl+shift+c=copy_to_clipboard' -> (trigger, action). '=' can be a key
    ('ctrl+=') so the separator is the first '=' not right after '+'/'>'."""
    for i, ch in enumerate(value):
        if ch == "=" and i > 0 and value[i - 1] not in "+>":
            return value[:i].strip(), value[i + 1:].strip()
    return value.strip(), ""


def _ghostty_key(trigger: str) -> str:
    prefixes = []
    while re.match(r"^(global|all|unconsumed|performable):", trigger):
        p, trigger = trigger.split(":", 1)
        prefixes.append(p)
    key = split_combo(trigger.replace("physical:", ""))
    return f"{key}  ({', '.join(prefixes)})" if prefixes else key


def load_ghostty(ctx: Ctx) -> list[Keymap]:
    user, clear = [], False
    for main in [p for p in ctx.paths if p.is_file()][:1]:
        for path, lines in read_tree(main, _GHOSTTY_INCLUDE):
            comments = block_comments(lines, "#", lambda t: t.startswith("keybind"))
            for i, line in enumerate(lines):
                m = _GHOSTTY_BIND.match(line)
                if not m:
                    continue
                if m.group(1) == "clear":
                    clear, user = True, []
                    continue
                trigger, action = _ghostty_split(m.group(1))
                user.append(Keymap(_ghostty_key(trigger), action, comments.get(i, ""), "",
                                   f"{rel(path)}:{i + 1}", removed=action == "unbind"))
    defaults = []
    if ctx.defaults and not clear:
        out = run(["ghostty", "+list-keybinds", "--default", "--plain"], sandbox=True) or ""
        for line in out.splitlines():
            m = _GHOSTTY_BIND.match(line)
            if m:
                trigger, action = _ghostty_split(m.group(1))
                defaults.append(Keymap(_ghostty_key(trigger), action, "", "", "ghostty defaults",
                                       default=True))
    return merge(defaults, user)


# ------------------------------------------------------------ Alacritty --

def load_alacritty(ctx: Ctx) -> list[Keymap]:
    main = ctx.first()
    if not main:
        return []
    out = []
    for path in _alacritty_files(main):
        try:
            data = tomllib.loads(read_text(path) or "")
        except tomllib.TOMLDecodeError as e:
            ctx.notes.append(f"{rel(path)}: invalid TOML ({e})")
            continue
        binds = data.get("keyboard", {}).get("bindings") or data.get("key_bindings") or []
        for b in binds if isinstance(binds, list) else []:
            if not isinstance(b, dict) or "key" not in b:
                continue
            mods = [m for m in str(b.get("mods", "")).split("|") if m.strip()]
            action = b.get("action") or ""
            if "chars" in b:
                action = f"send {b['chars']!r}"
            elif "command" in b:
                c = b["command"]
                action = "run " + (" ".join([c.get("program", "")] + list(c.get("args", [])))
                                   if isinstance(c, dict) else str(c))
            section = str(b.get("mode", ""))
            out.append(Keymap(combo(mods, str(b["key"])), str(action), "", section, rel(path)))
    if ctx.defaults:
        ctx.notes.append("Alacritty cannot print its defaults; see `man 5 alacritty-bindings`.")
    return out


def _alacritty_files(main: Path) -> list[Path]:
    """Imported files first (they are overridden by the main file)."""
    try:
        data = tomllib.loads(read_text(main) or "")
    except tomllib.TOMLDecodeError:
        return [main]
    imports = data.get("general", {}).get("import") or data.get("import") or []
    files = [p for i in imports if isinstance(i, str) for p in expand(i, main.parent) if p.is_file()]
    return files + [main]


# ----------------------------------------------------------------- foot --

_FOOT_SECTIONS = {"key-bindings": "", "search-bindings": "search", "url-bindings": "url",
                  "text-bindings": "text", "mouse-bindings": "mouse"}
_FOOT_INCLUDE = re.compile(r"^\s*include\s*=\s*(?P<path>.+?)\s*$")


def _foot_parse(lines, source, default: bool, comments=None) -> list[Keymap]:
    out, section = [], None
    for i, raw in enumerate(lines):
        line = raw[2:] if default and raw.startswith("# ") else raw
        if default and not raw.startswith(("# ", "[")):
            continue
        s = re.match(r"^\s*\[([\w-]+)\]", line)
        if s:
            section = _FOOT_SECTIONS.get(s.group(1))
            continue
        if section is None:
            continue
        m = re.match(r"^\s*([^=#\s][^=]*?)\s*=\s*(.+?)\s*$", line)
        if not m:
            continue
        action, value = m.group(1), m.group(2)
        cmd = re.match(r"^\[([^\]]*)\]\s*(.*)$", value)
        if cmd:
            action, value = f"{action} [{cmd.group(1)}]", cmd.group(2)
        src = source if default else f"{source}:{i + 1}"
        desc = (comments or {}).get(i, "")
        if value == "none":
            out.append(Keymap("", action, "", section, src, removed=True))
            continue
        for chord in value.split():
            out.append(Keymap(split_combo(chord), action, desc, section, src, default=default))
    return out


def load_foot(ctx: Ctx) -> list[Keymap]:
    user = []
    main = ctx.first()
    if main:
        for path, lines in read_tree(main, _FOOT_INCLUDE):
            comments = block_comments(lines, "#", lambda t: "=" in t)
            user += _foot_parse(lines, rel(path), False, comments)
    defaults = []
    if ctx.defaults:
        for p in expand("/etc/xdg/foot/foot.ini") + expand("/usr/share/foot/foot.ini"):
            text = read_text(p)
            if text:
                defaults = _foot_parse(text.splitlines(), "foot defaults", True)
                break
    # 'action=none' disables every default chord of that action.
    disabled = {(k.section, k.action) for k in user if k.removed}
    defaults = [d for d in defaults if (d.section, d.action) not in disabled]
    return merge(defaults, [k for k in user if not k.removed])


# -------------------------------------------------------------- WezTerm --

def load_wezterm(ctx: Ctx) -> list[Keymap]:
    main = ctx.first()
    if ctx.live:  # opt-in: wezterm evaluates the Lua config to answer
        out = run(["wezterm", "--config-file", str(main), "show-keys"] if main else ["wezterm", "show-keys"])
        if out:
            return _wezterm_show_keys(out, "wezterm show-keys", False)
        ctx.notes.append("`wezterm show-keys` failed: falling back to reading the Lua config.")
    user = _wezterm_static(main) if main else []
    text = read_text(main) if main else ""
    no_defaults = bool(re.search(r"disable_default_key_bindings\s*=\s*true", text or ""))
    defaults = []
    if ctx.defaults and not no_defaults:
        out = run(["wezterm", "-n", "show-keys"], sandbox=True) or ""  # -n: skip the user config
        defaults = _wezterm_show_keys(out, "wezterm defaults", True)
    return merge(defaults, user)


def _wezterm_show_keys(text: str, source: str, default: bool) -> list[Keymap]:
    out, section = [], ""
    for line in text.splitlines():
        if re.match(r"^\s*Default key table", line):
            section = ""
        elif m := re.match(r"^\s*Key Table:\s*(\S+)", line):
            section = m.group(1)
        elif re.match(r"^\s*Mouse", line):
            section = "mouse"
        elif "->" in line:
            left, action = line.split("->", 1)
            cols = re.split(r"\s{2,}", left.strip())
            mods = cols[0].split("|") if len(cols) > 1 else []
            key = re.sub(r"^(phys|mapped):", "", cols[-1].strip())
            out.append(Keymap(combo([m.strip() for m in mods if m.strip() and m.strip() != "NONE"], key),
                              action.strip(), "", section, source, default=default))
    return out


def _wezterm_static(path: Path) -> list[Keymap]:
    text = read_text(path) or ""
    clean = lua.strip_comments(text)
    out = []
    for m in re.finditer(r"\{\s*key\s*=", clean):
        end = lua.balanced(clean, m.start())
        _, named = lua.table_fields(clean[m.start():end])
        key = lua.string_value(named.get("key", "")) or ""
        mods = (lua.string_value(named.get("mods", "")) or "").replace("|", " ").split()
        if key and "action" in named:
            line = clean.count("\n", 0, m.start()) + 1
            out.append(Keymap(combo(mods, re.sub(r"^(phys|mapped):", "", key)),
                              lua.short(named["action"]), "", "", f"{rel(path)}:{line}"))
    return out


APPS = [
    App("kitty", "Kitty", "terminal", load_kitty, paths=(f"{CONFIG}/kitty/kitty.conf",),
        bins=("kitty",), icon="kitty"),
    App("ghostty", "Ghostty", "terminal", load_ghostty,
        paths=(f"{CONFIG}/ghostty/config", f"{CONFIG}/ghostty/config.ghostty"),
        bins=("ghostty",), icon="com.mitchellh.ghostty"),
    App("alacritty", "Alacritty", "terminal", load_alacritty,
        paths=(f"{CONFIG}/alacritty/alacritty.toml", "~/.alacritty.toml"),
        bins=("alacritty",), icon="Alacritty"),
    App("foot", "foot", "terminal", load_foot, paths=(f"{CONFIG}/foot/foot.ini",),
        bins=("foot",), icon="foot"),
    App("wezterm", "WezTerm", "terminal", load_wezterm,
        paths=(f"{CONFIG}/wezterm/wezterm.lua", "~/.wezterm.lua"),
        bins=("wezterm",), icon="org.wezfurlong.wezterm"),
]
