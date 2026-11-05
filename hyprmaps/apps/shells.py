"""Shells: zsh (bindkey / ZLE), bash (readline: inputrc + bind) and fish.

Defaults come from the shell itself started without any rc file (``zsh -f``,
``bash --norc --noprofile``, ``fish --no-config``) inside a throw-away HOME,
so nothing of the user runs and no history is written. User binds come from
reading the rc files and what they ``source``.
"""

import re
import shlex
from pathlib import Path

from ..model import SEQ, Keymap, block_comments, combo, merge
from ..sources import CONFIG, HOME, read_text, read_tree, rel, run
from .base import App, Ctx

# ------------------------------------------------- key sequence decoding --

_CSI_FINAL = {"A": "Up", "B": "Down", "C": "Right", "D": "Left", "H": "Home", "F": "End",
              "Z": "Tab", "P": "F1", "Q": "F2", "R": "F3", "S": "F4", "M": "Enter"}
_CSI_TILDE = {"1": "Home", "2": "Insert", "3": "Delete", "4": "End", "5": "PageUp",
              "6": "PageDown", "7": "Home", "8": "End", "15": "F5", "17": "F6", "18": "F7",
              "19": "F8", "20": "F9", "21": "F10", "23": "F11", "24": "F12", "200": "Paste start",
              "201": "Paste end"}
_TERMINFO = {"kcuu1": "Up", "kcud1": "Down", "kcub1": "Left", "kcuf1": "Right", "khome": "Home",
             "kend": "End", "kpp": "PageUp", "knp": "PageDown", "kdch1": "Delete",
             "kich1": "Insert", "kbs": "Backspace", "kcbt": "Shift + Tab", "kLFT5": "Ctrl + Left",
             "kRIT5": "Ctrl + Right", "kDC5": "Ctrl + Delete", "kDC": "Shift + Delete",
             **{f"kf{i}": f"F{i}" for i in range(1, 13)}}
_CARET = {"?": "Backspace", "I": "Tab", "M": "Enter", "@": "Ctrl + Space", "[": "Esc"}


def readline_to_caret(s: str) -> str:
    """'\\C-x\\M-f\\e[A' (readline / fish 3 escapes) -> '^X^[f^[[A'."""
    out, i = "", 0
    while i < len(s):
        if s.startswith(("\\C-", "\\c"), i) and not s.startswith("\\C-\\", i):
            n = 3 if s[i + 2] == "-" else 2
            ch = s[i + n:i + n + 1]
            out += "^?" if ch == "?" else "^" + ch.upper()
            i += n + 1
        elif s.startswith("\\M-", i):
            out += "^["
            i += 3
        elif s.startswith(("\\e", "\\E"), i):
            out += "^["
            i += 2
        elif s.startswith("\\b", i):
            out += "^?"
            i += 2
        elif s.startswith("\\t", i):
            out += "^I"
            i += 2
        elif s.startswith(("\\n", "\\r"), i):
            out += "^M"
            i += 2
        elif re.match(r"\\[0-7]{3}", s[i:]):
            code = int(s[i + 1:i + 4], 8)
            out += "^?" if code == 127 else ("^" + chr(code + 64) if code < 32 else chr(code))
            i += 4
        elif s[i] == "\\" and i + 1 < len(s):
            out += s[i + 1]
            i += 2
        else:
            out += s[i]
            i += 1
    return out


