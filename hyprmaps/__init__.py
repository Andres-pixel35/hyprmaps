"""hyprmaps: read every keymap of a Hyprland desktop and browse it in rofi.

The whole package is read-only: it reads config files, runs a few
side-effect-free dump commands (``hyprctl binds -j``, ``tmux list-keys`` on a
private socket, ...) and shows the result in rofi. It never writes a file of
the user; the only thing it can write is the opt-in cache (cache.py).
"""

__version__ = "1.0.0"
