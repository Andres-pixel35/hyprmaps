"""Browsers.

Firefox family (Firefox, Zen, LibreWolf, Floorp, Waterfox):
- defaults: the ``<key>`` elements of the installed browser's main window,
  read from its ``omni.ja`` (located through the profile's compatibility.ini),
  with key letters and names from its Fluent strings. Fork-specific shortcuts
  (Zen workspaces, ...) and version changes are therefore exact. A built-in
  table is the fallback when the install is not found.
- your changes: ``<profile>/customKeys.json`` (about:keyboard).
- extensions: ``commands`` of each extension manifest, overridden by
  ``<profile>/extension-settings.json`` (Manage Extension Shortcuts).

Chromium family (Chromium, Chrome, Brave, Vivaldi, Edge, Opera):
- defaults: a table of Chromium's Linux accelerators; Edge and Opera have their
  own tables. Brave and Vivaldi keep their full table in the profile, which is
  read instead.
- your changes: Brave ``brave.accelerators``, Vivaldi ``vivaldi.actions``,
  Opera ``Keybindings`` (defaults from a JSON resource in its opera.pak).
- extensions: ``extensions.commands`` in the profile's Preferences.

qutebrowser: defaults from the installed package's configdata.yml, your binds
from autoconfig.yml (``:bind``) and config.py.
"""

import configparser
import html
import json
import os
import re
from pathlib import Path

from ..model import SEQ, Keymap, block_comments, combo, humanize, merge, pretty_angle_keys
from ..sources import CONFIG, HOME, Jar, expand, getenv, pak_find, read_json, read_text, rel, which
from .base import App, Ctx
from .chromium_ids import COMMAND_IDS

# ------------------------------------------------------------ Firefox family --

# Fallback when the installed browser cannot be read: Firefox's own <key>
# elements (Firefox 159, Linux, en-US). (key id, keys, description); several keys = alternatives.
_FIREFOX = [
    ("key_newNavigatorTab", "Ctrl+T", "New tab"),
    ("key_newNavigator", "Ctrl+N", "New window"),
    ("key_privatebrowsing", "Ctrl+Shift+P", "New private window"),
    ("key_close", "Ctrl+W", "Close tab"),
    ("key_closeWindow", "Ctrl+Shift+W", "Close window"),
    ("key_restoreLastClosedTabOrWindowOrSession", "Ctrl+Shift+T", "Reopen closed tab"),
    ("key_undoCloseWindow", "Ctrl+Shift+N", "Reopen closed window"),
    ("key_quitApplication", "Ctrl+Q", "Quit"),
    *[(f"key_selectTab{i}", f"Alt+{i}", f"Go to tab {i}") for i in range(1, 9)],
    ("key_selectLastTab", "Alt+9", "Go to last tab"),
    ("key_searchTabs", "Ctrl+Shift+A", "Search tabs"),
    ("key_showAllTabs", "Ctrl+Shift+Tab", "List all tabs"),
    ("key_toggleMute", "Ctrl+M", "Mute or unmute tab"),
    ("focusURLBar", "Ctrl+L", "Focus address bar"),
    ("focusURLBar2", "Alt+D", "Focus address bar"),
    ("key_search", "Ctrl+K", "Web search"),
    ("key_search2", "Ctrl+J", "Web search"),
    ("key_find", "Ctrl+F", "Find in page"),
    ("key_findAgain", "Ctrl+G", "Find next"),
    ("key_findAgain2", "F3", "Find next"),
    ("key_findPrevious", "Ctrl+Shift+G", "Find previous"),
    ("key_findPrevious2", "Shift+F3", "Find previous"),
    ("key_reload", "Ctrl+R", "Reload"),
    ("key_reload2", "F5", "Reload"),
    ("key_reload_skip_cache", "Ctrl+Shift+R", "Reload, bypassing the cache"),
    ("key_reload_skip_cache2", "Ctrl+F5", "Reload, bypassing the cache"),
    ("key_stop", "Esc", "Stop loading"),
    ("goBackKb", "Alt+Left", "Back"),
    ("goForwardKb", "Alt+Right", "Forward"),
    ("goBackKb2", "Ctrl+[", "Back"),
    ("goForwardKb2", "Ctrl+]", "Forward"),
    ("goHome", "Alt+Home", "Home page"),
    ("addBookmarkAsKb", "Ctrl+D", "Bookmark this page"),
    ("bookmarkAllTabsKb", "Ctrl+Shift+D", "Bookmark all tabs"),
    ("viewBookmarksSidebarKb", "Ctrl+B", "Bookmarks sidebar"),
    ("viewBookmarksToolbarKb", "Ctrl+Shift+B", "Show/hide bookmarks toolbar"),
    ("manBookmarkKb", "Ctrl+Shift+O", "Library: all bookmarks"),
    ("key_gotoHistory", "Ctrl+H", "History sidebar"),
    ("showAllHistoryKb", "Ctrl+Shift+H", "Library: all history"),
    ("toggleSidebarKb", "Ctrl+Alt+Z", "Show/hide sidebar"),
    ("viewOpenTabsSidebarKb", "Ctrl+Alt+U", "Open tabs sidebar"),
    ("key_openDownloads", "Ctrl+Shift+Y", "Downloads"),
    ("key_openAddons", "Ctrl+Shift+F", "Add-ons and themes"),
    ("key_sanitize", "Ctrl+Shift+Delete", "Clear recent history"),
    ("key_savePage", "Ctrl+S", "Save page"),
    ("printKb", "Ctrl+P", "Print"),
    ("openFileKb", "Ctrl+O", "Open file"),
    ("key_viewSource", "Ctrl+U", "View page source"),
    ("key_viewInfo", "Ctrl+I", "Page info"),
    ("key_screenshot", "Ctrl+Shift+S", "Take a screenshot"),
    ("key_togglePictureInPicture", "Ctrl+Shift+]", "Picture-in-Picture"),
    ("viewGenaiChatSidebarKb", "Ctrl+Alt+X", "AI chatbot sidebar"),
    ("key_aboutProcesses", "Shift+Esc", "Task manager"),
    ("key_switchTextDirection", "Ctrl+Shift+X", "Switch text direction"),
    ("key_fullZoomEnlarge", "Ctrl++", "Zoom in"),
    ("key_fullZoomReduce", "Ctrl+-", "Zoom out"),
    ("key_fullZoomReset", "Ctrl+0", "Reset zoom"),
    ("key_enterFullScreen", "F11", "Full screen"),
]

