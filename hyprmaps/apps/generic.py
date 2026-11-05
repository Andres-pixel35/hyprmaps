"""Keymaps of any app the tool does not know.

Two formats for user-defined apps (see docs/adding-apps.md):
- ``regex``: one regular expression applied to every line, with the named
  groups ``key`` (required), ``action``, ``desc`` and ``section``;
- ``auto``: heuristics that find most "bind KEY ACTION", "KEY = ACTION",
  "ACTION = KEY" lines, and key/action objects inside JSON or TOML files.
"""

import json
import re
import shlex
import tomllib

from ..model import Keymap, block_comments, pretty_angle_keys, split_combo
from ..sources import read_text, rel
from .base import Ctx
from .editors import loads_jsonc
from .shells import decode_caret, decode_fish, readline_to_caret

_MOD = r"(?:ctrl|control|alt|shift|super|mod[1-5]|meta|cmd|win|hyper|primary|[CMASs])"
_KEYISH = re.compile(rf"^(?:<[^<>\s]+>\S*|\^\S|\\[CMce]\S*|(?:{_MOD}[-+])+\S+|F\d{{1,2}}|\S)$", re.I)
_DIRECTIVE = re.compile(r"^\s*(?:[a-z]?(?:nore)?map!?|bind\w*|keybind\w*|bindkey|bindsym|key)\b\s*(=)?\s*(?P<rest>.+)$", re.I)
_HYPR_MOD = r"\$\w+|super|shift|ctrl|control|alt|mod[2-5]|caps|meta|win|logo"
_SECTION = re.compile(r"^\s*\[[^\[\]]+\]\s*$")
_KEY_SECTION = re.compile(r"^\s*\[[^\[\]]*(?:key|bind|shortcut|hotkey)[^\[\]]*\]\s*$", re.I)
# Same matches as ^\s*(?P<a>[^=#;\[\]][^=]*?)\s*[=:]\s*(?P<b>.+?)\s*$ (callers strip a and b),
# without its cubic backtracking on long lines without a separator.
_PAIR = re.compile(r"^(?:\s*+(?=[^=#;\[\]\s])|(?=\s))(?P<a>[^=]+?)[=:](?P<b>.+)$")
_KEY_FIELDS = ("key", "keys", "on", "shortcut", "keybinding", "bind", "trigger", "accelerator", "hotkey")
_ACTION_FIELDS = ("command", "action", "run", "exec", "cmd", "do", "args")
_DESC_FIELDS = ("desc", "description", "title", "label", "name", "help")


def looks_like_key(s: str) -> bool:
    s = s.strip().strip("'\"")
    return bool(s) and all(_KEYISH.match(p) for p in s.split())


def auto_key(raw: str) -> str:
    """Readable key from whatever notation the file uses."""
    s = raw.strip().strip("'\"")
    if re.search(r"<[^<>\s]+>", s):
        return pretty_angle_keys(s)
    if s.startswith("^") or "\\" in s:
        return decode_caret(readline_to_caret(s))
    if "+" in s and len(s) > 1:
        return " › ".join(split_combo(p) for p in s.split())
    if re.match(rf"^{_MOD}-.", s, re.I):
        return decode_fish(s)
    return s


def load_generic(ctx: Ctx) -> list[Keymap]:
    fmt = str(ctx.options.get("format", "auto"))
    out = []
    for path in [p for p in ctx.paths if p.is_file()]:
        text = read_text(path)
        if text is None:
            ctx.notes.append(f"{rel(path)}: not readable as text")
            continue
        if fmt == "regex":
            out += _regex(ctx, path, text)
        elif path.suffix in (".json", ".jsonc", ".toml"):
            out += _structured(ctx, path, text)
        else:
            out += _auto_lines(path, text, str(ctx.options.get("comment", "#")))
    if not out:
        ctx.notes.append("No keymaps recognised: try format = \"regex\" (docs/adding-apps.md).")
    return out


def _regex(ctx: Ctx, path, text: str) -> list[Keymap]:
    try:
        pattern = re.compile(str(ctx.options.get("pattern", "")))
    except re.error as e:
        ctx.notes.append(f"invalid pattern: {e}")
        return []
    if "key" not in pattern.groupindex:
        ctx.notes.append("the pattern needs a (?P<key>...) group")
        return []
    out = []
    for i, line in enumerate(text.splitlines()):
        m = pattern.search(line)
        if m and m.group("key"):
            g = m.groupdict()
            out.append(Keymap(auto_key(g["key"]), (g.get("action") or "").strip(),
                              (g.get("desc") or "").strip(), (g.get("section") or "").strip(),
                              f"{rel(path)}:{i + 1}"))
    return out


