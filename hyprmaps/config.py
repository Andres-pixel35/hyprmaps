"""User settings: ~/.config/hyprmaps/config.toml and descriptions.toml.

Both files are only read, never created or written: copy the examples from
the repository to create them. Every setting has a default, so the tool runs
without any file.
"""

import re
import sys
import tomllib
from pathlib import Path

from .sources import CONFIG, read_text

CONFIG_DIR = CONFIG / "hyprmaps"

APPEARANCE = {
    # rofi theme -------------------------------------------------------------
    "theme": "",                 # path to a .rasi file; "" = your normal rofi theme
    "width": "60%",              # window width (px, %, em); "" = keep the theme's
    "lines": 15,                 # visible rows; 0 = keep the theme's
    "location": "center",        # center, north, south, east, west, north-west, ...; "" = theme's
    "font": "",                  # whole window, e.g. "JetBrainsMono Nerd Font 11"; "" = theme's
    "background": "",            # colours: any rofi colour (#rrggbb, #rrggbbaa, rgba(...)); "" = theme's
    "foreground": "",
    "accent": "",                # border, prompt and the current app in the app bar
    "selected_background": "",
    "selected_foreground": "",
    "border_width": "",          # e.g. "2px"
    "border_radius": "",         # e.g. "12px"
    "padding": "",               # e.g. "12px"
    "force_layout": True,        # make sure the message box (app bar, notes) is shown
    "icons": True,               # app icons in the app list
    # rows ---------------------------------------------------------------------
    "markup": True,              # styled rows (bold keys, dim actions); false = plain text
    "key_font": "monospace",     # font family of the key column (keeps the columns aligned)
    "key_color": "",             # colour of the key column; "" = normal text colour
    "max_key_width": 26,         # key column width cap, in characters
    "show_action": True,         # show the raw action next to the description
    "show_section": True,        # show the mode / table (insert, copy-mode, ...)
    "default_tag": "default",    # tag on built-in defaults; "" = no tag
    "user_first": True,          # list your own binds before the app defaults
}

KEYS = {
    "next_app": "Shift+Right,Control+Tab",
    "prev_app": "Shift+Left,Control+ISO_Left_Tab",
    "toggle_defaults": "Alt+d",
}

# Values end up inside a rofi theme string: allow only what themes need.
_SAFE = re.compile(r"^[\w\s#%().,+\-/]*$")


def load_config(path: Path | None = None) -> dict:
    path = path or CONFIG_DIR / "config.toml"
    text = read_text(path)
    cfg = {}
    if text is not None:
        try:
            cfg = tomllib.loads(text)
        except (tomllib.TOMLDecodeError, RecursionError) as e:  # [[[[... too deep
            print(f"hyprmaps: {path}: {e} (using defaults)", file=sys.stderr)
    for name, kind in (("appearance", dict), ("keys", dict), ("apps", dict), ("custom", list), ("categories", list)):
        if not isinstance(cfg.get(name, kind()), kind):
            print(f"hyprmaps: {path}: {name} must be a {'table' if kind is dict else 'list'}; ignored", file=sys.stderr)
            del cfg[name]
    for app, table in list(cfg.get("apps", {}).items()):
        if not isinstance(table, dict):
            print(f"hyprmaps: {path}: apps.{app} must be a table; ignored", file=sys.stderr)
            del cfg["apps"][app]
    for table in [*cfg.get("apps", {}).values(), *(c for c in cfg.get("custom", []) if isinstance(c, dict))]:
        if isinstance(table.get("paths"), str):  # paths = "~/x" is paths = ["~/x"], not 4 one-letter paths
            table["paths"] = [table["paths"]]
    for name in ("lines", "max_key_width"):
        try:
            int(cfg.get("appearance", {}).get(name) or 0)
        except (TypeError, ValueError):
            print(f"hyprmaps: {path}: appearance.{name} must be a number; ignored", file=sys.stderr)
            del cfg["appearance"][name]
    cfg["appearance"] = {**APPEARANCE, **cfg.get("appearance", {})}
    cfg["keys"] = {**KEYS, **cfg.get("keys", {})}
    cfg.setdefault("apps", {})
    cfg.setdefault("custom", [])
    return cfg


def load_descriptions(path: Path | None = None) -> dict:
    """{app_id: {key: description}}; keys can be 'Ctrl + T' or '[insert] <C-s>'."""
    path = path or CONFIG_DIR / "descriptions.toml"
    text = read_text(path)
    if text is None:
        return {}
    try:
        data = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, RecursionError) as e:  # [[[[... too deep
        print(f"hyprmaps: {path}: {e} (descriptions ignored)", file=sys.stderr)
        return {}
    return {app: {str(k): str(v) for k, v in table.items()}
            for app, table in data.items() if isinstance(table, dict)}


def _safe(name: str, value) -> str:
    v = str(value).strip()
    if v and not _SAFE.match(v):
        print(f"hyprmaps: appearance.{name} = {v!r} has characters a theme cannot take; ignored",
              file=sys.stderr)
        return ""
    return v


def theme_args(ap: dict) -> list[str]:
    """rofi arguments for the [appearance] settings; empty settings change nothing."""
    args, rules = [], []
    theme = str(ap.get("theme", "")).strip()
    if theme:
        p = Path(theme).expanduser()
        if p.is_file():
            args += ["-theme", str(p)]
        else:
            print(f"hyprmaps: theme {theme!r} not found; using your normal rofi theme", file=sys.stderr)
    window = []
    for name, prop in (("width", "width"), ("background", "background-color"),
                       ("accent", "border-color"), ("border_width", "border"),
                       ("border_radius", "border-radius"), ("padding", "padding")):
        v = _safe(name, ap.get(name, ""))
        if v:
            window.append(f"{prop}: {v};")
    loc = _safe("location", ap.get("location", ""))
    if loc:
        window.append(f"location: {loc}; anchor: {loc}; x-offset: 0; y-offset: 0;")
    if window:
        rules.append("window { " + " ".join(window) + " }")
    lines = int(ap.get("lines") or 0)
    if lines > 0:
        rules.append(f"listview {{ lines: {lines}; fixed-height: true; dynamic: false; }}")
    fg, accent = _safe("foreground", ap.get("foreground", "")), _safe("accent", ap.get("accent", ""))
    if fg:
        rules.append(f"element-text, entry, message, textbox {{ text-color: {fg}; }}")
    if accent:
        rules.append(f"prompt {{ text-color: {accent}; }}")
    sel_bg = _safe("selected_background", ap.get("selected_background", ""))
    sel_fg = _safe("selected_foreground", ap.get("selected_foreground", ""))
    if sel_bg or sel_fg:
        sel = (f"background-color: {sel_bg};" if sel_bg else "") + (f" text-color: {sel_fg};" if sel_fg else "")
        rules.append(f"element selected.normal, element selected.active {{ {sel} }}")
        rules.append("element-text { background-color: transparent; text-color: inherit; }")
    if ap.get("force_layout", True):
        rules.append("mainbox { children: [ inputbar, message, listview ]; }")
        rules.append("message { enabled: true; padding: 4px 12px; }")
    font = str(ap.get("font", "")).strip()
    if font and '"' not in font:
        args += ["-font", font]
    for r in rules:
        args += ["-theme-str", r]
    return args
