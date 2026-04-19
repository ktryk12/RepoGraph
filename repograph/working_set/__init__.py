"""WorkingSet — first-class context object for RepoGraph consumers."""

from .builder import build
from .models import WorkingSet, WorkingSetFile, WorkingSetSymbol
from .serializer import to_compact, to_prompt_context

__all__ = [
    "build", "WorkingSet", "WorkingSetFile", "WorkingSetSymbol",
    "to_prompt_context", "to_compact",
]
