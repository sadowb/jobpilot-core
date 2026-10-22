"""One row of the profile table. Its JSON value can be text or a structured section."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class ProfileEntry(BaseModel):
    model_config = ConfigDict(strict=True)
    key: str
    value: Any
    source: str
    confirmed_at: datetime | None = None