# Shortcuts that are not <key> elements of the main window (tab strip, find
# bar, DevTools), so they are always added. DevTools ids are "key_" + its tool id.
_FF_EXTRA = [
    ("", "Ctrl+Tab Ctrl+PageDown", "Next tab"),
    ("", "Ctrl+Shift+Tab Ctrl+PageUp", "Previous tab"),
    ("", "Ctrl+Shift+PageUp", "Move tab left"),
    ("", "Ctrl+Shift+PageDown", "Move tab right"),
    ("", "F6", "Focus address bar"),
    ("", "/", "Quick find"),
    ("", "F7", "Caret browsing"),
    ("key_toggleToolboxF12", "F12", "Web developer tools"),
    ("key_toggleToolbox", "Ctrl+Shift+I", "Web developer tools"),
    ("key_browserToolbox", "Ctrl+Alt+Shift+I", "Browser toolbox"),
    ("key_browserConsole", "Ctrl+Shift+J", "Browser console"),
    ("key_responsiveDesignMode", "Ctrl+Shift+M", "Responsive design mode"),
    ("key_inspector", "Ctrl+Shift+C", "Inspect element"),
    ("key_webconsole", "Ctrl+Shift+K", "Web console"),
    ("key_jsdebugger", "Ctrl+Shift+Z", "JavaScript debugger"),
    ("key_netmonitor", "Ctrl+Shift+E", "Network monitor"),
    ("key_styleeditor", "Shift+F7", "Style editor"),
    ("key_performance", "Shift+F5", "Performance"),
    ("key_storage", "Shift+F9", "Storage inspector"),
    ("key_accessibility", "Shift+F12", "Accessibility inspector"),
]
_FF_NAMES = {ident: desc for ident, _, desc in _FIREFOX + _FF_EXTRA if ident}

_FF_MODS = {"accel": "Ctrl", "control": "Ctrl", "alt": "Alt", "shift": "Shift",
            "meta": "Super", "os": "Super", "access": "Alt"}
_FF_KEYS = {"BACK": "Backspace", "ESCAPE": "Esc", "RETURN": "Enter"}
_FF_XHTML = "chrome/browser/content/browser/browser.xhtml"
# Where about:keyboard (and Zen) name the shortcuts that have no menu item.
_FF_TITLE_SCRIPTS = ("moz-src/browser/components/customkeys/CustomKeysParent.sys.mjs",
                     "modules/zen/kbs/ZenKeyboardShortcuts.sys.mjs")
_XML_ATTR = re.compile(r'([\w:-]+)="([^"]*)"')


def _table_keymaps(rows, source: str):
    """Expand 'Ctrl+G F3' alternatives into one Keymap per key."""
    for row in rows:
        ident, keys, desc = row if len(row) == 3 else ("", *row)
        for k in keys.split():
            mods, key = (k[:-2].split("+"), "+") if k.endswith("++") else (k.split("+")[:-1], k.split("+")[-1])
            yield ident, Keymap(combo(mods, key), "", desc, "", source, default=True)


def _ff_combo(modifiers: str, key: str) -> str:
    """Firefox notation (modifiers="accel,shift", key="T" or keycode="VK_F5")."""
    mods = [_FF_MODS.get(m.strip().lower(), m.strip()) for m in modifiers.split(",") if m.strip()]
    key = key.removeprefix("VK_")
    return combo(mods, "Space" if key == " " else _FF_KEYS.get(key, key))


def firefox_profile(root: Path) -> Path | None:
    """Default profile dir of a Firefox-family browser data root."""
    ini = configparser.RawConfigParser(strict=False)
    ini.optionxform = str
    try:
        ini.read(root / "profiles.ini", encoding="utf-8")
    except configparser.Error:
        return None

    def path_of(value, relative=True):
        p = root / value if relative else Path(value)
        return p if p.is_dir() else None

    for sec in ini.sections():  # [InstallXXXX] Default= is the profile in use
        if sec.startswith("Install") and ini.get(sec, "Default", fallback=None):
            p = path_of(ini.get(sec, "Default"))
            if p:
                return p
    profiles = [s for s in ini.sections() if s.startswith("Profile")]
    profiles.sort(key=lambda s: ini.get(s, "Default", fallback="0") != "1")
    for sec in profiles:
        p = path_of(ini.get(sec, "Path", fallback=""), ini.get(sec, "IsRelative", fallback="1") == "1")
        if p:
            return p
    return None


def firefox_app_dir(profile: Path) -> Path | None:
    """The ``browser`` dir (holding omni.ja) of the install that last ran this profile."""
    ini = configparser.RawConfigParser(strict=False)
    try:
        ini.read(profile / "compatibility.ini", encoding="utf-8")
    except configparser.Error:
        return None
    d = ini.get("Compatibility", "LastAppDir", fallback="")
    cands = [Path(d)] if d.startswith("/") else []
    if d.startswith("/snap/"):  # the recorded revision may be gone after a refresh
        cands.append(Path(re.sub(r"^/snap/([^/]+)/[^/]+/", r"/snap/\1/current/", d)))
    m = re.search(r"/\.var/app/([^/]+)/", str(profile))
    if d.startswith("/app/") and m:  # Flatpak records the path inside its sandbox
        rest = d.removeprefix("/app/")
        cands = [base / m.group(1) / "current/active/files" / rest
                 for base in (HOME / ".local/share/flatpak/app", Path("/var/lib/flatpak/app"))]
    return next((c for c in cands if (c / "omni.ja").is_file()), None)


def _ff_locales(profile: Path) -> list[str]:
    """UI locales to try, best first: the browser's requested locales, then $LANG."""
    m = re.search(r'user_pref\("intl\.locale\.requested",\s*"([^"]*)"\)', read_text(profile / "prefs.js") or "")
    found = [x.strip() for x in m.group(1).split(",") if x.strip()] if m else []
    lang = (getenv("LANG") or "").split(".")[0].replace("_", "-")
    return [*found, lang, lang.split("-")[0], "en-US"]


_FTL_MSG = re.compile(r"^(-?[A-Za-z][\w-]*)\s*=\s*(.*)$")
_FTL_ATTR = re.compile(r"^\s+\.([\w-]+)\s*=\s*(.*)$")
_FTL_PLATFORM = re.compile(r"\{\s*PLATFORM\(\)\s*->(.*)\}", re.S)
_FTL_VARIANT = re.compile(r"(\*?)\[(\w+)\]\s*(.*?)\s*(?=\*?\[\w+\]|$)", re.S)


