"""Artifact retention management for AIQA reports, screenshots, and audit logs."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any


class RetentionManager:
    """Enforces retention bounds on AIQA artifact directories (reports, screenshots)."""

    def __init__(
        self,
        max_age_days: float = 30.0,
        max_files: int | None = None,
        allowed_extensions: tuple[str, ...] = (".json", ".xml", ".html", ".png", ".jpg", ".jsonl"),
    ) -> None:
        if max_age_days < 0:
            raise ValueError("max_age_days must be non-negative")
        if max_files is not None and max_files < 0:
            raise ValueError("max_files must be non-negative")
        self.max_age_days = max_age_days
        self.max_files = max_files
        self.allowed_extensions = allowed_extensions

    def prune(
        self,
        directory: Path | str,
        *,
        dry_run: bool = False,
        now_epoch: float | None = None,
    ) -> dict[str, Any]:
        """Prune expired or excess artifact files from the target directory."""
        target = Path(directory)
        if not target.exists() or not target.is_dir():
            return {"pruned_count": 0, "retained_count": 0, "pruned_files": []}

        now = now_epoch if now_epoch is not None else time.time()
        cutoff = now - (self.max_age_days * 86400.0)

        files = [
            f
            for f in target.rglob("*")
            if f.is_file() and f.suffix.lower() in self.allowed_extensions
        ]
        # Sort newest first by modification time
        files.sort(key=lambda f: f.stat().st_mtime, reverse=True)

        to_prune: list[Path] = []
        retained: list[Path] = []

        for idx, file_path in enumerate(files):
            mtime = file_path.stat().st_mtime
            expired_by_age = mtime < cutoff
            expired_by_count = self.max_files is not None and idx >= self.max_files
            if expired_by_age or expired_by_count:
                to_prune.append(file_path)
            else:
                retained.append(file_path)

        pruned_paths: list[str] = []
        for file_path in to_prune:
            pruned_paths.append(str(file_path))
            if not dry_run:
                file_path.unlink(missing_ok=True)

        return {
            "pruned_count": len(pruned_paths),
            "retained_count": len(retained),
            "pruned_files": pruned_paths,
        }


__all__ = ["RetentionManager"]
