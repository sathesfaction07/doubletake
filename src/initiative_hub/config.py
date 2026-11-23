"""Config. `IH_`-prefixed environment variables or a .env file."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="IH_", env_file=".env", extra="ignore")

    submissions: Path = Path("data/submissions.jsonl")
    truth: Path = Path("data/truth.jsonl")

    date_field: str = "target_date"
    amount_field: str = "annual_saving"

    slice_by: str = "team"
    """Field to group by when reporting per-slice knowability.

    Slicing is the point rather than a convenience: a dataset large enough to establish its date
    order can be cut into slices that individually cannot, and the aggregate report will never say
    so. `report --sliced` shows the difference.
    """

    confidence: float = Field(default=0.99, gt=0.0, lt=1.0)
    """Target confidence for `doubletake rows-needed`.

    Bounded strictly inside 0 and 1 because both endpoints are degenerate: 0 needs no rows and 1 is
    unreachable, since no finite number of ambiguous dates guarantees a disambiguating one.
    """

    # No list-typed fields on purpose: Pydantic Settings parses those as JSON, so `IH_X=a,b` would
    # fail with a decode error that reads like a bug in the tool rather than a bad value.
