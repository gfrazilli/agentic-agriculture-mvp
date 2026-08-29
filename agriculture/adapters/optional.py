"""Helpers for optional Google Cloud SDK dependencies."""

import importlib
from types import ModuleType


class MissingGoogleDependency(ImportError):
    """An adapter was instantiated without its optional Google SDK installed."""


def load_google_module(module_name: str, distribution_name: str) -> ModuleType:
    try:
        return importlib.import_module(module_name)
    except ImportError as exc:
        raise MissingGoogleDependency(
            f"{distribution_name} is required to use this Google Cloud adapter. "
            f"Install the project's Google Cloud dependency set before instantiating it."
        ) from exc
