"""Registry of known apps, plus the apps the user defines in the config."""

import re
import sys
import traceback

from ..model import Keymap
from ..sources import expand
from . import browsers, editors, misc, multiplexers, shells, terminals, wm
from .base import CATEGORIES, App, Ctx
from .generic import load_generic

BUILTIN: list[App] = [*wm.APPS, *terminals.APPS, *shells.APPS, *multiplexers.APPS,
                      *editors.APPS, *browsers.APPS, *misc.APPS]
_BY_ID = {a.id: a for a in BUILTIN}


def custom_app(spec: dict) -> App | None:
    """An app from a [[custom]] table: name, paths and a format, which is
    'auto', 'regex' or the id of a built-in app whose reader should be used."""
    name = str(spec.get("name", "")).strip()
    paths = spec.get("paths") or ([spec["path"]] if spec.get("path") else [])
    if not name or not isinstance(paths, list) or not paths:
        return None
    fmt = str(spec.get("format", "auto"))
    load = _BY_ID[fmt].load if fmt in _BY_ID else load_generic
    category = spec.get("category", "other")
    app_id = str(spec.get("id") or re.sub(r"\W+", "-", name.lower()).strip("-"))
    return App(app_id, name, category if category in CATEGORIES else "other", load,
               paths=tuple(map(str, paths)), icon=str(spec.get("icon", "")), options=dict(spec))


def all_apps(cfg: dict, extra_files=()) -> list[App]:
    """Built-in apps (minus disabled ones) + [[custom]] apps + --add files."""
    opts = cfg.get("apps", {})
    apps = [a for a in BUILTIN if opts.get(a.id, {}).get("enabled", True) is not False]
    for spec in cfg.get("custom", []):
        app = custom_app(spec) if isinstance(spec, dict) else None
        if app is None:
            print(f"hyprmaps: ignoring [[custom]] entry without name/paths: {spec}", file=sys.stderr)
        elif not any(a.id == app.id for a in apps):
            apps.append(app)
    for name, path in extra_files:
        apps.append(App(f"file:{path}", name, "other", load_generic, paths=(path,)))
    return apps


def is_present(app: App, cfg: dict) -> bool:
    """Installed or configured. Apps listed in the config are always present."""
    o = cfg.get("apps", {}).get(app.id, {})
    if o.get("paths") or app.id.startswith("file:") or not app.bins:
        return bool(app.config_paths(o.get("paths")))
    return app.detected()


def load(app: App, cfg: dict) -> tuple[list[Keymap], list[str]]:
    """Run one app's reader. A broken reader turns into a note, never a crash."""
    o = cfg.get("apps", {}).get(app.id, {})
    ctx = Ctx(app, app.config_paths(o.get("paths")),
              defaults=bool(o.get("defaults", cfg.get("defaults", True))),
              live=bool(o.get("live", False)), options={**app.options, **o})
    try:
        keymaps = app.load(ctx)
    except Exception as e:  # one bad parser must not hide every other app
        traceback.print_exc(file=sys.stderr)
        return [], ctx.notes + [f"Reader error: {type(e).__name__}: {e}"]
    return keymaps, ctx.notes


def paths_for(app: App, cfg: dict) -> list[str]:
    o = cfg.get("apps", {}).get(app.id, {})
    return [str(p) for p in app.config_paths(o.get("paths"))] or \
        [str(x) for p in (o.get("paths") or app.paths) for x in expand(p)]
