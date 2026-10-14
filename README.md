# Grounded BigQuery Analytics Assistant — PoC

Natural-language questions → validated SQL → BigQuery execution → answers **only from actual rows**.

**Domain-agnostic:** tables and metrics live in `config/*.yaml`. The default `thelook_ecommerce` entries are **sample/reference data only** — not a product assumption. Point `allowed_tables.yaml` and `metrics.yaml` at your own datasets for a real use case.

## Design principle

> The LLM proposes. Python validates. BigQuery executes. Actual results determine the answer.

## PR1 fixes (vs earlier starter)

| Fix | Detail |
|-----|--------|
| Schema | Real thelook columns (`order_id`, not `id`); live INFORMATION_SCHEMA preferred |
| Metric required | Ad-hoc SQL without `metric_id` is refused |
| Structured metrics | `agg`, `column`, `filters`, `time_column` drive alignment checks |
| Dates | Model may emit `time_range`; **Python** binds `@start_date` / `@end_date` from trusted clock |
| Params | Date bounds bound as `TIMESTAMP` for TIMESTAMP columns |
| Validator | CTEs and UNION allowed; CTE aliases not treated as physical tables |
| pytest | `pytest.ini` with `pythonpath = .` so `pytest -q` works |
| Provenance | `schema_source`, `metrics_version`, resolved date parameters |

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# set GOOGLE_CLOUD_PROJECT=...

gcloud auth application-default login
```

## Tests

```bash
pytest -q
# or: python -m pytest -q
```

## Demo (offline, no LLM key)

```bash
python -m app.main "How many orders were created in January 2025?"
python -m app.main "What were sales last month?"   # clarification
```

## Swap to your domain

1. Edit `config/allowed_tables.yaml` → your fully-qualified tables.
2. Edit `config/metrics.yaml` → your approved metrics (`agg`, `column`, `filters`, `time_column`, `tables`).
3. Set `LIVE_SCHEMA=true` so the assistant loads real columns from INFORMATION_SCHEMA.
4. Optionally replace DemoProvider patterns in `app/llm.py` for offline smoke tests on your domain.

## Cost safeguards

- Dry-run before every execution
- `MAX_BYTES_BILLED` (default 100 MB)
- Timeout + row limit
- Table allow-list + read-only SQL policy
- Configure GCP billing budgets/alerts as an extra layer

## Limitations (still a PoC)

- Filter polarity checks are pragmatic (text + structure), not full SQL semantics
- No golden numeric regression suite against live BigQuery yet (`tests/golden/` is the next step)
- No audit log persistence yet
- Not a production IAM boundary — use a dedicated read-only service account

## Architecture

```
question
 → trusted DateContext
 → schema (live INFORMATION_SCHEMA or static fallback)
 → metrics.yaml (versioned)
 → LLM/Demo proposes SQL + metric_id + optional time_range
 → require metric_id
 → metric alignment (tables, agg, time column, filters)
 → bind dates in Python
 → sqlglot policy (SELECT/WITH/UNION, allow-list)
 → dry-run + execute
 → deterministic answer + provenance
```