def _auto_lines(path, text: str, comment: str) -> list[Keymap]:
    lines = text.splitlines()
    comments = block_comments(lines, comment, lambda t: bool(_DIRECTIVE.match(t)))
    # An INI file with a [key bindings]-like section keeps its keys there: 'name = value'
    # lines in other sections are settings ('progressBarChar=■'), not keymaps.
    keys_only_in_section = any(_KEY_SECTION.match(t) for t in lines)
    in_key_section, out = False, []
    for i, line in enumerate(lines):
        if not line.strip() or line.lstrip().startswith(comment):
            continue
        if _SECTION.match(line):
            in_key_section = bool(_KEY_SECTION.match(line))
            continue
        body, _, trailing = line.partition(f" {comment} ") if comment else (line, "", "")
        pair_ok = in_key_section or not keys_only_in_section
        found = _directive(body) or (pair_ok and _pair(body))
        if found:
            key, action = found
            out.append(Keymap(auto_key(key), action, trailing.strip() or comments.get(i, ""), "",
                              f"{rel(path)}:{i + 1}"))
    return out


def _directive(line: str):
    """'map ctrl+t new_tab', 'bind = SUPER, Q, exec, kitty', 'keybind = a=b'."""
    m = _DIRECTIVE.match(line)
    if not m:
        return None
    rest = m.group("rest").strip()
    if m.group(1) and "," in rest:
        f = [x.strip() for x in rest.split(",")]
        if len(f) >= 3 and _hypr_mods(f[0]):  # 'MODS, KEY, dispatcher...' (Hyprland-like)
            return (f"{f[0].replace(' ', '+')}+{f[1]}" if f[0] else f[1]), ", ".join(f[2:])
        return f[0], ", ".join(f[1:])  # 'KEY, action...' (kew-like)
    if m.group(1) and "=" in rest:  # 'trigger=action'
        k, _, a = rest.partition("=")
        return (k, a) if looks_like_key(k) else None
    try:
        tokens = shlex.split(rest, posix=False)
    except ValueError:
        tokens = rest.split()
    while tokens and tokens[0].startswith("-") and len(tokens[0]) > 1:  # options like --silent
        tokens.pop(0)
    if tokens and looks_like_key(tokens[0]):
        return tokens[0], " ".join(tokens[1:])
    return None


def _hypr_mods(field: str) -> bool:
    """True for a Hyprland modifier field: '', 'SUPER SHIFT', 'CTRL_ALT', '$mainMod'."""
    tokens = [t for t in re.split(r"[\s_+&]+", field) if t]
    return not field or bool(tokens) and all(re.fullmatch(_HYPR_MOD, t, re.I) for t in tokens)


def _pair(line: str):
    m = _PAIR.match(line)
    if not m:
        return None
    a, b = m.group("a").strip(), m.group("b").strip()
    if looks_like_key(a) and not looks_like_key(b):
        return a, b
    if looks_like_key(b) and not looks_like_key(a) and not b.strip("'\"").isdigit():
        return b, a
    return None


def _structured(ctx: Ctx, path, text: str) -> list[Keymap]:
    try:
        data = tomllib.loads(text) if path.suffix == ".toml" else loads_jsonc(text)
    except (tomllib.TOMLDecodeError, json.JSONDecodeError) as e:
        ctx.notes.append(f"{rel(path)}: cannot parse ({e})")
        return []
    out = []

    def walk(node, section, depth):
        if depth > 8:
            return
        if isinstance(node, dict):
            keyf = next((f for f in _KEY_FIELDS if isinstance(node.get(f), (str, list))), None)
            if keyf:
                key = node[keyf]
                key = " ".join(map(str, key)) if isinstance(key, list) else key
                act = next((node[f] for f in _ACTION_FIELDS if f in node), "")
                desc = next((str(node[f]) for f in _DESC_FIELDS if isinstance(node.get(f), str)), "")
                act = " ".join(map(str, act)) if isinstance(act, list) else str(act)
                out.append(Keymap(auto_key(str(key)), act, desc, section, rel(path)))
                return
            for k, v in node.items():
                if isinstance(v, str) and looks_like_key(k):
                    out.append(Keymap(auto_key(k), v, "", section, rel(path)))
                else:
                    walk(v, k if isinstance(v, (dict, list)) and not section else section, depth + 1)
        elif isinstance(node, list):
            for v in node:
                walk(v, section, depth + 1)

    walk(data, "", 0)
    return out
