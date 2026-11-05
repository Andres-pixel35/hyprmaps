"""Just enough Lua reading to pull keymaps out of config files.

It is not a Lua interpreter: it finds calls, splits their top-level
arguments and evaluates string literals / concatenations of known string
variables. Everything else is kept as source text.
"""

import re

_OPEN = {"(": ")", "{": "}", "[": "]"}
_BLOCK_OPEN = {"function", "if", "do", "repeat"}
_BLOCK_CLOSE = {"end", "until"}
_WORD = re.compile(r"[A-Za-z_]\w*")
_LONG = re.compile(r"\[(=*)\[")


def strip_comments(text: str) -> str:
    """Blank out comments (keeping offsets and newlines) so scans ignore them."""
    out, i, n = list(text), 0, len(text)
    while i < n:
        c = text[i]
        if c in "\"'":
            i = _skip_string(text, i)
            continue
        if text.startswith("[[", i) or re.match(r"\[=+\[", text[i:i + 8]):
            i = _skip_long(text, i)
            continue
        if text.startswith("--", i):
            j = i + 2
            if re.match(r"\[=*\[", text[j:j + 8]):
                end = _skip_long(text, j)
            else:
                end = text.find("\n", i)
                end = n if end == -1 else end
            for k in range(i, end):
                if out[k] != "\n":
                    out[k] = " "
            i = end
            continue
        i += 1
    return "".join(out)


def _skip_string(text: str, i: int) -> int:
    q, i = text[i], i + 1
    while i < len(text):
        if text[i] == "\\":
            i += 2
            continue
        if text[i] == q or text[i] == "\n":
            return i + 1
        i += 1
    return i


def _skip_long(text: str, i: int) -> int:
    m = _LONG.match(text, i)  # not text[i:]: a copy of the rest of the file per long string
    if not m:
        return i + 1
    end = text.find("]" + m.group(1) + "]", i)
    return len(text) if end == -1 else end + len(m.group(1)) + 2


def balanced(text: str, i: int) -> int:
    """Index just past the bracket that closes the one at ``text[i]``."""
    stack, n = [], len(text)
    while i < n:
        c = text[i]
        if c in "\"'":
            i = _skip_string(text, i)
            continue
        if c == "[" and re.match(r"\[=*\[", text[i:i + 8]):
            i = _skip_long(text, i)
            continue
        if c in _OPEN:
            stack.append(_OPEN[c])
        elif stack and c == stack[-1]:
            stack.pop()
            if not stack:
                return i + 1
        i += 1
    return n


def split_top(text: str, sep: str = ",") -> list[str]:
    """Split on ``sep`` outside brackets, strings and function...end blocks."""
    parts, depth, block, cur, i, n = [], 0, 0, [], 0, len(text)
    while i < n:
        c = text[i]
        if c in "\"'":
            j = _skip_string(text, i)
            cur.append(text[i:j])
            i = j
            continue
        if c == "[" and re.match(r"\[=*\[", text[i:i + 8]):
            j = _skip_long(text, i)
            cur.append(text[i:j])
            i = j
            continue
        at_word_start = (c.isalpha() or c == "_") and (i == 0 or not (text[i - 1].isalnum() or text[i - 1] == "_"))
        m = _WORD.match(text, i) if at_word_start else None
        if m:
            w = m.group(0)
            if w in _BLOCK_OPEN:
                block += 1
            elif w in _BLOCK_CLOSE and block:
                block -= 1
            cur.append(w)
            i = m.end()
            continue
        if c in "({[":
            depth += 1
        elif c in ")}]":
            depth -= 1
        if c == sep and depth == 0 and block == 0:
            parts.append("".join(cur).strip())
            cur = []
        else:
            cur.append(c)
        i += 1
    tail = "".join(cur).strip()
    if tail or parts:
        parts.append(tail)
    return [p for p in parts if p != ""]


def find_calls(text: str, name_re: str):
    """Yield (callee, args_text, line_number) for calls matching ``name_re``.
    ``text`` should already have comments stripped."""
    for m in re.finditer(r"(?<![\w.:])(" + name_re + r")\s*\(", text):
        start = m.end() - 1
        end = balanced(text, start)
        yield m.group(1), text[start + 1:end - 1], text.count("\n", 0, m.start()) + 1


def string_value(expr: str) -> str | None:
    """Value of a single Lua string literal, else None."""
    e = expr.strip()
    if len(e) >= 2 and e[0] in "\"'" and e[-1] == e[0]:
        body = e[1:-1]
        return re.sub(r"\\(.)", lambda m: {"n": "\n", "t": "\t"}.get(m.group(1), m.group(1)), body)
    m = re.fullmatch(r"\[(=*)\[(.*)\]\1\]", e, re.S)
    return m.group(2) if m else None


def concat_value(expr: str, variables: dict, unknown: str = "*") -> tuple[str, bool]:
    """Evaluate ``a .. " + " .. b``; unknown parts become ``unknown``.
    Returns (value, fully_known)."""
    out, known = [], True
    for part in _split_concat(expr):
        v = string_value(part)
        if v is None:
            v = variables.get(part.strip())
        if v is None:
            out.append(unknown)
            known = False
        else:
            out.append(v)
    return "".join(out), known


def _split_concat(expr: str) -> list[str]:
    parts, depth, cur, i = [], 0, "", 0
    while i < len(expr):
        c = expr[i]
        if c in "\"'":
            j = _skip_string(expr, i)
            cur += expr[i:j]
            i = j
            continue
        if c in "({[":
            depth += 1
        elif c in ")}]":
            depth -= 1
        if depth == 0 and expr.startswith("..", i):
            parts.append(cur)
            cur, i = "", i + 2
            continue
        cur += c
        i += 1
    parts.append(cur)
    return [p.strip() for p in parts if p.strip()]


def string_vars(text: str) -> dict:
    """``local name = "value"`` (and global) string assignments in a file."""
    found = {}
    for m in re.finditer(r"^\s*(?:local\s+)?([A-Za-z_]\w*)\s*=\s*(\"[^\"\n]*\"|'[^'\n]*')\s*$", text, re.M):
        found[m.group(1)] = string_value(m.group(2))
    return found


def table_fields(table_src: str) -> tuple[list[str], dict]:
    """Split ``{ "a", "b", desc = "x" }`` into positional items and named fields
    (values kept as source text)."""
    inner = table_src.strip()
    if inner.startswith("{") and inner.endswith("}"):
        inner = inner[1:-1]
    pos, named = [], {}
    for item in split_top(inner):
        m = re.match(r"^([A-Za-z_]\w*)\s*=\s*(.+)$", item, re.S)
        if m and not item.lstrip().startswith(("\"", "'")):
            named[m.group(1)] = m.group(2).strip()
        else:
            m2 = re.match(r'^\[\s*("[^"]*"|\'[^\']*\')\s*\]\s*=\s*(.+)$', item, re.S)
            if m2:
                named[string_value(m2.group(1))] = m2.group(2).strip()
            else:
                pos.append(item)
    return pos, named


def short(src: str, limit: int = 80) -> str:
    """One-line preview of a Lua expression (functions, tables)."""
    s = " ".join(src.split())
    return s if len(s) <= limit else s[:limit - 1] + "…"
