"""ASGI runtime entry point for IndustryPulse."""

from __future__ import annotations

from industrypulse.api.app import app, create_app

__all__ = ["app", "create_app"]
