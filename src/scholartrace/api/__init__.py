"""ScholarTrace HTTP API components."""

from fastapi import FastAPI

from scholartrace.api.events import create_event_router
from scholartrace.api.factory import create_app

__all__ = ["app", "create_app", "create_event_router"]


def __getattr__(name: str) -> FastAPI:
    if name == "app":
        from scholartrace.api.app import app

        return app
    raise AttributeError(name)
