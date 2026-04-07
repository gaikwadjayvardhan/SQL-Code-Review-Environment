"""
Tests for SQL Review Environment
Run: python -m pytest test_env.py -v   (or: PYTHONPATH=. pytest test_env.py -v)

API contract under openenv-core>=0.2.0:
  - env.reset()        -> SQLReviewObservation
  - env.step(action)   -> SQLReviewObservation  (reward in obs.reward, done in obs.done)
  - env.state          -> SQLReviewState  (property, not method)
  - state.step_count   -> int
"""

import pytest
from env_core import (
    SQLReviewAction,
    SQLReviewEnv,
    TASKS,
    grade_action,
    is_done,
)


# ---------------------------------------------------------------------------
# Grader unit tests
# ---------------------------------------------------------------------------

class TestGraderEasy:
    task = TASKS["sql-injection-easy"]

    def test_perfect_action(self):
        action = SQLReviewAction(
            issues_found=["sql_injection", "unparameterized_query", "select_star"],
            severity_ratings={"sql_injection": "critical", "unparameterized_query": "critical"},
            suggested_fix="Use parameterized query: db.execute('SELECT id FROM users WHERE username=%s', (username,))",
            explanation="SQL injection via string concatenation; use prepared statements",
        )
        reward = grade_action(self.task, action, step=1)
        assert reward.value >= 0.85

    def test_partial_action(self):
        action = SQLReviewAction(
            issues_found=["sql_injection"],
            severity_ratings={"sql_injection": "critical"},
            suggested_fix="",
            explanation="Found injection",
        )
        reward = grade_action(self.task, action, step=1)
        assert 0.1 < reward.value < 0.85

    def test_false_positive_penalty(self):
        action = SQLReviewAction(
            issues_found=["sql_injection", "missing_index", "cartesian_product"],
            severity_ratings={},
            suggested_fix="",
            explanation="",
        )
        reward = grade_action(self.task, action, step=1)
        # Penalty applied for missing_index, cartesian_product (not in task)
        assert reward.breakdown["false_positive_penalty"] < 0

    def test_empty_action(self):
        action = SQLReviewAction(issues_found=[], severity_ratings={}, suggested_fix="", explanation="")
        reward = grade_action(self.task, action, step=3)
        assert reward.value < 0.15


class TestGraderMedium:
    task = TASKS["perf-review-medium"]

    def test_all_perf_issues(self):
        action = SQLReviewAction(
            issues_found=["missing_index", "select_star", "no_limit"],
            severity_ratings={"missing_index": "high", "select_star": "medium"},
            suggested_fix=(
                "CREATE INDEX idx_users_country ON users(country); "
                "CREATE INDEX idx_orders_created ON orders(created_at); "
                "SELECT specific cols; add LIMIT 1000"
            ),
            explanation="Missing indices cause full scans, SELECT * wastes bandwidth, no LIMIT risks OOM",
        )
        reward = grade_action(self.task, action, step=1)
        assert reward.value >= 0.80

    def test_only_index(self):
        action = SQLReviewAction(
            issues_found=["missing_index"],
            severity_ratings={"missing_index": "high"},
            suggested_fix="Add index",
            explanation="",
        )
        reward = grade_action(self.task, action, step=2)
        # partial credit
        assert 0.15 < reward.value < 0.70


class TestGraderHard:
    task = TASKS["full-review-hard"]

    def test_finds_all_issues(self):
        action = SQLReviewAction(
            issues_found=[
                "sql_injection", "unparameterized_query", "select_star",
                "n_plus_one", "cartesian_product", "hardcoded_credentials", "no_limit"
            ],
            severity_ratings={
                "sql_injection": "critical",
                "hardcoded_credentials": "critical",
                "cartesian_product": "high",
                "n_plus_one": "high",
            },
            suggested_fix=(
                "Use parameterized queries. Fix JOIN: JOIN users ON orders.user_id = users.id. "
                "Batch-load items with WHERE order_id IN (...). Remove hardcoded password. "
                "Add LIMIT. Select specific columns."
            ),
            explanation="Multiple critical issues: injection, credentials, cartesian join, N+1",
        )
        reward = grade_action(self.task, action, step=1)
        assert reward.value >= 0.85

    def test_hard_task_is_genuinely_hard(self):
        """A simple action should score poorly on the hard task."""
        action = SQLReviewAction(
            issues_found=["sql_injection"],
            severity_ratings={"sql_injection": "critical"},
            suggested_fix="parameterize",
            explanation="found injection",
        )
        reward = grade_action(self.task, action, step=1)
        assert reward.value < 0.40