def decode_caret(seq: str) -> str:
    """'^[[1;5C' -> 'Ctrl + Right', '^X^E' -> 'Ctrl + X › Ctrl + E', '^[f' -> 'Alt + F'."""
    m = re.fullmatch(r"\$\{?terminfo\[(\w+)\]\}?|\$\{?key\[(\w+)\]\}?", seq)
    if m:
        name = m.group(1) or m.group(2)
        return _TERMINFO.get(name, name) if m.group(1) else combo([], name)
    tokens, i, alt = [], 0, False
    while i < len(seq):
        tok = None
        if seq.startswith("^[", i) and i + 2 < len(seq) and seq[i + 2] in "[O":
            m = re.match(r"\^\[[\[O]([\d;]*)([A-Za-z~])", seq[i:])
            if m:
                params, final = m.group(1).split(";"), m.group(2)
                key = _CSI_TILDE.get(params[0], "?") if final == "~" else _CSI_FINAL.get(final, final)
                mods = []
                if len(params) > 1 and params[1].isdigit():
                    bits = int(params[1]) - 1
                    mods = [n for b, n in ((4, "Ctrl"), (2, "Alt"), (1, "Shift")) if bits & b]
                if final == "Z":
                    mods.append("Shift")
                tok = combo(mods, key)
                i += m.end()
        if tok is None and seq.startswith("^[", i):
            if i + 2 >= len(seq):
                tok, i = "Esc", i + 2
            else:
                alt, i = True, i + 2
                continue
        if tok is None and seq[i] == "^" and i + 1 < len(seq):
            ch = seq[i + 1]
            tok = _CARET.get(ch) or combo(["Ctrl"], ch.lower())
            i += 2
        if tok is None:
            tok = "Space" if seq[i] == " " else seq[i]
            i += 1
        if alt:  # ESC prefix = Alt; '^[f' and '^[F' are different binds
            if len(tok) == 1 and tok.isalpha():
                tok = combo(["Alt"] + (["Shift"] if tok.isupper() else []), tok)
            elif tok.startswith("Ctrl + "):
                tok = "Ctrl + Alt + " + tok[7:]
            else:
                tok = "Alt + " + tok
            alt = False
        tokens.append(tok)
    return SEQ.join(tokens)


def decode_fish(key: str) -> str:
    """fish 4 'ctrl-x,ctrl-e' / 'alt-backspace' and fish 3 '\\cx' / '\\e[A'."""
    if "\\" in key or key.startswith("^"):
        return decode_caret(readline_to_caret(key))
    chords = []
    for chord in key.split(","):
        parts = chord.split("-")
        if len(parts) > 1 and parts[-1] == "":
            parts = parts[:-2] + ["-"]
        chords.append(combo(parts[:-1], parts[-1]))
    return SEQ.join(chords)


# Possessive: nothing after each part can start with what it matches, so no match changes,
# but a long run of blanks no longer backtracks (cubic time).
_FUNC_HEAD = re.compile(r"^\s*+(?:function\s++)?([\w.:-]++)\s*+(?:\(\s*+\))?\s*+\{?\s*+$")


def _function_comments(files) -> dict:
    """Comment block above each shell function: 'name() {' / 'function name'."""
    found = {}
    for _, lines in files:
        comments = block_comments(lines, "#")
        for i, line in enumerate(lines):
            m = _FUNC_HEAD.match(line)
            if m and ("(" in line or line.lstrip().startswith("function")) and comments.get(i):
                found[m.group(1)] = comments[i]
    return found


def _tokens(line: str) -> list[str] | None:
    try:
        return shlex.split(line, comments=True)
    except ValueError:
        return None


# ------------------------------------------------------------------ zsh --

_ZSH_SOURCE = re.compile(r"^\s*(?:source|\.)\s+(?P<path>[^;&|]+?)\s*$")
_ZSH_SKIP = {"self-insert", "undefined-key", "digit-argument", "self-insert-unmeta"}
_ZSH_SECTIONS = {"main": "", "emacs": "", "viins": "", "vicmd": "vicmd"}


def _shell_vars(files_text: str) -> dict:
    return {m.group(1): m.group(2).strip("'\"").replace("$HOME", str(HOME))
            for m in re.finditer(r"^\s*(?:export\s+)?(\w+)=(\S+)\s*$", files_text, re.M)}


