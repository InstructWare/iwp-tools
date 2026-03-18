from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class Diagnostic:
    code: str
    message: str
    file_path: str
    line: int = 0
    column: int = 0
    severity: str = "error"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
