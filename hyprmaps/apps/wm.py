"""Hyprland: live binds from ``hyprctl binds -j`` (what is really active),
enriched with what only the config files know (the exec command, comments).
Works with both the Lua config (``hyprland.lua``) and the classic
``hyprland.conf``. Without a running Hyprland the config files alone are used.
"""

import json
import re
from pathlib import Path

from .. import luaparse as lua
from ..model import Keymap, block_comments, combo
from ..sources import CONFIG, read_text, read_tree, rel, run
from .base import App, Ctx

_MASK = [(64, "Super"), (4, "Ctrl"), (8, "Alt"), (1, "Shift"), (32, "Hyper"), (128, "AltGr")]
_MOD_WORDS = re.compile(r"SUPER|WIN|LOGO|MOD4|SHIFT|CTRL|CONTROL|ALT|MOD1|MOD3|MOD5", re.I)
_CONF_BIND = re.compile(r"^\s*bind([a-z]*)\s*=\s*(.*)$")
_CONF_VAR = re.compile(r"^\s*\$(\w+)\s*=\s*(.*?)\s*$")
_CONF_SOURCE = re.compile(r"^\s*source\s*=\s*(?P<path>.+?)\s*$")
_CONF_SUBMAP = re.compile(r"^\s*submap\s*=\s*(\S+)")


def _mods_from_mask(mask: int) -> list[str]:
    return [name for bit, name in _MASK if mask & bit]


def _norm(mods, key: str) -> tuple:
    """Comparable form of a bind for matching live and static data."""
    return frozenset(m.lower() for m in mods), key.lower()


def load(ctx: Ctx) -> list[Keymap]:
    static = _static(ctx)
    live = _live()
    if live is None:
        ctx.notes.append("Hyprland is not running: showing the config files only.")
        return [Keymap(combo(s["mods"], s["key"]), s["action"], s["desc"], s["section"], s["source"])
                for s in static]
    exact = {_norm(s["mods"], s["key"]): s for s in static if s["key"] != "*"}
    wild = [s for s in static if s["key"] == "*"]
    out = []
    for b in live:
        mods = _mods_from_mask(b.get("modmask", 0))
        key = b.get("key") or (f"code:{b['keycode']}" if b.get("keycode") else "")
        disp = b.get("dispatcher", "")
        disp_name = re.sub(r"^HL\.\w+\((.*)\)$", r"\1", disp)
        lua_mode = disp != disp_name
        arg = "" if lua_mode else b.get("arg", "")
        s = exact.get(_norm(mods, key)) or next(
            (w for w in wild if _norm(w["mods"], "")[0] == _norm(mods, "")[0]
             and w["dispatcher"] == disp_name), None)
        action = s["action"] if s else f"{disp_name} {arg}".strip()
        desc = (b.get("description") if b.get("has_description") else "") or (s["desc"] if s else "")
        section = b.get("submap") or ""
        out.append(Keymap(combo(mods, key), action, desc, section, s["source"] if s else "hyprctl"))
    return out


def _live():
    out = run(["hyprctl", "binds", "-j"])
    if not out:
        return None
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, list) else None


def _static(ctx: Ctx) -> list[dict]:
    lua_main = next((p for p in ctx.paths if p.suffix == ".lua"), None)
    if lua_main:
        return _static_lua(lua_main.parent)
    conf = next((p for p in ctx.paths if p.suffix == ".conf"), None)
    return _static_conf(conf) if conf else []


