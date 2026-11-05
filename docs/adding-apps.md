# Other paths and other apps

There are three cases. Pick the one that matches:

| Your situation | Do this |
|----------------|---------|
| A supported app whose config lives elsewhere (dotfiles repo, Flatpak, ...) | [Change its path](#1-a-supported-app-in-another-place) |
| An app hyprmaps does not know, to keep in the list | [Add a `[[custom]]` app](#2-an-app-hyprmaps-does-not-know) |
| A file to look at once | [`--add` or **＋ Read another file…**](#3-a-file-just-for-now) |

## 1. A supported app in another place

```toml
# ~/.config/hyprmaps/config.toml
[apps.kitty]
paths = ["~/dotfiles/kitty/kitty.conf"]

[apps.nvim]
paths = ["~/dotfiles/nvim"]                 # a folder: every .lua / .vim inside

[apps.zen]
paths = ["~/.var/app/app.zen_browser.zen/.zen"]   # browsers: the folder with profiles.ini
```

`paths` replaces the default locations; `~`, `$VARS` and globs work. The app id comes from `hyprmaps --list`, which also confirms the new path was found.

## 2. An app hyprmaps does not know

Add one `[[custom]]` block per app:

```toml
[[custom]]
name = "My app"                      # shown in rofi (required)
paths = ["~/.config/myapp/keys.conf"] # required
category = "other"                   # wm, terminal, shell, alias, multiplexer, editor, browser, files, media, launcher, other
format = "auto"                      # how to read it (below)
icon = "input-keyboard"              # optional, any icon name
```

The id used in `descriptions.toml` and `--only` is the lowercase name with dashes (`My App` → `my-app`); set `id = "..."` to choose it.

### `format = "auto"` (default)

The automatic reader recognises the common shapes:

| Shape | Example |
|-------|---------|
| directive + key + action | `bind ctrl+q quit` · `map <C-n> next` · `keybind = ctrl+t=new_tab` · `bind = SUPER, Q, exec, kitty` |
| key = action | `ctrl+shift+t = new_tab` |
| action = key | `copy = Control+c` |
| JSON / TOML objects | `{ "shortcut": "ctrl+o", "command": "open", "description": "Open" }`, and maps like `{ "ctrl-s": "save" }` |

For descriptions, a trailing ` # comment` or the comment block above the line is used. Set `comment = "//"` (or `";"`, `"--"`) when the file uses another comment prefix.

### `format = "regex"`

When auto misses lines, describe one line with a regular expression (Python syntax). Named groups:

| Group | Required | Becomes |
|-------|----------|---------|
| `key` | yes | the key (any notation; it is prettified) |
| `action` | no | the action |
| `desc` | no | the description |
| `section` | no | the mode |

```toml
[[custom]]
name = "kew"
category = "media"
paths = ["~/.config/kew/kewrc"]
format = "regex"
pattern = '^bind\s*=\s*(?P<key>[^,]+),\s*(?P<action>[^#]+?)\s*(?:#\s*(?P<desc>.*))?$'
```

> [!TIP]
> Use single quotes (`'...'`) for patterns in TOML, so backslashes stay as they are.

### `format = "<app id>"`

The file has the same syntax as a supported app (a fork, a second instance, a shared include). Read it with that app's reader:

```toml
[[custom]]
name = "Work kitty"
category = "terminal"
paths = ["~/work/kitty-work.conf"]
format = "kitty"
```

## 3. A file just for now

- From the terminal (repeatable; the name is optional):

  ```bash
  hyprmaps --add kew=~/.config/kew/kewrc --add ~/.config/foo/keys.conf
  ```

- From rofi: on the first screen choose **＋ Read another file…** and type the path.

Both use the automatic reader and last only for that session. Move the file into a `[[custom]]` block to keep it.

## Checking the result

```bash
hyprmaps --only my-app --dump     # TSV: app, mode, key, description, action, source, default
hyprmaps --list                    # notes such as "No keymaps recognised" appear under the app
```

When a reader finds nothing, the note suggests `format = "regex"`. The [agent prompt](agent-prompt.md) can also research the right path and pattern for you.