def ftl_parse(text: str, msgs: dict) -> dict:
    """Minimal Fluent reader: id -> {"": value, "attr": value}. Lines that
    continue a value are joined with spaces; selectors stay as text."""
    cur, attr = None, ""
    for line in text.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        m = _FTL_MSG.match(line)
        if m:
            cur, attr = msgs.setdefault(m.group(1), {}), ""
            cur[attr] = m.group(2).strip()
            continue
        m = _FTL_ATTR.match(line)
        if cur is not None and m:
            attr = m.group(1)
            cur[attr] = m.group(2).strip()
        elif cur is not None and line[:1].isspace():
            cur[attr] = f"{cur.get(attr, '')} {line.strip()}".strip()
        else:
            cur = None
    return msgs


def ftl_value(msgs: dict, ident: str, attr: str = "") -> str:
    """Text of a message: PLATFORM() picks the linux variant, -terms are
    filled in; anything else that needs runtime arguments gives ''."""
    v = msgs.get(ident, {}).get(attr, "")
    m = _FTL_PLATFORM.search(v)
    if m:
        variants = {name: (star, text) for star, name, text in _FTL_VARIANT.findall(m.group(1))}
        default = next((text for star, text in variants.values() if star), "")
        v = v[:m.start()] + variants.get("linux", ("", default))[1] + v[m.end():]
    v = re.sub(r"\{\s*(-[\w-]+)\s*\}", lambda t: msgs.get(t.group(1), {}).get("", ""), v)
    v = re.sub(r'\{\s*"([^"]*)"\s*\}', r"\1", v)
    return "" if "{" in v else v.strip()


def _attrs(tag: str) -> dict:
    return {k: html.unescape(v) for k, v in _XML_ATTR.findall(tag)}


def _ff_titles(xhtml: str, msgs: dict, scripts) -> dict:
    """key id -> name, as about:keyboard shows it: the label of the menu item
    using the key, else the string its script assigns to the key."""
    titles = {}
    for js in scripts:
        for ident, l10n in re.findall(r'"([\w-]+)",\s*"((?:customkeys|zen)-[\w-]+)"', js):
            titles.setdefault(ident, ftl_value(msgs, l10n))
        # numbered keys built in a loop: `key_selectTab${i}` -> `customkeys-nav-select-tab-${i}`
        for prefix, l10n in re.findall(r"`([\w-]+)\$\{[^}]+\}`,\s*`([\w-]+)\$\{[^}]+\}`", js):
            for n in range(13):
                titles.setdefault(f"{prefix}{n}", ftl_value(msgs, f"{l10n}{n}"))
    for tag in re.findall(r"<menuitem\b[^>]*>", xhtml):
        a = _attrs(tag)
        label = ftl_value(msgs, a.get("data-l10n-id", ""), "label")
        if a.get("key") and label:
            titles[a["key"]] = label
    return {k: v for k, v in titles.items() if v}


def firefox_defaults(app_dir: Path, locales) -> tuple[list[tuple[str, Keymap]], dict] | None:
    """(key id, Keymap) for every shortcut the installed browser's main window
    defines, and the names of all its keys (also the unassigned ones), or None
    when its omni.ja cannot be read."""
    with Jar(app_dir / "omni.ja") as app, Jar(app_dir.parent / "omni.ja") as gre:
        def get(name):
            return app.read(name) or gre.read(name) or ""

        xhtml = get(_FF_XHTML)
        if not xhtml:
            return None
        loc = next(x for x in locales if x == "en-US" or f"localization/{x}/browser/browserSets.ftl" in app.members)
        msgs = {}
        hrefs = re.findall(r'<link rel="localization" href="([^"]+)"', xhtml)
        for href in [*hrefs, "browser/customkeys.ftl", "browser/preferences/zen-preferences.ftl"]:
            ftl_parse(get(f"localization/{loc}/{href}"), msgs)
        titles = _ff_titles(xhtml, msgs, [get(s) for s in _FF_TITLE_SCRIPTS])
    source = rel(app_dir / "omni.ja")
    out = []
    for tag in re.findall(r"<key\b[^>]*>", xhtml):
        a = _attrs(tag)
        ident, l10n = a.get("id", ""), a.get("data-l10n-id", "")
        if not ident or a.get("internal") == "true" or a.get("disabled") == "true":
            continue  # internal = text editing keys (copy, paste, ...) that cannot be changed
        key = a.get("key") or a.get("keycode") or ftl_value(msgs, l10n, "key") or ftl_value(msgs, l10n, "keycode")
        if key:
            name = titles.get(ident) or _FF_NAMES.get(ident) or _ff_name(ident)
            out.append((ident, Keymap(_ff_combo(a.get("modifiers", ""), key), ident, name, "", source, default=True)))
    return out, titles


def _ff_name(ident: str) -> str:
    return humanize(re.sub(r"^key_|Kb$", "", ident)) or ident


def _ff_custom(profile: Path, table: list, names: dict, notes: list) -> list[Keymap]:
    """Apply customKeys.json (about:keyboard) to the defaults table; ``names``
    are the browser's names for its key ids."""
    path = profile / "customKeys.json"
    custom = read_json(path, limit=2 * 1024 * 1024)
    custom = custom if isinstance(custom, dict) else {}
    out = [k for ident, k in table if ident not in custom]
    known = {**_FF_NAMES, **names}
    cleared = 0
    for ident, spec in custom.items():
        spec = spec if isinstance(spec, dict) else {}
        key = spec.get("key") or spec.get("keycode") or ""
        if not key:  # {} = the user removed this shortcut
            cleared += 1
            continue
        out.append(Keymap(_ff_combo(spec.get("modifiers", ""), key), ident,
                          known.get(ident) or _ff_name(ident), "", rel(path)))
    notes.append(f"Profile: {rel(profile)}" + (f" · {cleared} shortcut(s) cleared" if cleared else ""))
    return out


# ---------------------------------------------------------------- extensions --

_EXT_SPECIAL = {
    "_execute_action": "Open the extension's toolbar popup",
    "_execute_browser_action": "Open the extension's toolbar popup",
    "_execute_page_action": "Open the extension's address bar popup",
    "_execute_sidebar_action": "Open the extension's sidebar",
}
_PLUS_MODS = {"control": "Ctrl", "ctrl": "Ctrl", "macctrl": "Ctrl", "meta": "Super",
              "command": "Super", "search": "Super", "alt": "Alt", "altgr": "AltGr", "shift": "Shift"}
_DOM_KEYS = {"Quote": "'", "Backquote": "`"}
_FF_BUILTIN = ("app-builtin", "app-builtin-addons", "app-system-defaults", "app-system-addons")


