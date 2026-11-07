import json
from pathlib import Path

import pytest

from backend.models import Checklist

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def checklist():
    return Checklist.model_validate(json.loads((ROOT / "config" / "checklist.default.json").read_text("utf-8")))