# ---------------------------------------------------------------------------
# Environment integration tests  (updated for openenv-core>=0.2.0 API)
# ---------------------------------------------------------------------------

class TestEnvLifecycle:
    def test_reset_returns_obs(self):
        env = SQLReviewEnv("sql-injection-easy")
        obs = env.reset()
        assert obs.step == 0
        assert (
            "SELECT" in obs.sql_snippet
            or "select" in obs.sql_snippet.lower()
            or "username" in obs.sql_snippet
        )

    def test_step_returns_observation(self):
        """step() must return an observation (not a tuple)."""
        env = SQLReviewEnv("sql-injection-easy")
        env.reset()
        action = SQLReviewAction(
            issues_found=["sql_injection"], severity_ratings={}, suggested_fix="", explanation=""
        )
        obs = env.step(action)
        # Must be a SQLReviewObservation — NOT a tuple
        assert hasattr(obs, "step"), "step() should return an observation object, not a tuple"
        assert obs.step == 1
        assert 0.0 <= obs.reward <= 1.0

    def test_done_flag_in_observation(self):
        """done must be accessible via obs.done, not as a separate return value."""
        env = SQLReviewEnv("sql-injection-easy")
        env.reset()
        action = SQLReviewAction(
            issues_found=[], severity_ratings={}, suggested_fix="", explanation=""
        )
        done = False
        for _ in range(TASKS["sql-injection-easy"]["max_steps"]):
            obs = env.step(action)
            done = obs.done
        assert done is True

    def test_state_is_property(self):
        """env.state must be a property (not a method), returning SQLReviewState."""
        env = SQLReviewEnv("perf-review-medium")
        env.reset()
        # Access as property — do NOT call env.state()
        state = env.state
        assert state.step_count == 0, f"Expected step_count=0, got {state.step_count}"

        action = SQLReviewAction(
            issues_found=["missing_index"], severity_ratings={}, suggested_fix="", explanation=""
        )
        env.step(action)
        state = env.state
        assert state.step_count == 1, f"Expected step_count=1, got {state.step_count}"

    def test_state_fields(self):
        """State object must expose task_name, rewards list, last_reward."""
        env = SQLReviewEnv("sql-injection-easy")
        env.reset()
        state = env.state
        assert state.task_name == "sql-injection-easy"
        assert isinstance(state.rewards, list)

    def test_all_tasks_instantiate(self):
        for task_name in TASKS:
            env = SQLReviewEnv(task_name)
            obs = env.reset()
            assert obs.sql_snippet
            assert obs.context

    def test_reward_in_range(self):
        for task_name in TASKS:
            env = SQLReviewEnv(task_name)
            env.reset()
            action = SQLReviewAction(
                issues_found=["sql_injection", "missing_index"],
                severity_ratings={"sql_injection": "critical"},
                suggested_fix="parameterized query with index",
                explanation="found issues",
            )
            obs = env.step(action)
            assert 0.0 <= obs.reward <= 1.0, f"Reward out of range for {task_name}: {obs.reward}"

    def test_is_done_on_perfect(self):
        task = TASKS["sql-injection-easy"]
        action = SQLReviewAction(
            issues_found=list(task["required_issues"]),
            severity_ratings={},
            suggested_fix="",
            explanation="",
        )
        assert is_done(task, action, 0.9) is True

    def test_unknown_task_raises(self):
        with pytest.raises(ValueError):
            SQLReviewEnv("nonexistent-task")