def plus_combo(text: str) -> str:
    """'Alt+Shift+U' (extensions), 'Control+Shift+KeyT' (Brave), 'ctrl+shift+t'
    (Vivaldi) -> 'Ctrl + Shift + T'. Meta is the Super key here, as in Chromium."""
    parts = [*text[:-2].split("+"), "+"] if text.endswith("++") else text.split("+")
    m = re.fullmatch(r"Key([A-Z])|Digit(\d)", parts[-1])
    key = (m.group(1) or m.group(2)) if m else _DOM_KEYS.get(parts[-1], parts[-1])
    return combo([_PLUS_MODS.get(p.lower(), p) for p in parts[:-1]], key)


def _short_name(name: str) -> str:
    """'Proton Pass: Free Password Manager' -> 'Proton Pass'."""
    return re.split(r"\s+[-–—|]\s+|:\s+", name, maxsplit=1)[0].strip() or name


def ext_manifest(read) -> tuple[str, dict]:
    """(name, {command: (suggested key, description)}) of an extension;
    ``read(member)`` returns the text of a file of the extension."""

    def loads(text):
        try:
            return json.loads(text) if text else None
        except ValueError:
            return None

    manifest = loads(read("manifest.json"))
    if not isinstance(manifest, dict):
        return "", {}
    loc = manifest.get("default_locale")
    raw = loads(read(f"_locales/{loc}/messages.json")) if isinstance(loc, str) and "/" not in loc else None
    msgs = {k.lower(): str(v.get("message", "")) for k, v in raw.items() if isinstance(v, dict)} \
        if isinstance(raw, dict) else {}

    def tr(s):
        return re.sub(r"__MSG_(\w+)__", lambda m: msgs.get(m.group(1).lower(), ""), s) if isinstance(s, str) else ""

    commands = manifest.get("commands")
    out = {}
    for name, spec in (commands if isinstance(commands, dict) else {}).items():
        spec = spec if isinstance(spec, dict) else {}
        sk = spec.get("suggested_key")
        key = (sk.get("linux") or sk.get("default") or "") if isinstance(sk, dict) else ""
        out[name] = (key if isinstance(key, str) else "", tr(spec.get("description")))
    return tr(manifest.get("name")), out


def _ext_manifest_at(path: Path) -> tuple[str, dict]:
    if path.is_dir():
        return ext_manifest(lambda member: read_text(path / member))
    with Jar(path) as jar:
        return ext_manifest(jar.read)


def _ext_keymap(name: str, command: str, key: str, desc: str, source: str) -> Keymap:
    desc = desc or _EXT_SPECIAL.get(command) or humanize(command) or command
    return Keymap(plus_combo(key), command, desc, _short_name(name), source)


def firefox_extensions(profile: Path) -> list[Keymap]:
    """Shortcuts of the enabled extensions: manifest suggestions, overridden by
    the changes made in Manage Extension Shortcuts (extension-settings.json)."""
    addons = (read_json(profile / "extensions.json") or {}).get("addons")
    settings = profile / "extension-settings.json"
    overrides = (read_json(settings) or {}).get("commands")
    overrides = overrides if isinstance(overrides, dict) else {}
    out = []
    for a in addons if isinstance(addons, list) else []:
        if not isinstance(a, dict) or a.get("type") != "extension" or not a.get("active") \
                or a.get("location") in _FF_BUILTIN or not isinstance(a.get("path"), str):
            continue
        path = Path(a["path"])
        if not path.exists():
            continue
        name, commands = _ext_manifest_at(path)
        name = (a.get("defaultLocale") or {}).get("name") or name or str(a.get("id"))
        for command, (key, desc) in commands.items():
            source = f"{rel(path)} (manifest)"
            entries = (overrides.get(command) or {}).get("precedenceList") or []
            for e in entries if isinstance(entries, list) else []:
                if isinstance(e, dict) and e.get("id") == a.get("id") and e.get("enabled", True):
                    key, source = str((e.get("value") or {}).get("shortcut", "")), rel(settings)
            if key:  # "" = no shortcut, or removed by the user
                out.append(_ext_keymap(name, command, key, desc, source))
    return out


def load_firefox(ctx: Ctx) -> list[Keymap]:
    root = next((p for p in ctx.paths if (p / "profiles.ini").is_file()), None)
    profile = firefox_profile(root) if root else None
    app_dir = firefox_app_dir(profile) if profile else None
    found = firefox_defaults(app_dir, _ff_locales(profile)) if app_dir else None
    table, names = found or (list(_table_keymaps(_FIREFOX, "Firefox defaults")), {})
    if not found:
        ctx.notes.append("Installed browser not found: the standard Firefox shortcuts are shown.")
    table = [*table, *_table_keymaps(_FF_EXTRA, "Firefox defaults")] if ctx.defaults else []
    if not profile:
        ctx.notes.append("No profile found: showing the defaults only.")
        return [k for _, k in table]
    return _ff_custom(profile, table, names, ctx.notes) + firefox_extensions(profile)


# ----------------------------------------------------------- Chromium family --

