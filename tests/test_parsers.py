"""Parser tests with inline fixtures. Run: python3 -m unittest discover -s tests

Loaders run with defaults=False so no external command is needed: only the
file readers are exercised. Fixtures live in a temp dir per test.
"""

import gzip
import io
import json
import struct
import sys
import tempfile
import textwrap
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hyprmaps import luaparse as lua  # noqa: E402
from hyprmaps import model, sources  # noqa: E402
from hyprmaps.apps import base, browsers, editors, generic, misc, multiplexers, shells, terminals, wm  # noqa: E402
from hyprmaps.cli import finalize  # noqa: E402
from hyprmaps.config import theme_args  # noqa: E402
from hyprmaps.model import Keymap  # noqa: E402


class Fixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, name: str, text: str) -> Path:
        p = self.dir / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(text).lstrip("\n"))
        return p

    def load(self, loader, *paths, **kw):
        app = base.App("test", "Test", "other", loader)
        ctx = base.Ctx(app, list(paths), defaults=False, **kw)
        return {(k.section, k.key): k for k in loader(ctx) if not k.removed}, ctx


# ------------------------------------------------------------------ model --

class ModelTest(unittest.TestCase):
    def test_combo_notations(self):
        self.assertEqual(model.split_combo("ctrl+shift+t"), "Ctrl + Shift + T")
        self.assertEqual(model.split_combo("ctrl++"), "Ctrl + +")
        self.assertEqual(model.split_combo("kitty_mod>n"), "Kitty_mod › n")
        self.assertEqual(model.split_combo("super+shift+return"), "Super + Shift + Enter")
        self.assertEqual(model.split_combo("Control+Shift+c"), "Ctrl + Shift + C")

    def test_angle_keys(self):
        self.assertEqual(model.pretty_angle_keys("<C-x>gg"), "Ctrl + X › gg")
        self.assertEqual(model.pretty_angle_keys("<A-CR>"), "Alt + Enter")
        self.assertEqual(model.pretty_angle_keys("<C-->"), "Ctrl + -")

    def test_humanize(self):
        self.assertEqual(model.humanize("new_tab_with_cwd"), "New tab with cwd")
        self.assertEqual(model.humanize("editor.action.commentLine"), "Comment line")
        self.assertEqual(model.humanize("exec kitty"), "")

    def test_merge_overrides_and_removes(self):
        d = [Keymap("A", "x", default=True), Keymap("B", "y", default=True)]
        u = [Keymap("A", "z"), Keymap("B", "", removed=True)]
        out = model.merge(d, u)
        self.assertEqual([(k.key, k.action) for k in out], [("A", "z")])

    def test_block_comments_cover_group_until_blank(self):
        lines = ["# Move focus", "bind a", "bind b", "", "bind c", "# map x y", "bind d"]
        c = model.block_comments(lines, "#", lambda t: t.startswith("map "))
        self.assertEqual((c[1], c[2], c[4], c[6]), ("Move focus", "Move focus", "", ""))


class LuaTest(unittest.TestCase):
    def test_split_top_respects_functions_and_strings(self):
        parts = lua.split_top('"n", "<C-a>", function() local a, b = 1, 2 end, { desc = "x, y" }')
        self.assertEqual(len(parts), 4)
        self.assertTrue(parts[2].startswith("function"))

    def test_strip_comments_keeps_strings(self):
        self.assertEqual(lua.strip_comments('a = "--not" -- yes').rstrip(), 'a = "--not"')

    def test_concat_unknown_part_is_wildcard(self):
        self.assertEqual(lua.concat_value('mod .. " + " .. key', {"mod": "SUPER"}), ("SUPER + *", False))


class SourcesTest(Fixture):
    def test_binary_and_cycles(self):
        (self.dir / "bin").write_bytes(b"\0\1\2")
        self.assertIsNone(sources.read_text(self.dir / "bin"))
        a = self.write("a.conf", "include b.conf\n")
        self.write("b.conf", "include a.conf\n")
        import re
        files = list(sources.read_tree(a, re.compile(r"^include (?P<path>\S+)")))
        self.assertEqual(len(files), 2)

    def test_expand_unknown_variable_gives_nothing(self):
        self.assertEqual(sources.expand("$DOES_NOT_EXIST_123/x"), [])


# ------------------------------------------------------------- hyprland --

class HyprlandTest(Fixture):
    def test_conf(self):
        p = self.write("hyprland.conf", """
            $mainMod = SUPER
            $term = kitty
            # Terminal
            bind = $mainMod, Q, exec, $term # my terminal
            bindd = SUPER SHIFT, E, Exit Hyprland, exit,
            bindm = $mainMod, mouse:272, movewindow
            source = ./extra.conf
            submap = resize
            binde = , right, resizeactive, 10 0
            submap = reset
        """)
        self.write("extra.conf", "bind = ALT, F4, killactive,\n")
        got = {(s["section"], model.combo(s["mods"], s["key"])): s for s in wm._static_conf(p)}
        self.assertEqual(got[("", "Super + Q")]["action"], "exec kitty")
        self.assertEqual(got[("", "Super + Q")]["desc"], "my terminal")
        self.assertEqual(got[("", "Super + Shift + E")]["desc"], "Exit Hyprland")
        self.assertIn(("", "Super + MouseLeft"), got)
        self.assertIn(("resize", "Right"), got)
        self.assertIn(("", "Alt + F4"), got)

    def test_lua(self):
        self.write("hyprland.lua", """
            local mainMod = "SUPER"
            -- Launch
            hl.bind(mainMod .. " + A", hl.dsp.exec_cmd("kitty"), { description = "Terminal" })
            -- Workspaces
            for i, key in ipairs(keys) do
                hl.bind(mainMod .. " + " .. key, hl.dsp.focus({ workspace = i }))
            end
        """)
        got = wm._static_lua(self.dir)
        self.assertEqual(got[0]["desc"], "Terminal")
        self.assertEqual(got[0]["action"], "exec kitty")
        self.assertEqual((got[1]["key"], got[1]["dispatcher"], got[1]["desc"]), ("*", "focus", "Workspaces"))


# ------------------------------------------------------------ terminals --

