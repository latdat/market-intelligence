"""Production TOML loader for static source configuration."""

import tomllib
from pathlib import Path

from pydantic import ValidationError

from market_intelligence.source_registry.models import SourceConfig


class SourceConfigLoadError(ValueError):
    """Raised when a source configuration file cannot be loaded safely."""

    def __init__(self, path: Path, detail: str) -> None:
        self.path = path
        self.detail = detail
        super().__init__(f"cannot load source config {path}: {detail}")


def load_source_config(path: Path) -> SourceConfig:
    """Load and validate one TOML file, including filename/source identity."""
    try:
        with path.open("rb") as config_file:
            payload = tomllib.load(config_file)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise SourceConfigLoadError(path, str(error)) from error

    try:
        source = SourceConfig.model_validate(payload)
    except ValidationError as error:
        raise SourceConfigLoadError(path, str(error)) from error

    if path.stem != source.source_id:
        raise SourceConfigLoadError(
            path,
            f"filename must match source_id {source.source_id!r}",
        )
    return source


def load_source_configs(directory: Path) -> tuple[SourceConfig, ...]:
    """Load all source TOML files in deterministic filename order."""
    paths = sorted(directory.glob("*.toml"))
    if not paths:
        raise SourceConfigLoadError(directory, "no source TOML files found")
    return tuple(load_source_config(path) for path in paths)
