"""Command line entry point."""

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import __version__
from . import apps as registry
from .config import CONFIG_DIR, load_config, load_descriptions
from .sources import rel, which

# C0/C1 controls and DEL -> space: config text must not drive the terminal (escapes) nor split TSV rows.
_PRINTABLE = dict.fromkeys([*range(32), *range(127, 160)], " ")


def finalize(app, keymaps, descriptions: dict, user_first: bool):
    """Drop unbinds and exact duplicates, apply descriptions.toml, order rows."""
    table = descriptions.get(app.id, {})
    seen, out = set(), []
    for k in keymaps:
        ident = (k.section, k.key, k.action)
        if k.removed or not k.key or ident in seen:
            continue
        seen.add(ident)
        k.desc = table.get(f"[{k.section}] {k.key}") or table.get(k.key) or k.desc
        out.append(k)
    # stable sort: inside each group the file order is kept; main mode before other modes
    out.sort(key=lambda k: (k.default if user_first else False, k.section != ""))
    return out


def collect(apps, cfg, descriptions):
    """Load every app in parallel -> [(App, keymaps, notes)] in the given order."""
    store = None
    if cfg.get("cache") is True:
        from .cache import Cache  # imported only when enabled: without it hyprmaps writes no file
        store = Cache()
    load = store.load if store else registry.load
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda a: load(a, cfg), apps))
    if store:
        store.save()
    user_first = bool(cfg["appearance"].get("user_first", True))
    return [(a, finalize(a, kms, descriptions, user_first), notes) for a, (kms, notes) in zip(apps, results, strict=True)]


def _parse_add(values):
    out = []
    for v in values or []:
        name, sep, path = v.partition("=")
        if not sep:
            name, path = Path(v).name, v
        out.append((name.strip() or Path(path).name, str(Path(path.strip()).expanduser())))
    return out


def _print_list(loaded, all_apps, present):
    print(f"{'':2}{'ID':14}{'NAME':16}{'CATEGORY':22}{'KEYMAPS':>8}  CONFIG")
    counts = {a.id: (len(k), notes) for a, k, notes in loaded}
    for app in all_apps:
        mark = "✔" if app.id in present else "·"
        n, notes = counts.get(app.id, ("", []))
        paths = ", ".join(rel(Path(p)) for p in app.config_paths()) or "-"
        print(f"{mark:2}{app.id[:13]:14}{app.name[:15]:16}{registry.CATEGORIES.get(app.category, app.category):22}"
              f"{n!s:>8}  {paths}")
        for note in notes:
            print(f"{'':54}↳ {note.translate(_PRINTABLE)}")


def _print_dump(loaded, as_json: bool):
    rows = [{"app": a.id, "app_name": a.name, "section": k.section, "key": k.key, "description": k.desc,
             "action": k.action, "source": k.source, "default": k.default}
            for a, kms, _ in loaded for k in kms]
    if as_json:
        json.dump(rows, sys.stdout, ensure_ascii=False, indent=1)
        print()
        return
    cols = ["app", "section", "key", "description", "action", "source", "default"]
    print("\t".join(cols))
    for r in rows:
        print("\t".join(str(r[c]).translate(_PRINTABLE) for c in cols))


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="hyprmaps",
        description="Browse the keymaps of your Hyprland desktop in rofi. Read-only: no file is written, except the cache if you turn it on.")
    p.add_argument("--all", action="store_true", help="skip the first question and include every app found")
    p.add_argument("--only", metavar="IDS", help="comma-separated app ids to show, e.g. hyprland,kitty,nvim")
    p.add_argument("--add", action="append", metavar="[NAME=]PATH",
                   help="also read this file with the automatic reader (repeatable)")
    p.add_argument("--config", metavar="PATH", type=Path, help=f"config file (default {rel(CONFIG_DIR / 'config.toml')})")
    p.add_argument("--descriptions", metavar="PATH", type=Path,
                   help=f"descriptions file (default {rel(CONFIG_DIR / 'descriptions.toml')})")
    p.add_argument("--list", action="store_true", help="print every known app, whether it was found and where")
    p.add_argument("--dump", action="store_true", help="print the keymaps as TSV instead of opening rofi")
    p.add_argument("--json", action="store_true", help="with --dump: print JSON")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = p.parse_args(argv)

    cfg = load_config(args.config)
    descriptions = load_descriptions(args.descriptions)
    all_apps = registry.all_apps(cfg, _parse_add(args.add))
    present = [a for a in all_apps if registry.is_present(a, cfg)]

    if args.only:
        ids = [i.strip() for i in args.only.split(",") if i.strip()]
        unknown = [i for i in ids if not any(a.id == i for a in all_apps)]
        if unknown:
            print(f"hyprmaps: unknown app id(s): {', '.join(unknown)} (see --list)", file=sys.stderr)
            return 2
        present = [a for i in ids for a in all_apps if a.id == i]
    # files given with --add are always wanted
    present += [a for a in all_apps if a.id.startswith("file:") and a not in present]

    if args.list:
        _print_list(collect(present, cfg, descriptions), all_apps, {a.id for a in present})
        return 0
    if args.dump:
        _print_dump(collect(present, cfg, descriptions), args.json)
        return 0
    if not which("rofi"):
        print("hyprmaps: rofi is not installed (use --dump to print the keymaps)", file=sys.stderr)
        return 1
    if not present:
        print("hyprmaps: no known app found (see --list)", file=sys.stderr)
        return 1

    from .ui import run_ui
    chosen_on_cli = bool(args.all or args.only)
    ask = not chosen_on_cli and cfg.get("ask", True) is not False
    if not ask and not chosen_on_cli and cfg.get("categories"):
        present = [a for a in present if a.category in cfg["categories"]]
    # In rofi the global `defaults` is only the starting state of the Alt+d toggle,
    # so defaults are always loaded (per-app `defaults = false` still drops them).
    ui_cfg = {**cfg, "defaults": True}
    run_ui(cfg, present, ask, lambda apps: collect(apps, ui_cfg, descriptions))
    return 0
