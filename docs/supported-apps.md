# Supported apps

For each app this page lists:
- **Reads**: the files (or commands) that hold your own binds;
- **Defaults**: where the app's built-in shortcuts come from;
- **Description from**: what fills the description column.

`$CONFIG` means `$XDG_CONFIG_HOME`, usually `~/.config`. The id in brackets is what `--only`, `[apps.<id>]` and `descriptions.toml` use. To read an app from somewhere else, see [adding-apps.md](adding-apps.md).

> [!NOTE]
> An app appears when its program is in `PATH` **or** its config path exists. `hyprmaps --list` shows what was found.

## Window manager

| App | Reads | Defaults | Description from |
|-----|-------|----------|------------------|
| Hyprland `hyprland` | `hyprctl binds -j` (the binds that are really active), enriched with `$CONFIG/hypr/hyprland.lua` + every `.lua` beside it, or `hyprland.conf` + its `source =` files | none (Hyprland has no default binds) | `bindd` / `{ description = ... }`, a trailing `# comment`, the comment block above the bind |

The config files supply what `hyprctl` lacks: the full `exec` command, `$variables` and comments. Binds created in a Lua loop are matched by modifier and dispatcher. Without a running Hyprland, the config files alone are shown.

## Terminals

| App | Reads | Defaults | Description from |
|-----|-------|----------|------------------|
| kitty `kitty` | `$CONFIG/kitty/kitty.conf` + `include` / `globinclude` | `/usr/share/doc/kitty/kitty.conf` (kitty's commented reference config) | kitty's own wording for that action, then the comment above |
| Ghostty `ghostty` | `$CONFIG/ghostty/config` (or `config.ghostty`) + `config-file =` | `ghostty +list-keybinds --default` | the comment above; else the action |
| Alacritty `alacritty` | `$CONFIG/alacritty/alacritty.toml` + `import` | none: listed in `man 5 alacritty-bindings` | the action |
| foot `foot` | `$CONFIG/foot/foot.ini` + `include =` (key, search, url, text and mouse bindings) | `/etc/xdg/foot/foot.ini` | the comment above |
| WezTerm `wezterm` | `{ key = ..., mods = ..., action = ... }` tables in `wezterm.lua` | `wezterm -n show-keys` | the action |

> [!TIP]
> WezTerm with `live = true` runs `wezterm show-keys` with your config. That is exact even for keys built by Lua code, but it executes `wezterm.lua`.

## Shells

| App | Reads | Defaults | Description from |
|-----|-------|----------|------------------|
| zsh `zsh` | `~/.zshrc` (or `$ZDOTDIR`) + `source` / `.` files; with oh-my-zsh also `$ZSH/lib/key-bindings.zsh` and the enabled plugins | `zsh -f -c 'bindkey -M emacs'` (or `viins` + `vicmd` in vi mode) | the comment above the **widget function**, then above the `bindkey` |
| bash `bash` | `~/.inputrc` (+ `$include`), `bind` lines in `~/.bashrc` / `~/.bash_profile` | `bash --norc --noprofile -i -c 'bind -p'` | same as zsh |
| fish `fish` | `config.fish`, `functions/fish_user_key_bindings.fish`, `conf.d/*.fish` | `fish --no-config -c bind` | same as zsh |

Escape sequences are translated: `^[[1;5C` → `Ctrl + Right`, `\C-x\C-e` → `Ctrl + X › Ctrl + E`, `${terminfo[kcuu1]}` → `Up`. Only the keymap in use is shown (emacs, or vi if `bindkey -v`, `set -o vi` or the vi-mode plugin is found).

## Shell aliases

Aliases show as their own apps in the **Shell aliases** group: the name in the key column, the expansion as the action. Everything else works as for keymaps: search, the defaults toggle, <kbd>Enter</kbd> for the source line, `descriptions.toml` (e.g. `[zsh-aliases]` `ll = "Long listing"`), `--dump` and `--only`.

| App | Reads | Defaults | Description from |
|-----|-------|----------|------------------|
| zsh aliases `zsh-aliases` | `alias` / `unalias` lines in `~/.zshrc` (or `$ZDOTDIR`) + `source` / `.` files; with oh-my-zsh also `$ZSH/lib/*.zsh`, the enabled plugins (both tagged default) and `$ZSH_CUSTOM/*.zsh` (yours) | `zsh -f -c 'alias -L'` | a trailing `# comment`, the comment above, else the expansion |
| bash aliases `bash-aliases` | `alias` / `unalias` lines in `~/.bashrc`, `~/.bash_profile`, `~/.bash_aliases` + `source` / `.` files | `bash --norc --noprofile -i -c alias` | same as zsh |
| fish aliases `fish-aliases` | `alias` and `abbr` in `config.fish` and `conf.d/*.fish`, aliases saved with `funcsave` in `functions/*.fish` | `fish --no-config -c 'alias; abbr --show'` | same as zsh |

zsh global (`alias -g`) and suffix (`alias -s`) aliases show the mode `[global]` / `[suffix]`, fish abbreviations `[abbr]`. The files are read, not run, so an alias inside an `if` is listed whether or not the condition holds, and aliases made by a loop or by a plugin manager other than oh-my-zsh are not seen. An alias defined after `&&` or `||` (e.g. `is-at-least 2.8 && alias gfa=… || alias gfa=…`) is not shown at all: picking one branch could show the wrong command, and oh-my-zsh's git plugin defines four aliases this way (`gfa`, `gpf`, `gpsupf`, `gsta`). Only the first command of a line is read: in `alias g=git; alias h=history`, `h` is not shown; put each alias on its own line. If you set `paths` for `[apps.zsh]`, set them for `[apps.zsh-aliases]` too.

## Terminal multiplexers

| App | Reads | Defaults | Description from |
|-----|-------|----------|------------------|
| tmux `tmux` | the **running** server if there is one (`tmux -N list-keys`), else `~/.tmux.conf` / `$CONFIG/tmux/tmux.conf` + `source-file` | a private `tmux -f /dev/null` server on a temp socket | `list-keys -N` notes, `bind -N "note"`, the comment above, then the `send-keys -X` command name |
| Zellij `zellij` | `$CONFIG/zellij/config.kdl` (`keybinds` block, `unbind`, `clear-defaults`) | `zellij setup --dump-config` | the action name |

tmux keys are shown with the real prefix (`Ctrl + B › %`). The prefix table comes first, then root, the copy-mode table of your `mode-keys`, and mouse.

## Code editors

| App | Reads | Defaults | Description from |
|-----|-------|----------|------------------|
| Neovim `nvim` | every `.lua` / `.vim` file under `$CONFIG/nvim`: `vim.keymap.set`, `nvim_(buf_)set_keymap`, `map()`-style wrappers, lazy.nvim `keys = {}`, `nnoremap` & co. | with `live = true`: Neovim's own default mappings | `desc =`, then the comment above |
| Vim `vim` | `~/.vimrc`, `~/.vim/vimrc`, `~/.vim/plugin`, `~/.vim/after` | none | trailing `" comment`, then the comment above |
| VS Code `vscode`, Code - OSS `vscode-oss`, VSCodium `vscodium`, Cursor `cursor`, Windsurf `windsurf` | `$CONFIG/<Code\|Code - OSS\|VSCodium\|Cursor\|Windsurf>/User/keybindings.json` (comments and trailing commas are fine) | none: they live inside the app | the command name |
| Helix `helix` | `[keys.*]` in `$CONFIG/helix/config.toml`, nested tables become sequences (`Space › f`) | none: `:help keymap` | the command name |
| Zed `zed` | `$CONFIG/zed/keymap.json` (contexts become modes) | none | the action name |

Vim-family keys keep their native notation (`<leader>ff`, `<C-s>`) and the notes show what `<leader>` is.

> [!TIP]
> Neovim with `live = true` asks Neovim itself (`nvim --headless -i NONE -n`). It then also lists plugin and built-in mappings, and fills Lua callbacks with the source from your files. It loads your config, so plugins start; state and cache go to a temp dir and Neovim saves nothing, but a plugin that writes (e.g. a plugin manager installing into `~/.local/share/nvim`) still can.

## Browsers

Browsers keep their shortcuts inside the app, so hyprmaps reads them from the browser's own data: the installed program for the defaults, your profile for your changes and your extensions. Only the profile in use is read (the default Firefox profile, Chromium's last used one).