def _zsh_files(ctx: Ctx, aliases: bool = False):
    """The rc files and what they source. With oh-my-zsh also its lib file(s)
    and the enabled plugins; ``aliases`` takes every lib file plus the
    $ZSH_CUSTOM/*.zsh files, in the order oh-my-zsh.sh loads them."""
    main = ctx.first()
    if not main:
        return [], False
    first_text = read_text(main) or ""
    variables = _shell_vars(first_text)
    files = list(read_tree(main, _ZSH_SOURCE, variables))
    omz = variables.get("ZSH")
    omz_used = bool(omz) and any(p.name == "oh-my-zsh.sh" for p, _ in files)
    if omz_used:
        extra = sorted((Path(omz) / "lib").glob("*.zsh")) if aliases else [Path(omz) / "lib" / "key-bindings.zsh"]
        if variables.get("ENABLE_CORRECTION") != "true":  # correction.zsh aliases apply only with this flag
            extra = [p for p in extra if p.name != "correction.zsh"]
        m = re.search(r"^\s*plugins=\(([^)]*)\)", first_text, re.M)
        custom = variables.get("ZSH_CUSTOM", f"{omz}/custom")
        for plugin in (m.group(1).split() if m else []):
            for base in (custom, omz):
                p = Path(base) / "plugins" / plugin / f"{plugin}.plugin.zsh"
                if p.is_file():
                    extra.append(p)
                    break
        if aliases:
            extra += sorted(Path(custom).glob("*.zsh"))
        # OMZ files load where oh-my-zsh.sh is sourced: later .zshrc lines override them,
        # so the sourcing file is split there and its tail goes after the OMZ files.
        at = next(i for i, (p, _) in enumerate(files) if p.name == "oh-my-zsh.sh") + 1
        tail = []
        for j, (p, lines) in enumerate(files[:at]):
            n = next((n for n, line in enumerate(lines) if "oh-my-zsh.sh" in line and _ZSH_SOURCE.match(line)), None)
            if n is not None:
                files[j] = (p, lines[:n + 1])
                tail = [(p, [""] * (n + 1) + lines[n + 1:])]  # blank head keeps the line numbers
                break
        omz_files = [(p, text.splitlines()) for p in extra if (text := read_text(p)) is not None]
        files[at:at] = omz_files + tail
    return files, omz_used


def load_zsh(ctx: Ctx) -> list[Keymap]:
    files, _ = _zsh_files(ctx)
    all_text = "\n".join("\n".join(lines) for _, lines in files)
    vi = bool(re.search(r"^\s*bindkey\s+-v\b|^\s*set\s+-o\s+vi\b", all_text, re.M)) or \
        bool(re.search(r"^\s*plugins=\([^)]*\bvi-mode\b", all_text, re.M))
    main_maps = ("viins", "vicmd") if vi else ("emacs",)
    widget_doc = _function_comments(files)
    user = []
    for path, lines in files:
        is_omz = "/.oh-my-zsh/" in str(path) or "/oh-my-zsh/" in str(path)
        comments = block_comments(lines, "#", lambda t: t.startswith("bindkey"))
        for i, line in enumerate(lines):
            if not re.match(r"^\s*bindkey\s", line):
                continue
            k = _zsh_bindkey(_tokens(line.strip()) or [], main_maps)
            if not k:
                continue
            k.desc = widget_doc.get(k.action, "") or comments.get(i, "")
            k.source, k.default = f"{rel(path)}:{i + 1}", is_omz
            user.append(k)
    defaults = []
    if ctx.defaults:
        for keymap in main_maps:
            out = run(["zsh", "-f", "-c", f"bindkey -M {keymap}"], sandbox=True) or ""
            for line in out.splitlines():
                t = _tokens(line)
                if not t or len(t) != 2 or t[1] in _ZSH_SKIP:
                    continue
                defaults.append(Keymap(decode_caret(readline_to_caret(t[0])), t[1], "", _ZSH_SECTIONS.get(keymap, keymap),
                                       "zsh defaults", default=True))
    ctx.notes.append(f"Keymap shown: {'vi (insert + vicmd)' if vi else 'emacs'}.")
    return merge(defaults, user)


