"""The rofi screens: category picker -> app list -> keymap view.

rofi runs in dmenu mode and returns the index of the chosen row (-format i).
Exit codes: 0 = Enter, 1 = Esc, 10.. = kb-custom-1.. (our next/prev/toggle keys).
"""

import os
import re
import subprocess
import sys
from html import escape
from pathlib import Path

from .apps.base import CATEGORIES, App
from .apps.generic import load_generic
from .apps.misc import rofi_bindings
from .config import theme_args
from .model import humanize
from .sources import read_text

ACCEPT, CANCEL, NEXT, PREV, TOGGLE = 0, 1, 10, 11, 12
_CUSTOM = {"next_app": "custom-1", "prev_app": "custom-2", "toggle_defaults": "custom-3"}


def _norm_key(k: str) -> str:
    parts = k.strip().lower().split("+")
    return "+".join(sorted(parts[:-1]) + parts[-1:])


def key_args(keys: dict) -> list[str]:
    """kb-custom-1..3 = our keys. Any rofi binding already using one of them
    loses only that key, otherwise rofi refuses to start ("already bound")."""
    ours = {_norm_key(k) for v in keys.values() for k in str(v).split(",") if k.strip()}
    args = []
    for name, _, current, _ in rofi_bindings():
        if name in _CUSTOM.values():
            continue
        kept = [k for k in current if _norm_key(k) not in ours]
        if kept != current:
            args += [f"-kb-{name}", ",".join(kept)]
    for setting, kb in _CUSTOM.items():
        args += [f"-kb-{kb}", str(keys.get(setting, ""))]
    return args


def desktop_icons() -> dict:
    """{executable: Icon=} from the installed .desktop files, so the app list
    shows the same icons as the app launcher. User files take precedence."""
    dirs = [Path.home() / ".local/share/applications"]
    dirs += [Path(d) / "applications" for d in os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(":") if d]
    icons = {}
    for d in dirs:
        for f in sorted(d.glob("*.desktop")) if d.is_dir() else []:
            text = read_text(f) or ""
            exe = re.search(r"^Exec=(?:env\s+(?:\S+=\S+\s+)*)?\"?([^\s\"]+)", text, re.M)
            icon = re.search(r"^Icon=(.+)$", text, re.M)
            if exe and icon:
                icons.setdefault(Path(exe.group(1)).name, icon.group(1).strip())
    return icons


def _first_key(keys: dict, setting: str) -> str:
    return str(keys.get(setting, "")).split(",")[0].replace("Shift+", "⇧").replace("Control+", "Ctrl+")


class Rofi:
    def __init__(self, cfg: dict):
        self.ap = cfg["appearance"]
        self.common = ["rofi", "-dmenu", "-i"] + theme_args(self.ap) + key_args(cfg["keys"])
        self.base = self.common + ["-no-custom", "-format", "i"]
        if self.ap.get("markup", True):
            self.base.append("-markup-rows")

    def show(self, rows: list[str], prompt: str, mesg: str = "", selected: int = 0, icons=None):
        args = self.base + ["-p", prompt, "-selected-row", str(max(selected, 0))]
        if mesg:
            args += ["-mesg", mesg]
        lines = [r.replace("\n", " ").replace("\0", " ") for r in rows]  # \0 starts rofi row options
        if icons and self.ap.get("icons", True):
            args.append("-show-icons")
            lines = [f"{line}\0icon\x1f{icon}" if icon else line for line, icon in zip(lines, icons, strict=True)]
        try:
            proc = subprocess.run(args, input="\n".join(lines), capture_output=True, text=True)
        except OSError as e:
            sys.exit(f"hyprmaps: cannot start rofi: {e}")
        if proc.returncode not in (0, 1) and proc.returncode < 10:
            sys.exit(f"hyprmaps: rofi failed: {proc.stderr.strip()}")
        out = proc.stdout.strip()
        index = int(out) if out.lstrip("-").isdigit() and int(out) >= 0 else None
        return proc.returncode, index

    def ask_text(self, prompt: str, mesg: str) -> str:
        """Free text input (used to type a file path)."""
        proc = subprocess.run(self.common + ["-p", prompt, "-mesg", mesg], input="",
                              capture_output=True, text=True)
        return proc.stdout.strip() if proc.returncode == 0 else ""


# ------------------------------------------------------------------ rows --

def _m(text: str) -> str:
    """Escape for Pango and drop control characters."""
    return escape("".join(c if c >= " " else " " for c in text))  # quotes too: also used in attributes


