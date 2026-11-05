# Document your keymaps with an AI agent

hyprmaps finds every key on its own, but many have no description: a bare `bind = SUPER, Q, exec, ~/scripts/shot.sh` says little, and a shell alias shows only what it expands to. A coding agent can read those configs and scripts, look up the official docs, and write a short description for each key.

The prompt below works with **Claude Code**, **Codex**, **OpenCode** or any agent that can run shell commands and search the web.

**What the agent is allowed to change:** only two files, `~/.config/hyprmaps/config.toml` (new paths and apps) and `~/.config/hyprmaps/descriptions.toml` (the descriptions). It also installs the tool if it is missing. Your app configs are never edited.

## How to use it

1. Open the agent in any folder and paste the whole prompt. If hyprmaps is not installed yet, the agent clones it first.
2. When it finishes, review the two files it wrote, then press your hyprmaps key.

> [!TIP]
> On a large setup (1,000+ keymaps) the agent works app by app. You can limit the run by adding "Only document: hyprland, kitty, nvim" at the end of the prompt.

## The prompt

````text
You are going to document every keyboard shortcut and shell alias of my Hyprland desktop
using the read-only tool "hyprmaps". Work autonomously, step by step, and verify each step.

RULES (they override everything below)
1. Never edit, move, rename or delete any existing config file, script or dotfile.
   The ONLY files you may create or edit are:
     - ~/.config/hyprmaps/config.toml
     - ~/.config/hyprmaps/descriptions.toml
     - ~/.local/share/hyprmaps (only to clone the tool) and the symlink ~/.local/bin/hyprmaps
2. If config.toml or descriptions.toml already exist, read them first and keep every
   existing entry exactly as it is. Only add new entries.
3. Do not install system packages. hyprmaps needs Python >= 3.11 and rofi; if one is
   missing, stop and tell me.
4. Never invent. Every description must be backed by the config line, the script or
   command it runs, a local man page, or official documentation you found on the web.
   If you cannot confirm what a key does, leave it out and list it in the final report.
5. Use only read-only commands to inspect my system (cat, ls, grep, man, hyprctl binds,
   pacman -Qq / dpkg -l / flatpak list, hyprmaps --list/--dump).

STEP 1 - Install and check the tool
- Check: `python3 --version` (>= 3.11) and `command -v rofi`.
- If `command -v hyprmaps` finds nothing:
    git clone https://github.com/Andres-pixel35/hyprmaps ~/.local/share/hyprmaps
    mkdir -p ~/.local/bin
    ln -s ~/.local/share/hyprmaps/bin/hyprmaps ~/.local/bin/hyprmaps
- Read ~/.local/share/hyprmaps/README.md and docs/*.md: they explain the config
  format, [[custom]] apps, "format = auto | regex | <app id>" and descriptions.toml.
- Run `hyprmaps --list`. "✔" = found and read. "·" = not found. Notes under an app
  explain problems.

STEP 2 - Make sure every app with shortcuts is read
- Find the apps I really use: the exec commands in my Hyprland binds
  (`hyprmaps --only hyprland --dump`), the folders in ~/.config, and the installed
  packages.
- For each app that has configurable keyboard shortcuts and is missing or marked "·":
  a. Search the web (official docs or wiki first) for "<app> keybindings config file
     location" and the syntax of its keymap lines. Check the path exists on my machine.
  b. If hyprmaps supports the app but the path differs, add to config.toml:
       [apps.<id>]
       paths = ["<real path>"]
  c. If the app is not supported, add a [[custom]] block (name, category, paths).
     Try format = "auto" first. Check it with
       hyprmaps --only <id> --dump
     If keys are missing or wrong, use format = "regex" with a pattern that has the
     named groups key/action/desc/section (TOML single-quoted string), or
     format = "<id of a supported app with the same syntax>".
  d. Apps that keep shortcuts only inside the program (no file): skip, list them in the report.
- Shell aliases are separate apps: zsh-aliases, bash-aliases, fish-aliases. They read the
  same rc files as zsh, bash and fish, but have their own config entry: if you set
  [apps.zsh] paths, set the same paths in [apps.zsh-aliases] (bash and fish alike).
- Run `hyprmaps --list` again and confirm every added app shows "✔" and a keymap count.

STEP 3 - Export, one app at a time
- For each app id from --list (marked ✔):
    hyprmaps --only <id> --dump --json
  Each entry has: app, section (mode), key, description, action, source (file:line,
  or "<app> defaults"), default (true = a built-in default of the app).
- The work list is every entry whose "description" is empty. For the *-aliases apps an
  undocumented alias has its expansion as description: there, the entries whose
  "description" equals "action" are the work list (the key is the alias name).

STEP 4 - Write the descriptions
For each undocumented entry:
- default = false (my own binds): open the source file at that line and, when the action
  runs a script or command, read that script too. Describe what it does in MY setup.
  Example: "exec ~/scripts/screenshots.sh ~/Pictures/Screenshots shot" ->
  "Screenshot a region to ~/Pictures/Screenshots".
- default = true (built-ins): look up the action in the app's official documentation
  or man page (e.g. `man zshzle`, `man 3 readline`, the tmux/kitty/mpv docs on the web).
- Shell aliases: describe what typing the alias does. Read the expansion, and the function
  or script it calls when it calls one of mine. Aliases with default = true come from the
  shell or oh-my-zsh: use the oh-my-zsh plugin README or the man page of the command.
  Example: "gst" = "git status" -> "Show the working tree status".
- Style: at most 60 characters, start with a verb, sentence case, no final period.
  Describe the effect, not the mechanism ("Open a new tab here", not "Run new_tab_with_cwd").
  Write in English, unless descriptions.toml already uses another language.
- Format of ~/.config/hyprmaps/descriptions.toml: one table per app id, the key
  written EXACTLY as the "key" field (including spaces and the "›" separator):
      [kitty]
      "Ctrl + Shift + A" = "Open a new tab in the current folder"
  When the same key means different things in different modes, prefix the mode:
      [nvim]
      "[insert] <C-s>" = "Save and return to normal mode"
  Shell aliases use the alias name as key; zsh global/suffix aliases and fish
  abbreviations have the mode "global", "suffix" or "abbr":
      [zsh-aliases]
      "gst" = "Show the working tree status"
      "[global] ..." = "Two folders up"
  Always quote the key: alias names like "..." or "g.l" are not valid bare TOML keys,
  or turn into nested tables.
  Keys containing a backslash or a double quote: use a TOML literal key in single quotes,
  e.g.  '\' = "...". Never write two entries with the same key in one table.
- After each app:
    python3 -c "import tomllib; tomllib.load(open('$HOME/.config/hyprmaps/descriptions.toml','rb'))"
    hyprmaps --only <id> --dump
  The TOML must load, and the entries you described must now show their description.

STEP 5 - Report
Finish with a short report:
- a table: app | keymaps | undocumented before | undocumented after;
- apps you added or re-pointed in config.toml, and the source you used for each;
- keys you could not document, and why;
- anything that needs my decision, e.g. the same key bound twice in one app, or a bind
  whose script no longer exists.
````

## After the run

- Open hyprmaps: the new descriptions appear in normal (not italic) text.
- Add `--dump` to see the whole result in a terminal: `hyprmaps --all --dump | less`.
- To start over, delete the entries you do not want from `descriptions.toml`. Nothing else was changed.
