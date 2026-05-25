#!/usr/bin/env python3
"""
database_llm.py  — run locally
Interactive natural-language query interface for the HMDA mortgage database.

Pipeline:
  User question
    -> local LLM (Qwen2.5-3B or Phi-4-mini via llama-cpp-python)
    -> SQL extraction
    -> SSH tunnel to ilab (paramiko)
    -> ilab_script.py runs the query against postgres.cs.rutgers.edu
    -> results printed to terminal

Setup:
    pip install llama-cpp-python paramiko
    Download model GGUF (see README) and set MODEL_PATH below.
"""

from __future__ import annotations

import os
import re
import sys
import getpass
import paramiko
from llama_cpp import Llama

# ── Configuration ────────────────────────────────────────────────────────────

# Path to the GGUF model file (download with huggingface-cli or wget — see README)
MODEL_PATH = os.environ.get(
    "MODEL_PATH",
    os.path.join(os.path.dirname(__file__), "models", "qwen2.5-3b-instruct-q4_k_m.gguf"),
)

# Path to the schema file fed to the LLM as context
SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "schema_subset.sql")

# Remote ilab settings
ILAB_HOST = "ilab.cs.rutgers.edu"
ILAB_PORT = 22
REMOTE_SCRIPT = "~/ilab_script.py"   # path on the ilab machine

# LLM generation settings (match the project spec)
CONTEXT_WINDOW = 2048
MAX_TOKENS = 200

# ── Load schema once at startup ───────────────────────────────────────────────

def load_schema(path: str) -> str:
    try:
        with open(path, "r") as f:
            return f.read()
    except FileNotFoundError:
        print(f"[ERROR] Schema file not found: {path}", file=sys.stderr)
        sys.exit(1)


# ── LLM helpers ───────────────────────────────────────────────────────────────

def load_model(model_path: str) -> Llama:
    if not os.path.isfile(model_path):
        print(
            f"[ERROR] Model file not found: {model_path}\n"
            "  Download it with:\n"
            "    huggingface-cli download Qwen/Qwen2.5-3B-Instruct-GGUF "
            "qwen2.5-3b-instruct-q4_k_m.gguf --local-dir ./models\n"
            "  Then set MODEL_PATH in database_llm.py or as an environment variable.",
            file=sys.stderr,
        )
        sys.exit(1)

    print("[*] Loading LLM model (first load may take ~30 s) …", flush=True)
    llm = Llama(
        model_path=model_path,
        n_ctx=CONTEXT_WINDOW,
        n_threads=os.cpu_count(),
        verbose=False,
    )
    print("[*] Model loaded.", flush=True)
    return llm


def build_prompt(schema: str, question: str) -> str:
    """
    Construct a prompt that strongly guides the model to emit only SQL.
    Ending with ```sql is the key trick — the model will continue the code fence.
    """
    return (
        "You are an expert PostgreSQL query writer. "
        "Given the database schema below, write a single SQL SELECT query "
        "that answers the user's question. "
        "Output ONLY the SQL query inside a ```sql code fence. "
        "Do not explain. Do not add text before or after the query.\n\n"
        "### Database Schema:\n"
        f"{schema}\n\n"
        "### Question:\n"
        f"{question}\n\n"
        "### SQL Query:\n"
        "```sql\n"
    )


def ask_llm(llm: Llama, prompt: str) -> str:
    output = llm(
        prompt,
        max_tokens=MAX_TOKENS,
        stop=["```", ";", "\n\n"],   # stop at closing fence, semicolon, or blank line
        echo=False,
    )
    return output["choices"][0]["text"]


# ── SQL extraction ─────────────────────────────────────────────────────────────

# Patterns to match a complete SQL SELECT (or WITH … SELECT) statement
_SQL_FENCE_RE = re.compile(
    r"```(?:sql)?\s*((?:WITH|SELECT)[\s\S]+?)(?:```|;|$)",
    re.IGNORECASE,
)
_SQL_INLINE_RE = re.compile(
    r"((?:WITH\s+\w[\s\S]*?)?SELECT\s+[\s\S]+?)(?:;|$)",
    re.IGNORECASE,
)