# Chromium's Linux accelerator table (chrome/browser/ui/accelerator_table.cc).
_CHROMIUM = [
    ("Ctrl+T", "New tab"), ("Ctrl+N", "New window"), ("Ctrl+Shift+N", "New incognito window"),
    ("Ctrl+W Ctrl+F4", "Close tab"), ("Ctrl+Shift+W Alt+F4", "Close window"),
    ("Ctrl+Shift+T", "Reopen closed tab"),
    ("Ctrl+Tab Ctrl+PageDown", "Next tab"), ("Ctrl+Shift+Tab Ctrl+PageUp", "Previous tab"),
    ("Ctrl+Shift+PageDown", "Move tab right"), ("Ctrl+Shift+PageUp", "Move tab left"),
    *[(f"Ctrl+{i} Alt+{i}", f"Go to tab {i}") for i in range(1, 9)], ("Ctrl+9 Alt+9", "Go to last tab"),
    ("Ctrl+Shift+A", "Search tabs"), ("Ctrl+L Alt+D", "Focus address bar"),
    ("Ctrl+K Ctrl+E", "Search from the address bar"), ("F6", "Focus next pane"),
    ("Shift+F6", "Focus previous pane"), ("Ctrl+F6", "Focus the page"),
    ("Alt+Shift+T", "Focus toolbar"), ("Alt+Shift+B", "Focus bookmarks bar"),
    ("Ctrl+F", "Find in page"), ("Ctrl+G F3", "Find next"), ("Ctrl+Shift+G Shift+F3", "Find previous"),
    ("Esc", "Stop loading or close find bar"),
    ("Ctrl+R F5", "Reload"), ("Ctrl+Shift+R Ctrl+F5 Shift+F5", "Reload, bypassing the cache"),
    ("Alt+Left", "Back"), ("Alt+Right", "Forward"), ("Alt+Home", "Home page"),
    ("Ctrl+D", "Bookmark this page"), ("Ctrl+Shift+D", "Bookmark all tabs"),
    ("Ctrl+Shift+B", "Show/hide bookmarks bar"), ("Ctrl+Shift+O", "Bookmark manager"),
    ("Ctrl+H", "History"), ("Ctrl+J", "Downloads"), ("Ctrl+Shift+Delete", "Clear browsing data"),
    ("Ctrl+S", "Save page"), ("Ctrl+P", "Print"), ("Ctrl+Shift+P", "Print using the system dialog"),
    ("Ctrl+O", "Open file"), ("Ctrl+U", "View page source"),
    ("F12 Ctrl+Shift+I", "Developer tools"), ("Ctrl+Shift+J", "JavaScript console"),
    ("Ctrl+Shift+C", "Inspect element"),
    ("Ctrl++ Ctrl+Shift++", "Zoom in"), ("Ctrl+- Ctrl+Shift+-", "Zoom out"), ("Ctrl+0", "Reset zoom"),
    ("F11", "Full screen"), ("F7", "Caret browsing"), ("F1", "Help"),
    ("Shift+Esc", "Task manager"), ("Alt+F Alt+E", "Open the menu"), ("F10", "Focus the menu"),
    ("Ctrl+Shift+M", "Switch profile"), ("Ctrl+Shift+L", "Collapse/expand vertical tabs"),
    ("Alt+Shift+N", "New split view"), ("Alt+Shift+R", "Reading mode"),
    ("Alt+Shift+C", "Add a new tab to the group"), ("Alt+Shift+P", "Create a new tab group"),
    ("Alt+Shift+X", "Focus next tab group"), ("Alt+Shift+Z", "Focus previous tab group"),
    ("Alt+Shift+W", "Close tab group"),
]
# Edge's own list (learn.microsoft.com "Configurable Microsoft Edge commands"
# and the Edge keyboard shortcuts page). Edge cannot change them on Linux.
_EDGE = [
    ("Ctrl+T", "New tab"), ("Ctrl+N", "New window"), ("Ctrl+Shift+N", "New InPrivate window"),
    ("Ctrl+W Ctrl+F4", "Close tab"), ("Ctrl+Shift+W Alt+F4", "Close window"),
    ("Ctrl+Shift+T", "Reopen closed tab"), ("Ctrl+Shift+K", "Duplicate tab"),
    ("Ctrl+Tab Ctrl+PageDown", "Next tab"), ("Ctrl+Shift+Tab Ctrl+PageUp", "Previous tab"),
    *[(f"Ctrl+{i}", f"Go to tab {i}") for i in range(1, 9)], ("Ctrl+9", "Go to last tab"),
    ("Ctrl+M", "Mute or unmute tab"), ("Ctrl+Shift+,", "Vertical tabs on/off"),
    ("Alt+D Ctrl+L F4", "Focus address bar"), ("Ctrl+E Ctrl+K", "Search from the address bar"),
    ("Ctrl+Shift+L", "Paste and go"), ("Ctrl+Shift+V", "Paste without formatting"),
    ("Ctrl+F", "Find in page"), ("Ctrl+G F3", "Find next"), ("Ctrl+Shift+G Shift+F3", "Find previous"),
    ("Esc", "Stop loading or close find bar"),
    ("Ctrl+R F5", "Reload"), ("Ctrl+Shift+R Shift+F5", "Reload, bypassing the cache"),
    ("Alt+Left", "Back"), ("Alt+Right", "Forward"), ("Alt+Home", "Home page"),
    ("Ctrl+D", "Add this tab to favorites"), ("Ctrl+Shift+D", "Add all tabs to favorites"),
    ("Ctrl+Shift+O", "Favorites"), ("Ctrl+Shift+B", "Show/hide favorites bar"), ("Ctrl+Shift+Y", "Collections"),
    ("Ctrl+H", "History"), ("Ctrl+J", "Downloads"), ("Ctrl+Shift+Delete", "Clear browsing data"),
    ("Ctrl+S", "Save page"), ("Ctrl+P", "Print"), ("Ctrl+Shift+P", "Print using the system dialog"),
    ("Ctrl+O", "Open file"), ("Ctrl+U", "View page source"),
    ("F12", "Open/close developer tools"), ("Ctrl+Shift+I", "Developer tools"),
    ("Ctrl+Shift+J", "Developer tools: console"), ("Ctrl+Shift+C", "Inspect element"),
    ("Ctrl++", "Zoom in"), ("Ctrl+-", "Zoom out"), ("Ctrl+0", "Reset zoom"), ("F11", "Full screen"),
    ("F7", "Caret browsing"), ("F9", "Immersive Reader"), ("Ctrl+Shift+U", "Read Aloud"),
    ("Ctrl+Shift+S", "Web capture"), ("Ctrl+Shift+X", "Web select"),
    ("Ctrl+Shift+E", "Search the selected text in the sidebar"),
    ("Ctrl+Shift+M", "Switch profile or browse as guest"), ("Shift+Esc", "Task manager"),
    ("Alt+E Alt+F", "Open the Settings and more menu"), ("Alt F10", "Focus the Settings and more button"),
    ("F6", "Focus next pane"), ("Shift+F6", "Focus previous pane"), ("Ctrl+F6", "Focus the page"),
    ("Alt+Shift+T", "Focus toolbar"), ("Alt+Shift+B", "Focus favorites bar"),
    ("Alt+Shift+A", "Focus a notification"), ("Alt+Shift+R", "Focus the Immersive Reader toolbar"),
    ("Alt+Shift+I", "Send feedback"), ("F1", "Help"),
]