def _static_conf(path: Path) -> list[dict]:
    files = list(read_tree(path, _CONF_SOURCE))
    variables = {}
    for _, lines in files:
        for line in lines:
            m = _CONF_VAR.match(line)
            if m:
                variables[m.group(1)] = m.group(2).split("#", 1)[0].strip()

    def subst(s):
        return re.sub(r"\$(\w+)", lambda m: variables.get(m.group(1), m.group(0)), s)

    out = []
    for fpath, lines in files:
        comments = block_comments(lines, "#", lambda t: bool(_CONF_BIND.match(t)))
        section = ""
        for i, line in enumerate(lines):
            sm = _CONF_SUBMAP.match(line)
            if sm:
                section = "" if sm.group(1) == "reset" else sm.group(1)
                continue
            m = _CONF_BIND.match(line)
            if not m:
                continue
            flags, value = m.group(1), m.group(2)
            value, _, trailing = re.sub(r"##", "\0", value).partition("#")
            value, trailing = value.replace("\0", "#"), trailing.strip()
            fields = [f.strip() for f in value.split(",")]
            if len(fields) < 3:
                continue
            desc = ""
            if "d" in flags and len(fields) >= 4:
                desc = fields.pop(2)
            mods = [w for w in _MOD_WORDS.findall(subst(fields[0]))]
            key, dispatcher = subst(fields[1]), fields[2]
            args = subst(",".join(fields[3:]).strip())
            if "m" in flags and dispatcher in ("movewindow", "resizewindow"):
                args, dispatcher = dispatcher, "mouse"
            out.append({"mods": mods, "key": key, "dispatcher": dispatcher,
                        "action": f"{dispatcher} {args}".strip(),
                        "desc": desc or trailing or comments.get(i, ""),
                        "section": section, "source": f"{rel(fpath)}:{i + 1}"})
    return out


def _static_lua(conf_dir: Path) -> list[dict]:
    files = []
    for p in sorted(conf_dir.rglob("*.lua")):
        text = read_text(p)
        if text is not None:
            files.append((p, text))
    variables = {}
    for _, text in files:
        variables.update(lua.string_vars(lua.strip_comments(text)))
    out = []
    for path, text in files:
        comments = block_comments(text.splitlines(), "--", lambda t: "hl.bind" in t)
        for _, args, line in lua.find_calls(lua.strip_comments(text), r"hl\.bind"):
            parts = lua.split_top(args)
            if len(parts) < 2:
                continue
            keyspec, _ = lua.concat_value(parts[0], variables)
            tokens = [t.strip() for t in keyspec.split("+")]
            if keyspec.rstrip().endswith("+"):  # "SUPER + +" binds the plus key
                tokens = [t for t in tokens if t] + ["+"]
            mods, key = tokens[:-1], tokens[-1] if tokens else "*"
            if any(m == "*" for m in mods):
                continue  # a variable modifier cannot be matched reliably
            dispatcher, action = _lua_dispatcher(parts[1], variables)
            desc = ""
            if len(parts) > 2:
                _, named = lua.table_fields(parts[2])
                desc = lua.string_value(named.get("description", "")) or ""
            out.append({"mods": mods, "key": key, "dispatcher": dispatcher, "action": action,
                        "desc": desc or comments.get(line - 1, ""), "section": "",
                        "source": f"{rel(path)}:{line}"})
    return out


def _lua_dispatcher(expr: str, variables: dict) -> tuple[str, str]:
    e = expr.strip()
    m = re.match(r"^hl\.dsp\.([\w.]+)\s*\((.*)\)$", e, re.S)
    if not m:
        return "function", "Lua: " + lua.short(e, 60)
    name = m.group(1).split(".")[-1]
    inner = m.group(2).strip()
    if name in ("exec_cmd", "exec"):
        cmd, known = lua.concat_value(inner, variables, unknown="…")
        return name, "exec " + (cmd if known or cmd.strip("…") else lua.short(inner))
    return name, f"{m.group(1)}({lua.short(inner, 60)})" if inner else m.group(1)


APPS = [
    App("hyprland", "Hyprland", "wm", load,
        paths=(f"{CONFIG}/hypr/hyprland.lua", f"{CONFIG}/hypr/hyprland.conf"),
        bins=("Hyprland", "hyprctl"), icon="hyprland"),
]