class Formatter:
    def __init__(self, ap: dict):
        self.ap = ap
        self.markup = bool(ap.get("markup", True))

    def key(self, key: str, width: int) -> str:
        padded = key.ljust(width)
        if not self.markup:
            return padded
        color = self.ap.get("key_color", "")
        inner = f"<b>{_m(padded)}</b>"
        if color:
            inner = f"<span foreground='{_m(color)}'>{inner}</span>"
        font = self.ap.get("key_font", "monospace")
        return f"<span font_family='{_m(font)}'>{inner}</span>" if font and font != "monospace" else f"<tt>{inner}</tt>"

    def row(self, k, width: int, app_tag: str = "") -> str:
        desc = k.desc
        auto = not desc
        if auto:
            desc = humanize(k.action) or k.action or "(no action)"
        parts = []
        if app_tag:
            parts.append(f"<tt><small>{_m(app_tag)}</small></tt>" if self.markup else app_tag)
        parts.append(self.key(k.key, width))
        parts.append(f"<i>{_m(desc)}</i>" if self.markup and auto else (_m(desc) if self.markup else desc))
        extras = []
        # an auto description is the action (or its humanized name): no need to repeat it
        if self.ap.get("show_action", True) and k.action and not auto and k.action != desc:
            extras.append(_short(k.action, 70))
        if self.ap.get("show_section", True) and k.section:
            extras.append(f"[{k.section}]")
        if k.default and self.ap.get("default_tag"):
            extras.append(str(self.ap["default_tag"]))
        if extras:
            tail = "   ".join(extras)
            parts.append(f"<small><span alpha='60%'>{_m(tail)}</span></small>" if self.markup else tail)
        return "  ".join(parts)

    def width(self, keymaps) -> int:
        cap = int(self.ap.get("max_key_width", 26) or 26)
        return min(max((len(k.key) for k in keymaps), default=4), cap)


def _short(s: str, n: int) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[:n - 1] + "…"


# --------------------------------------------------------------- screens --

