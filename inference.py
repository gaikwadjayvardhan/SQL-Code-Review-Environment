"""
Inference Script — SQL Code Review Environment
================================================
Runs an LLM agent against all 3 SQL review tasks and emits structured logs.

Uses the OpenEnv GenericEnvClient when available; falls back to plain
requests so the script works before openenv-core is on PyPI.

Environment variables required:
  API_BASE_URL   LLM API endpoint (default: https://router.huggingface.co/v1)
  MODEL_NAME     Model identifier (default: Qwen/Qwen2.5-72B-Instruct)
  HF_TOKEN       HuggingFace / API key
  ENV_BASE_URL   SQL Review environment base URL (default: http://localhost:7860)
  IMAGE_NAME     Docker image name (optional, for docker-based runs)

stdout format (strict):
  [START] task=<name> env=sql-code-review model=<model>
  [STEP]  step=<n> action=<str> reward=<0.00> done=<true|false> error=<msg|null>
  [END]   success=<true|false> steps=<n> score=<0.000> rewards=<r1,r2,...>
"""

import json
import os
import textwrap
from typing import Any, Dict, List, Optional

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Imports from env_core (for type hints; GenericEnvClient used for HTTP calls)
# ---------------------------------------------------------------------------
try:
    from env_core import SQLReviewAction, SQLReviewObservation, SQLReviewEnv  # noqa: F401
except ImportError:
    pass  # env_core may not be present in every execution context

# ---------------------------------------------------------------------------
# Try the real openenv GenericEnvClient; fall back to a thin requests shim
# ---------------------------------------------------------------------------
try:
    from openenv.core.generic_client import GenericEnvClient, GenericAction  # type: ignore

    class _EnvClientAdapter:
        """Wraps GenericEnvClient to expose a simple sync reset/step API."""

        def __init__(self, base_url: str):
            self._client = GenericEnvClient(base_url=base_url)
            self._sync = self._client.sync()

        def reset(self, task: str) -> Dict[str, Any]:
            return self._sync.reset(task=task)

        def step(self, action: Dict[str, Any]) -> Dict[str, Any]:
            return self._sync.step(GenericAction(**action))

except ImportError:
    # ── Fallback: thin wrapper around plain HTTP requests ───────────────────

    class _EnvClientAdapter:  # type: ignore[no-redef]
        def __init__(self, base_url: str):
            self._base = base_url.rstrip("/")

        def reset(self, task: str) -> Dict[str, Any]:
            import requests  # lazy import — not available in every execution context
            r = requests.post(f"{self._base}/reset", json={"task": task}, timeout=30)
            r.raise_for_status()
            return r.json()

        def step(self, action: Dict[str, Any]) -> Dict[str, Any]:
            import requests  # lazy import — not available in every execution context
            r = requests.post(f"{self._base}/step", json={"action": action}, timeout=30)
            r.raise_for_status()
            return r.json()


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
API_KEY      = os.getenv("HF_TOKEN") or os.getenv("API_KEY") or "dummy"
API_BASE_URL = os.getenv("API_BASE_URL", "https://router.huggingface.co/v1")
MODEL_NAME   = os.getenv("MODEL_NAME",   "Qwen/Qwen2.5-72B-Instruct")
ENV_BASE_URL = os.getenv("ENV_BASE_URL", "http://localhost:7860")
IMAGE_NAME   = os.getenv("IMAGE_NAME")          # for docker-based runs
BENCHMARK    = "sql-code-review"

TASKS = [
    "sql-injection-easy",
    "perf-review-medium",
    "full-review-hard",
]

VALID_ISSUES = [
    "sql_injection", "missing_index", "n_plus_one", "select_star",
    "no_limit", "implicit_type_cast", "cartesian_product",
    "hardcoded_credentials", "unparameterized_query", "missing_where_clause",
]

MAX_STEPS = 10  # hard ceiling; each task's own max_steps is the soft limit

SYSTEM_PROMPT = textwrap.dedent("""
You are an expert database engineer and security auditor performing SQL code review.

Your job is to analyze SQL snippets and identify issues in these categories:
  - sql_injection          : User input concatenated directly into SQL string
  - unparameterized_query  : Query built by string concatenation (even without obvious injection)
  - select_star            : Using SELECT * instead of named columns
  - missing_index          : Query filters/joins on un-indexed columns
  - n_plus_one             : A query inside a loop (each row triggers another query)
  - no_limit               : Query has no LIMIT clause on potentially large tables
  - cartesian_product      : Multiple tables in FROM without a JOIN condition
  - hardcoded_credentials  : Passwords, tokens, or secrets hardcoded in the query
  - implicit_type_cast     : Comparing columns of mismatched types
  - missing_where_clause   : DELETE or UPDATE with no WHERE

For each issue, rate severity: critical | high | medium | low
Provide a corrected SQL snippet or concrete fix recommendation.

Respond ONLY with valid JSON matching this schema:
{
  "issues_found": ["issue_id", ...],
  "severity_ratings": {"issue_id": "severity", ...},
  "suggested_fix": "corrected SQL or fix description",
  "explanation": "brief explanation"
}

No markdown, no preamble — raw JSON only.
""").strip()


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def log_start(task: str, env: str, model: str) -> None:
    print(f"[START] task={task} env={env} model={model}", flush=True)


