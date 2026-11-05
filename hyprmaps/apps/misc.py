"""Desktop tools often found next to Hyprland: rofi, fuzzel, mpv, imv,
zathura, yazi, ranger, lf, Thunar."""

import re
import shlex
import tomllib

from ..model import SEQ, Keymap, block_comments, combo, humanize, merge, pretty_angle_keys, split_combo
from ..sources import CONFIG, expand, read_text, rel, run
from .base import App, Ctx
from .terminals import _foot_parse

# ----------------------------------------------------------------- rofi --


def rofi_bindings() -> list[tuple[str, str, list[str], bool]]:
    """(name, description, keys, is_default) of every rofi kb-* option.
    `rofi -help` prints each option's description and current value, tagged
    (Default) or (Configuration file)."""
    out = []
    lines = (run(["rofi", "-help"]) or "").splitlines()
    for i, line in enumerate(lines[:-1]):
        m = re.match(r"^\s*-kb-([\w-]+) \[string\]\s+(.*)$", line)
        v = re.match(r"^\s*(.*?)\s+\((Default|[^)]*)\)\s*$", lines[i + 1]) if m else None
        if v:
            keys = [k for k in v.group(1).split(",") if k]
            out.append((m.group(1), m.group(2), keys, v.group(2) == "Default"))
    return out


def load_rofi(ctx: Ctx) -> list[Keymap]:
    out = []
    for name, desc, keys, is_default in rofi_bindings():
        if name.startswith(("custom-", "select-")) or (is_default and not ctx.defaults):
            continue
        for key in keys:
            out.append(Keymap(split_combo(key), f"kb-{name}", desc, "",
                              "rofi defaults" if is_default else "rofi config", default=is_default))
    return out


# --------------------------------------------------------------- fuzzel --

def load_fuzzel(ctx: Ctx) -> list[Keymap]:
    main = ctx.first()
    user = _foot_parse((read_text(main) or "").splitlines(), rel(main), False) if main else []
    defaults = []
    if ctx.defaults:
        for p in expand("/etc/xdg/fuzzel/fuzzel.ini") + expand("/usr/share/doc/fuzzel/fuzzel.ini"):
            text = read_text(p)
            if text:
                defaults = _foot_parse(text.splitlines(), "fuzzel defaults", True)
                break
    disabled = {k.action for k in user if k.removed}
    return merge([d for d in defaults if d.action not in disabled], [k for k in user if not k.removed])


# ------------------------------------------------------------------ mpv --

_MPV_DOCS = ("/usr/share/doc/mpv/input.conf", "/usr/local/share/doc/mpv/input.conf")


def _mpv_line(line: str):
    """'Ctrl+s screenshot  # take a screenshot' -> (key, command, desc, section)."""
    body, _, desc = line.partition(" #")
    parts = body.split(None, 1)
    if len(parts) < 2:
        return None
    key, cmd, section = parts[0], parts[1].strip(), ""
    m = re.match(r"^\{([\w-]+)\}\s*(.*)$", cmd)
    if m:
        section, cmd = m.group(1), m.group(2)
    return key, cmd, desc.strip(), section


def _mpv_key(key: str) -> str:
    """mpv writes a key sequence with '-' ('g-p' is g, then p); a lone or trailing '-' is the minus key."""
    parts = key.split("-")
    if len(parts) > 1 and all(parts):
        return SEQ.join(split_combo(p) for p in parts)
    return split_combo(key)


