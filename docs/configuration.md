# Configuration

hyprmaps reads two optional files and never writes them (the only file it can write is the opt-in [cache](#cache)):

| File | Purpose | Start from |
|------|---------|------------|
| `~/.config/hyprmaps/config.toml` | behaviour, look, app paths, extra apps | [`config.example.toml`](../config.example.toml) |
| `~/.config/hyprmaps/descriptions.toml` | your own text for any key | [`descriptions.example.toml`](../descriptions.example.toml) |

```bash
mkdir -p ~/.config/hyprmaps
cp config.example.toml       ~/.config/hyprmaps/config.toml
cp descriptions.example.toml ~/.config/hyprmaps/descriptions.toml
```

Use other locations with `--config PATH` and `--descriptions PATH`. A file with a TOML error is reported on stderr and ignored, so the tool still opens.

## Start-up

```toml
ask = true          # ask which groups to include
categories = []     # groups ticked at start (or used directly when ask = false)
defaults = true     # show each app's built-in defaults
```

`defaults = false` starts rofi with the defaults hidden (<kbd>Alt</kbd>+<kbd>d</kbd> still shows them) and leaves them out of `--dump`.

- **Always open the same groups without asking:**

  ```toml
  ask = false
  categories = ["wm", "terminal", "shell", "editor"]
  ```

- **Group ids:** `wm`, `terminal`, `shell`, `alias` (shell aliases), `multiplexer`, `editor`, `browser`, `files`, `media`, `launcher`, `other`.
- `--all` and `--only` on the command line skip the question too, whatever the config says.

## Cache

```toml
cache = true        # default false
```

Keeps the loaded keymaps between runs: hyprmaps then opens in about half the time (most of a run is spent reading configs and compiling its own code). An edit shows up on the very next run: each run checks, per app, everything the last read depended on, and reads the app again only if something changed:

- every file it read or looked for, even one that did not exist (a new include file counts), by inode, size and change times;
- the names in every folder it listed (a new file matching a glob counts);
- the environment variables it used, and which program `PATH` finds (installing or upgrading an app counts). Starting from a terminal or from a Hyprland bind both use the cache;
- the answers of live sources, asked again: `hyprctl binds`, a running tmux, `live = true` apps.

That check takes a few milliseconds. The built-in defaults an app prints (`zsh -f`, `ghostty --default`, ...) are read again when its program changes. `descriptions.toml` is applied on every run, so it needs no cache. A file saved in the last two seconds is read but not cached yet.

The cache is the only thing hyprmaps writes: `~/.cache/hyprmaps/keymaps.json` (readable only by you) and the compiled code in `~/.cache/hyprmaps/pycache/` (the second run after turning it on writes it). To clear it, or after turning `cache` off, delete `~/.cache/hyprmaps`.

## Make a choice permanent

What you tick or untick in the start question lasts for that run only. hyprmaps is read-only and never writes a file (except the opt-in cache, which holds no choices), so it does not remember it. The only way to make a lasting change is to write it in `~/.config/hyprmaps/config.toml`:

- **Never read an app**: it is not read, not shown in rofi, not in `--list`, `--dump` or `--only`.

  ```toml
  [apps.tmux]
  enabled = false
  ```

  The id is the one `hyprmaps --list` prints (`tmux`, `zsh-aliases`, ...). To get the app back, delete these lines.
- **Untick a whole group at start**: list the groups you want ticked; the others start unticked and you can still tick them in rofi.

  ```toml
  categories = ["wm", "terminal", "shell", "editor"]
  ```

## Keys inside rofi

```toml
[keys]
next_app = "Shift+Right,Control+Tab"
prev_app = "Shift+Left,Control+ISO_Left_Tab"
toggle_defaults = "Alt+d"
```

Keys use rofi's syntax, with a comma between alternatives. If one of your rofi bindings already uses one of these keys, it gives up **only that key, only in this window**, because rofi refuses to start with a key bound twice. Your rofi config is not changed.

<kbd>Enter</kbd> (details) and <kbd>Esc</kbd> (back) are rofi's own accept and cancel keys.

## Per-app settings

Use the app id shown by `hyprmaps --list`:

```toml
[apps.kitty]
paths = ["~/dotfiles/kitty/kitty.conf"]   # read this instead of the default location

[apps.mpv]
defaults = false                          # only my input.conf, not mpv's defaults

[apps.thunar]
enabled = false                           # never show Thunar

[apps.nvim]
live = true                               # ask Neovim itself (see below)
```

| Setting | Meaning |
|---------|---------|
| `enabled = false` | Never read the app, and hide it everywhere (rofi, `--list`, `--dump`, `--only`). |
| `paths = [...]` | Files or folders to read instead of the defaults. `~`, `$VARS` and globs (`*`, `**`) work. An app with `paths` appears only when those paths exist. |
| `defaults = false` | Never load this app's built-in defaults (not even with <kbd>Alt</kbd>+<kbd>d</kbd>). |
| `live = true` | Neovim and WezTerm only: get the keymaps from the app itself. More complete (plugin and Lua-generated keys), but it **runs your config inside the app**. Neovim runs with no shada, no swap and a temporary state/cache dir, so Neovim itself saves nothing; but plugins do start, and one that writes (a plugin manager installing a missing plugin into `~/.local/share/nvim`, say) still can. The command is killed, with anything it started, after 4 seconds. |

Apps the tool does not know are added with `[[custom]]`, explained in [adding-apps.md](adding-apps.md).

## Descriptions

When a key has no description, or you want different wording, write your own:

```toml
# ~/.config/hyprmaps/descriptions.toml
[hyprland]
"Super + A" = "Open the terminal (kitty)"

[nvim]
"<leader>e" = "Show the diagnostic under the cursor"
"[insert] <C-s>" = "Save and go back to normal mode"
```

- One table per app id.
- The key must be written **exactly** as hyprmaps shows it. To copy it, run `hyprmaps --dump --only nvim`, which prints the keys in column 3.
- `"[mode] key"` limits the text to one mode (the mode is column 2 of `--dump`). A plain `"key"` applies to that key in every mode.
- A description here wins over anything found in the config files.

> [!TIP]
> Filling this file by hand is tedious. The [agent prompt](agent-prompt.md) makes a coding agent write it for every undocumented key.

## Appearance

The `[appearance]` table has its own guide: [appearance.md](appearance.md).
