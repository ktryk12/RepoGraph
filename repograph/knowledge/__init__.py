"""Knowledge graph indexers for RepoGraph — docs, ownership, config, CI."""

from .enricher import KnowledgeIndexResult, index_knowledge

__all__ = ["index_knowledge", "KnowledgeIndexResult"]
