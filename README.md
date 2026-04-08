---
title: SQL Code Review Environment
emoji: 🔍
colorFrom: blue
colorTo: purple
sdk: docker
app_port: 7860
pinned: false
tags:
  - openenv
  - sql
  - code-review
  - security
  - ai-agent
---

# SQL Code Review — OpenEnv Environment

An **OpenEnv-compatible environment** that simulates real-world SQL code review tasks. An AI agent receives SQL snippets in context (schema, business purpose, performance data) and must identify security vulnerabilities, performance bottlenecks, and correctness issues — then suggest concrete fixes.

---

## Motivation

SQL code review is a daily task for senior engineers, DBAs, and security teams. Mistakes — missed injection vulnerabilities, N+1 queries, absent indices — have real consequences: data breaches, production outages, and degraded user experience. Training agents to perform rigorous, multi-dimensional SQL review has immediate practical value for automated CI/CD pipelines, code assistants, and security scanning tools.

---

## Tasks

| Task Name | Difficulty | Max Steps | Issues Required |
|-----------|-----------|-----------|-----------------|
| `sql-injection-easy` | Easy | 3 | SQL injection, unparameterized query, SELECT * |
| `perf-review-medium` | Medium | 4 | Missing index, SELECT *, no LIMIT |
| `full-review-hard` | Hard | 5 | Injection + credentials + cartesian join + N+1 + no limit + SELECT * |

### Task 1: `sql-injection-easy`
A Python login handler concatenates user input directly into a SQL string. The agent must detect the injection vector, flag the unparameterized query pattern, and note the SELECT * anti-pattern. A beginner security audit.

### Task 2: `perf-review-medium`
A multi-table reporting query joins 50M-row and 2M-row tables without indices, uses SELECT *, and has no LIMIT. The agent must identify the performance failure modes and suggest the right indices and query rewrites.

### Task 3: `full-review-hard`
A complex admin endpoint with **7 distinct issues**: SQL injection via f-string, unparameterized inner loop query, implicit cartesian product (missing JOIN ON), hardcoded password in query string, SELECT * on all tables, N+1 query pattern in Python loop, and no LIMIT. This genuinely challenges frontier models because the issues interact — fixing only injection misses the structural bugs.

---

## Action Space

```json
{
  "issues_found": ["sql_injection", "missing_index"],
  "severity_ratings": {
    "sql_injection": "critical",
    "missing_index": "high"
  },
  "suggested_fix": "USE parameterized queries; CREATE INDEX ...",
  "explanation": "String concatenation allows injection. Large table join lacks indices."
}
```

**Valid issue identifiers:**
- `sql_injection` — user input concatenated into SQL
- `unparameterized_query` — query built by string concatenation
- `select_star` — `SELECT *` instead of named columns
- `missing_index` — filter/join on un-indexed column
- `n_plus_one` — query executed inside a loop
- `no_limit` — no `LIMIT` clause on large table
- `cartesian_product` — multiple tables with no JOIN condition
- `hardcoded_credentials` — passwords/tokens hardcoded in SQL
- `implicit_type_cast` — mismatched column type comparisons
- `missing_where_clause` — DELETE/UPDATE without WHERE

**Severity levels:** `critical` | `high` | `medium` | `low`

---

## Observation Space

```json
{
  "sql_snippet": "SELECT * FROM users WHERE ...",
  "context": "Table: users(id, username, ...). Called from HTTP login endpoint.",
  "step": 1,
  "feedback": "Missed issues: missing_index | Good fix suggestion.",
  "issues_remaining": 2
}
```

---

## Reward Function

Composite score in `[0.0, 1.0]`:

| Component | Weight | Description |
|-----------|--------|-------------|
| Issue detection | 0–0.50 | Fraction of required issues correctly identified |
| Severity accuracy | 0–0.20 | Correct `critical`/`high` ratings on key issues |
| Fix quality | 0–0.20 | Fix text contains expected keywords (parameterized, INDEX, LIMIT, JOIN, etc.) |
| False positive penalty | −0.05 each | Issues flagged that aren't present (max −0.10) |
| Efficiency bonus | 0–0.10 | Reward for finding all issues in fewer steps |