def log_step(step: int, action: str, reward: float, done: bool, error: Optional[str]) -> None:
    error_val = error if error else "null"
    done_val = str(done).lower()
    # Compress action for log line — no newlines
    action_safe = action.replace("\n", " ").replace("\r", "")[:120]
    print(f"[STEP] step={step} action={action_safe} reward={reward:.2f} done={done_val} error={error_val}", flush=True)


def log_end(success: bool, steps: int, score: float, rewards: List[float]) -> None:
    rewards_str = ",".join(f"{r:.2f}" for r in rewards)
    print(f"[END] success={str(success).lower()} steps={steps} score={score:.3f} rewards={rewards_str}", flush=True)


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

def build_user_prompt(obs: Dict[str, Any], step: int, history: List[str]) -> str:
    history_block = "\n".join(history[-3:]) if history else "None"
    feedback = obs.get("feedback", "")
    remaining = obs.get("issues_remaining", -1)
    return textwrap.dedent(f"""
SQL snippet to review:
```
{obs['sql_snippet']}
```

Context: {obs['context']}

Step: {step}
Previous feedback: {feedback if feedback else 'None'}
Issues remaining (hint): {remaining if remaining >= 0 else 'unknown'}

Recent history:
{history_block}

Identify all issues and respond with JSON only.
Valid issue identifiers: {', '.join(VALID_ISSUES)}
""").strip()


def get_agent_action(
    client: OpenAI,
    obs: Dict[str, Any],
    step: int,
    history: List[str],
) -> Dict[str, Any]:
    """Call the LLM and parse JSON action. Returns a safe default on failure."""
    user_prompt = build_user_prompt(obs, step, history)
    try:
        completion = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": user_prompt},
            ],
            temperature=0.2,
            max_tokens=600,
            stream=False,
        )
        raw = (completion.choices[0].message.content or "").strip()
        # Strip markdown fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        parsed = json.loads(raw.strip())
        # Sanitize
        action = {
            "issues_found": parsed.get("issues_found", []),
            "severity_ratings": parsed.get("severity_ratings", {}),
            "suggested_fix": str(parsed.get("suggested_fix", "")),
            "explanation": str(parsed.get("explanation", "")),
        }
        return action
    except Exception as exc:
        print(f"[DEBUG] Agent parse error step={step}: {exc}", flush=True)
        return {
            "issues_found": [],
            "severity_ratings": {},
            "suggested_fix": "",
            "explanation": f"parse error: {exc}",
        }


# ---------------------------------------------------------------------------
# Episode Runner
# ---------------------------------------------------------------------------

def run_episode(client: OpenAI, env_client: _EnvClientAdapter, task_name: str) -> None:
    rewards: List[float] = []
    steps_taken = 0
    score = 0.0
    success = False
    history: List[str] = []
    error_msg: Optional[str] = None

    log_start(task=task_name, env=BENCHMARK, model=MODEL_NAME)

    try:
        reset_resp = env_client.reset(task_name)
        obs = reset_resp["observation"]
        max_steps = reset_resp.get("max_steps", MAX_STEPS)
        done = False

        for step in range(1, max_steps + 1):
            if done:
                break

            action = get_agent_action(client, obs, step, history)
            action_summary = f"found={action['issues_found']}"

            try:
                step_resp = env_client.step(action)
                obs      = step_resp["observation"]
                reward   = float(step_resp["reward"])
                done     = bool(step_resp["done"])
                error_msg = None
            except Exception as e:
                reward    = 0.0
                done      = True
                error_msg = str(e)

            rewards.append(reward)
            steps_taken = step
            log_step(step=step, action=action_summary, reward=reward, done=done, error=error_msg)

            history.append(
                f"Step {step}: {action['issues_found']} → reward {reward:.2f} | "
                f"feedback: {obs.get('feedback', '')[:80]}"
            )

            if done:
                break

        # Score = average reward across steps, already in [0, 1]
        score = sum(rewards) / max(len(rewards), 1)
        score = min(max(score, 0.0), 1.0)
        success = score >= 0.5

    except Exception as exc:
        error_msg = str(exc)
        print(f"[DEBUG] Episode error: {exc}", flush=True)

    finally:
        log_end(success=success, steps=steps_taken, score=score, rewards=rewards)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    client = OpenAI(base_url=API_BASE_URL, api_key=API_KEY)
    env_client = _EnvClientAdapter(base_url=ENV_BASE_URL)

    task_env = os.getenv("SQL_REVIEW_TASK", "")
    tasks_to_run = [task_env] if task_env and task_env in TASKS else TASKS

    for task_name in tasks_to_run:
        run_episode(client, env_client, task_name)
        print("", flush=True)  # blank line between episodes


if __name__ == "__main__":
    main()
