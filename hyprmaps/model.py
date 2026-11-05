"""The Keymap record and the helpers that turn raw key notations into one
readable style ("Ctrl + Shift + T", sequences joined with " › ")."""

import re
from dataclasses import dataclass

SEQ = " › "  # separator between the keys of a sequence (kitty "a>b", tmux prefix, ...)


@dataclass
class Keymap:
    key: str               # display form, e.g. "Super + Shift + K"
    action: str = ""       # what the config binds the key to, verbatim
    desc: str = ""         # human description; "" = the config gives none
    section: str = ""      # mode / table / submap / group, "" = main
    source: str = ""       # "path:line", "defaults", "hyprctl", ...
    default: bool = False  # True = built-in default of the app, not from the user config
    removed: bool = False  # True = an unbind; used only while merging


# Canonical modifier names and their display order.
_MOD_ORDER = ["Super", "Hyper", "Ctrl", "Alt", "AltGr", "Shift"]
_MOD_ALIASES = {
    "super": "Super", "mod4": "Super", "win": "Super", "logo": "Super", "cmd": "Super",
    "command": "Super", "meta": "Alt", "m": "Alt", "a": "Alt", "alt": "Alt", "mod1": "Alt",
    "opt": "Alt", "option": "Alt", "ctrl": "Ctrl", "control": "Ctrl", "c": "Ctrl",
    "ctl": "Ctrl", "primary": "Ctrl", "accel": "Ctrl", "shift": "Shift", "s": "Shift",
    "hyper": "Hyper", "mod3": "Hyper", "mod5": "AltGr", "altgr": "AltGr",
    "kitty_mod": "kitty_mod",
}
# Keys whose names differ between apps; keys not listed keep their own name.
_KEY_ALIASES = {
    "return": "Enter", "enter": "Enter", "cr": "Enter", "kp_enter": "KP_Enter",
    "esc": "Esc", "escape": "Esc", "space": "Space", " ": "Space", "tab": "Tab",
    "bs": "Backspace", "backspace": "Backspace", "bspace": "Backspace", "del": "Delete",
    "delete": "Delete", "ins": "Insert", "insert": "Insert", "home": "Home", "end": "End",
    "pgup": "PageUp", "page_up": "PageUp", "pageup": "PageUp", "prior": "PageUp", "ppage": "PageUp",
    "pgdwn": "PageDown", "pgdown": "PageDown", "page_down": "PageDown", "pagedown": "PageDown",
    "next": "PageDown", "npage": "PageDown", "up": "Up", "down": "Down", "left": "Left",
    "right": "Right", "arrowup": "Up", "arrowdown": "Down", "arrowleft": "Left",
    "arrowright": "Right", "plus": "+", "minus": "-", "equal": "=", "comma": ",",
    "period": ".", "slash": "/", "backslash": "\\", "semicolon": ";", "apostrophe": "'",
    "grave": "`", "bracketleft": "[", "bracketright": "]", "less": "<", "greater": ">",
    "sharp": "#", "print": "Print", "menu": "Menu", "lt": "<", "gt": ">", "bar": "|",
    "leader": "Leader", "wheel_up": "WheelUp", "wheel_down": "WheelDown",
    "mbtn_left": "MouseLeft", "mbtn_right": "MouseRight", "mbtn_mid": "MouseMiddle",
    "mouse:272": "MouseLeft", "mouse:273": "MouseRight", "mouse:274": "MouseMiddle",
    "mouse_up": "WheelUp", "mouse_down": "WheelDown",
}


def pretty_key(name: str) -> str:
    """Normalise one key name (no modifiers): 'return' -> 'Enter', 'f5' -> 'F5'."""
    k = name.strip()
    low = k.lower()
    if low in _KEY_ALIASES:
        return _KEY_ALIASES[low]
    if re.fullmatch(r"f\d{1,2}", low):
        return low.upper()
    if len(k) == 1:
        return k
    if low.startswith(("xf86", "kp_")):
        return k
    return k[:1].upper() + k[1:]


def pretty_mod(name: str) -> str:
    return _MOD_ALIASES.get(name.strip().lower(), name.strip().title())


def combo(mods, key: str) -> str:
    """Build 'Ctrl + Shift + T' from modifier names and a key name."""
    names = []
    for m in mods:
        p = pretty_mod(m)
        if p and p not in names:
            names.append(p)
    names.sort(key=lambda m: _MOD_ORDER.index(m) if m in _MOD_ORDER else -1)
    k = pretty_key(key)
    # With a modifier a lone letter is the physical key, so show it like on the keycap.
    if names and len(k) == 1 and k.isalpha():
        k = k.upper()
    return " + ".join(names + [k]) if k else " + ".join(names)