class TerminalsTest(Fixture):
    def test_kitty(self):
        p = self.write("kitty.conf", """
            kitty_mod ctrl+alt
            # Open a tab here
            map kitty_mod+t new_tab_with_cwd
            map --mode=resize h resize_window narrower
            map ctrl+shift+q
            include extra.conf
        """)
        self.write("extra.conf", "map f1 show_kitty_doc overview\n")
        got, _ = self.load(terminals.load_kitty, p)
        self.assertEqual(got[("", "Ctrl + Alt + T")].desc, "Open a tab here")
        self.assertIn(("resize", "h"), got)
        self.assertIn(("", "F1"), got)
        self.assertNotIn(("", "Ctrl + Shift + Q"), got)

    def test_ghostty(self):
        p = self.write("config", """
            keybind = ctrl+shift+==increase_font_size:1
            keybind = global:super+grave=toggle_quick_terminal
            keybind = ctrl+a>n=new_window
            keybind = ctrl+shift+w=unbind
        """)
        got, _ = self.load(terminals.load_ghostty, p)
        self.assertEqual(got[("", "Ctrl + Shift + =")].action, "increase_font_size:1")
        self.assertIn(("", "Super + `  (global)"), got)
        self.assertIn(("", "Ctrl + A › n"), got)

    def test_alacritty(self):
        p = self.write("alacritty.toml", """
            [[keyboard.bindings]]
            key = "N"
            mods = "Control|Shift"
            action = "CreateNewWindow"
            [[keyboard.bindings]]
            key = "L"
            mods = "Control"
            chars = "\\u000c"
            mode = "~Vi"
        """)
        got, _ = self.load(terminals.load_alacritty, p)
        self.assertEqual(got[("", "Ctrl + Shift + N")].action, "CreateNewWindow")
        self.assertIn(("~Vi", "Ctrl + L"), got)

    def test_foot(self):
        p = self.write("foot.ini", """
            [main]
            font=x
            [key-bindings]
            # Scroll
            scrollback-up-page=Shift+Page_Up Control+k
            pipe-visible=[sh -c "xurls | fuzzel"] Control+Shift+u
            [search-bindings]
            cancel=Escape
        """)
        got, _ = self.load(terminals.load_foot, p)
        self.assertEqual(got[("", "Shift + PageUp")].desc, "Scroll")
        self.assertIn(("", "Ctrl + K"), got)
        self.assertTrue(got[("", "Ctrl + Shift + U")].action.startswith("pipe-visible [sh"))
        self.assertIn(("search", "Esc"), got)

    def test_wezterm_static(self):
        p = self.write("wezterm.lua", """
            config.keys = {
              { key = 'T', mods = 'CTRL|SHIFT', action = act.SpawnTab 'CurrentPaneDomain' },
              -- { key = 'X', mods = 'CTRL', action = act.Nope },
            }
        """)
        got = {k.key: k for k in terminals._wezterm_static(p)}
        self.assertEqual(list(got), ["Ctrl + Shift + T"])

    def test_wezterm_show_keys(self):
        out = "Default key table\n-----\n  CTRL   Tab   ->   ActivateTabRelative(1)\n" \
              "Key Table: copy_mode\n---\n  NONE   Escape  ->  CopyMode('Close')\n"
        got = {(k.section, k.key) for k in terminals._wezterm_show_keys(out, "x", True)}
        self.assertEqual(got, {("", "Ctrl + Tab"), ("copy_mode", "Esc")})


# --------------------------------------------------------------- shells --

class ShellsTest(Fixture):
    def test_decode(self):
        self.assertEqual(shells.decode_caret("^[[1;5C"), "Ctrl + Right")
        self.assertEqual(shells.decode_caret("^X^E"), "Ctrl + X › Ctrl + E")
        self.assertEqual(shells.decode_caret("^[F"), "Alt + Shift + F")
        self.assertEqual(shells.decode_caret(shells.readline_to_caret(r"\e[3~")), "Delete")
        self.assertEqual(shells.decode_fish("ctrl-x,ctrl-e"), "Ctrl + X › Ctrl + E")
        self.assertEqual(shells.decode_fish(r"\cr"), "Ctrl + R")

    def test_zsh(self):
        p = self.write(".zshrc", """
            # Edit the command line in $EDITOR
            my-widget() { zle edit-command-line }
            zle -N my-widget
            bindkey '^X^E' my-widget
            bindkey -M vicmd 'k' up-line
            bindkey -s '^[l' 'ls\\n'
            bindkey "${terminfo[kcuu1]}" up-line-or-search
            bindkey -r '^S'
        """)
        got, ctx = self.load(shells.load_zsh, p)
        self.assertEqual(got[("", "Ctrl + X › Ctrl + E")].desc, "Edit the command line in $EDITOR")
        self.assertNotIn(("vicmd", "k"), got)  # emacs keymap is active
        self.assertTrue(got[("", "Alt + L")].action.startswith("types"))
        self.assertIn(("", "Up"), got)
        self.assertIn("Keymap shown: emacs.", ctx.notes)

    def test_bash(self):
        rc = self.write(".bashrc", """
            # Fuzzy history
            bind -x '"\\C-r": fzf_history'
        """)
        rl = self.write(".inputrc", '"\\e[A": history-search-backward\nControl-u: unix-line-discard\n')
        got, _ = self.load(shells.load_bash, rc, rl)
        self.assertEqual(got[("", "Ctrl + R")].action, "run fzf_history")
        self.assertEqual(got[("", "Up")].action, "history-search-backward")
        self.assertIn(("", "Ctrl + U"), got)

    def test_fish(self):
        p = self.write("config.fish", "bind ctrl-f forward-word\nbind -M insert \\cr history-pager\n")
        got, _ = self.load(shells.load_fish, p)
        self.assertIn(("", "Ctrl + F"), got)
        self.assertIn(("insert", "Ctrl + R"), got)

    def test_zsh_aliases(self):
        omz = self.dir / ".oh-my-zsh"
        self.write(".oh-my-zsh/oh-my-zsh.sh", "for f in $ZSH/lib/*.zsh; do source $f; done\n")
        self.write(".oh-my-zsh/lib/directories.zsh", "alias -g ...='../..'\nalias -- -='cd -'\nalias md='mkdir -p'\n")
        self.write(".oh-my-zsh/lib/correction.zsh", "alias mv='nocorrect mv'\n")  # needs ENABLE_CORRECTION
        self.write(".oh-my-zsh/plugins/git/git.plugin.zsh", "alias gst='git status'\n")
        self.write(".oh-my-zsh/custom/mine.zsh", "alias k=kubectl\n")
        p = self.write(".zshrc", f"""
            export ZSH="{omz}"
            plugins=(git)
            source $ZSH/oh-my-zsh.sh

            # Long listing
            alias ll='ls -lh'
            alias files='thunar . & disown'  # open the file manager

            alias -s txt=nvim
            alias g=git; alias h=history
            unalias md
            # alias old=commented-out

            # Git
            alias gs='git status'
            alias gd='git diff'
            # --jobs=<n> needs git 2.8
            is-at-least 2.8 && export JOBS=10
            alias gf='git fetch'
        """)
        got, _ = self.load(shells.load_zsh_aliases, p)
        self.assertEqual((got[("", "gs")].desc, got[("", "gd")].desc), ("Git", "Git"))  # a header covers its block
        self.assertEqual(got[("", "gf")].desc, "git fetch")  # other code ends the comment's reach
        self.assertEqual(got[("", "ll")].action, "ls -lh")
        self.assertEqual(got[("", "ll")].desc, "Long listing")
        self.assertEqual(got[("", "files")].action, "thunar . & disown")
        self.assertEqual(got[("", "files")].desc, "open the file manager")
        self.assertEqual(got[("suffix", "txt")].action, "nvim")
        self.assertEqual(got[("global", "...")].action, "../..")
        self.assertEqual(got[("", "-")].action, "cd -")
        self.assertEqual(got[("", "g")].desc, "git")  # no comment: the expansion is the description
        self.assertNotIn(("", "h"), got)  # only the first command of a line is read
        self.assertNotIn(("", "md"), got)  # unalias wins
        self.assertNotIn(("", "mv"), got)
        self.assertNotIn(("", "old"), got)
        self.assertTrue(got[("", "gst")].default and got[("global", "...")].default)
        self.assertFalse(got[("", "k")].default or got[("", "ll")].default)

    def test_zsh_omz_order(self):
        omz = self.dir / ".oh-my-zsh"
        self.write(".oh-my-zsh/oh-my-zsh.sh", "")
        self.write(".oh-my-zsh/lib/key-bindings.zsh", "bindkey '^[[A' up-line-or-beginning-search\n")
        p = self.write(".zshrc", f"""
            bindkey '^R' before-omz
            export ZSH="{omz}"
            source $ZSH/oh-my-zsh.sh
            bindkey '^[[A' history-search-backward
        """)
        got, _ = self.load(shells.load_zsh, p)
        self.assertEqual(got[("", "Up")].action, "history-search-backward")  # .zshrc after omz wins
        self.assertEqual(got[("", "Up")].source, f"{p}:4")
        self.assertEqual(got[("", "Ctrl + R")].action, "before-omz")

    def test_zsh_alias_defaults(self):
        p = self.write(".zshrc", "alias run-help='man -a'\n")
        app = base.App("test", "Test", "alias", shells.load_zsh_aliases)
        with mock.patch.object(shells, "run", return_value="alias run-help=man\nalias which-command=whence\n"):
            got = {k.key: k for k in shells.load_zsh_aliases(base.Ctx(app, [p]))}
        self.assertEqual((got["run-help"].action, got["run-help"].default), ("man -a", False))
        self.assertEqual((got["which-command"].action, got["which-command"].default), ("whence", True))

    def test_bash_aliases(self):
        al = self.write(".bash_aliases", "alias la='ls -A'\n")
        rc = self.write(".bashrc", f"""
            if [ -f {al} ]; then
                . {al}
            fi
            alias it='echo it'\\''s'
            alias broken='no end
        """)
        app = base.App("test", "Test", "alias", shells.load_bash_aliases)
        kms = shells.load_bash_aliases(base.Ctx(app, [rc, al], defaults=False))
        self.assertEqual([k.key for k in kms].count("la"), 1)
        self.assertEqual({k.key: k.action for k in kms}["it"], "echo it's")
        self.assertNotIn("broken", [k.key for k in kms])

    def test_fish_aliases(self):
        p = self.write("config.fish", """
            alias ll 'ls -lh'
            alias la='ls -A'
            abbr -a gco git checkout
            abbr --add !! --position anywhere --function last_history_item
            abbr -a tmp rm
            abbr -e tmp
        """)
        self.write("functions/vi.fish", "function vi --wraps=nvim --description 'alias vi=nvim'\n  nvim $argv\nend\n")
        got, _ = self.load(shells.load_fish_aliases, p, self.dir / "functions")
        self.assertEqual(got[("", "ll")].action, "ls -lh")
        self.assertEqual(got[("", "la")].action, "ls -A")
        self.assertEqual(got[("abbr", "gco")].action, "git checkout")
        self.assertEqual(got[("abbr", "!!")].action, "runs last_history_item")
        self.assertEqual(got[("", "vi")].action, "nvim")
        self.assertNotIn(("abbr", "tmp"), got)


