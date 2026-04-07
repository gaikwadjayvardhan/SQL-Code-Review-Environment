"""
SQL Code Review Environment — Core Logic
=========================================
Simulates a real-world SQL code review task where an AI agent must:
1. Identify issues in SQL queries (security, performance, correctness)
2. Classify issue severity
3. Suggest concrete fixes

Tasks:
  - sql-injection-easy   : Detect SQL injection in a simple query
  - perf-review-medium   : Identify performance bottlenecks and missing indices
  - full-review-hard     : Comprehensive review (security + perf + correctness)
"""

from __future__ import annotations

import re
import time
from typing import Any, Dict, Generic, List, Optional, TypeVar

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# openenv-core base class shims
# ---------------------------------------------------------------------------
# When openenv-core is installed these are imported from the real package.
# The try/except lets the file work standalone until the package is on PyPI.
# ---------------------------------------------------------------------------
try:
    from openenv.core.env_server.interfaces import (
        Action,
        Observation,
        State,
        Environment,
    )
except ImportError:
    # ── Shims — mirror the openenv-core 0.2.x public API ───────────────────

    class Action(BaseModel):
        """Base action submitted by the agent."""
        metadata: Dict[str, Any] = Field(default_factory=dict)

    class Observation(BaseModel):
        """Base observation returned to the agent."""
        done: bool = Field(default=False)
        reward: float = Field(default=0.0)
        metadata: Dict[str, Any] = Field(default_factory=dict)

    class State(BaseModel):
        """Base environment state."""
        step_count: int = Field(default=0)

    ActionT = TypeVar("ActionT", bound=Action)
    ObsT = TypeVar("ObsT", bound=Observation)
    StateT = TypeVar("StateT", bound=State)

    class Environment(Generic[ActionT, ObsT, StateT]):
        """Base environment interface."""

        def reset(self, seed=None, episode_id=None, **kwargs) -> ObsT:  # type: ignore[return]
            raise NotImplementedError

        def step(self, action: ActionT, timeout_s=None, **kwargs) -> ObsT:  # type: ignore[return]
            raise NotImplementedError

        @property
        def state(self) -> StateT:  # type: ignore[return]
            raise NotImplementedError


# ---------------------------------------------------------------------------
# Typed Models
# ---------------------------------------------------------------------------


class SQLReviewAction(Action):
    """Action submitted by the agent."""

    issues_found: List[str] = Field(
        default_factory=list,
        description="List of issue identifiers found. Valid values: "
                    "'sql_injection', 'missing_index', 'n_plus_one', "
                    "'select_star', 'no_limit', 'implicit_type_cast', "
                    "'cartesian_product', 'hardcoded_credentials', "
                    "'unparameterized_query', 'missing_where_clause'",
    )
    severity_ratings: Dict[str, str] = Field(
        default_factory=dict,
        description="Map of issue -> severity ('critical', 'high', 'medium', 'low')",
    )
    suggested_fix: str = Field(
        default="",
        description="The agent's suggested corrected SQL or explanation (free text)",
    )
    explanation: str = Field(
        default="",
        description="Brief explanation of findings",
    )
    # inherited: metadata: Dict[str, Any] = {}


class SQLReviewObservation(Observation):
    """What the agent sees each step."""

    sql_snippet: str = Field(description="The SQL code to review")
    context: str = Field(description="Business context / table schema hints")
    step: int = Field(description="Current step number")
    feedback: str = Field(default="", description="Feedback from previous action")
    issues_remaining: int = Field(
        default=-1, description="How many issues remain unfound (-1 if unknown)"
    )
    # inherited: done: bool, reward: float, metadata: dict


class SQLReviewState(State):
    """Full environment state snapshot."""

    # inherited: step_count: int
    task_name: str = Field(default="")
    last_reward: float = Field(default=0.0)
    rewards: List[float] = Field(default_factory=list)
    last_action: Optional[Dict[str, Any]] = Field(default=None)
    feedback: str = Field(default="")


class SQLReviewReward(BaseModel):
    value: float
    breakdown: Dict[str, float]


# ---------------------------------------------------------------------------
# Task Definitions
# ---------------------------------------------------------------------------