# Opera's own list (help.opera.com/en/latest/shortcuts, Windows/Linux), used
# only when the install's keybinding defaults cannot be read.
_OPERA = [
    ("Ctrl+T", "New tab"), ("Ctrl+N", "New window"), ("Ctrl+Shift+N", "New private window"),
    ("Ctrl+W", "Close tab"), ("Alt+F4", "Close window"), ("Ctrl+Shift+T", "Reopen closed tab"),
    ("Ctrl+Tab", "Next tab"), ("Ctrl+Shift+Tab", "Previous tab"), ("Ctrl+`", "Go to the previously active tab"),
    ("Ctrl+M", "Tab menu"), ("Ctrl+L", "Focus address bar"),
    ("Ctrl+F", "Find in page"), ("Ctrl+G", "Find next"), ("Ctrl+Shift+G", "Find previous"),
    ("Ctrl+R", "Reload"), ("Alt+Left", "Back"), ("Alt+Right", "Forward"),
    ("Ctrl+J", "Downloads"), ("Ctrl+Shift+E", "Extensions"), ("Ctrl+H", "History"), ("Alt+P", "Settings"),
    ("Ctrl+O", "Open file"), ("Ctrl+S", "Save page"), ("Ctrl+P", "Print"), ("Ctrl+U", "View page source"),
    ("Ctrl++", "Zoom in"), ("Ctrl+-", "Zoom out"), ("Ctrl+0", "Reset zoom"), ("F11", "Full screen"),
    ("F1", "Help"), ("Ctrl+Shift+X", "Quit"),
]
_TABLES = {"edge": (_EDGE, "Edge defaults"), "opera": (_OPERA, "Opera defaults")}
_CHROMIUM_NOTE = {
    "brave": "No shortcut settings in the Brave profile yet:",
    "vivaldi": "No shortcut settings in the Vivaldi profile and no Vivaldi install found:",
}
_TABLE_NOTE = {
    "edge": "Edge cannot change its own shortcuts on Linux: its standard shortcuts are shown.",
    "opera": "Opera's install was not found: its documented standard shortcuts are shown.",
}
_COMMAND_NAMES = dict(item.split(":", 1) for item in COMMAND_IDS.split())


def _get(data, *keys):
    """data[k1][k2]..., or None when a level is missing or not a dict."""
    for k in keys:
        if not isinstance(data, dict):
            return None
        data = data.get(k)
    return data


def chromium_profile(root: Path) -> Path | None:
    """The last used profile of a Chromium-family data dir (Opera keeps it in the root)."""
    last = _get(read_json(root / "Local State"), "profile", "last_used")
    for name in ([last] if isinstance(last, str) and last else []) + ["Default"]:
        if (root / name / "Preferences").is_file():
            return root / name
    return root if (root / "Preferences").is_file() else None


def _version_key(path: Path):
    return [int(x) if x.isdigit() else 0 for x in re.split(r"[._]", path.name)]


def _chromium_ext_manifest(profile: Path, ext: str, setting) -> tuple[str, dict]:
    """Name and commands of an installed extension: its unpacked dir (``path``
    setting) or the newest version under <profile>/Extensions/<id>/."""
    path = setting.get("path") if isinstance(setting, dict) else None
    cands = [profile / "Extensions" / path] if isinstance(path, str) and path else []
    ext_dir = profile / "Extensions" / ext
    if "/" not in ext and ext_dir.is_dir():
        cands += sorted((p for p in ext_dir.iterdir() if p.is_dir()), key=_version_key, reverse=True)
    for c in cands:  # an absolute "path" (unpacked extension) replaces the base
        if (c / "manifest.json").is_file():
            return _ext_manifest_at(c)
    return "", {}


def chromium_extensions(profile: Path, prefs: list) -> list[Keymap]:
    """Extension shortcuts: Preferences ``extensions.commands`` maps
    "linux:<accelerator>[:<extension id>]" -> {extension, command_name}. Chromium
    writes the manifest suggestions there on install, so it holds every active one."""
    commands, settings = {}, {}
    for p in reversed(prefs):  # the first file wins
        commands.update(_get(p, "extensions", "commands") or {})
        settings.update(_get(p, "extensions", "settings") or {})
    manifests, out = {}, []
    for shortcut, v in commands.items():
        platform, _, accel = shortcut.partition(":")
        ext, command = _get(v, "extension"), _get(v, "command_name")
        if platform != "linux" or not isinstance(ext, str) or not isinstance(command, str):
            continue
        if ext not in manifests:
            manifests[ext] = _chromium_ext_manifest(profile, ext, settings.get(ext))
        name, cmds = manifests[ext]
        out.append(_ext_keymap(name or ext, command, accel.split(":")[0], cmds.get(command, ("", ""))[1],
                               rel(profile / "Preferences")))
    return out


def _brave_keymaps(prefs: list, source: str) -> list[Keymap] | None:
    """Brave keeps every shortcut by command id: ``brave.default_accelerators``
    (built in) and ``brave.accelerators`` (in use, after your changes)."""
    defaults = next((d for p in prefs if isinstance(d := _get(p, "brave", "default_accelerators"), dict)), None)
    if not defaults:
        return None
    current = next((c for p in prefs if isinstance(c := _get(p, "brave", "accelerators"), dict)), {})
    out = []
    for cid, keys in {**defaults, **current}.items():
        action = _COMMAND_NAMES.get(str(cid), f"command_{cid}").lower()
        base = defaults.get(cid) if isinstance(defaults.get(cid), list) else []
        for k in keys if isinstance(keys, list) else []:
            if isinstance(k, str) and k:
                out.append(Keymap(plus_combo(k), action, humanize(action), "", source, default=k in base))
    return out


_VIVALDI_DIRS = ("/opt/vivaldi", "/opt/vivaldi-snapshot", "/usr/lib/vivaldi", "/usr/share/vivaldi",
                 "~/.local/share/flatpak/app/com.vivaldi.Vivaldi/current/active/files/extra/vivaldi",
                 "/var/lib/flatpak/app/com.vivaldi.Vivaldi/current/active/files/extra/vivaldi")


def vivaldi_defaults() -> dict:
    """Vivaldi's factory shortcuts: ``vivaldi.actions`` in the install's prefs_definitions.json."""
    exe = which("vivaldi", "vivaldi-stable", "vivaldi-snapshot")
    dirs = ([Path(os.path.realpath(exe)).parent] if exe else []) + [Path(d).expanduser() for d in _VIVALDI_DIRS]
    for d in dirs:
        spec = _get(read_json(d / "resources/vivaldi/prefs_definitions.json"), "vivaldi", "actions")
        table = (_get(spec, "default_linux") or _get(spec, "default")) if isinstance(spec, dict) else None
        if isinstance(table, list) and table and isinstance(table[0], dict):
            return table[0]
    return {}