def _zsh_bindkey(t: list[str], main_maps) -> Keymap | None:
    """Parse the tokens of one 'bindkey ...' command."""
    t, keymap, macro, remove = t[1:], "", False, False
    while t and t[0].startswith("-") and len(t[0]) > 1:
        opt = t.pop(0)
        if opt == "-M" and t:
            keymap = t.pop(0)
        elif opt == "-a":
            keymap = "vicmd"
        elif opt == "-s":
            macro = True
        elif opt == "-r":
            remove = True
        elif opt in ("-e", "-v", "-A", "-N", "-l", "-L", "-d", "-D"):
            return None
    if not t:
        return None
    keymap = keymap or main_maps[0]
    if keymap in ("emacs", "viins", "vicmd") and keymap not in main_maps:
        return None
    action = "" if remove else (f"types {t[1]!r}" if macro and len(t) > 1 else (t[1] if len(t) > 1 else ""))
    if not action and not remove:
        return None
    key = t[0] if t[0].startswith("$") else readline_to_caret(t[0])  # bindkey also takes \e, \C-x
    return Keymap(decode_caret(key), action, "", _ZSH_SECTIONS.get(keymap, keymap), removed=remove)


# ----------------------------------------------------------------- bash --

_INPUTRC_INCLUDE = re.compile(r"^\s*\$include\s+(?P<path>\S+)")
_BASH_SOURCE = _ZSH_SOURCE
_RL_SKIP = {"self-insert", "do-lowercase-version", "digit-argument"}
_RL_NAMES = {"rubout": "Backspace", "del": "Delete", "esc": "Esc", "escape": "Esc", "lfd": "Ctrl + J",
             "newline": "Enter", "ret": "Enter", "return": "Enter", "space": "Space", "spc": "Space",
             "tab": "Tab"}


def _rl_key(spec: str) -> str:
    """inputrc key: '"\\C-x\\C-r"' (sequence) or 'Control-u' / 'Meta-Rubout' (name)."""
    spec = spec.strip()
    if spec.startswith('"') and spec.endswith('"'):
        return decode_caret(readline_to_caret(spec[1:-1]))
    parts = spec.split("-")
    mods = [{"control": "Ctrl", "c": "Ctrl", "meta": "Alt", "m": "Alt"}.get(p.lower(), p) for p in parts[:-1]]
    key = _RL_NAMES.get(parts[-1].lower(), parts[-1])
    return combo(mods, key)


def _rl_line(line: str):
    """'"\\C-a": beginning-of-line' -> (key, action) for inputrc / bind -p lines."""
    m = re.match(r'^\s*("(?:[^"\\]|\\.)*"|[\w-]+)\s*:\s*(.+?)\s*$', line)
    if not m or line.lstrip().startswith(("#", "$", "set ")):
        return None
    return _rl_key(m.group(1)), m.group(2)


def load_bash(ctx: Ctx) -> list[Keymap]:
    user, vi = [], False
    bashrc = [p for p in ctx.paths if p.name in (".bashrc", ".bash_profile")]
    inputrc = [p for p in ctx.paths if p.name == ".inputrc"]
    rc_files = [f for p in bashrc for f in read_tree(p, _BASH_SOURCE)]
    func_doc = _function_comments(rc_files)
    for p in inputrc:
        for path, lines in read_tree(p, _INPUTRC_INCLUDE):
            comments = block_comments(lines, "#")
            for i, line in enumerate(lines):
                if re.match(r"^\s*set\s+editing-mode\s+vi", line):
                    vi = True
                kv = _rl_line(line)
                if kv:
                    user.append(Keymap(kv[0], kv[1], comments.get(i, ""), "", f"{rel(path)}:{i + 1}"))
    for path, lines in rc_files:
        comments = block_comments(lines, "#", lambda t: t.startswith("bind "))
        for i, line in enumerate(lines):
            if re.match(r"^\s*set\s+-o\s+vi\b", line):
                vi = True
            if not re.match(r"^\s*bind\s", line):
                continue
            t = _tokens(line.strip()) or []
            shell_cmd = "-x" in t
            spec = next((a for a in t[1:] if ":" in a and not a.startswith("-")), None)
            kv = _rl_line(spec) if spec else None
            if not kv:
                continue
            action = kv[1].strip("'\"")
            desc = func_doc.get(action.split()[0], "") if action else ""
            user.append(Keymap(kv[0], ("run " if shell_cmd else "") + action,
                               desc or comments.get(i, ""), "", f"{rel(path)}:{i + 1}"))
    defaults = []
    if ctx.defaults:
        modes = (("vi-insert", ""), ("vi-command", "vi-command")) if vi else (("emacs", ""),)
        for mode, section in modes:
            out = run(["bash", "--norc", "--noprofile", "-i", "-c", f"bind -m {mode} -p"], sandbox=True) or ""
            for line in out.splitlines():
                kv = _rl_line(line)
                if kv and kv[1] not in _RL_SKIP:
                    defaults.append(Keymap(kv[0], kv[1], "", section, "readline defaults", default=True))
    ctx.notes.append(f"Readline mode shown: {'vi' if vi else 'emacs'}.")
    return merge(defaults, user)


