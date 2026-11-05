# Personalise the look

By default hyprmaps opens with **your normal rofi theme**, adjusted only where a cheat sheet needs it: 60% wide, 15 rows, centred, with the message box shown.

You can change the look in three ways:
1. tweak single values in `[appearance]`;
2. use a whole `.rasi` theme only for this window;
3. go plain text.

All of it lives in `~/.config/hyprmaps/config.toml` (see [configuration.md](configuration.md) to create it). Empty values (`""`) leave the theme alone.

## How it is applied

```text
your rofi theme  (or `theme = "file.rasi"`)
      └─ + the [appearance] values, passed as rofi -theme-str for this window only
```

Your rofi config files are never modified. Each value is checked before it reaches rofi: anything with characters a theme cannot contain (`;`, `{`, quotes, ...) is ignored with a warning on stderr.

## Window

| Setting | Default | Example | Notes |
|---------|---------|---------|-------|
| `theme` | `""` | `"~/.config/rofi/keymaps.rasi"` | A full theme just for this window. |
| `width` | `"60%"` | `"1100px"`, `"80em"` | `""` keeps the theme's width. |
| `lines` | `15` | `25` | Visible rows; `0` keeps the theme's. |
| `location` | `"center"` | `"north"`, `"south-east"` | `""` keeps the theme's position and offsets. |
| `font` | `""` | `"JetBrainsMono Nerd Font 11"` | Font of the whole window. |
| `background` | `""` | `"#1e1e2eee"`, `"rgba(30,30,46,0.95)"` | Window background. A more opaque one helps reading over a busy screen. |
| `foreground` | `""` | `"#cdd6f4"` | Text colour. |
| `accent` | `""` | `"#89b4fa"` | Border, prompt and the current app in the app bar. |
| `selected_background` | `""` | `"#313244"` | Highlighted row. |
| `selected_foreground` | `""` | `"#f5c2e7"` | |
| `border_width` | `""` | `"2px"` | |
| `border_radius` | `""` | `"12px"` | |
| `padding` | `""` | `"12px"` | Inside the window. |
| `force_layout` | `true` | `false` | Forces `inputbar, message, listview`, so the app bar and notes always show. Turn it off if your theme has a custom layout that it breaks. |
| `icons` | `true` | `false` | App icons in the app list, taken from your `.desktop` files. |

## Rows

```text
 Ctrl + Shift + Up   Scroll line up   scroll_line_up smooth   [mode]   default
 └ key column ┘      └ description ┘  └──── action ────────┘  └ mode ┘ └ tag ┘
```

| Setting | Default | What it changes |
|---------|---------|-----------------|
| `markup` | `true` | Bold keys, dim actions, italic auto-descriptions. `false` = plain text. Searching then matches only visible text. |
| `key_font` | `"monospace"` | Font family of the key column. Keep it monospace so descriptions line up. |
| `key_color` | `""` | Colour of the keys, e.g. `"#f9e2af"`. |
| `max_key_width` | `26` | Longest key column in characters; longer keys push their row to the right. |
| `show_action` | `true` | The raw action after the description. |
| `show_section` | `true` | The mode / table, e.g. `[insert]`, `[copy-mode-vi]`. |
| `default_tag` | `"default"` | Tag on the apps' built-in defaults; `""` hides it. |
| `user_first` | `true` | Your binds before the defaults. `false` keeps the file order. |

## Recipes

### Keep my theme, just bigger

```toml
[appearance]
width = "75%"
lines = 25
```

### A Catppuccin Mocha look

```toml
[appearance]
font = "JetBrainsMono Nerd Font 11"
background = "#1e1e2ef2"
foreground = "#cdd6f4"
accent = "#89b4fa"
selected_background = "#313244"
selected_foreground = "#f5c2e7"
key_color = "#f9e2af"
border_width = "2px"
border_radius = "12px"
padding = "12px"
```

### Use a dedicated .rasi theme

Write a theme just for the cheat sheet, e.g. `~/.config/rofi/keymaps.rasi`:

```css
* {
    bg: #11111bf0;  fg: #cdd6f4;  accent: #cba6f7;
    background-color: transparent;
    text-color: @fg;
    font: "Iosevka 12";
}
window   { width: 70%; background-color: @bg; border: 2px; border-color: @accent; border-radius: 14px; padding: 14px; }
mainbox  { children: [ inputbar, message, listview ]; spacing: 8px; }
inputbar { children: [ prompt, entry ]; }
prompt   { text-color: @accent; padding: 0 10px 0 0; }
message  { padding: 4px 0; }
listview { lines: 18; scrollbar: false; }
element  { padding: 4px 8px; border-radius: 6px; }
element selected { background-color: #313244; }
```

```toml
[appearance]
theme = "~/.config/rofi/keymaps.rasi"
width = ""        # let the .rasi decide
lines = 0
location = ""
```

> [!TIP]
> Values left in `[appearance]` still apply on top of `theme`. Empty them, as above, when the `.rasi` should have the last word.

### Plain and fast

```toml
[appearance]
markup = false
icons = false
show_action = false
default_tag = ""
```

## Trying a change

- Run `hyprmaps --only hyprland`: it opens straight on one app, with no start question.
- To check a `.rasi` file before using it, run `rofi -theme ~/.config/rofi/keymaps.rasi -dump-theme`. A broken file prints an error instead of the theme.
