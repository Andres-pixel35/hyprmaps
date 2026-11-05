"""Code editors: Neovim, Vim, VS Code (and forks), Helix, Zed."""

import json
import re
import tomllib
from pathlib import Path

from .. import luaparse as lua
from ..model import SEQ, Keymap, block_comments, combo, split_combo
from ..sources import CONFIG, read_text, rel, run
from .base import App, Ctx

_MODES = {"n": "", "i": "insert", "v": "visual", "x": "visual", "s": "select", "o": "operator",
          "t": "terminal", "c": "command", "": "n/v/o", "!": "insert/command", "l": "insert/command"}

# --------------------------------------------------------- Neovim / Vim --

# The last segment of a callee that can define a keymap: vim.keymap.set, map, nnoremap, ...
_LUA_MAP_CALL = r"[\w.:]*(?:keymap\.set|nvim_set_keymap|nvim_buf_set_keymap|\b[nvxsoitc]?(?:nore)?map|\bkeymap)"
_VIM_MAP = re.compile(r"^\s*([nvxsoilct]?)(?:nore)?map(!?)\s+"
                      r"((?:<(?:buffer|silent|expr|nowait|unique|script|special)>\s*)*)(\S+)\s+(.+?)\s*$")


def _modes(expr: str) -> list[str] | None:
    """'"n"' -> ['n'], '{ "n", "v" }' -> ['n', 'v']; None when not a mode literal."""
    v = lua.string_value(expr)
    if v is not None:
        return list(v) if re.fullmatch(r"[nvxsoilct!]*", v) and len(v) <= 4 else None
    if expr.strip().startswith("{"):
        pos, named = lua.table_fields(expr)
        modes = [lua.string_value(p) for p in pos]
        if not named and modes and all(m is not None and re.fullmatch(r"[nvxsoilct!]?", m) for m in modes):
            return modes
    return None


def _section(modes) -> str:
    names = []
    for m in modes:
        n = _MODES.get(m, m)
        if n not in names:
            names.append(n)
    return ", ".join(n or "normal" for n in names) if names != [""] else ""


def _lua_keymaps(path: Path, text: str) -> list[Keymap]:
    clean = lua.strip_comments(text)
    comments = block_comments(text.splitlines(), "--", lambda t: "map" in t and "(" in t)
    out = []
    for callee, args, line in lua.find_calls(clean, _LUA_MAP_CALL):
        parts = lua.split_top(args)
        last = re.split(r"[.:]", callee)[-1]
        if last == "nvim_buf_set_keymap":
            parts = parts[1:]
        implied = re.fullmatch(r"([nvxsoitc])(?:nore)?map", last)
        if implied and parts and lua.string_value(parts[0]) is not None and _modes(parts[0]) is None:
            modes, lhs_i = [implied.group(1)], 0  # nmap("<leader>x", rhs): mode from the name
        else:
            modes = _modes(parts[0]) if parts else None
            lhs_i = 1
        if modes is None or len(parts) <= lhs_i + 1:
            continue
        lhs = lua.string_value(parts[lhs_i])
        if lhs is None:
            continue
        rhs_src = parts[lhs_i + 1]
        rhs = lua.string_value(rhs_src)
        desc = ""
        if len(parts) > lhs_i + 2 and parts[lhs_i + 2].lstrip().startswith("{"):
            _, named = lua.table_fields(parts[lhs_i + 2])
            desc = lua.string_value(named.get("desc", "")) or ""
            if rhs == "" and "callback" in named:
                rhs = None
                rhs_src = named["callback"]
        action = rhs if rhs is not None else lua.short(rhs_src)
        out.append(Keymap(lhs, action, desc or comments.get(line - 1, ""), _section(modes),
                          f"{rel(path)}:{line}"))
    out += _lazy_keys(path, clean)
    return out


def _lazy_keys(path: Path, clean: str) -> list[Keymap]:
    """lazy.nvim specs: keys = { { "<leader>ff", rhs, desc = "...", mode = {...} }, ... }."""
    out = []
    for m in re.finditer(r"\bkeys\s*=\s*\{", clean):
        start = m.end() - 1
        table = clean[start:lua.balanced(clean, start)]
        for item in lua.split_top(table[1:-1]):
            line = clean.count("\n", 0, start + table.find(item)) + 1 if item in table else 0
            if item.startswith("{"):
                pos, named = lua.table_fields(item)
            else:
                pos, named = [item], {}
            lhs = lua.string_value(pos[0]) if pos else None
            if lhs is None:
                continue
            rhs_src = pos[1] if len(pos) > 1 else ""
            rhs = lua.string_value(rhs_src)
            action = rhs if rhs is not None else (lua.short(rhs_src) if rhs_src else "(lazy-loads the plugin)")
            modes = _modes(named["mode"]) if "mode" in named else ["n"]
            out.append(Keymap(lhs, action, lua.string_value(named.get("desc", "")) or "",
                              _section(modes or ["n"]), f"{rel(path)}:{line}"))
    return out


