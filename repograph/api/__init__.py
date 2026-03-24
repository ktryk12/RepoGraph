"""FastAPI entrypoints for RepoGraph."""

from .routes import app, create_app, router

__all__ = ["app", "create_app", "router"]
