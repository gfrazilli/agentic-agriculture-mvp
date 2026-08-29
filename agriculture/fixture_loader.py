from pathlib import Path
from typing import Final

from pydantic import BaseModel

from agriculture.schemas import Analysis, BoundarySuggestion, Field

FIXTURE_MODELS: Final[dict[str, tuple[str, type[BaseModel]]]] = {
    "field-draft": ("field-draft.example.json", Field),
    "boundary-suggestion": ("boundary-suggestion.example.json", BoundarySuggestion),
    "analysis-running": ("analysis-running.example.json", Analysis),
    "analysis-result": ("analysis-result.example.json", Analysis),
}


def fixture_names() -> tuple[str, ...]:
    return tuple(FIXTURE_MODELS)


def load_fixture(name: str) -> BaseModel:
    try:
        filename, model = FIXTURE_MODELS[name]
    except KeyError:
        raise KeyError(f"Unknown fixture {name!r}.") from None
    path = Path(__file__).resolve().parent / "fixtures" / filename
    return model.model_validate_json(path.read_text(encoding="utf-8"))
