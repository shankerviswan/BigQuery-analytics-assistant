# Grounded BigQuery Analytics Assistant — PoC (improved)

A deliberately small local Python prototype. The LLM proposes SQL; Python applies policy and metric-alignment checks; BigQuery dry-runs and executes; the answer is formatted from actual returned rows only.

**Core principle:** The LLM proposes. Python validates. BigQuery executes. Actual results determine the answer.

## What this version adds over the original starter

| Feature | Status |
|---------|--------|
| Table allow-list + read-only SQL (sqlglot) | Yes |
| Dry-run + max bytes billed + timeout + row limit | Yes |
| Hand-reviewed metric registry (YAML) | Yes |
| Clarification on ambiguous "sales/revenue" | Yes |
| Deterministic answer formatting (no LLM narration of numbers) | Yes |
| **Live schema introspection** (INFORMATION_SCHEMA, allow-listed only) | **New** |
| **Trusted date/timezone context** injected into LLM prompt | **New** |
| **Lightweight metric-to-SQL alignment checks** | **New** |
| Explicit DATE parameter typing for `*_date` params | **New** |
| Structured provenance (job_id, bytes, date_context, alignment_note) | **New** |

## Prerequisites

- Python 3.11+
- Google Cloud SDK (`gcloud`)
- A Google Cloud project with billing enabled and BigQuery API enabled
- Optional: OpenAI-compatible LLM endpoint

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env
# Edit .env → set GOOGLE_CLOUD_PROJECT
```

```bash
gcloud config set project YOUR_PROJECT_ID
gcloud services enable bigquery.googleapis.com
gcloud auth application-default login
```

## Cost safeguards

- Public dataset storage is free; **queries run in your project** and may incur charges.
- Default `MAX_BYTES_BILLED=100000000` (~100 MB).
- Every query is dry-run first.
- Configure GCP billing budgets/alerts as an extra safety net.

## Run tests (no network / no LLM)

```bash
pytest -q
```

## Run the demo

```bash
# Offline demo provider (no LLM key needed)
python -m app.main "How many orders were created in January 2025?"

# Ambiguous question → clarification, no execution
python -m app.main "What were sales last month?"
```

With a real model, set `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` in `.env`.

## Architecture (request flow)

```
User question
  → trusted DateContext (server clock + timezone)
  → schema context (live INFORMATION_SCHEMA or static fallback)
  → metric registry (YAML)
  → LLM / DemoProvider proposes SQL + metric_id + params  OR  clarification
  → metric alignment check (required tables, key filters)
  → sqlglot policy (SELECT only, allow-list, single statement)
  → BigQuery dry-run + max bytes
  → BigQuery execute (parameterized)
  → deterministic answer from rows + provenance
```

## Important limitations (still a PoC)

- Metric alignment is **heuristic**, not a full semantic layer.
- No regression suite of golden questions with known numeric answers yet.
- Schema context is column names + types only (no sample values, no relationships graph).
- Relative dates beyond last/this month and explicit January-YYYY are limited in the demo provider.
- Not a production security boundary — review IAM, authorized views, and column-level policies before real data.

## Suggested next steps

1. Golden-question regression suite (known answers against the public dataset).
2. Stronger metric templates (SQL fragments the model must compose around).
3. Audit log (question, metric_id, SQL, job_id, bytes, status).
4. Optional: adopt patterns from Open_Data_QnA (ValidateSQL agent) or MetricFlow for a real semantic layer.

## References used while improving this PoC

- sqlglot AST policy (already present; retained)
- Google Open_Data_QnA — explicit validate/debug agents (pattern only)
- Vanna — schema + documentation training (we use live schema + YAML metrics instead of vector RAG for simplicity)
- nlqueries — YAML knowledge base idea (our metrics.yaml is the lightweight equivalent)