# --------------------------------------------------------- multiplexers --

class MultiplexersTest(Fixture):
    def test_tmux_static(self):
        p = self.write(".tmux.conf", """
            set -g prefix C-a
            set -g mode-keys vi
            # Split panes
            bind | split-window -h
            bind -n M-Left select-pane -L
            bind -N "Reload config" r source-file ~/.tmux.conf
            bind -T copy-mode-vi v send-keys -X begin-selection
            bind P {
              display-message "multi"
            }
            unbind C-b
        """)
        app = base.App("tmux", "tmux", "multiplexer", multiplexers.load_tmux)
        res = multiplexers._tmux_static(base.Ctx(app, [p]))
        got = {(k.section, k.key): k for k in res["binds"] if not k.removed}
        self.assertEqual(res["prefix"], "C-a")
        self.assertEqual(got[("", "Ctrl + A › |")].desc, "Split panes")
        self.assertIn(("root (no prefix)", "Alt + Left"), got)
        self.assertEqual(got[("", "Ctrl + A › r")].desc, "Reload config")
        self.assertEqual(got[("copy-mode-vi", "v")].desc, "Begin selection")
        self.assertIn("multi", got[("", "Ctrl + A › P")].action)

    def test_tmux_list_and_notes(self):
        listing = 'bind-key    -T prefix       \\"     split-window\nbind-key -r -T prefix M-Up resize-pane -U 5\n'
        notes = multiplexers._tmux_notes('C-b "     Split window vertically\n', "C-b")
        got = {k.key: k for k in multiplexers._tmux_list(listing, notes, "Ctrl + B", {"prefix"}, "x", True)}
        self.assertEqual(got['Ctrl + B › "'].desc, "Split window vertically")
        self.assertIn("Ctrl + B › Alt + Up", got)

    def test_zellij(self):
        kdl = """
            keybinds {
                normal {
                    bind "Ctrl g" { SwitchToMode "Locked"; }
                    bind "Alt n" "Alt N" { NewPane; }
                }
                shared_except "locked" {
                    // comment { not a block }
                    bind "Alt h" { MoveFocus "Left"; }
                }
                unbind "Ctrl q"
            }
        """
        out, clear_all, _ = multiplexers.zellij_parse(textwrap.dedent(kdl), "cfg", False)
        got = {(k.section, k.key): k for k in out}
        self.assertEqual(got[("", "Ctrl + G")].action, "SwitchToMode Locked")
        self.assertEqual(got[("", "Alt + N")].desc, "New pane")
        self.assertIn(("all except locked", "Alt + H"), got)
        self.assertTrue(got[("*", "Ctrl + Q")].removed)
        self.assertFalse(clear_all)


# -------------------------------------------------------------- editors --