def split_combo(text: str, sep: str = "+") -> str:
    """'ctrl+shift+t' -> 'Ctrl + Shift + T'. A trailing separator is the key
    itself ('ctrl++' is Ctrl and '+'). Sequences joined by '>' are kept."""
    parts_out = []
    for chord in _split_sequence(text):
        if chord.endswith(sep + sep):
            parts = chord[:-2].split(sep) + [sep]
        elif chord == sep:
            parts = [sep]
        else:
            parts = chord.split(sep)
        parts = [p for p in parts if p != ""] or [chord]
        parts_out.append(combo(parts[:-1], parts[-1]))
    return SEQ.join(parts_out)


def _split_sequence(text: str):
    """Split a key sequence written as 'a>b>c'; a lone '>' key stays a key."""
    text = text.strip()
    if ">" not in text or text == ">":
        return [text]
    out, cur = [], ""
    for ch in text:
        # '>' separates chords unless it is the key itself (first char or after a separator)
        if ch == ">" and cur and cur[-1] not in "+-":
            out.append(cur)
            cur = ""
        else:
            cur += ch
    out.append(cur)
    return [c for c in out if c]


_VIM_TOKEN = re.compile(r"<[^<>\s]+>|.", re.S)
_VIM_NAMED = {"cr": "Enter", "return": "Enter", "enter": "Enter", "esc": "Esc",
              "space": "Space", "tab": "Tab", "bs": "Backspace", "del": "Delete",
              "up": "Up", "down": "Down", "left": "Left", "right": "Right",
              "leader": "Leader", "localleader": "LocalLeader", "lt": "<", "bar": "|",
              "bslash": "\\", "home": "Home", "end": "End", "pageup": "PageUp",
              "pagedown": "PageDown", "insert": "Insert"}


def pretty_angle_keys(text: str) -> str:
    """Readable form of the '<C-x>gg<Esc>' notation used by vim-like apps
    (zathura, ranger, lf, qutebrowser, yazi, helix): 'Ctrl + X › gg › Esc'."""
    tokens, plain = [], ""
    for tok in _VIM_TOKEN.findall(text.strip()):
        if len(tok) > 2 and tok.startswith("<") and tok.endswith(">"):
            if plain:
                tokens.append(plain)
                plain = ""
            tokens.append(_angle_token(tok[1:-1]))
        else:
            plain += tok
    if plain:
        tokens.append(plain)
    return SEQ.join(tokens)


def _angle_token(inner: str) -> str:
    parts = inner.split("-")
    if len(parts) > 1 and parts[-1] == "":  # '<C-->' is Ctrl and '-'
        parts = parts[:-2] + ["-"]
    key = parts[-1]
    key = _VIM_NAMED.get(key.lower(), key)
    return combo(parts[:-1], key) if len(parts) > 1 else pretty_key(key)


def humanize(action: str) -> str:
    """Best-effort description from an identifier-like action:
    'new_tab_with_cwd' -> 'New tab with cwd', 'editor.action.commentLine' ->
    'Comment line'. Returns '' when the action is not a plain identifier."""
    a = action.strip()
    if not a or not re.fullmatch(r"[\w.:/-]+(\(\))?", a):
        return ""
    a = a.removesuffix("()")
    last = [p for p in re.split(r"[.:/]+", a) if p]
    if not last:
        return ""
    words = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", last[-1]).replace("_", " ").replace("-", " ")
    words = " ".join(words.split()).lower()
    return words[:1].upper() + words[1:]


def merge(defaults, user, ident=lambda k: (k.section, k.key)):
    """Overlay user keymaps on the app defaults: a user bind replaces the
    default with the same identity (section + key by default); a removed user
    bind deletes it. Between user binds with the same identity the last one
    wins, as in the apps themselves."""
    last = {ident(k): k for k in user}
    out = [d for d in defaults if ident(d) not in last]
    return out + [k for k in last.values() if not k.removed]


def block_comments(lines, prefix: str, is_code=None):
    """For each line index, the comment block that documents it.

    A comment block (consecutive comment lines) applies to the code lines that
    follow it until the next blank line, so a '# Move focus' header documents
    all four focus binds below it. Commented-out code (``is_code`` returns
    True) is not a description.
    """
    out, current, prev_comment = {}, "", False
    for i, raw in enumerate(lines):
        s = raw.strip()
        if not s:
            current, prev_comment = "", False
            continue
        if s.startswith(prefix):
            text = s[len(prefix):].lstrip(prefix[-1:] + ":").strip().strip("-=#*~_ ")
            if not text or (is_code and is_code(text)):
                prev_comment = False
                continue
            current = (current + " " + text) if prev_comment else text
            prev_comment = True
            continue
        prev_comment = False
        out[i] = current
    return out