| App | Reads (your changes) | Defaults | Description from |
|-----|----------------------|----------|------------------|
| Firefox `firefox`, Zen `zen`, LibreWolf `librewolf`, Floorp `floorp`, Waterfox `waterfox` | `<profile>/customKeys.json`: what you changed in the browser's keyboard shortcut settings | the installed browser's `omni.ja` (found through `<profile>/compatibility.ini`), so fork shortcuts such as Zen's workspaces and split view are included; else a built-in Firefox table | the browser's own name for the shortcut (menu label or shortcut settings page) |
| Brave `brave` | `brave.accelerators` in `<profile>/Preferences` (Settings → System → Shortcuts) | `brave.default_accelerators` in the same file | the command name |
| Vivaldi `vivaldi` | `vivaldi.actions` in `<profile>/Preferences` (Settings → Keyboard) | `resources/vivaldi/prefs_definitions.json` of the install | the command name |
| Opera `opera` | `Keybindings` in `<profile>/Preferences` (Settings → Shortcuts → Configure shortcuts); single-key shortcuts only when enabled there | the keybinding table inside the install's `opera.pak`; else Opera's documented list | the command name |
| Edge `edge` | nothing: Edge cannot change its own shortcuts on Linux | built-in table from Microsoft's list of Edge shortcuts | built-in table |
| Chromium `chromium`, Chrome `chrome` | nothing: these browsers have no setting to change their own shortcuts | built-in table of Chromium's Linux shortcuts | built-in table |
| qutebrowser `qutebrowser` | `$CONFIG/qutebrowser/autoconfig.yml` (`:bind`), then `config.bind` / `config.unbind` in `config.py` | `configdata.yml` of the installed package | the comment above, in `config.py` |

