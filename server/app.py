"""
server/app.py — OpenEnv entry point
====================================
Re-exports the FastAPI ``app`` object from the root ``app`` module so that
openenv-core can locate it via the ``server.app:app`` import path declared
in ``openenv.yaml``.

Also exposes ``main()`` for uvicorn-based startup.
"""

import os

import uvicorn

from app import app  # noqa: F401  (re-export for openenv)

__all__ = ["app"]


def main():
    port = int(os.getenv("PORT", 7860))
    uvicorn.run("server.app:app", host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
