# Natural-Language Database Query Interface 

> A local AI agent using Qwen2.5-3B that translates natural language into executable SQL to securely query a remote PostgreSQL mortgage database.

**Team members:** ELIJAH BYOUN, GERMAN D BURSET ROMERO, ENZO YUEN, HARRISON M VILLAMARIA  
**Course:** CS336 — Introduction to Database Systems  

---

## What Was Built -- German and Harrison

| File | Runs on | Purpose |
|------|---------|---------|
| `schema_full.sql` | postgres.cs.rutgers.edu | Full HMDA schema + ~200 rows of sample data |
| `schema_subset.sql` | local (LLM context) | Compact schema fed to the LLM (fits 2048-token window) |
| `ilab_script.py` | ilab.cs.rutgers.edu | Takes a SELECT query, runs it against postgres, prints formatted table |
| `database_llm.py` | local machine | Interactive loop: question → LLM → SQL extraction → SSH → results |

---

## Features Implemented -- Elijah and Enzo

- [x] **ilab script** — `ilab_script.py` takes a SQL `SELECT` as argument or stdin, returns formatted ASCII table
- [x] **Local LLM** — Qwen2.5-3B-Instruct (≤4 B parameters) via `llama-cpp-python`
- [x] **Prompt engineering** — schema + question → `SELECT` query, fenced with `` ```sql `` to guide the model
- [x] **SQL extraction** — regex extracts first valid `SELECT` / `WITH…SELECT` from LLM output
- [x] **Interactive loop** — reads questions until user types `exit`
- [x] **SSH tunnel** — `paramiko` uploads `ilab_script.py`, passes SQL, returns results; passwords collected via `getpass` (never visible)
- [x] **End-to-end pipeline** — all pieces assembled in `database_llm.py`

---

## Setup

### 1 — Install local dependencies

```bash
pip install llama-cpp-python paramiko
```

On Apple Silicon (M1/M2/M3), for a Metal-accelerated build:

```bash
CMAKE_ARGS="-DGGML_METAL=on" pip install llama-cpp-python --force-reinstall --no-cache-dir
```

### 2 — Download the model

```bash
pip install huggingface_hub
mkdir -p models
huggingface-cli download Qwen/Qwen2.5-3B-Instruct-GGUF \
    qwen2.5-3b-instruct-q4_k_m.gguf \
    --local-dir ./models
```

The GGUF file is ~1.9 GB. Alternatively use the Phi-4-mini model:

```bash
huggingface-cli download microsoft/Phi-4-mini-instruct-gguf \
    Phi-4-mini-instruct-Q4_K_M.gguf \
    --local-dir ./models
# Then set MODEL_PATH in database_llm.py to the Phi file.
```

### 3 — Set up the database on ilab

SSH into ilab and load the schema:

```bash
ssh <netid>@ilab.cs.rutgers.edu
psql -h postgres.cs.rutgers.edu -U <netid> -d <netid> -f schema_full.sql
```

### 4 — Install ilab dependencies

On ilab (ilab uses an externally-managed Python, so a venv is required):

```bash
python3 -m venv ~/path/to/venv
source ~/path/to/venv/bin/activate
pip install psycopg2-binary
```

### 5 — Run locally

```bash
python3 database_llm.py
```

You will be prompted for:
- ilab username  
- ilab password (hidden)

PostgreSQL authentication is handled automatically via GSSAPI (Kerberos) when running from ilab — no separate database password is needed.

---

## Example queries

```text
Question> How many mortgages have a loan value greater than the applicant income?
Question> What is the average income of owner occupied applications?
Question> What is the most common loan denial reason?
Question> How many applications were denied in 2023?
Question> What percentage of applications were for FHA-insured loans?
```

---

## How SQL extraction works

The LLM prompt ends with `` ```sql\n `` so the model immediately continues with SQL.  
`extract_sql()` in `database_llm.py` uses two regex patterns:

1. Fenced block — `` ```sql … ``` ``  
2. Bare `SELECT …` up to the first semicolon or blank line

The SQL is validated to start with `SELECT` or `WITH` before being sent to the ilab machine.

---

## What Was Challenging

- Getting `llama-cpp-python` to compile on Apple Silicon required Metal build flags.
- Tuning the prompt so the model outputs only SQL, without prose before the query.
- Escaping single quotes in SQL queries when passing them through `paramiko`/bash.
- Keeping the schema small enough to fit in a 2048-token context window while retaining enough column-comment hints for the LLM.

## What Was Interesting

- The `stop` tokens trick (stopping generation at the closing fence or semicolon) dramatically cut down on the regex work needed to extract valid SQL.
- Paramiko's `sftp.put()` makes it easy to keep `ilab_script.py` in sync with the local copy — it overwrites on every run.

## Citations

- HMDA dataset schema: public domain, CFPB  
  https://www.consumerfinance.gov/data-research/hmda/
- Qwen2.5-3B-Instruct model: Apache 2.0 license  
  https://huggingface.co/Qwen/Qwen2.5-3B-Instruct-GGUF
- `llama-cpp-python` documentation:  
  https://llama-cpp-python.readthedocs.io/
- `paramiko` documentation:  
  https://www.paramiko.org/
- psycopg2 documentation:  
  https://www.psycopg.org/docs/
