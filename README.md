# Grounded BigQuery Analytics Assistant — PoC

Natural-language questions → validated SQL → BigQuery execution → answers **only from actual rows**.

**Domain-agnostic:** tables and metrics live in `config/*.yaml`. The default `thelook_ecommerce` entries are **sample/reference data only** — not a product assumption. Point `allowed_tables.yaml` and `metrics.yaml` at your own datasets for a real use case.

## Design principle

> The LLM proposes. Python validates. BigQuery executes. Actual results determine the answer.

## How a question is answered

The model does not browse the BigQuery project. Each question sends only what you configured.

1. You list the allowed tables in `config/allowed_tables.yaml` and the approved metrics in `config/metrics.yaml`.
2. Python loads the columns of those tables from BigQuery (`LIVE_SCHEMA=true`) and attaches the metric file and today's date.
3. The model may propose SQL, a `metric_id`, and a time range such as `last_month` or `january_2024`. It may also ask for clarification.
4. Python refuses the proposal if the metric, table, aggregate, filter, or date bounds do not match the metric file.
5. Python fills in `@start_date` and `@end_date` from the trusted clock. The model does not choose those instants.
6. Python dry-runs the SQL, then runs it only if it is read-only, uses an allow-listed table, and stays under `MAX_BYTES_BILLED`.
7. The number in the answer is a cell BigQuery returned. The response also includes the SQL, the job id, and the bytes processed.

With `LLM_*` empty, a few demo phrases are answered without a model. Any other question needs an OpenAI-compatible endpoint in `.env`.

## Large project

Do not register every raw table. Start from the questions people actually ask, about ten, and write one metric per question. Add a table to the allow-list only when a metric needs it.

When a question would need several joins, create a BigQuery view that already applies those joins and filters. Allow-list the view and point the metric at it. The model then chooses the metric and the date range. The join stays in SQL you can test yourself.

Columns are read live from the tables you listed. You do not type column lists into the YAML.

Not built yet: a `joins` block that requires exact keys such as `product_id = id`, and an `AUDIT_LOG_PATH` that would append each answer to a file. A join note in the metric file is documentation only. The checker does not enforce it.

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

## Demo

No LLM key is required. Leave `LLM_BASE_URL`, `LLM_API_KEY`, and `LLM_MODEL` empty and the demo provider answers a few fixed phrasings.

Once Application Default Credentials exist, the order-count command runs a real BigQuery job. The sales command stops at clarification and does not query.

```bash
python -m app.main "How many orders were created in January 2024?"
python -m app.main "What were sales last month?"   # clarification, no query
```

## Verified live query

Recorded 2026-10-14 with the demo provider (no LLM) against public `thelook_ecommerce.orders`, job run in project `bigquery-assistance`:

| Field | Value |
|---|---|
| Question | How many orders were created in January 2024? |
| Metric | `order_count` |
| Time range | `january_2024` (`America/Los_Angeles`) |
| Parameters | `2024-01-01T00:00:00-08:00` .. `2024-02-01T00:00:00-08:00` |
| Schema | `live` |
| Answer | `order_count: 1409` |
| Bytes processed | 2,009,344 |
| Bytes billed | 10,485,760 (BigQuery’s 10 MiB on-demand minimum) |
| Job id | `62faf47c-6e8b-4493-8e85-efa3501d7e45` |

Same month, cancelled orders only. Asked with `deepseek-flash` and thinking disabled. The model proposed the SQL. Python checked it, bound the dates, and BigQuery returned the count:

| Field | Value |
|---|---|
| Question | cancelled-order count for January 2024? |
| Metric | `cancelled_order_count` |
| Filter | `LOWER(o.status) = 'cancelled'` |
| Parameters | same January 2024 bounds as above |
| Schema | `live` |
| Answer | `cancelled_order_count: 212` |
| Bytes processed | 3,296,426 |
| Bytes billed | 10,485,760 |
| Job id | `8e03c28d-b7d7-4353-978f-3e1daaf18182` |

212 is the cancelled subset of the 1,409 orders. `pytest` replays both counts. It does not re-query BigQuery.

## Swap to your domain

See **Large project** above. Do not copy the whole warehouse into these files.

1. Edit `config/allowed_tables.yaml` → the few fully-qualified tables or views this assistant may read.
2. Edit `config/metrics.yaml` → one approved metric per question (`agg`, `column`, `filters`, `time_column`, `tables`).
3. Set `LIVE_SCHEMA=true` so the assistant loads real columns from INFORMATION_SCHEMA.
4. Set `BUSINESS_TIMEZONE` to the timezone those metrics use.
5. Optionally replace DemoProvider patterns in `app/llm.py` for offline smoke tests on your domain.

## Cost safeguards

- Dry-run before every execution
- `MAX_BYTES_BILLED` (default 100 MB)
- Timeout + row limit
- Table allow-list + read-only SQL policy
- Configure GCP billing budgets/alerts as an extra layer

## Limitations (still a PoC)

- Filter and join checks are structural. A join can pass without being the metric’s real keys
- Two live counts are recorded for January 2024: 1409 orders and 212 cancelled orders. The suite replays them and does not re-run BigQuery
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