**Extension shortcuts** are listed for every browser above except qutebrowser, with the extension's name as the mode (`[Proton Pass]`):
- Firefox family: the `commands` of each enabled extension's manifest, overridden by the changes in *Manage Extension Shortcuts* (`<profile>/extension-settings.json`);
- Chromium family: `extensions.commands` in `<profile>/Preferences`, named from the extension's manifest.

A shortcut you **cleared** disappears from the list, and a changed one moves to its new key.

> [!NOTE]
> Tested on Firefox 159 and Zen 1.24. Opera's defaults were checked against the Opera 139 package. The Brave, Vivaldi, Opera-changes, Chromium-extension and qutebrowser formats were taken from their source code or program files and checked with test fixtures, not on a real profile.

## File managers

| App | Reads | Defaults | Description from |
|-----|-------|----------|------------------|
| yazi `yazi` | `$CONFIG/yazi/keymap.toml` (`keymap`, `prepend_keymap`, `append_keymap` of every layer) | none: built into yazi | `desc` |
| ranger `ranger` | `$CONFIG/ranger/rc.conf` (`map`, `cmap`, `pmap`, `tmap`, `copymap`, `unmap`) | ranger's own `rc.conf`, if installed with docs | the comment above |
| lf `lf` | `$CONFIG/lf/lfrc` (`map`, `nmap`, `vmap`, `cmap`) | none | the comment above |
| Thunar `thunar` | `$CONFIG/Thunar/accels.scm`: uncommented lines are yours | the commented lines of the same file | the action name |

## Media & documents

| App | Reads | Defaults | Description from |
|-----|-------|----------|------------------|
| mpv `mpv` | `$CONFIG/mpv/input.conf` | `/usr/share/doc/mpv/input.conf` (skipped with `no-input-default-bindings`) | the `# comment` at the end of the line |
| imv `imv` | `[binds]` in `$CONFIG/imv/config` | `/etc/imv_config` (skipped with `suppress_default_binds`) | the action |
| zathura `zathura` | `map [mode] key command` in `$CONFIG/zathura/zathurarc` | none | the comment above |

## Launchers

| App | Reads | Defaults | Description from |
|-----|-------|----------|------------------|
| rofi `rofi` | `rofi -help` (every `kb-*` option, its value and whether it comes from your config) | same | rofi's own help text |
| fuzzel `fuzzel` | `[key-bindings]` in `$CONFIG/fuzzel/fuzzel.ini` | `/etc/xdg/fuzzel/fuzzel.ini` | the action name |

## Not listed?

Any text, JSON or TOML file can be read: see [adding-apps.md](adding-apps.md). To fill in the descriptions for you, use the [agent prompt](agent-prompt.md).