TASKS: Dict[str, Dict[str, Any]] = {
    "sql-injection-easy": {
        "description": "Detect SQL injection vulnerability in a login query",
        "difficulty": "easy",
        "max_steps": 3,
        "sql_snippet": """-- User login handler (Python + raw SQL)
def get_user(username):
    query = "SELECT * FROM users WHERE username = '" + username + "'"
    return db.execute(query)
""",
        "context": (
            "Table: users(id INT PK, username VARCHAR(50), password_hash VARCHAR(256), role VARCHAR(20)). "
            "This function is called directly from the HTTP login endpoint with user-supplied input."
        ),
        "required_issues": {"sql_injection", "unparameterized_query", "select_star"},
        "required_severities": {"sql_injection": "critical", "unparameterized_query": "critical"},
        "fix_keywords": ["parameterized", "placeholder", "%s", "?", "prepared statement", "execute(query,"],
    },

    "perf-review-medium": {
        "description": "Identify performance issues in a reporting query",
        "difficulty": "medium",
        "max_steps": 4,
        "sql_snippet": """SELECT o.*, u.*, p.*
FROM orders o
JOIN users u ON o.user_id = u.id
JOIN products p ON o.product_id = p.id
WHERE u.country = 'US'
ORDER BY o.created_at DESC;
""",
        "context": (
            "Tables: orders(50M rows, no index on created_at or user_id), "
            "users(2M rows, no index on country), products(100k rows). "
            "This query runs in a nightly dashboard report. Average runtime: 4 minutes."
        ),
        "required_issues": {"missing_index", "select_star", "no_limit"},
        "required_severities": {"missing_index": "high", "select_star": "medium"},
        "fix_keywords": ["index", "LIMIT", "SELECT o.id", "specific columns", "CREATE INDEX"],
    },

    "full-review-hard": {
        "description": "Comprehensive review: security + performance + correctness",
        "difficulty": "hard",
        "max_steps": 5,
        "sql_snippet": """-- Admin report: fetch all orders for a user
def get_orders_report(user_id, status):
    sql = f\"\"\"
        SELECT *
        FROM orders, users, order_items
        WHERE orders.user_id = {user_id}
        AND orders.status = '{status}'
        AND password = 'admin123'
    \"\"\"
    results = []
    for row in db.execute(sql):
        items = db.execute(
            "SELECT * FROM order_items WHERE order_id = " + str(row['id'])
        )
        results.append({**row, 'items': items})
    return results
""",
        "context": (
            "Tables: orders(user_id FK, status VARCHAR, created_at), "
            "users(id PK, email, password_hash), order_items(order_id FK, product_id, qty, price). "
            "No JOIN condition between orders and users → implicit cross join. "
            "This endpoint is accessible to all authenticated users."
        ),
        "required_issues": {
            "sql_injection", "unparameterized_query", "select_star",
            "n_plus_one", "cartesian_product", "hardcoded_credentials", "no_limit"
        },
        "required_severities": {
            "sql_injection": "critical",
            "hardcoded_credentials": "critical",
            "cartesian_product": "high",
            "n_plus_one": "high",
        },
        "fix_keywords": [
            "parameterized", "JOIN", "LIMIT", "specific columns",
            "prepared", "index", "WHERE order_id IN"
        ],
    },
}


# ---------------------------------------------------------------------------
# Graders
# ---------------------------------------------------------------------------


def grade_action(task: Dict[str, Any], action: SQLReviewAction, step: int) -> SQLReviewReward:
    """
    Score an agent's review action against the task ground truth.
    Returns partial credit for partial progress.
    """
    required = task["required_issues"]
    required_sev = task.get("required_severities", {})
    fix_kws = task.get("fix_keywords", [])
    max_steps = task["max_steps"]

    found = set(action.issues_found)
    correct_found = found & required
    false_positives = found - required

    # Issue Detection (0–0.50)
    issue_score = len(correct_found) / max(len(required), 1)
    issue_score *= 0.50

    # Severity Accuracy (0–0.20)
    sev_hits = 0
    for issue, expected_sev in required_sev.items():
        agent_sev = action.severity_ratings.get(issue, "").lower()
        if agent_sev == expected_sev:
            sev_hits += 1
    sev_score = (sev_hits / max(len(required_sev), 1)) * 0.20

    # Fix Quality (0–0.20)
    fix_text = (action.suggested_fix + " " + action.explanation).lower()
    kw_hits = sum(1 for kw in fix_kws if kw.lower() in fix_text)
    fix_score = min(kw_hits / max(len(fix_kws), 1), 1.0) * 0.20

    # False Positive Penalty (−0.05 each, min 0)
    fp_penalty = min(len(false_positives) * 0.05, 0.10)

    # Step efficiency bonus: reward finishing with fewer steps
    efficiency_bonus = max(0.0, 0.10 * (1 - (step - 1) / max_steps))

    total = max(0.0, issue_score + sev_score + fix_score - fp_penalty + efficiency_bonus)
    total = min(total, 1.0)

    return SQLReviewReward(
        value=round(total, 4),
        breakdown={
            "issue_detection": round(issue_score, 4),
            "severity_accuracy": round(sev_score, 4),
            "fix_quality": round(fix_score, 4),
            "false_positive_penalty": round(-fp_penalty, 4),
            "efficiency_bonus": round(efficiency_bonus, 4),
        },
    )


