"""Backward-compatible facade for live connector package."""

from app.services.live_connector import *  # noqa: F403
from app.services.live_connector import __all__ as __all__
from app.services.live_training_data import live_training_data

__all__ = list(__all__) + ["live_training_data"]