class Session:
    def __init__(self, cfg: dict, rofi: Rofi):
        self.cfg, self.rofi = cfg, rofi
        self.fmt = Formatter(cfg["appearance"])
        self.show_defaults = True
        self.keys = cfg["keys"]
        self.icons = desktop_icons() if cfg["appearance"].get("icons", True) else {}
        self.esc_label = "back"  # what Esc does in the keymap view, shown in its hint line

    # --- step 1: what to include -------------------------------------------
    def pick_categories(self, apps: list[App], preselected: set) -> list[App] | None:
        chosen, sel = set(preselected), 0
        while True:
            by_cat = {c: [a for a in apps if a.category == c] for c in CATEGORIES}
            cats = [c for c, lst in by_cat.items() if lst]
            rows = ["▶  <b>Show keymaps</b>" if self.fmt.markup else "▶  Show keymaps"]
            for c in cats:
                mark = "☑" if c in chosen else "☐"
                names = ", ".join(a.name for a in by_cat[c])
                rows.append(f"{mark}  {_m(CATEGORIES[c])}   <small><span alpha='60%'>{_m(names)}</span></small>"
                            if self.fmt.markup else f"{mark}  {CATEGORIES[c]}   {names}")
            rows.append("＋  Read another file…")
            mesg = ("What should be included?  <b>Enter</b> toggles a group · "
                    "<b>Enter</b> on ▶ continues · <b>Esc</b> quits")
            code, idx = self.rofi.show(rows, "Keymaps", mesg, sel)
            if code != ACCEPT or idx is None:
                return None
            sel = idx
            if idx == 0:
                picked = [a for a in apps if a.category in chosen]
                if picked:
                    return picked
                continue
            if idx == len(rows) - 1:
                app = self._ask_file()
                if app:
                    apps.append(app)
                    chosen.add(app.category)
                continue
            cat = cats[idx - 1]
            chosen.symmetric_difference_update({cat})

    def _ask_file(self) -> App | None:
        path = self.rofi.ask_text("File", "Type the path of a file with keymaps (e.g. ~/.config/app/keys.conf).\n"
                                          "It is read with the automatic reader; add it to config.toml to keep it.")
        if not path:
            return None
        p = Path(path).expanduser()
        if not p.is_file():
            self.rofi.show([f"Not a file: {_m(path)}"], "Error", "Press Esc to go back")
            return None
        return App(f"file:{p}", p.name, "other", load_generic, paths=(str(p),))

    # --- step 2/3: browse ----------------------------------------------------
    def browse(self, loaded: list, came_from_picker: bool) -> bool:
        """loaded = [(App, keymaps, notes)]. Returns True to go back to the picker."""
        if len(loaded) == 1:  # one app: open its keymaps straight away, Esc leaves this step
            self.esc_label = "back" if came_from_picker else "quit"
            return self.keymap_view(loaded, 1) == "back" and came_from_picker
        self.esc_label = "back"
        sel = 0
        while True:
            rows, icons = [], []
            total = sum(len(self._visible(k)) for _, k, _ in loaded)
            rows.append(self._app_row("All apps", "everything below", total, None))
            icons.append("view-list")
            for app, kms, _ in loaded:
                rows.append(self._app_row(app.name, CATEGORIES.get(app.category, ""), len(self._visible(kms)),
                                          sum(not k.default for k in kms)))
                icons.append(next((self.icons[b] for b in app.bins if b in self.icons), app.icon))
            back = "back to the groups" if came_from_picker else "quit"
            code, idx = self.rofi.show(rows, "Keymaps", f"<b>Enter</b> opens an app · <b>Esc</b> {back}",
                                       sel, icons)
            if code != ACCEPT or idx is None:
                return came_from_picker
            sel = idx
            result = self.keymap_view(loaded, idx)
            if result == "quit":
                return False

    def _app_row(self, name, category, count, mine):
        mine_txt = f" · {mine} yours" if mine else ""
        noun = "aliases" if category == CATEGORIES["alias"] else "keymaps"
        if not self.fmt.markup:
            return f"{name}   {category} · {count} {noun}{mine_txt}"
        return f"<b>{_m(name)}</b>   <small><span alpha='60%'>{_m(category)} · {count} {noun}{mine_txt}</span></small>"

    def _visible(self, keymaps):
        return keymaps if self.show_defaults else [k for k in keymaps if not k.default]

    def keymap_view(self, loaded: list, pos: int):
        """pos 0 = all apps, n = loaded[n-1]."""
        sel, details = 0, None
        while True:
            if pos == 0:
                pairs = [(app, k) for app, kms, _ in loaded for k in self._visible(kms)]
                notes, title = [], "All apps"
            else:
                app, kms, notes = loaded[pos - 1]
                pairs, title = [(app, k) for k in self._visible(kms)], app.name
            width = self.fmt.width([k for _, k in pairs])
            tagw = max((len(a.name) for a, _ in pairs), default=0)
            if pairs:
                rows = [self.fmt.row(k, width, a.name.ljust(tagw) if pos == 0 else "") for a, k in pairs]
            else:
                rows = ["(no keymaps found — see the notes above)"]
            mesg = self._mesg(loaded, pos, notes, pairs[details][1] if details is not None and pairs else None)
            code, idx = self.rofi.show(rows, title, mesg, sel)
            if code == CANCEL:
                return "back"
            if code == NEXT:
                pos, sel, details = (pos + 1) % (len(loaded) + 1), 0, None
            elif code == PREV:
                pos, sel, details = (pos - 1) % (len(loaded) + 1), 0, None
            elif code == TOGGLE:
                self.show_defaults, sel, details = not self.show_defaults, 0, None
            elif code == ACCEPT and idx is not None and pairs:
                details = None if details == idx else idx
                sel = idx
            elif code != ACCEPT:
                return "quit"

    def _mesg(self, loaded, pos, notes, detail) -> str:
        names = ["All"] + [a.name for a, _, _ in loaded]
        accent = self.cfg["appearance"].get("accent", "")
        bar = []
        for i, n in enumerate(names):
            if i == pos:
                cur = f"<b><u>{_m(n)}</u></b>"
                bar.append(f"<span foreground='{_m(accent)}'>{cur}</span>" if accent else cur)
            else:
                bar.append(f"<span alpha='55%'>{_m(n)}</span>")
        lines = ["  ".join(_window(bar, pos, 9))]
        if detail is not None:
            lines.append(self._details(detail))
        elif notes:
            lines.append(f"<small>{_m(' · '.join(notes))}</small>")
        defaults = "hide" if self.show_defaults else "show"
        lines.append(f"<small><span alpha='70%'><b>{_m(_first_key(self.keys, 'prev_app'))}</b>/"
                     f"<b>{_m(_first_key(self.keys, 'next_app'))}</b> app · <b>Enter</b> details · "
                     f"<b>{_m(_first_key(self.keys, 'toggle_defaults'))}</b> {defaults} defaults · "
                     f"<b>Esc</b> {self.esc_label}</span></small>")
        return "\n".join(lines)

    def _details(self, k) -> str:
        rows = [f"<b>{_m(k.key)}</b>  {_m(k.desc or '(no description in the config)')}"]
        if k.action:
            rows.append(f"Action: <tt>{_m(_short(k.action, 160))}</tt>")
        info = [f"Mode: {k.section}" if k.section else "", f"From: {k.source}" if k.source else "",
                "built-in default" if k.default else ""]
        rows.append(_m(" · ".join(i for i in info if i)))
        return "\n".join(rows)


def _window(items, pos, size):
    """Show at most ``size`` app names around the current one."""
    if len(items) <= size:
        return items
    start = max(0, min(pos - size // 2, len(items) - size))
    out = items[start:start + size]
    return (["…"] if start else []) + out + (["…"] if start + size < len(items) else [])


def run_ui(cfg, apps, ask: bool, load_all):
    """Main loop. ``load_all(apps)`` -> [(App, keymaps, notes)]."""
    s = Session(cfg, Rofi(cfg))
    s.show_defaults = bool(cfg.get("defaults", True))
    wanted = cfg.get("categories") or list(CATEGORIES)
    preselected = {a.category for a in apps if a.category in wanted}
    while True:
        chosen = s.pick_categories(apps, preselected) if ask else apps
        if not chosen:
            return
        preselected = {a.category for a in chosen}
        if not s.browse(load_all(chosen), ask) or not ask:
            return