This rewards **partial progress** — finding 2 of 7 issues yields non-zero reward — while penalizing hallucination and rewarding efficiency.

---

## Setup & Usage

### Prerequisites

```bash
pip install uv       # fast package manager (replaces pip for this project)
```

### Local Development (without Docker)

```bash
git clone <repo-url>
cd <repo-dir>

uv sync              # install all dependencies into .venv
uv run server        # start the FastAPI server on port 7860
```

### Local Development (Docker)

```bash
# Build and run the environment server
docker build -t sql-review-env .
docker run -p 7860:7860 sql-review-env

# In another terminal, run the inference script
export HF_TOKEN=hf_...
export MODEL_NAME=Qwen/Qwen2.5-72B-Instruct
export API_BASE_URL=https://router.huggingface.co/v1
export ENV_BASE_URL=http://localhost:7860
python inference.py
```

### Running a single task

```bash
export SQL_REVIEW_TASK=sql-injection-easy
python inference.py
```

### API Usage

```bash
# Reset to a task
curl -X POST http://localhost:7860/reset \
  -H "Content-Type: application/json" \
  -d '{"task": "perf-review-medium"}'

# Submit a review action
curl -X POST http://localhost:7860/step \
  -H "Content-Type: application/json" \
  -d '{
    "action": {
      "issues_found": ["missing_index", "select_star", "no_limit"],
      "severity_ratings": {"missing_index": "high"},
      "suggested_fix": "CREATE INDEX idx ON users(country); SELECT specific cols; add LIMIT 1000",
      "explanation": "Three performance issues identified"
    }
  }'

# Get current state
curl http://localhost:7860/state
```

### Run Tests

```bash
uv run pytest test_env.py -v
```

---

## Deploy to HuggingFace Spaces

This repo is configured to run as a **Docker-based HF Space** on port 7860. The `sdk: docker` and `app_port: 7860` fields in this README's YAML frontmatter tell HF Spaces how to build and serve it automatically.

### First-time setup

```bash
# 1. Create a new Space on huggingface.co
#    → New Space → SDK: Docker → Port: 7860

# 2. Add the HF Space as a git remote
git remote add space https://huggingface.co/spaces/<your-username>/<space-name>

# 3. Push to HF Spaces
git push space main
```

### Updating the Space

```bash
git add .
git commit -m "your message"

# Push to GitHub
git push origin main

# Push to HF Space
git push space main
```

### Push to both GitHub + HF Spaces at once (optional)

```bash
# Run once to set up dual-push on 'origin'
git remote set-url --add --push origin https://github.com/<your-username>/<repo>.git
git remote set-url --add --push origin https://huggingface.co/spaces/<your-username>/<space-name>.git

# From now on, a single command pushes to both
git push origin main
```

> **Note:** HF Spaces builds and redeploys automatically on every push. Monitor progress in the Space's **Logs** tab. The Space is live once you see `Uvicorn running on http://0.0.0.0:7860` in the logs.

---

## Baseline Scores

Expected scores with `Qwen/Qwen2.5-72B-Instruct` (may vary by temperature):

| Task | Expected Score | Notes |
|------|---------------|-------|
| `sql-injection-easy` | 0.65–0.85 | Most models spot injection readily |
| `perf-review-medium` | 0.50–0.70 | Index suggestions often incomplete |
| `full-review-hard` | 0.30–0.55 | Cartesian product and N+1 often missed |

---

## Project Structure

```
.
├── app.py              # FastAPI server (/reset, /step, /state, /tasks)
├── env_core.py         # Environment logic, graders, typed Pydantic models
├── inference.py        # Inference script (OpenEnv stdout log format)
├── openenv.yaml        # OpenEnv environment metadata
├── pyproject.toml      # Project deps & scripts (managed by uv)
├── uv.lock             # Locked dependency tree
├── Dockerfile          # Container build (python:3.11-slim + uv sync)
├── test_env.py         # Unit + integration tests
└── server/
    ├── __init__.py
    └── app.py          # Re-exports app; uvicorn entrypoint for `uv run server`
```

---

## HuggingFace Space

Tagged: `openenv` | `sql` | `code-review` | `security`

The Space runs the FastAPI server on port 7860. The `/reset` endpoint is the liveness ping used by the OpenEnv validator.