def is_done(task: Dict[str, Any], action: SQLReviewAction, reward: float) -> bool:
    """Episode ends when all required issues found OR reward is high enough."""
    required = task["required_issues"]
    found = set(action.issues_found)
    all_found = required.issubset(found)
    return all_found or reward >= 0.85


def generate_feedback(task: Dict[str, Any], action: SQLReviewAction, reward: SQLReviewReward) -> str:
    required = task["required_issues"]
    found = set(action.issues_found)
    missed = required - found
    extra = found - required

    parts = []
    if missed:
        parts.append(f"Missed issues: {', '.join(sorted(missed))}")
    if extra:
        parts.append(f"False positives (not real issues here): {', '.join(sorted(extra))}")
    if reward.value >= 0.85:
        parts.append("Excellent review — all critical issues identified.")
    elif reward.value >= 0.5:
        parts.append("Good progress. Refine severity ratings and fix suggestions.")
    else:
        parts.append("Review incomplete. Look more carefully at the query.")

    return " | ".join(parts) if parts else "Keep going."


# ---------------------------------------------------------------------------
# Environment State Machine
# ---------------------------------------------------------------------------


class SQLReviewEnv(Environment[SQLReviewAction, SQLReviewObservation, SQLReviewState]):
    """
    OpenEnv-compliant SQL Code Review environment.

    Conforms to openenv-core==0.2.x interface:
      - reset(seed, episode_id, **kwargs) -> SQLReviewObservation
      - step(action, timeout_s, **kwargs)  -> SQLReviewObservation  (reward in obs.reward)
      - state -> SQLReviewState  (property)
    """

    def __init__(self, task_name: str = "sql-injection-easy"):
        if task_name not in TASKS:
            raise ValueError(f"Unknown task: {task_name}. Valid: {list(TASKS.keys())}")
        self.task_name = task_name
        self.task = TASKS[task_name]
        self._step = 0
        self._done = False
        self._last_reward = 0.0
        self._last_action: Optional[SQLReviewAction] = None
        self._feedback = ""
        self._start_time = time.time()
        self._rewards: List[float] = []

    # ── OpenEnv interface ───────────────────────────────────────────────────

    def reset(self, seed=None, episode_id=None, **kwargs) -> SQLReviewObservation:
        self._step = 0
        self._done = False
        self._last_reward = 0.0
        self._last_action = None
        self._feedback = ""
        self._start_time = time.time()
        self._rewards = []
        return self._make_obs(done=False, reward=0.0)

    def step(
        self,
        action: SQLReviewAction,
        timeout_s=None,
        **kwargs,
    ) -> SQLReviewObservation:
        if self._done:
            raise RuntimeError("Episode is done. Call reset() first.")

        self._step += 1
        reward_obj = grade_action(self.task, action, self._step)
        self._last_reward = reward_obj.value
        self._last_action = action
        self._rewards.append(reward_obj.value)

        done = is_done(self.task, action, reward_obj.value) or self._step >= self.task["max_steps"]
        self._done = done
        self._feedback = generate_feedback(self.task, action, reward_obj)

        return self._make_obs(done=done, reward=reward_obj.value)

    @property
    def state(self) -> SQLReviewState:
        return SQLReviewState(
            step_count=self._step,
            task_name=self.task_name,
            last_reward=self._last_reward,
            rewards=list(self._rewards),
            last_action=self._last_action.model_dump() if self._last_action else None,
            feedback=self._feedback,
        )

    # ── Internal helpers ────────────────────────────────────────────────────

    def _make_obs(self, done: bool, reward: float) -> SQLReviewObservation:
        required = self.task["required_issues"]
        found = set(self._last_action.issues_found) if self._last_action else set()
        remaining = len(required - found) if self._last_action else -1
        return SQLReviewObservation(
            sql_snippet=self.task["sql_snippet"],
            context=self.task["context"],
            step=self._step,
            feedback=self._feedback,
            issues_remaining=remaining,
            done=done,
            reward=reward,
        )