class EditorsTest(Fixture):
    def test_nvim_lua(self):
        self.write("nvim/init.lua", """
            vim.g.mapleader = " "
            -- Save
            vim.keymap.set({ "n", "i" }, "<C-s>", "<cmd>w<cr>", { desc = "Save file" })
            local map = vim.keymap.set
            map("n", "<leader>ff", builtin.find_files)
            nmap("<leader>q", ":q<CR>")
            vim.api.nvim_buf_set_keymap(0, "n", "gd", "", { callback = function() go() end })
            local function wrap(mode, l, r) vim.keymap.set(mode, l, r) end
            return {
              "nvim-telescope/telescope.nvim",
              keys = {
                { "<leader>fg", "<cmd>Telescope live_grep<cr>", desc = "Grep", mode = { "n", "v" } },
                "<leader>fb",
              },
            }
        """)
        self.write("nvim/plugin/old.vim", 'nnoremap <silent> <leader>w :w<CR> " write\n')
        got, ctx = self.load(editors.load_vimlike, self.dir / "nvim")
        self.assertEqual(got[("normal, insert", "<C-s>")].desc, "Save file")
        self.assertEqual(got[("", "<leader>ff")].action, "builtin.find_files")
        self.assertIn(("", "<leader>q"), got)
        self.assertIn("go()", got[("", "gd")].action)
        self.assertEqual(got[("normal, visual", "<leader>fg")].desc, "Grep")
        self.assertIn(("", "<leader>fb"), got)
        self.assertEqual(got[("", "<leader>w")].desc, "write")
        self.assertIn("<leader> = Space", ctx.notes)
        self.assertEqual(len(got), 7)  # the wrapper's own vim.keymap.set(mode, l, r) is not a map

    def test_vscode_jsonc(self):
        p = self.write("keybindings.json", """
            // my keys
            [
              { "key": "ctrl+k ctrl+s", "command": "workbench.action.openGlobalKeybindings" },
              { "key": "ctrl+shift+/", "command": "editor.action.blockComment", "when": "editorTextFocus" },
              { "key": "ctrl+p", "command": "-workbench.action.quickOpen" }, /* removed */
            ]
        """)
        got, ctx = self.load(editors.load_vscode, p)
        self.assertIn(("", "Ctrl + K › Ctrl + S"), got)
        self.assertIn("[when editorTextFocus]", got[("", "Ctrl + Shift + /")].action)
        self.assertEqual(len(got), 2)
        self.assertTrue(any("1 default binding" in n for n in ctx.notes))

    def test_helix(self):
        p = self.write("config.toml", """
            [keys.normal]
            C-s = ":w"
            [keys.normal.space]
            f = "file_picker"
            [keys.insert]
            j = { k = "normal_mode" }
        """)
        got, _ = self.load(editors.load_helix, p)
        self.assertIn(("", "Ctrl + S"), got)
        self.assertEqual(got[("", "Space › f")].action, "file_picker")
        self.assertIn(("insert", "j › k"), got)

    def test_zed(self):
        p = self.write("keymap.json", """
            [ { "context": "Editor", "bindings": { "ctrl-shift-d": "editor::DuplicateLine",
                "ctrl-k ctrl-c": ["editor::ToggleComments", {"advance_downwards": false}], } } ]
        """)
        got, _ = self.load(editors.load_zed, p)
        self.assertIn(("Editor", "Ctrl + Shift + D"), got)
        self.assertIn(("Editor", "Ctrl + K › Ctrl + C"), got)


# ------------------------------------------------------------- browsers --