def _vim_keymaps(path: Path, lines: list[str]) -> list[Keymap]:
    comments = block_comments(lines, '"', lambda t: bool(_VIM_MAP.match(t)))
    out = []
    for i, line in enumerate(lines):
        m = _VIM_MAP.match(line)
        if not m:
            continue
        mode = m.group(1) or ("!" if m.group(2) else "")
        rhs, _, trailing = m.group(5).partition(' "')  # vim trailing comments start with ' "'
        out.append(Keymap(m.group(4), rhs.strip(), trailing.strip() or comments.get(i, ""),
                          _section([mode]), f"{rel(path)}:{i + 1}"))
    return out


def _vim_files(paths) -> list[Path]:
    files = []
    for p in paths:
        if p.is_file():
            files.append(p)
        elif p.is_dir():
            files += sorted(x for x in p.rglob("*") if x.suffix in (".lua", ".vim") and x.is_file())
    return files[:300]


def _leader(texts) -> str:
    for t in texts:
        m = re.search(r"""(?:vim\.g\.mapleader\s*=|let\s+(?:g:)?mapleader\s*=)\s*(["'])(.*?)\1""", t)
        if m:
            return m.group(2)
    return "\\"


def load_vimlike(ctx: Ctx) -> list[Keymap]:
    out, texts = [], []
    for f in _vim_files(ctx.paths):
        text = read_text(f)
        if text is None:
            continue
        texts.append(text)
        out += _lua_keymaps(f, text) if f.suffix == ".lua" else _vim_keymaps(f, text.splitlines())
    leader = _leader(texts)
    ctx.notes.append(f"<leader> = {'Space' if leader == ' ' else leader}")
    if ctx.live and ctx.app.id == "nvim":
        live = _nvim_live()
        if live is not None:
            ctx.notes.append("Read from Neovim itself (live = true).")
            return _enrich_live(live, out, "<Space>" if leader == " " else leader)
        ctx.notes.append("Live Neovim query failed: showing what the config files contain.")
    return out


def _enrich_live(live, static, leader: str) -> list[Keymap]:
    """Live maps know every map but show Lua callbacks as '<Lua function>';
    the static read knows the source code and file. Combine both."""
    by_lhs = {(k.key.replace("<leader>", leader), k.section): k for k in static}
    for k in live:
        s = by_lhs.get((k.key, k.section))
        if s:
            if k.action == "<Lua function>":
                k.action = s.action
            k.desc, k.source = k.desc or s.desc, s.source
        elif k.desc.startswith(":help "):  # Neovim's built-in default mappings
            k.default = True
    return live


_NVIM_DUMP = """lua local r = {}
for _, m in ipairs({'n','i','v','x','s','o','t','c'}) do
  for _, k in ipairs(vim.api.nvim_get_keymap(m)) do
    if not k.lhs:match('^<Plug>') and not k.lhs:match('^<SNR>') then
      table.insert(r, {m, k.lhs, k.rhs or (k.callback and '<Lua function>') or '', k.desc or ''})
    end
  end
end
io.stdout:write(vim.json.encode(r))"""


def _nvim_live() -> list[Keymap] | None:
    # -i NONE: no shada write; -n: no swap files; state/cache go to a temp dir.
    out = run(["nvim", "--headless", "-i", "NONE", "-n", "-c", _NVIM_DUMP, "-c", "qa!"], sandbox="state")
    try:
        rows = json.loads(out or "")
    except json.JSONDecodeError:
        return None
    merged = {}
    for mode, lhs, rhs, desc in rows:
        k = merged.get((lhs, rhs))
        if k:
            k.section = _section(k.section.split(", ") + [_MODES.get(mode, mode)]) if k.section else _MODES.get(mode, mode)
        else:
            merged[(lhs, rhs)] = Keymap(lhs.replace(" ", "<Space>"), rhs, desc, _MODES.get(mode, mode), "nvim (live)")
    return list(merged.values())


# ------------------------------------------------------ VS Code & forks --

def loads_jsonc(text: str):
    """JSON with // and /* */ comments and trailing commas (VS Code, Zed)."""
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            out.append(text[i:j + 1])
            i = j + 1
        elif text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j == -1 else j
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end == -1 else end + 2
        else:
            out.append(c)
            i += 1
    return json.loads(re.sub(r",(\s*[\]}])", r"\1", "".join(out)))


def _chords(key: str, sep: str) -> str:
    return SEQ.join(split_combo(chord, sep) for chord in key.split())


