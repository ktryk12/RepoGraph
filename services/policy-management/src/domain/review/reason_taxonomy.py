from __future__ import annotations

from pathlib import Path
from typing import Dict, Iterable, List

import yaml


def load_reason_taxonomy(path: Path | str) -> Dict[str, Dict[str, str]]:
    with Path(path).open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    codes = data.get("codes", {}) if isinstance(data, dict) else {}
    return {str(code): value for code, value in codes.items() if isinstance(code, str)}


def validate_reason_codes(codes: Iterable[str], taxonomy: Dict[str, Dict[str, str]]) -> List[str]:
    unknown = [code for code in codes if code not in taxonomy]
    return sorted(set(unknown))
