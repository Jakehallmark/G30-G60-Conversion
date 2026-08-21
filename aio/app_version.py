"""Application version metadata (single source of truth: version.json)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

_VERSION_FILE = Path(__file__).with_name("version.json")


@dataclass(frozen=True)
class VersionInfo:
    version: str
    build: int

    @property
    def tag(self) -> str:
        return f"v{self.version}"

    @property
    def display(self) -> str:
        return f"{self.version} (build {self.build})"

    @property
    def windows_file_version(self) -> str:
        """Four-part version for Windows PE metadata (major.minor.patch.build)."""
        parts = self.version.split(".")
        while len(parts) < 3:
            parts.append("0")
        return f"{parts[0]}.{parts[1]}.{parts[2]}.{self.build}"

    @property
    def windows_product_version(self) -> str:
        return self.version


@lru_cache(maxsize=1)
def get_version_info() -> VersionInfo:
    # utf-8-sig: Windows PowerShell Set-Content -Encoding utf8 writes a BOM.
    with _VERSION_FILE.open(encoding="utf-8-sig") as fh:
        data = json.load(fh)
    return VersionInfo(version=str(data["version"]), build=int(data["build"]))