# ----------------------------------------------------------------- fish --

def _fish_tokens(line: str) -> list[str] | None:
    """fish escapes (\\cr, \\e) must survive: split without POSIX unescaping."""
    try:
        words = shlex.split(line, comments=True, posix=False)
    except ValueError:
        return None
    return [w[1:-1] if len(w) > 1 and w[0] == w[-1] and w[0] in "'\"" else w for w in words]


def _fish_bind(t: list[str], source: str) -> Keymap | None:
    t, mode, erase = t[1:], "default", False
    while t and t[0].startswith("-") and len(t[0]) > 1 and not t[0].startswith("-\\"):
        opt = t.pop(0)
        if opt in ("-M", "--mode") and t:
            mode = t.pop(0)
        elif opt in ("-m", "--sets-mode") and t:
            t.pop(0)
        elif opt in ("-e", "--erase"):
            erase = True
        elif opt in ("-k", "--key") and t:
            t[0] = "${key[" + t[0] + "]}"
        elif opt in ("-a", "--all", "-K", "--key-names", "-f", "--function-names", "-L", "--list-modes"):
            return None
    if not t or (len(t) < 2 and not erase):
        return None
    key = decode_caret(t[0]) if t[0].startswith("${key[") else decode_fish(t[0])
    return Keymap(key, " ".join(t[1:]), "", "" if mode == "default" else mode, source, removed=erase)


def _fish_files(ctx: Ctx):
    """(path, lines) of the configured files and of the *.fish files in the configured dirs."""
    files = []
    for p in ctx.paths:
        for f in ([p] if p.is_file() else sorted(p.glob("*.fish"))):
            text = read_text(f)
            if text is not None:
                files.append((f, text.splitlines()))
    return files


def load_fish(ctx: Ctx) -> list[Keymap]:
    files = _fish_files(ctx)
    vi = any(re.search(r"^\s*fish_vi_key_bindings\b", "\n".join(lines), re.M) for _, lines in files)
    func_doc = _function_comments(files)
    user = []
    for path, lines in files:
        comments = block_comments(lines, "#", lambda t: t.startswith("bind "))
        for i, line in enumerate(lines):
            if not re.match(r"^\s*bind\s", line):
                continue
            k = _fish_bind(_fish_tokens(line.strip()) or [], f"{rel(path)}:{i + 1}")
            if k:
                k.desc = func_doc.get(k.action.split(" ")[0], "") if k.action else ""
                k.desc = k.desc or comments.get(i, "")
                user.append(k)
    defaults = []
    if ctx.defaults:
        script = ("fish_vi_key_bindings; " if vi else "") + "bind"
        out = run(["fish", "--no-config", "-c", script], sandbox=True) or ""
        for line in out.splitlines():
            k = _fish_bind([w for w in (_fish_tokens(line) or []) if w != "--preset"], "fish defaults")
            if k and k.action not in ("self-insert", "self-insert-notfirst", "and"):
                k.default = True
                defaults.append(k)
    return merge(defaults, user)


