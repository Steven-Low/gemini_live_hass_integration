
from typing import TypedDict, Any, Optional


class ExposedEntity(TypedDict):
    names: str
    domain: str
    state: str
    areas: Optional[str]
    attributes: dict[str, Any]