def extract_sql(raw_text: str) -> str | None:
    """
    Pull the first SELECT (or WITH…SELECT) query out of LLM output.
    Returns the query string (without trailing semicolon), or None.
    """
    # 1. Try fenced code block
    m = _SQL_FENCE_RE.search(raw_text)
    if m:
        return _clean_sql(m.group(1))

    # 2. Try bare SELECT statement
    m = _SQL_INLINE_RE.search(raw_text)
    if m:
        return _clean_sql(m.group(1))

    return None


def _clean_sql(sql: str) -> str:
    sql = sql.strip().rstrip(";").strip()
    # Collapse excessive whitespace while preserving structure
    sql = re.sub(r"\n{3,}", "\n", sql)
    return sql


# ── SSH tunnel helper ──────────────────────────────────────────────────────────

def run_query_on_ilab(
    sql: str,
    username: str,
    password: str,
) -> str:
    """
    Open an SSH connection to ilab, upload ilab_script.py if needed,
    run it with the SQL query, and return stdout as a string.
    """
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        client.connect(
            hostname=ILAB_HOST,
            port=ILAB_PORT,
            username=username,
            password=password,
            timeout=30,
        )
    except paramiko.AuthenticationException:
        return "[ERROR] SSH authentication failed. Check your ilab username/password."
    except Exception as exc:
        return f"[ERROR] SSH connection failed: {exc}"

    # Upload ilab_script.py to the remote home directory (once)
    local_script = os.path.join(os.path.dirname(__file__), "ilab_script.py")
    remote_script_path = "ilab_script.py"
    try:
        sftp = client.open_sftp()
        sftp.put(local_script, remote_script_path)
        sftp.close()
    except Exception as exc:
        client.close()
        return f"[ERROR] Could not upload ilab_script.py: {exc}"

    # Escape the SQL query for safe shell passing
    sql_escaped = sql.replace("'", "'\\''")   # single-quote escape for bash
    command = f"~/path/to/venv/bin/python3 {remote_script_path} '{sql_escaped}'"

    try:
        _, stdout, stderr = client.exec_command(command, timeout=60)
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
    except Exception as exc:
        client.close()
        return f"[ERROR] Remote execution failed: {exc}"
    finally:
        client.close()

    if err.strip():
        return f"{out}\n[STDERR] {err.strip()}"
    return out


# ── Main interactive loop ─────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("  HMDA Mortgage Database — Natural Language Query Interface")
    print("=" * 60)
    print("Type your question in plain English. Type 'exit' to quit.\n")

    # Collect SSH / DB credentials upfront (hidden input)
    ilab_user = input("ilab username: ").strip()
    ilab_pass = getpass.getpass("ilab password: ")

    # Load resources
    schema = load_schema(SCHEMA_PATH)
    llm    = load_model(MODEL_PATH)

    print("\n[*] Ready. Ask away!\n")

    while True:
        try:
            question = input("Question> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n[*] Interrupted. Goodbye.")
            break

        if question == "exit":
            print("[*] Goodbye.")
            break

        if not question:
            continue

        # ── Step 1: Build prompt and call LLM ──────────────────────────────
        print("\n[*] Generating SQL …", flush=True)
        prompt = build_prompt(schema, question)
        raw_output = ask_llm(llm, prompt)

        print(f"\n--- LLM raw output ---\n{raw_output}\n----------------------")

        # ── Step 2: Extract SQL ─────────────────────────────────────────────
        sql = extract_sql(raw_output)

        if not sql:
            print("[!] Could not extract a valid SELECT query from the LLM output.")
            print("    Try rephrasing your question.\n")
            continue

        print(f"\n--- Extracted SQL ---\n{sql}\n--------------------\n")

        # ── Step 3: Run on ilab via SSH ─────────────────────────────────────
        print("[*] Running query on ilab …", flush=True)
        result = run_query_on_ilab(sql, ilab_user, ilab_pass)

        # ── Step 4: Display results ─────────────────────────────────────────
        print("\n=== Query Results ===")
        print(result)
        print("=" * 20 + "\n")


if __name__ == "__main__":
    main()