# -------------------------------------------------------------- aliases --
# An alias is a Keymap row: key = the alias name, action = what it expands
# to, section = "global" / "suffix" (zsh -g / -s) or "abbr" (fish abbr).
# shortcut: static reading only, so aliases made in loops or by plugin managers
# oh-my-zsh does not load are missed, and ones inside an `if` always count.
# shortcut: `cond && alias x=a || alias x=b` lines are skipped, since either branch may be
# wrong (omz git: gfa, gpf, gpsupf, gsta); upgrade with a live `zsh -i -c 'alias -L'` mode.
# shortcut: only the first command of a line is read (`alias g=git; alias h=history` misses h),
# upgrade by splitting the words at ; and parsing each command when such lines show up.

_ALIAS_LINE = re.compile(r"^\s*(?:alias|unalias|abbr|function)\s")
_ALIAS_CODE = ("alias ", "unalias ", "abbr ")  # commented-out code, not a description


def _alias_words(line: str):
    """Words of the first command on a line (up to ; & |) and its trailing # comment."""
    lex = shlex.shlex(line, posix=True, punctuation_chars=";&|")
    lex.whitespace_split, lex.commenters = True, ""
    words = []
    try:
        for w in lex:
            if w.startswith("#"):  # an unquoted word starting with # opens a comment
                return words, (w + " " + lex.instream.read()).strip("# \t")
            if w[:1] in (";", "&", "|"):
                break
            words.append(w)
    except ValueError:  # unbalanced quotes: keep what was read
        pass
    return words, ""


def _posix_alias(words) -> list[Keymap]:
    """zsh / bash: 'alias [-g|-s] name=value ...' and 'unalias [-g|-s] name ...'."""
    cmd, args, section = words[0], words[1:], ""
    if cmd not in ("alias", "unalias"):
        return []
    while args and args[0].startswith("-") and "=" not in args[0]:
        opt = args.pop(0)
        if opt == "--":
            break
        if cmd == "unalias" and "a" in opt:  # unalias -a: shortcut, not followed
            return []
        section = "global" if "g" in opt else "suffix" if "s" in opt else section
    out = []
    for a in args:
        name, eq, value = a.partition("=")
        if cmd == "unalias":
            out.append(Keymap(a, section=section, removed=True))
        elif name and eq:
            out.append(Keymap(name, value, section=section))
    return out


_ABBR_VALUE_OPTS = {"-p", "--position", "-f", "--function", "--regex", "-c", "--command"}
_ABBR_QUERY_OPTS = {"-s", "--show", "-l", "--list", "-q", "--query", "--rename", "-h", "--help"}


def _fish_alias(words) -> list[Keymap]:
    """fish: 'alias name value' / 'alias name=value', 'abbr [-a] name expansion'
    and the 'function name --description "alias name=value"' that funcsave writes."""
    cmd, args = words[0], words[1:]
    if cmd == "alias":
        args = [a for a in args if not a.startswith("-")]  # --save, -s
        if not args:
            return []
        name, eq, value = args[0].partition("=")
        value = value if eq else " ".join(args[1:])
        return [Keymap(name, value)] if name and value else []
    if cmd == "function":
        for i, a in enumerate(args[1:], 1):
            if a in ("-d", "--description") and i + 1 < len(args):
                desc = args[i + 1]
            elif a.startswith("--description="):
                desc = a.removeprefix("--description=")
            else:
                continue
            name, eq, value = desc.removeprefix("alias ").partition("=")
            if desc.startswith("alias ") and eq and name == args[0]:
                return [Keymap(name, value)]
        return []
    if cmd != "abbr":
        return []
    rest, func, erase, it = [], "", False, iter(args)
    for a in it:
        if a in _ABBR_QUERY_OPTS:
            return []
        if a in _ABBR_VALUE_OPTS:
            value = next(it, "")
            func = value if a in ("-f", "--function") else func
        elif a in ("-e", "--erase"):
            erase = True
        elif not a.startswith("-") or rest:  # -a, --add, -g, -U, --set-cursor, --
            rest.append(a)
    if not rest:
        return []
    if erase:
        return [Keymap(n, section="abbr", removed=True) for n in rest]
    value = " ".join(rest[1:]) or (f"runs {func}" if func else "")
    return [Keymap(rest[0], value, section="abbr")] if value else []


