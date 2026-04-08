"""
FastAPI server — exposes the SQL Review environment via HTTP.

Hand-rolled OpenEnv-compatible implementation exposing the standard
/reset, /step, /state, and /tasks endpoints. We intentionally do NOT use
create_fastapi_app from openenv-core because its route layout differs from
the /reset + /step contract expected by the inference client and validator.
"""

import os
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from env_core import SQLReviewAction, SQLReviewObservation, SQLReviewEnv, TASKS

import uvicorn

# ---------------------------------------------------------------------------
# Hand-rolled OpenEnv-compatible FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="SQL Code Review Environment",
    description="OpenEnv-compatible environment for SQL review tasks",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory session store (single-session for benchmark runner)
_env: Optional[SQLReviewEnv] = None

# ── Request / Response schemas ──────────────────────────────────────────

class ResetRequest(BaseModel):
    task: str = "sql-injection-easy"

class StepRequest(BaseModel):
    action: Dict[str, Any]

class ResetResponse(BaseModel):
    observation: Dict[str, Any]
    task: str
    task_description: str
    difficulty: str
    max_steps: int
    valid_issues: list

class StepResponse(BaseModel):
    observation: Dict[str, Any]
    reward: float
    done: bool
    info: Dict[str, Any]

class StateResponse(BaseModel):
    state: Dict[str, Any]

# ── Endpoints ───────────────────────────────────────────────────────────

@app.get("/")
def root():
    return {
        "name": "SQL Code Review Environment",
        "version": "1.0.0",
        "tasks": list(TASKS.keys()),
        "endpoints": ["/reset", "/step", "/state", "/tasks"],
    }

@app.get("/tasks")
def list_tasks():
    return {
        name: {
            "description": t["description"],
            "difficulty": t["difficulty"],
            "max_steps": t["max_steps"],
            "num_required_issues": len(t["required_issues"]),
        }
        for name, t in TASKS.items()
    }

@app.post("/reset", response_model=ResetResponse)
def reset(req: ResetRequest = None):
    global _env
    task_name = (req.task if req else None) or "sql-injection-easy"
    if task_name not in TASKS:
        raise HTTPException(status_code=400, detail=f"Unknown task: {task_name}")

    _env = SQLReviewEnv(task_name=task_name)
    obs = _env.reset()
    task = TASKS[task_name]

    valid_issues = [
        "sql_injection", "missing_index", "n_plus_one", "select_star",
        "no_limit", "implicit_type_cast", "cartesian_product",
        "hardcoded_credentials", "unparameterized_query", "missing_where_clause",
    ]

    return ResetResponse(
        observation=obs.model_dump(),
        task=task_name,
        task_description=task["description"],
        difficulty=task["difficulty"],
        max_steps=task["max_steps"],
        valid_issues=valid_issues,
    )

@app.post("/step", response_model=StepResponse)
def step(req: StepRequest):
    global _env
    if _env is None:
        raise HTTPException(
            status_code=400, detail="Environment not initialized. Call /reset first."
        )

    try:
        action = SQLReviewAction(**req.action)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Invalid action: {e}")

    try:
        obs = _env.step(action)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return StepResponse(
        observation=obs.model_dump(),
        reward=obs.reward,
        done=obs.done,
        info={
            "step": obs.step,
            "feedback": obs.feedback,
        },
    )

@app.get("/state", response_model=StateResponse)
def state():
    global _env
    if _env is None:
        raise HTTPException(
            status_code=400, detail="Environment not initialized. Call /reset first."
        )
    return StateResponse(state=_env.state.model_dump())


# ---------------------------------------------------------------------------
# Entrypoint (used by `uv run server` via pyproject.toml [project.scripts])
# ---------------------------------------------------------------------------


def main():
    port = int(os.getenv("PORT", 7860))
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
