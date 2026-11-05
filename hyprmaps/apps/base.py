"""What an app is, and the context its loader receives."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..model import Keymap
from ..sources import expand, which

# Order here is the order of the category picker and of the app list.
CATEGORIES = {
    "wm": "Window manager",
    "terminal": "Terminal",
    "shell": "Shell",
    "alias": "Shell aliases",
    "multiplexer": "Terminal multiplexer",
    "editor": "Code editor",
    "browser": "Browser",
    "files": "File manager",
    "media": "Media & documents",
    "launcher": "Launcher",
    "other": "Other",
}


@dataclass
class App:
    id: str
    name: str
    category: str
    load: Callable[["Ctx"], list[Keymap]]
    paths: tuple = ()   # default config locations (files, dirs or globs)
    bins: tuple = ()    # executables whose presence means "installed"
    icon: str = ""      # icon name for rofi's app list
    options: dict = field(default_factory=dict)  # [[custom]] settings (format, pattern, ...)

    def config_paths(self, override=None) -> list[Path]:
        """Existing config paths: the user's override if given, else the defaults."""
        found = []
        for p in (override or self.paths):
            found.extend(x for x in expand(p) if x.exists() and x not in found)
        return found

    def detected(self) -> bool:
        return bool(which(*self.bins)) or bool(self.config_paths())


@dataclass
class Ctx:
    """Everything a loader needs. Loaders append user-facing hints to ``notes``."""
    app: App
    paths: list[Path]
    defaults: bool = True   # include the app's built-in default keymaps
    live: bool = False      # opt-in: ask the running app (may execute user config code)
    options: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)

    def first(self) -> Path | None:
        files = [p for p in self.paths if p.is_file()]
        return files[0] if files else None