def _vivaldi_keymaps(prefs: list, source: str, notes: list) -> list[Keymap] | None:
    """Vivaldi keeps its whole shortcut table in ``vivaldi.actions[0]``:
    {"COMMAND_CLOSE_TAB": {"shortcuts": ["ctrl+w", ...]}, ...}."""
    actions = next((a for p in prefs if isinstance(a := _get(p, "vivaldi", "actions"), list) and a), [{}])
    current = actions[0] if isinstance(actions[0], dict) else {}
    defaults = vivaldi_defaults()
    if not current and not defaults:
        return None
    if current and not defaults:
        notes.append("Vivaldi's defaults file was not found: your changes are not told apart.")
    out = []
    for command, spec in (current or defaults).items():
        keys = _get(spec, "shortcuts")
        base = _get(defaults.get(command), "shortcuts") or []
        for k in keys if isinstance(keys, list) else []:
            if isinstance(k, str) and k.strip():
                key = SEQ.join(plus_combo(t) for t in k.split())
                out.append(Keymap(key, command, humanize(command.removeprefix("COMMAND_")),
                                  "", source if current else "Vivaldi defaults", default=k in base))
    return out


_OPERA_DIRS = ("/usr/lib/opera", "/usr/lib/x86_64-linux-gnu/opera", "/usr/lib/x86_64-linux-gnu/opera-stable",
               "/opt/opera", "/snap/opera/current/usr/lib/x86_64-linux-gnu/opera")
# Groups that are not user shortcuts: settings, text editing, and keys Opera hardcodes.
_OPERA_SKIP = {"Settings", "ReadOnly", "ImportantCommands", "HardcodedNotConfigurableMayConflictWithOther"}


def opera_defaults() -> tuple[dict, str]:
    """Opera's factory keybindings ({group: {command: {"bindings": [...]}}})
    and where they were read: a JSON resource inside the install's opera.pak."""
    exe = which("opera", "opera-stable")
    dirs = ([Path(os.path.realpath(exe)).parent] if exe else []) + [Path(d) for d in _OPERA_DIRS]
    for d in dirs:
        text = pak_find(d / "opera.pak", b'"Keybindings"')
        try:
            table = _get(json.loads(text), "Keybindings") if text else None
        except ValueError:
            table = None
        if isinstance(table, dict):
            return table, rel(d / "opera.pak")
    return {}, ""


def _opera_keymaps(prefs: list, source: str, notes: list) -> list[Keymap] | None:
    """Opera keeps your changes under ``Keybindings`` in Preferences, with the
    same layout as its defaults: {group: {command: {"bindings": [...]}}}.
    Single-key shortcuts (group "Advanced") count only when enabled in settings."""
    defaults, default_source = opera_defaults()
    user = next((k for p in prefs if isinstance(k := _get(p, "Keybindings"), dict)), {})
    if not defaults and not user:
        return None
    if not defaults:
        notes.append("Opera's install was not found: your changes are not told apart from its defaults.")
    single_key = _get(user, "Settings", "AdvancedEnabled")
    single_key = single_key if isinstance(single_key, bool) else _get(defaults, "Settings", "AdvancedEnabled") is True
    out = []
    for group in {**defaults, **user}:
        if group in _OPERA_SKIP or (group == "Advanced" and not single_key):
            continue
        base = defaults.get(group) if isinstance(defaults.get(group), dict) else {}
        mine = user.get(group) if isinstance(user.get(group), dict) else {}
        section = "" if group == "Basic" else "single-key" if group == "Advanced" else humanize(group).lower()
        for command, spec in {**base, **mine}.items():
            keys, base_keys = _get(spec, "bindings"), _get(base.get(command), "bindings") or []
            for k in keys if isinstance(keys, list) else []:
                if isinstance(k, str) and k:
                    out.append(Keymap(plus_combo(k), command, humanize(command), section,
                                      source if command in mine else default_source, default=k in base_keys))
    return out


def load_chromium(ctx: Ctx) -> list[Keymap]:
    root = next((p for p in ctx.paths if p.is_dir()), None)
    profile = chromium_profile(root) if root else None
    prefs = [read_json(profile / f) for f in ("Preferences", "Secure Preferences")] if profile else []
    prefs = [p for p in prefs if isinstance(p, dict)]
    source = rel(profile / "Preferences") if profile else ""
    own = None
    if ctx.app.id == "brave":
        own = _brave_keymaps(prefs, source)
    elif ctx.app.id == "vivaldi":
        own = _vivaldi_keymaps(prefs, source, ctx.notes)
    elif ctx.app.id == "opera":
        own = _opera_keymaps(prefs, source, ctx.notes)
    if own is None:
        rows, label = _TABLES.get(ctx.app.id, (_CHROMIUM, "Chromium defaults"))
        own = [k for _, k in _table_keymaps(rows, label)]
        ctx.notes.append(_TABLE_NOTE.get(ctx.app.id) or _CHROMIUM_NOTE.get(
            ctx.app.id, "The browser's own shortcuts live inside the app:") + " the standard Chromium shortcuts are shown.")
    out = [k for k in own if ctx.defaults or not k.default]
    if profile:
        ctx.notes.append(f"Profile: {rel(profile)}")
        out += chromium_extensions(profile, prefs)
    else:
        ctx.notes.append("No profile found: extension shortcuts are not shown.")
    return out


# --------------------------------------------------------------- qutebrowser --

# config.bind('key', 'command'[, mode='normal']) / config.unbind('key'[, mode='normal'])
_QUTE_BIND = re.compile(r"""^\s*config\.(bind|unbind)\(\s*(['"])(.+?)\2\s*(?:,\s*(['"])(.*?)\4)?"""
                        r"""\s*(?:,\s*mode\s*=\s*['"](\w+)['"])?""")
_QUTE_NO_DEFAULTS = re.compile(r"^\s*c\.bindings\.default\s*=\s*\{\s*\}", re.M)
_QUTE_DEFAULTS = ("/usr/lib/python3*/site-packages/qutebrowser/config/configdata.yml",
                  "/usr/lib64/python3*/site-packages/qutebrowser/config/configdata.yml",
                  "/usr/lib/python3/dist-packages/qutebrowser/config/configdata.yml",
                  "~/.local/lib/python3*/site-packages/qutebrowser/config/configdata.yml",
                  "~/.local/share/pipx/venvs/qutebrowser/lib/python3*/site-packages/qutebrowser/config/configdata.yml")
# "key: value" or "key:" (opens a nested mapping); a key ends at a colon followed by a space
_YAML_LINE = re.compile(r"""^(\s*)("(?:[^"\\]|\\.)*"|'(?:[^']|'')*'|[^\s'"#-].*?):(?:\s+(.*))?$""")


def _yaml_scalar(s: str):
    s = s.strip()
    if s in ("null", "~"):
        return None
    if len(s) > 1 and s[0] == s[-1] == "'":
        return s[1:-1].replace("''", "'")
    if len(s) > 1 and s[0] == s[-1] == '"':
        try:
            return json.loads(s)
        except ValueError:
            return s[1:-1]
    return s


