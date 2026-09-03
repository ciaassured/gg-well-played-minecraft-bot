"""Training-run layout and atomic metadata persistence."""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RunDirectory:
    root: Path

    @classmethod
    def create(
        cls,
        parent: Path,
        configuration: dict[str, Any],
        run_id: str | None = None,
    ) -> RunDirectory:
        if run_id is not None and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", run_id):
            raise ValueError("run ID must be a single safe path component")
        parent = parent.resolve()
        parent.mkdir(parents=True, exist_ok=True)
        if run_id is not None:
            root = parent / run_id
            try:
                root.mkdir()
            except FileExistsError as exception:
                raise FileExistsError(f"run ID already exists: {run_id}") from exception
        else:
            timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            root = parent / f"run-{timestamp}"
            suffix = 1
            while True:
                try:
                    root.mkdir()
                    break
                except FileExistsError:
                    root = parent / f"run-{timestamp}-{suffix}"
                    suffix += 1
        run = cls(root)
        run.prepare()
        run.write_json("config.json", configuration)
        return run

    @classmethod
    def open(cls, root: Path) -> RunDirectory:
        run = cls(root.resolve())
        if not (run.root / "config.json").is_file():
            raise FileNotFoundError(f"not a training run: {run.root}")
        run.prepare()
        return run

    def prepare(self) -> None:
        for directory in (
            self.checkpoints,
            self.candidates,
            self.promoted,
            self.metrics,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    @property
    def checkpoints(self) -> Path:
        return self.root / "checkpoints"

    @property
    def candidates(self) -> Path:
        return self.checkpoints / "candidates"

    @property
    def promoted(self) -> Path:
        return self.checkpoints / "promoted"

    @property
    def metrics(self) -> Path:
        return self.root / "metrics"

    @property
    def untrained_checkpoint(self) -> Path:
        return self.checkpoints / "untrained.zip"

    @property
    def latest_checkpoint(self) -> Path:
        return self.checkpoints / "latest.zip"

    @property
    def best_checkpoint(self) -> Path:
        return self.checkpoints / "best.zip"

    def candidate_checkpoint(self, step: int) -> Path:
        return self.candidates / f"step-{step:08d}.zip"

    def promoted_checkpoint(self, step: int) -> Path:
        return self.promoted / f"step-{step:08d}.zip"

    def promote(self, candidate: Path, step: int) -> Path:
        retained = self.promoted_checkpoint(step)
        shutil.copy2(candidate, retained)
        shutil.copy2(candidate, self.best_checkpoint)
        return retained

    def write_json(self, relative_path: str | Path, value: Any) -> Path:
        destination = self.root / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        temporary.write_text(
            json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        temporary.replace(destination)
        return destination


def atomic_write_json(destination: Path, value: Any) -> Path:
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(destination)
    return destination


def find_run_for_checkpoint(checkpoint: Path) -> RunDirectory | None:
    for parent in (checkpoint.resolve().parent, *checkpoint.resolve().parents):
        if (parent / "config.json").is_file():
            return RunDirectory.open(parent)
    return None
