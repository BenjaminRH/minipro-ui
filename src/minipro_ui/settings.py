"""Small, atomic preferences and platform-appropriate application paths."""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path

from platformdirs import user_config_path

from .session_log import DEFAULT_LOG_PATH


def config_file() -> Path:
    return user_config_path("minipro-ui", appauthor=False) / "settings.json"


@dataclass
class Settings:
    """Only explicit preferences persist; hardware actions never auto-resume."""

    executable: str = ""
    programmer: str = "T48"
    recent_devices: list[str] = field(default_factory=list)
    save_log: bool = False
    log_path: str = DEFAULT_LOG_PATH
    image_directory: str = ""

    @classmethod
    def load(cls, path: Path | None = None) -> Settings:
        try:
            value = json.loads((path or config_file()).read_text())
            return cls(
                executable=str(value.get("executable", "")),
                save_log=value.get("save_log", False) is True,
                log_path=str(value.get("log_path", DEFAULT_LOG_PATH)),
                image_directory=str(value.get("image_directory", "")),
                programmer=value.get("programmer", "T48")
                if value.get("programmer", "T48")
                in {"TL866A", "TL866II", "T48", "T56", "T76"}
                else "T48",
                recent_devices=[
                    str(name) for name in value.get("recent_devices", [])[:12]
                ],
            )
        except (OSError, ValueError, TypeError, AttributeError):
            return cls()

    def save(self, path: Path | None = None) -> None:
        destination = path or config_file()
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(".tmp")
        temporary.write_text(json.dumps(asdict(self), indent=2) + "\n")
        temporary.replace(destination)

    def discover(self) -> Path | None:
        """Use an explicit executable or PATH; never install minipro implicitly."""
        if self.executable:
            candidate = Path(self.executable).expanduser()
            return candidate if is_executable(candidate) else None
        if system := shutil.which("minipro"):
            return Path(system)
        return None


def is_executable(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)