def yaml_mapping(text: str, path: list) -> dict:
    """The mapping at ``path`` of a YAML file made of nested ``key: value``
    lines, which is the shape of qutebrowser's configdata.yml and autoconfig.yml.
    Lists, flow collections and block strings are not understood (they are
    elsewhere in those files)."""
    root = {}
    stack = [(-1, root)]
    for line in text.splitlines():
        m = _YAML_LINE.match(line)
        if not m:
            continue
        indent = len(m.group(1))
        while stack[-1][0] >= indent:
            stack.pop()
        key, value = _yaml_scalar(m.group(2)), m.group(3)
        if value is None or not value.strip():
            child = {}
            stack[-1][1][key] = child
            stack.append((indent, child))
        else:
            stack[-1][1][key] = _yaml_scalar(value)
    for p in path:
        root = root.get(p) if isinstance(root, dict) else None
    return root if isinstance(root, dict) else {}


def _qute_binds(table: dict, source: str, default: bool = False) -> list[Keymap]:
    """{mode: {key: command or None}} -> Keymaps; None (``null``) unbinds the key."""
    out = []
    for mode, binds in table.items():
        for key, command in binds.items() if isinstance(binds, dict) else []:
            out.append(Keymap(pretty_angle_keys(str(key)), command or "", "", "" if mode == "normal" else str(mode),
                              source, default=default, removed=command is None))
    return out


def qutebrowser_defaults() -> list[Keymap]:
    """qutebrowser's default bindings, from the installed package's configdata.yml."""
    exe = which("qutebrowser")
    venv = [str(Path(os.path.realpath(exe)).parent.parent / "lib/python3*/site-packages/qutebrowser"
                "/config/configdata.yml")] if exe else []
    for pattern in [*_QUTE_DEFAULTS, *venv]:
        for path in expand(pattern):
            table = yaml_mapping(read_text(path, limit=8 * 1024 * 1024) or "", ["bindings.default", "default"])
            if table:
                return _qute_binds(table, rel(path), default=True)
    return []


def load_qutebrowser(ctx: Ctx) -> list[Keymap]:
    """Defaults, then autoconfig.yml (``:bind`` and the settings page), then
    config.py, which qutebrowser loads last."""
    base = ctx.paths[0].parent if ctx.paths else CONFIG / "qutebrowser"
    auto, main = base / "autoconfig.yml", base / "config.py"
    user = _qute_binds(yaml_mapping(read_text(auto) or "", ["settings", "bindings.commands", "global"]), rel(auto))
    text = read_text(main) or ""
    lines = text.splitlines()
    comments = block_comments(lines, "#", lambda t: "config." in t)
    for i, line in enumerate(lines):
        m = _QUTE_BIND.match(line)
        if m:
            mode = m.group(6) or "normal"
            user.append(Keymap(pretty_angle_keys(m.group(3)), m.group(5) or "", comments.get(i, ""),
                               "" if mode == "normal" else mode, f"{rel(main)}:{i + 1}",
                               removed=m.group(1) == "unbind"))
    defaults = []
    if ctx.defaults and not _QUTE_NO_DEFAULTS.search(text):
        defaults = qutebrowser_defaults()
        if not defaults:
            ctx.notes.append("qutebrowser's configdata.yml was not found: its default bindings are not shown.")
    return merge(defaults, user)


# ---------------------------------------------------------------------- apps --

_FIREFOX_FAMILY = [
    ("firefox", "Firefox", ("firefox",),
     ("~/.mozilla/firefox", "~/.var/app/org.mozilla.firefox/.mozilla/firefox",
      "~/snap/firefox/common/.mozilla/firefox"), "firefox"),
    ("zen", "Zen Browser", ("zen", "zen-browser"), ("~/.zen", "~/.var/app/app.zen_browser.zen/.zen"), "zen-browser"),
    ("librewolf", "LibreWolf", ("librewolf",),
     ("~/.librewolf", "~/.var/app/io.gitlab.librewolf-community/.librewolf"), "librewolf"),
    ("floorp", "Floorp", ("floorp",), ("~/.floorp", "~/.var/app/one.ablaze.floorp/.floorp"), "floorp"),
    ("waterfox", "Waterfox", ("waterfox",), ("~/.waterfox", "~/.var/app/net.waterfox.waterfox/.waterfox"),
     "waterfox"),
]
# (id, name, executables, data dirs: native, Flatpak, Snap)
_CHROMIUM_FAMILY = [
    ("chromium", "Chromium", ("chromium", "chromium-browser"),
     (f"{CONFIG}/chromium", "~/.var/app/org.chromium.Chromium/config/chromium",
      "~/snap/chromium/common/chromium"), "chromium"),
    ("chrome", "Google Chrome", ("google-chrome-stable", "google-chrome"),
     (f"{CONFIG}/google-chrome", "~/.var/app/com.google.Chrome/config/google-chrome"), "google-chrome"),
    ("brave", "Brave", ("brave", "brave-browser"),
     (f"{CONFIG}/BraveSoftware/Brave-Browser", "~/.var/app/com.brave.Browser/config/BraveSoftware/Brave-Browser"),
     "brave-browser"),
    ("vivaldi", "Vivaldi", ("vivaldi", "vivaldi-stable"),
     (f"{CONFIG}/vivaldi", "~/.var/app/com.vivaldi.Vivaldi/config/vivaldi"), "vivaldi"),
    ("edge", "Microsoft Edge", ("microsoft-edge-stable", "microsoft-edge"),
     (f"{CONFIG}/microsoft-edge", "~/.var/app/com.microsoft.Edge/config/microsoft-edge"), "microsoft-edge"),
    ("opera", "Opera", ("opera",), (f"{CONFIG}/opera", "~/.var/app/com.opera.Opera/config/opera"), "opera"),
]

APPS = [
    *[App(i, n, "browser", load_firefox, paths=p, bins=b, icon=icon) for i, n, b, p, icon in _FIREFOX_FAMILY],
    *[App(i, n, "browser", load_chromium, paths=p, bins=b, icon=icon) for i, n, b, p, icon in _CHROMIUM_FAMILY],
    App("qutebrowser", "qutebrowser", "browser", load_qutebrowser,
        paths=(f"{CONFIG}/qutebrowser/config.py", f"{CONFIG}/qutebrowser/autoconfig.yml"),
        bins=("qutebrowser",), icon="qutebrowser"),
]