def load_vscode(ctx: Ctx) -> list[Keymap]:
    out, disabled = [], 0
    for p in [x for x in ctx.paths if x.is_file()]:
        try:
            data = loads_jsonc(read_text(p) or "[]")
        except json.JSONDecodeError as e:
            ctx.notes.append(f"{rel(p)}: invalid JSON ({e.msg}, line {e.lineno})")
            continue
        for b in data if isinstance(data, list) else []:
            if not isinstance(b, dict) or "key" not in b or "command" not in b:
                continue
            cmd = str(b["command"])
            if cmd.startswith("-"):
                disabled += 1
                continue
            action = cmd + (f"  [when {b['when']}]" if b.get("when") else "")
            out.append(Keymap(_chords(str(b["key"]), "+"), action, "", "", rel(p)))
    if disabled:
        ctx.notes.append(f"{disabled} default binding(s) disabled in keybindings.json.")
    ctx.notes.append("Only your keybindings.json is shown: VS Code keeps its defaults inside the app.")
    return out


# ---------------------------------------------------------------- Helix --

def helix_key(key: str) -> str:
    """'C-s' -> 'Ctrl + S', 'A-ret' -> 'Alt + Enter', 'space' -> 'Space'."""
    parts = key.split("-")
    if len(parts) > 1 and parts[-1] == "":
        parts = parts[:-2] + ["-"]
    return combo(parts[:-1], {"ret": "Enter", "del": "Delete", "ins": "Insert"}.get(parts[-1], parts[-1]))


def load_helix(ctx: Ctx) -> list[Keymap]:
    main = ctx.first()
    if not main:
        return []
    try:
        data = tomllib.loads(read_text(main) or "")
    except tomllib.TOMLDecodeError as e:
        ctx.notes.append(f"invalid TOML: {e}")
        return []
    out = []

    def walk(table, mode, prefix):
        for k, v in table.items():
            keys = prefix + [helix_key(k)]
            if isinstance(v, dict):
                walk(v, mode, keys)
            else:
                action = " ; ".join(map(str, v)) if isinstance(v, list) else str(v)
                out.append(Keymap(SEQ.join(keys), action, "", "" if mode == "normal" else mode, rel(main)))

    for mode, table in (data.get("keys") or {}).items():
        if isinstance(table, dict):
            walk(table, mode, [])
    ctx.notes.append("Only your config.toml is shown: Helix defaults are in `:help keymap`.")
    return out


# ------------------------------------------------------------------ Zed --

def load_zed(ctx: Ctx) -> list[Keymap]:
    main = ctx.first()
    if not main:
        return []
    try:
        data = loads_jsonc(read_text(main) or "[]")
    except json.JSONDecodeError as e:
        ctx.notes.append(f"invalid JSON: {e.msg}")
        return []
    out = []
    for block in data if isinstance(data, list) else []:
        if not isinstance(block, dict):
            continue
        section = str(block.get("context", ""))
        for key, action in (block.get("bindings") or {}).items():
            if action is None:
                action = "(unbound)"
            elif isinstance(action, list):
                action = " ".join(json.dumps(a) if not isinstance(a, str) else a for a in action)
            out.append(Keymap(_chords(key, "-"), str(action), "", section, rel(main)))
    ctx.notes.append("Only your keymap.json is shown: Zed defaults are listed with `zed: open default keymap`.")
    return out


_CODE_FLAVOURS = [
    ("vscode", "VS Code", ("code",), "Code", "vscode"),
    ("vscode-oss", "Code - OSS", ("code-oss",), "Code - OSS", "code-oss"),
    ("vscodium", "VSCodium", ("codium",), "VSCodium", "vscodium"),
    ("cursor", "Cursor", ("cursor",), "Cursor", "cursor"),
    ("windsurf", "Windsurf", ("windsurf",), "Windsurf", "windsurf"),
]

APPS = [
    App("nvim", "Neovim", "editor", load_vimlike, paths=(f"{CONFIG}/nvim",), bins=("nvim",), icon="nvim"),
    App("vim", "Vim", "editor", load_vimlike,
        paths=("~/.vimrc", "~/.vim/vimrc", "~/.vim/plugin", "~/.vim/after"), bins=("vim",), icon="gvim"),
    *[App(i, name, "editor", load_vscode, paths=(f"{CONFIG}/{d}/User/keybindings.json",), bins=b, icon=icon)
      for i, name, b, d, icon in _CODE_FLAVOURS],
    App("helix", "Helix", "editor", load_helix, paths=(f"{CONFIG}/helix/config.toml",),
        bins=("hx", "helix"), icon="helix"),
    App("zed", "Zed", "editor", load_zed, paths=(f"{CONFIG}/zed/keymap.json",),
        bins=("zeditor", "zed"), icon="zed"),
]