def load_mpv(ctx: Ctx) -> list[Keymap]:
    user = []
    main = ctx.first()
    for i, line in enumerate((read_text(main) or "").splitlines() if main else []):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parsed = _mpv_line(line)
        if parsed:
            key, cmd, desc, section = parsed
            user.append(Keymap(_mpv_key(key), cmd, desc, section, f"{rel(main)}:{i + 1}",
                               removed=cmd == "ignore"))
    conf = read_text(expand(f"{CONFIG}/mpv/mpv.conf")[0]) or ""
    no_defaults = bool(re.search(r"^\s*(no-input-default-bindings|input-default-bindings\s*=\s*no)", conf, re.M))
    defaults = []
    doc = next((p for g in _MPV_DOCS for p in expand(g) if p.is_file()), None)
    if ctx.defaults and doc and not no_defaults:
        for line in (read_text(doc) or "").splitlines():
            # defaults are written commented out with no space: '#KEY command # description'
            if re.match(r"^#[^\s#]", line):
                parsed = _mpv_line(line[1:])
                if parsed and parsed[0] != "default-bindings" and parsed[1] != "ignore":
                    key, cmd, desc, section = parsed
                    defaults.append(Keymap(_mpv_key(key), cmd, desc, section, "mpv defaults", default=True))
    return merge(defaults, user)


# ------------------------------------------------------------------ imv --

def _imv_binds(text: str, source: str, default: bool):
    out, active, suppress = [], False, False
    for i, line in enumerate(text.splitlines()):
        s = re.match(r"^\s*\[(\w+)\]", line)
        if s:
            active = s.group(1) == "binds"
            continue
        if re.match(r"^\s*suppress_default_binds\s*=\s*true", line):
            suppress = True
        m = re.match(r"^\s*([^#=\s][^=]*?)\s*=\s*(.+?)\s*$", line) if active else None
        if m:
            out.append(Keymap(pretty_angle_keys(m.group(1).replace("+", "-")), m.group(2), "", "",
                              source if default else f"{source}:{i + 1}", default=default))
    return out, suppress


def load_imv(ctx: Ctx) -> list[Keymap]:
    main = ctx.first()
    user, suppress = _imv_binds(read_text(main) or "", rel(main), False) if main else ([], False)
    defaults = []
    if ctx.defaults and not suppress:
        defaults, _ = _imv_binds(read_text(expand("/etc/imv_config")[0]) or "", "imv defaults", True)
    return merge(defaults, user)


# --------------------------------------- zathura / ranger / lf (map ...) --

def _map_lines(ctx: Ctx, sections: dict, comment: str = "#", default_files=()):
    """Shared reader for 'map KEY COMMAND' files. ``sections`` maps the command
    word (map, cmap, pmap...) to a section name; 'unmap'-style words remove."""
    def parse(path, default):
        lines = (read_text(path) or "").splitlines()
        comments = block_comments(lines, comment, lambda t: t.split(" ")[0] in sections)
        out = []
        for i, line in enumerate(lines):
            m = re.match(r"^\s*(\w+)\s+(.*)$", line)
            if not m:
                continue
            word, rest = m.group(1), m.group(2)
            removed = word.startswith("un") and word[2:] in sections  # unmap, cunmap...
            if word not in sections and not removed:
                continue
            section = sections[word[2:] if removed else word]
            mode = re.match(r"^\[(\w+)\]\s*(.*)$", rest)  # zathura: map [fullscreen] ...
            if mode:
                section, rest = mode.group(1), mode.group(2)
            try:
                parts = shlex.split(rest, posix=False)
            except ValueError:
                parts = rest.split()
            if not parts:
                continue
            key = parts[0].strip("'\"")
            action = " ".join(parts[1:])
            out.append(Keymap(pretty_angle_keys(key), action, comments.get(i, ""), section,
                              f"{rel(path)}:{i + 1}" if not default else f"{ctx.app.name} defaults",
                              default=default, removed=removed or not action))
        return out

    user = [k for p in ctx.paths if p.is_file() for k in parse(p, False)]
    defaults = []
    if ctx.defaults:
        doc = next((p for g in default_files for p in expand(g) if p.is_file()), None)
        defaults = parse(doc, True) if doc else []
    return merge(defaults, user)


def load_zathura(ctx: Ctx) -> list[Keymap]:
    return _map_lines(ctx, {"map": ""})


