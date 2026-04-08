"""
server/app.py — OpenEnv entry point
====================================
This module re-exports the FastAPI ``app`` object from the root ``app``
module so that openenv-core can locate it via the conventional
``server.app:app`` import path declared in ``openenv.yaml``.
"""

from app import app  # noqa: F401  (re-export for openenv)

__all__ = ["app"]