class BrowsersTest(Fixture):
    def test_firefox_custom_keys(self):
        self.write("profiles.ini", "[Profile0]\nName=x\nIsRelative=1\nPath=abc.default\nDefault=1\n")
        self.write("abc.default/customKeys.json", json.dumps({
            "key_close": {"modifiers": "alt", "key": "X"},
            "key_search": {},
            "zen-new-thing": {"modifiers": "accel,shift", "key": "Y"},
        }))
        app = base.App("firefox", "Firefox", "browser", browsers.load_firefox)
        out = browsers.load_firefox(base.Ctx(app, [self.dir], defaults=True))
        by_desc = {k.desc: k.key for k in out}
        self.assertEqual(by_desc["Close tab"], "Alt + X")
        self.assertNotIn("Ctrl + K", [k.key for k in out])  # cleared
        self.assertEqual(by_desc["Zen new thing"], "Ctrl + Shift + Y")

    def zip_file(self, name: str, members: dict, omni: bool = False) -> Path:
        """Write a zip; ``omni`` moves the central directory to the front like
        Mozilla's omni.ja (4-byte header, then directory, then the files)."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for member, text in members.items():
                z.writestr(member, text)
        data = buf.getvalue()
        if omni:
            end = data.rfind(b"PK\x05\x06")
            n, cd_size, cd_off = struct.unpack("<10xHII", data[end:end + 20])
            shift = 4 + cd_size
            cd, pos = bytearray(data[cd_off:cd_off + cd_size]), 0
            for _ in range(n):
                off = struct.unpack("<I", cd[pos + 42:pos + 46])[0]
                cd[pos + 42:pos + 46] = struct.pack("<I", off + shift)
                pos += 46 + sum(struct.unpack("<HHH", cd[pos + 28:pos + 34]))
            eocd = bytearray(data[end:])
            eocd[16:20] = struct.pack("<I", 4)
            data = struct.pack("<I", 0) + bytes(cd) + data[:cd_off] + bytes(eocd)
        p = self.dir / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return p

    def test_jar_reads_zip_and_omni_layout(self):
        for omni in (False, True):
            p = self.zip_file(f"t{omni}.ja", {"a/b.txt": "hello", "c.json": "{}"}, omni=omni)
            with sources.Jar(p) as jar:
                self.assertEqual(jar.read("a/b.txt"), "hello")
                self.assertIsNone(jar.read("missing"))
        with sources.Jar(self.write("bad.ja", "not a zip")) as jar:
            self.assertEqual(jar.members, {})

    def test_fluent_values(self):
        msgs = browsers.ftl_parse(textwrap.dedent("""
            -brand-short-name = Zen
            downloads-shortcut =
                .key = { PLATFORM() ->
                    [macos] J
                   *[other] Y
                }
            addons-shortcut =
                .key = { PLATFORM() ->
                    [linux] F
                   *[other] A
                }
            about = About { -brand-short-name }
            count = { $n } tabs
            """), {})
        self.assertEqual(browsers.ftl_value(msgs, "downloads-shortcut", "key"), "Y")
        self.assertEqual(browsers.ftl_value(msgs, "addons-shortcut", "key"), "F")
        self.assertEqual(browsers.ftl_value(msgs, "about"), "About Zen")
        self.assertEqual(browsers.ftl_value(msgs, "count"), "")  # needs runtime arguments

    def firefox_install(self) -> Path:
        """A fake Zen install (browser/omni.ja + GRE omni.ja) and a profile that last ran it."""
        xhtml = """<window><link rel="localization" href="browser/browserSets.ftl"/>
            <key id="key_newNavigatorTab" data-l10n-id="tab-new-shortcut" modifiers="accel"/>
            <key id="goBackKb" keycode="VK_LEFT" command="Browser:Back" modifiers="alt"/>
            <key id="key_copy" data-l10n-id="copy-shortcut" modifiers="accel" internal="true"/>
            <key id="zen-split-view-grid" key="G" modifiers="accel,alt"/>
            <key id="zen-duplicate-tab" command="Browser:DuplicateTab"/>
            <menuitem id="menu_newNavigatorTab" key="key_newNavigatorTab" data-l10n-id="menu-new-tab"/>
            </window>"""
        ftl = "tab-new-shortcut =\n    .key = T\nmenu-new-tab =\n    .label = New Tab\ncopy-shortcut =\n    .key = C\n"
        app = self.dir / "opt/zen/browser"
        self.zip_file("opt/zen/browser/omni.ja", {
            "chrome/browser/content/browser/browser.xhtml": xhtml,
            "localization/en-US/browser/browserSets.ftl": ftl,
            "localization/en-US/browser/preferences/zen-preferences.ftl":
                "zen-split-view-shortcut-grid = Toggle Split View Grid\nzen-duplicate-tab-shortcut = Duplicate Tab\n",
            "modules/zen/kbs/ZenKeyboardShortcuts.sys.mjs":
                '["zen-split-view-grid", "zen-split-view-shortcut-grid"],\n'
                '["zen-duplicate-tab", "zen-duplicate-tab-shortcut"],',
        }, omni=True)
        self.zip_file("opt/zen/omni.ja", {"x": "y"}, omni=True)
        self.write("profiles.ini", "[Profile0]\nName=x\nIsRelative=1\nPath=abc.default\nDefault=1\n")
        self.write("abc.default/compatibility.ini", f"[Compatibility]\nLastAppDir={app}\n")
        return app

    def test_firefox_defaults_from_install(self):
        app = self.firefox_install()
        self.assertEqual(browsers.firefox_app_dir(self.dir / "abc.default"), app)
        table, names = browsers.firefox_defaults(app, ["es-CO", "en-US"])
        got = {ident: (k.key, k.desc) for ident, k in table}
        self.assertEqual(got["key_newNavigatorTab"], ("Ctrl + T", "New Tab"))  # menu label
        self.assertEqual(got["goBackKb"], ("Alt + Left", "Back"))              # fallback name
        self.assertEqual(got["zen-split-view-grid"], ("Ctrl + Alt + G", "Toggle Split View Grid"))
        self.assertNotIn("key_copy", got)           # internal: cannot be changed
        self.assertNotIn("zen-duplicate-tab", got)  # no key by default
        self.assertEqual(names["zen-duplicate-tab"], "Duplicate Tab")

    def test_firefox_custom_keys_over_install(self):
        self.firefox_install()
        self.write("abc.default/customKeys.json", json.dumps({
            "goBackKb": {},
            "zen-duplicate-tab": {"modifiers": "accel", "key": "T"},
        }))
        app = base.App("zen", "Zen", "browser", browsers.load_firefox)
        out = browsers.load_firefox(base.Ctx(app, [self.dir], defaults=True))
        keys = {k.action: k for k in out}
        self.assertNotIn("goBackKb", keys)  # cleared
        self.assertEqual((keys["zen-duplicate-tab"].key, keys["zen-duplicate-tab"].desc), ("Ctrl + T", "Duplicate Tab"))
        self.assertFalse(keys["zen-duplicate-tab"].default)
        self.assertTrue(keys["zen-split-view-grid"].default)

    def test_firefox_extensions(self):
        xpi = self.zip_file("abc.default/extensions/pass@x.xpi", {
            "manifest.json": json.dumps({"name": "__MSG_name__", "default_locale": "en", "commands": {
                "_execute_action": {"suggested_key": {"default": "Alt+Shift+P"}},
                "open-window": {"suggested_key": {"default": "Ctrl+Period", "linux": "Alt+L"},
                                "description": "__MSG_openWindow__"},
                "lock": {"suggested_key": {"default": "Alt+K"}, "description": "Lock"},
                "unbound": {"description": "Never bound"},
            }}),
            "_locales/en/messages.json": json.dumps({"name": {"message": "Pass: Password Manager"},
                                                     "openWindow": {"message": "Open in a window"}}),
        })
        self.write("abc.default/extensions.json", json.dumps({"addons": [
            {"id": "pass@x", "type": "extension", "active": True, "location": "app-profile", "path": str(xpi)},
            {"id": "off@x", "type": "extension", "active": False, "location": "app-profile", "path": str(xpi)},
        ]}))
        self.write("abc.default/extension-settings.json", json.dumps({"commands": {
            "_execute_action": {"precedenceList": [{"id": "pass@x", "value": {"shortcut": "Alt+P"}, "enabled": True}]},
            "lock": {"precedenceList": [{"id": "pass@x", "value": {"shortcut": ""}, "enabled": True}]},
        }}))
        got = {k.action: (k.key, k.desc, k.section) for k in browsers.firefox_extensions(self.dir / "abc.default")}
        self.assertEqual(got, {
            "_execute_action": ("Alt + P", "Open the extension's toolbar popup", "Pass"),
            "open-window": ("Alt + L", "Open in a window", "Pass"),
        })

    def test_plus_combo(self):
        self.assertEqual(browsers.plus_combo("Control+Shift+KeyT"), "Ctrl + Shift + T")
        self.assertEqual(browsers.plus_combo("Alt+Digit1"), "Alt + 1")
        self.assertEqual(browsers.plus_combo("Meta+BracketLeft"), "Super + [")
        self.assertEqual(browsers.plus_combo("ctrl+shift+arrowleft"), "Ctrl + Shift + Left")
        self.assertEqual(browsers.plus_combo("MacCtrl+Comma"), "Ctrl + ,")
        self.assertEqual(browsers.plus_combo("ctrl++"), "Ctrl + +")

    def chromium_profile(self, prefs: dict, secure: dict | None = None) -> Path:
        self.write("chromium/Local State", json.dumps({"profile": {"last_used": "Profile 1"}}))
        self.write("chromium/Profile 1/Preferences", json.dumps(prefs))
        if secure is not None:
            self.write("chromium/Profile 1/Secure Preferences", json.dumps(secure))
        return self.dir / "chromium"

    def test_chromium_extension_shortcuts(self):
        root = self.chromium_profile({"extensions": {"commands": {
            "linux:Ctrl+Shift+Y": {"extension": "abc", "command_name": "_execute_action", "global": False},
            "linux:MediaPlayPause:abc": {"extension": "abc", "command_name": "toggle", "global": False},
            "mac:Command+Shift+Y": {"extension": "abc", "command_name": "_execute_action", "global": False},
        }}}, secure={"extensions": {"settings": {"abc": {"path": "abc/1.10.0_0"}}}})
        self.write("chromium/Profile 1/Extensions/abc/1.10.0_0/manifest.json", json.dumps(
            {"name": "Player - controls", "commands": {"toggle": {"description": "Play or pause"}}}))
        app = base.App("chromium", "Chromium", "browser", browsers.load_chromium)
        ctx = base.Ctx(app, [root], defaults=False)
        got = {(k.key, k.desc, k.section) for k in browsers.load_chromium(ctx)}
        self.assertEqual(got, {("Ctrl + Shift + Y", "Open the extension's toolbar popup", "Player"),
                               ("MediaPlayPause", "Play or pause", "Player")})
        self.assertIn("Profile: ", " ".join(ctx.notes))

    def test_brave_accelerators(self):
        root = self.chromium_profile({"brave": {
            "default_accelerators": {"34014": ["Control+KeyT"], "34015": ["Control+KeyW", "Control+F4"]},
            "accelerators": {"34014": ["Control+KeyT"], "34015": ["Alt+KeyX"], "56044": ["Alt+KeyS"]},
        }})
        app = base.App("brave", "Brave", "browser", browsers.load_chromium)
        out = browsers.load_chromium(base.Ctx(app, [root], defaults=True))
        got = {(k.key, k.desc, k.default) for k in out}
        self.assertEqual(got, {("Ctrl + T", "New tab", True), ("Alt + X", "Close tab", False),
                               ("Alt + S", "Toggle sidebar", False)})

    def test_vivaldi_actions(self):
        root = self.chromium_profile({"vivaldi": {"actions": [{
            "COMMAND_CLOSE_TAB": {"shortcuts": ["meta+w"], "gestures": ["2"]},
            "COMMAND_NEW_TAB": {"shortcuts": ["ctrl+t"]},
        }]}})
        defaults = {"COMMAND_CLOSE_TAB": {"shortcuts": ["ctrl+w"]}, "COMMAND_NEW_TAB": {"shortcuts": ["ctrl+t"]}}
        app = base.App("vivaldi", "Vivaldi", "browser", browsers.load_chromium)
        with mock.patch.object(browsers, "vivaldi_defaults", return_value=defaults):
            out = browsers.load_chromium(base.Ctx(app, [root], defaults=True))
        got = {(k.key, k.desc, k.default) for k in out}
        self.assertEqual(got, {("Super + W", "Close tab", False), ("Ctrl + T", "New tab", True)})

    def pak_file(self, name: str, resources: list) -> Path:
        """A Chromium .pak (version 5) holding the given byte strings."""
        head = struct.pack("<IB3xHH", 5, 1, len(resources), 0)
        offset = len(head) + 6 * (len(resources) + 1)
        table, body = b"", b""
        for i, data in enumerate([*resources, b""]):
            table += struct.pack("<HI", i + 1, offset + len(body))
            body += data
        p = self.dir / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(head + table + body)
        return p

    def test_opera_keybindings(self):
        defaults = {"Keybindings": {
            "Settings": {"AdvancedEnabled": False},
            "ReadOnly": {"Copy": {"bindings": ["Ctrl+C"]}},
            "Basic": {"NewTab": {"bindings": ["Ctrl+T"]}, "CloseTab": {"bindings": ["Ctrl+W", "Ctrl+F4"]},
                      "DuplicateTab": {"bindings": []}},
            "Advanced": {"Back": {"bindings": ["Z"]}},
            "Messengers": {"OpenDiscord": {"bindings": []}},
        }}
        pak = self.pak_file("opera/opera.pak", [gzip.compress(b"<html>other</html>"),
                                                gzip.compress(json.dumps(defaults, indent=2).encode())])
        self.assertIn('"Keybindings"', sources.pak_find(pak, b'"Keybindings"'))
        root = self.chromium_profile({"Keybindings": {
            "Settings": {"AdvancedEnabled": True},
            "Basic": {"CloseTab": {"bindings": ["Alt+X"]}, "DuplicateTab": {"bindings": ["Ctrl+Shift+K"]}},
            "Messengers": {"OpenDiscord": {"bindings": ["Alt+Shift+D"]}},
        }})
        app = base.App("opera", "Opera", "browser", browsers.load_chromium)
        with mock.patch.object(browsers, "_OPERA_DIRS", (str(pak.parent),)), \
                mock.patch.object(browsers, "which", return_value=None):
            out = browsers.load_chromium(base.Ctx(app, [root], defaults=True))
        got = {(k.section, k.key, k.desc, k.default) for k in out}
        self.assertEqual(got, {("", "Ctrl + T", "New tab", True), ("", "Alt + X", "Close tab", False),
                               ("", "Ctrl + Shift + K", "Duplicate tab", False),
                               ("single-key", "Z", "Back", True),
                               ("messengers", "Alt + Shift + D", "Open discord", False)})

    def test_chromium_without_profile_uses_table(self):
        app = base.App("chrome", "Chrome", "browser", browsers.load_chromium)
        ctx = base.Ctx(app, [], defaults=True)
        out = browsers.load_chromium(ctx)
        self.assertIn(("Ctrl + J", "Downloads"), {(k.key, k.desc) for k in out})
        self.assertTrue(all(k.default for k in out))
        for app_id, row in (("edge", ("F9", "Immersive Reader")), ("opera", ("Alt + P", "Settings"))):
            app = base.App(app_id, app_id, "browser", browsers.load_chromium)
            got = {(k.key, k.desc) for k in browsers.load_chromium(base.Ctx(app, [], defaults=True))}
            self.assertIn(row, got)

    def test_qutebrowser_layers(self):
        configdata = textwrap.dedent("""
            bindings.default:
              no_autoconfig: true
              default:
                normal:
                  d: tab-close
                  "[[": navigate prev
                  ':': cmd-set-text :
                  J: tab-next
                insert:
                  <Ctrl-E>: edit-text
              type:
                name: Dict
              desc: |-
                Default keybindings: a block string
            """)
        defaults = browsers._qute_binds(browsers.yaml_mapping(configdata, ["bindings.default", "default"]),
                                        "configdata.yml", default=True)
        self.write("autoconfig.yml", """
            config_version: 2
            settings:
              bindings.commands:
                global:
                  normal:
                    ',m': spawn mpv {url}
                    J: null
            """)
        p = self.write("config.py", "# close it\nconfig.bind('d', 'tab-close -p')\nconfig.unbind('[[')\n")
        app = base.App("qutebrowser", "qutebrowser", "browser", browsers.load_qutebrowser)
        with mock.patch.object(browsers, "qutebrowser_defaults", return_value=defaults):
            out = browsers.load_qutebrowser(base.Ctx(app, [p], defaults=True))
        got = {(k.section, k.key): (k.action, k.desc, k.default) for k in out}
        self.assertEqual(got, {
            ("", "d"): ("tab-close -p", "close it", False),
            ("", ":"): ("cmd-set-text :", "", True),
            ("insert", "Ctrl + E"): ("edit-text", "", True),
            ("", ",m"): ("spawn mpv {url}", "", False),
        })

    def test_qutebrowser(self):
        p = self.write("config.py", "config.bind('<Ctrl-t>', 'open -t')\nconfig.bind(',m', 'spawn mpv {url}', mode='normal')\n")
        got, _ = self.load(browsers.load_qutebrowser, p)
        self.assertIn(("", "Ctrl + T"), got)
        self.assertIn(("", ",m"), got)


# ----------------------------------------------------------------- misc --

class MiscTest(Fixture):
    def test_mpv(self):
        p = self.write("input.conf", "# comment\nCtrl+s screenshot video  # shot\nSHARP cycle audio\n")
        got, _ = self.load(misc.load_mpv, p)
        self.assertEqual(got[("", "Ctrl + S")].desc, "shot")
        self.assertIn(("", "#"), got)

    def test_mpv_key_sequence(self):
        p = self.write("input.conf", "g-p script-binding select/select-playlist\nAlt+- add video-zoom -0.25\n- add volume -2\n")
        got, _ = self.load(misc.load_mpv, p)
        self.assertIn(("", "g › p"), got)
        self.assertIn(("", "Alt + -"), got)
        self.assertIn(("", "-"), got)

    def test_map_files(self):
        z = self.write("zathurarc", "map [fullscreen] <C-n> navigate next\nmap J zoom out\nunmap K\n")
        got, _ = self.load(misc.load_zathura, z)
        self.assertIn(("fullscreen", "Ctrl + N"), got)
        self.assertIn(("", "J"), got)
        lf = self.write("lfrc", "map <c-f> :fzf_jump\ncmap <tab> cmd-menu-complete\nmap x\n")
        got, _ = self.load(misc.load_lf, lf)
        self.assertIn(("", "Ctrl + F"), got)
        self.assertIn(("command line", "Tab"), got)
        self.assertNotIn(("", "x"), got)

    def test_yazi(self):
        p = self.write("keymap.toml", """
            [mgr]
            prepend_keymap = [ { on = ["g", "d"], run = "cd ~/Downloads", desc = "Go downloads" } ]
            [input]
            keymap = [ { on = "<C-c>", run = "close" } ]
        """)
        got, _ = self.load(misc.load_yazi, p)
        self.assertEqual(got[("", "g › d")].desc, "Go downloads")
        self.assertIn(("input", "Ctrl + C"), got)

    def test_thunar(self):
        p = self.write("accels.scm", '; (gtk_accel_path "<Actions>/ThunarWindow/new-tab" "<Primary>t")\n'
                                     '(gtk_accel_path "<Actions>/ThunarWindow/close-tab" "<Primary><Shift>w")\n')
        got, _ = self.load(misc.load_thunar, p)
        self.assertEqual(list(got), [("", "Ctrl + Shift + W")])  # the commented one is a default

    def test_rofi_bindings_parse(self):
        help_text = "\t-kb-mode-next [string]   Switch to the next mode.\n\t\tShift+Right,Control+Tab (Default)\n"
        with mock.patch.object(misc, "run", return_value=help_text):
            self.assertEqual(misc.rofi_bindings(),
                             [("mode-next", "Switch to the next mode.", ["Shift+Right", "Control+Tab"], True)])


class GenericTest(Fixture):
    def test_auto_lines(self):
        p = self.write("app.conf", """
            # Quit the app
            bind ctrl+q quit
            map <C-n> next
            ctrl+shift+t = new_tab
            copy = Control+c   # copy it
            volume = 50
        """)
        got, _ = self.load(generic.load_generic, p, options={"format": "auto"})
        self.assertEqual(got[("", "Ctrl + Q")].desc, "Quit the app")
        self.assertIn(("", "Ctrl + N"), got)
        self.assertEqual(got[("", "Ctrl + Shift + T")].action, "new_tab")
        self.assertEqual(got[("", "Ctrl + C")].desc, "copy it")
        self.assertEqual(len(got), 4)

    def test_auto_key_comma_action(self):
        p = self.write("kewrc", """
            [progress bar]
            progressBarElapsedEvenChar=■
            [key bindings]
            bind = Space, playPause
            bind = c, cycleVisualization
            bind = +, volUp, +5%
            bind = Shift+Tab, prevView
            bind = SUPER SHIFT, Q, exec, kitty
            bind = , XF86AudioMute, exec, mute
        """)
        got, _ = self.load(generic.load_generic, p, options={"format": "auto"})
        self.assertEqual(got[("", "Space")].action, "playPause")
        self.assertEqual(got[("", "c")].action, "cycleVisualization")
        self.assertEqual(got[("", "+")].action, "volUp, +5%")
        self.assertEqual(got[("", "Shift + Tab")].action, "prevView")
        self.assertEqual(got[("", "Super + Shift + Q")].action, "exec, kitty")
        self.assertEqual(got[("", "XF86AudioMute")].action, "exec, mute")
        self.assertEqual(len(got), 6)  # the [progress bar] setting is not a keymap

    def test_regex(self):
        p = self.write("kewrc", "key:Space=togglePause\nkey:n=next\n")
        got, _ = self.load(generic.load_generic, p,
                           options={"format": "regex", "pattern": r"^key:(?P<key>[^=]+)=(?P<action>.+)$"})
        self.assertEqual(got[("", "Space")].action, "togglePause")

    def test_structured_json(self):
        p = self.write("keys.json", json.dumps({"bindings": [{"shortcut": "ctrl+o", "command": "open", "description": "Open"}]}))
        got, _ = self.load(generic.load_generic, p, options={"format": "auto"})
        self.assertEqual(got[("bindings", "Ctrl + O")].desc, "Open")


# ------------------------------------------------------- config / output --

class ConfigAndOutputTest(unittest.TestCase):
    def test_single_app_opens_its_keymaps(self):
        from hyprmaps import config, ui
        shown = []

        class FakeRofi:  # records each screen and presses Esc
            def show(self, rows, prompt, mesg="", selected=0, icons=None):
                shown.append((prompt, mesg))
                return ui.CANCEL, None

        cfg = {"appearance": dict(config.APPEARANCE, icons=False), "keys": dict(config.KEYS)}
        app = base.App("kew", "kew", "media", generic.load_generic)
        loaded = [(app, [Keymap("q", "quit", "Quit", "", "kewrc:1")], [])]
        s = ui.Session(cfg, FakeRofi())
        self.assertFalse(s.browse(loaded, came_from_picker=False))  # Esc quits
        self.assertEqual(shown[0][0], "kew")  # no app list first
        self.assertIn("<b>Esc</b> quit", shown[0][1])
        self.assertTrue(s.browse(loaded, came_from_picker=True))  # Esc goes back to the groups
        self.assertIn("<b>Esc</b> back", shown[1][1])
        s.browse(loaded * 2, came_from_picker=False)
        self.assertEqual(shown[2][0], "Keymaps")  # several apps: the app list first

    def test_theme_args_reject_injection(self):
        args = theme_args({"width": "50%; } * { background: red", "lines": 10, "force_layout": False})
        self.assertNotIn("50%", " ".join(args))
        self.assertIn("listview { lines: 10; fixed-height: true; dynamic: false; }", args)

    def test_key_args_remove_only_conflicting_key(self):
        from hyprmaps import ui
        bindings = [("mode-next", "", ["Shift+Right", "Control+Tab"], True),
                    ("move-char-back", "", ["Left", "Control+b"], True)]
        with mock.patch.object(ui, "rofi_bindings", return_value=bindings):
            args = ui.key_args({"next_app": "Control+b", "prev_app": "Shift+Left", "toggle_defaults": "Alt+d"})
        self.assertEqual(args[:2], ["-kb-move-char-back", "Left"])
        self.assertNotIn("-kb-mode-next", args)

    def test_formatter_escapes_markup(self):
        from hyprmaps.ui import Formatter
        row = Formatter({"markup": True}).row(Keymap("<C-a>", "a & b", "x < y"), 6)
        self.assertIn("&lt;C-a&gt;", row)
        self.assertIn("x &lt; y", row)

    def test_finalize(self):
        app = base.App("kitty", "Kitty", "terminal", None)
        kms = [Keymap("F1", "x", default=True), Keymap("F2", "y"), Keymap("F2", "y"),
               Keymap("F3", "z", section="m"), Keymap("F4", "", removed=True)]
        out = finalize(app, kms, {"kitty": {"F2": "Mine", "[m] F3": "Moded"}}, True)
        self.assertEqual([(k.key, k.desc) for k in out], [("F2", "Mine"), ("F3", "Moded"), ("F1", "")])


class HardeningTest(Fixture):
    """Hostile or broken input: no hang, no crash, no escape sequence reaching the terminal."""

    def test_pair_is_linear_and_matches_the_old_regex(self):
        import re
        import time
        old = re.compile(r"^\s*(?P<a>[^=#;\[\]][^=]*?)\s*[=:]\s*(?P<b>.+?)\s*$")

        def pair(p, s):
            m = p.match(s)
            return m and (m.group("a").strip(), m.group("b").strip())
        for s in ["ctrl+t = new_tab", " := ", "  #a = b", "#a = b", "a = ", " = x", "a:b=c", "\t[x]: y", "k=v=w"]:
            self.assertEqual(pair(generic._PAIR, s), pair(old, s), s)
        t = time.monotonic()
        self.assertIsNone(generic._pair(" " * 50000 + "x"))  # the old regex took minutes here
        self.assertLess(time.monotonic() - t, 1)

    def test_jsonc_many_comments(self):
        text = "[\n" + "// commented out\n" * 20000 + '{"key": "a", "command": "b"} // tail\n]'
        self.assertEqual(editors.loads_jsonc(text), [{"key": "a", "command": "b"}])

    def test_pak_skips_a_backwards_offset(self):
        data = b"MARK hello"
        table = struct.pack("<HI", 1, 40) + struct.pack("<HI", 2, 30) + struct.pack("<HI", 0, 40)
        p = self.write("x.pak", "")
        p.write_bytes(struct.pack("<IIHH", 5, 1, 2, 0) + table + data)
        self.assertEqual(sources.pak_find(p, b"MARK"), "MARK hello")

    def test_recursive_glob_symlink_loop_ends(self):
        import glob
        d = self.dir / "loop"
        d.mkdir()
        (d / "a").symlink_to("..")
        (d / "b").symlink_to("..")  # two loops: glob would walk 2^40 paths
        (d / "x.conf").write_text("")
        self.assertEqual(sources.expand(str(self.dir / "**/*.conf")), [d / "x.conf"])
        self.assertEqual(sources.expand(str(self.dir / "**/*.lua")), [])  # no match: glob never ended
        (self.dir / "t/s[1]/.h").mkdir(parents=True)
        for f in ("t/k.conf", "t/s[1]/y.conf", "t/s[1]/.h/z.conf", "t/.dot.conf"):
            (self.dir / f).write_text("")
        for pat in ("t/**", "t/**/", "t/**/*.conf", "t/**/.*", "t/*/**/*.conf", "t/**/**", "t/**/s[[]1]/*"):
            p = str(self.dir / pat)
            self.assertEqual(sorted(sources._glob(p)), sorted(glob.glob(p, recursive=True)), pat)

    def test_timed_out_command_is_killed_with_its_children(self):
        import os
        import subprocess
        tag = f"hyprmaps-test-{os.getpid()}"
        with mock.patch.object(sources, "TIMEOUT", 0.5):
            self.assertIsNone(sources.run(["bash", "-c", f"(exec -a {tag} sleep 30) & sleep 30"]))
        left = subprocess.run(["pgrep", "-f", tag], capture_output=True, text=True).stdout.split()
        self.assertEqual(left, [])

    def test_string_paths_and_deep_toml(self):
        from hyprmaps.config import load_config
        p = self.write("c.toml", '[apps.kitty]\npaths = "~/k.conf"\n[[custom]]\nname = "X"\npaths = "~/x"\n')
        cfg = load_config(p)
        self.assertEqual((cfg["apps"]["kitty"]["paths"], cfg["custom"][0]["paths"]), (["~/k.conf"], ["~/x"]))
        deep = self.write("d.toml", "x = " + "[" * 5000 + "]" * 5000 + "\n")
        with mock.patch("sys.stderr", io.StringIO()):
            self.assertEqual(load_config(deep)["apps"], {})  # a warning, not a crash

    def test_mistyped_config_tables_are_ignored(self):
        from hyprmaps.config import load_config
        p = self.write("c.toml", 'keys = "x"\ncategories = 5\n[appearance]\nlines = "abc"\n[apps]\nkitty = 1\n')
        with mock.patch("sys.stderr", io.StringIO()):
            cfg = load_config(p)
        self.assertEqual(cfg["appearance"]["lines"], 15)
        self.assertEqual(cfg["apps"], {})
        self.assertNotIn("categories", cfg)
        self.assertEqual(cfg["keys"]["toggle_defaults"], "Alt+d")
        theme_args(cfg["appearance"])

    def test_dump_strips_control_characters(self):
        from hyprmaps.cli import _print_dump
        app = base.App("x", "X", "other", lambda ctx: [])
        k = Keymap("Ctrl + A", "act\x1b]0;pwned\x07", "line1\nline2\tx\x9b2J")
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            _print_dump([(app, [k], [])], False)
        row = out.getvalue().splitlines()[1]
        self.assertEqual(len(out.getvalue().splitlines()), 2)
        self.assertFalse(any(ord(c) < 32 and c != "\t" or 127 <= ord(c) < 160 for c in row))

    def test_tmux_empty_key_does_not_hide_the_other_binds(self):
        p = self.write("tmux.conf", 'bind ""\nbind x kill-pane\n')
        app = next(a for a in multiplexers.APPS if a.id == "tmux")
        with mock.patch.object(multiplexers, "run", return_value=None):
            out = multiplexers.load_tmux(base.Ctx(app, [p], defaults=False))
        self.assertIn("kill-pane", [k.action for k in out])

    def test_long_blank_lines_in_shell_files(self):
        import time
        t = time.monotonic()
        lines = ["key " + " " * 20000 + "x", "\t" * 20000 + "="]
        self.assertEqual(shells._function_comments([(Path("rc"), lines)]), {})
        self.assertLess(time.monotonic() - t, 1)  # the old regex was cubic here
        self.assertEqual(shells._function_comments([(Path("rc"), ["# Doc", "f () {"])]), {"f": "Doc"})

    def test_markup_attribute_quote_is_escaped(self):
        from hyprmaps.ui import _m
        self.assertNotIn("'", _m("#f00' weight='bold"))


if __name__ == "__main__":
    unittest.main()