def _aliases(files, parse, vendor=lambda path: False) -> list[Keymap]:
    """Aliases of (path, lines) files. Description: the trailing # comment, the
    comment block above, else the expansion itself."""
    out = []
    for path, lines in files:
        # other code ends a comment's reach: '# needs git 2.8' above an `if` is not about the aliases after it
        only = [s if _ALIAS_LINE.match(s) or s.lstrip().startswith("#") else "" for s in lines]
        comments = block_comments(only, "#", lambda t: t.startswith(_ALIAS_CODE))
        for i, line in enumerate(lines):
            if not _ALIAS_LINE.match(line):
                continue
            words, comment = _alias_words(line)
            for k in parse(words) if words else []:
                k.desc = comment or comments.get(i, "") or k.action
                k.source, k.default = f"{rel(path)}:{i + 1}", vendor(path)
                out.append(k)
    return out


def _default_aliases(argv, source: str, parse) -> list[Keymap]:
    """Aliases the shell prints when started without any rc file."""
    out = []
    for line in (run(argv, sandbox=True) or "").splitlines():
        words, _ = _alias_words(line)
        for k in parse(words) if words else []:
            k.desc, k.source, k.default = k.action, source, True
            out.append(k)
    return out


def load_zsh_aliases(ctx: Ctx) -> list[Keymap]:
    files, _ = _zsh_files(ctx, aliases=True)

    def vendor(path):  # oh-my-zsh's own aliases are defaults; $ZSH/custom/*.zsh are yours
        return ("/.oh-my-zsh/" in str(path) or "/oh-my-zsh/" in str(path)) and path.parent.name != "custom"

    defaults = _default_aliases(["zsh", "-f", "-c", "alias -L"], "zsh defaults", _posix_alias) if ctx.defaults else []
    return merge(defaults, _aliases(files, _posix_alias, vendor))


def load_bash_aliases(ctx: Ctx) -> list[Keymap]:
    files, seen = [], set()
    for p in ctx.paths:
        for path, lines in read_tree(p, _BASH_SOURCE):
            if path.resolve() not in seen:  # ~/.bash_aliases is a path of its own and often sourced too
                seen.add(path.resolve())
                files.append((path, lines))
    defaults = _default_aliases(["bash", "--norc", "--noprofile", "-i", "-c", "alias"], "bash defaults",
                                _posix_alias) if ctx.defaults else []
    return merge(defaults, _aliases(files, _posix_alias))


def load_fish_aliases(ctx: Ctx) -> list[Keymap]:
    defaults = _default_aliases(["fish", "--no-config", "-c", "alias; abbr --show"], "fish defaults",
                                _fish_alias) if ctx.defaults else []
    return merge(defaults, _aliases(_fish_files(ctx), _fish_alias))


APPS = [
    App("zsh", "zsh", "shell", load_zsh, paths=("$ZDOTDIR/.zshrc", "~/.zshrc"),
        bins=("zsh",), icon="utilities-terminal"),
    App("bash", "bash", "shell", load_bash, paths=("~/.bashrc", "~/.bash_profile", "~/.inputrc"),
        bins=("bash",), icon="utilities-terminal"),
    App("fish", "fish", "shell", load_fish,
        paths=(f"{CONFIG}/fish/config.fish", f"{CONFIG}/fish/functions/fish_user_key_bindings.fish",
               f"{CONFIG}/fish/conf.d"),
        bins=("fish",), icon="utilities-terminal"),
    App("zsh-aliases", "zsh aliases", "alias", load_zsh_aliases, paths=("$ZDOTDIR/.zshrc", "~/.zshrc"),
        bins=("zsh",), icon="utilities-terminal"),
    App("bash-aliases", "bash aliases", "alias", load_bash_aliases,
        paths=("~/.bashrc", "~/.bash_profile", "~/.bash_aliases"), bins=("bash",), icon="utilities-terminal"),
    App("fish-aliases", "fish aliases", "alias", load_fish_aliases,
        paths=(f"{CONFIG}/fish/config.fish", f"{CONFIG}/fish/conf.d", f"{CONFIG}/fish/functions"),
        bins=("fish",), icon="utilities-terminal"),
]