def load_ranger(ctx: Ctx) -> list[Keymap]:
    return _map_lines(ctx, {"map": "", "copymap": "", "cmap": "console", "pmap": "pager", "tmap": "taskview"},
                      default_files=("/usr/share/doc/ranger/config/rc.conf",
                                     "/usr/lib/python3*/site-packages/ranger/config/rc.conf"))


def load_lf(ctx: Ctx) -> list[Keymap]:
    return _map_lines(ctx, {"map": "", "nmap": "", "vmap": "visual", "cmap": "command line"})


# ----------------------------------------------------------------- yazi --

def load_yazi(ctx: Ctx) -> list[Keymap]:
    main = ctx.first()
    if not main:
        return []
    try:
        data = tomllib.loads(read_text(main) or "")
    except tomllib.TOMLDecodeError as e:
        ctx.notes.append(f"invalid TOML: {e}")
        return []
    out = []
    for layer, table in data.items():
        if not isinstance(table, dict):
            continue
        section = "" if layer in ("mgr", "manager") else layer
        for listname in ("prepend_keymap", "keymap", "append_keymap"):
            for b in table.get(listname, []) if isinstance(table.get(listname), list) else []:
                if not isinstance(b, dict) or "on" not in b:
                    continue
                on = b["on"] if isinstance(b["on"], list) else [b["on"]]
                run_ = b.get("run", "")
                action = "; ".join(run_) if isinstance(run_, list) else str(run_)
                out.append(Keymap(SEQ.join(pretty_angle_keys(str(k)) for k in on), action,
                                  str(b.get("desc", "")), section, rel(main)))
    ctx.notes.append("Only keymap.toml is shown: yazi's defaults are built into the app.")
    return out


# --------------------------------------------------------------- Thunar --

def load_thunar(ctx: Ctx) -> list[Keymap]:
    main = ctx.first()
    out = []
    for line in (read_text(main) or "").splitlines() if main else []:
        m = re.match(r'^\s*(;\s*)?\(gtk_accel_path\s+"([^"]+)"\s+"([^"]*)"\)', line)
        if not m or not m.group(3):
            continue
        default = bool(m.group(1))
        if default and not ctx.defaults:
            continue
        mods = re.findall(r"<(\w+)>", m.group(3))
        key = re.sub(r"<\w+>", "", m.group(3))
        action = m.group(2).rsplit("/", 1)[-1]
        out.append(Keymap(combo(mods, key), action, humanize(action), "",
                          "Thunar defaults" if default else rel(main), default=default))
    return out


APPS = [
    App("rofi", "rofi", "launcher", load_rofi, paths=(f"{CONFIG}/rofi/config.rasi",), bins=("rofi",), icon="rofi"),
    App("fuzzel", "fuzzel", "launcher", load_fuzzel, paths=(f"{CONFIG}/fuzzel/fuzzel.ini",), bins=("fuzzel",), icon="fuzzel"),
    App("mpv", "mpv", "media", load_mpv, paths=(f"{CONFIG}/mpv/input.conf",), bins=("mpv",), icon="mpv"),
    App("imv", "imv", "media", load_imv, paths=(f"{CONFIG}/imv/config",), bins=("imv",), icon="imv"),
    App("zathura", "zathura", "media", load_zathura, paths=(f"{CONFIG}/zathura/zathurarc",),
        bins=("zathura",), icon="org.pwmt.zathura"),
    App("yazi", "yazi", "files", load_yazi, paths=(f"{CONFIG}/yazi/keymap.toml",), bins=("yazi",), icon="yazi"),
    App("ranger", "ranger", "files", load_ranger, paths=(f"{CONFIG}/ranger/rc.conf",), bins=("ranger",), icon="ranger"),
    App("lf", "lf", "files", load_lf, paths=(f"{CONFIG}/lf/lfrc",), bins=("lf",), icon="lf"),
    App("thunar", "Thunar", "files", load_thunar, paths=(f"{CONFIG}/Thunar/accels.scm",),
        bins=("thunar",), icon="org.xfce.thunar"),
]
