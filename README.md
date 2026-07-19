# Aurora · Robot Vacuum AI Support

> An AI customer-support assistant for robot vacuums, built on a LangChain / LangGraph ReAct Agent with RAG. FastAPI backend, vanilla HTML/JS frontend, with token-level streaming, multi-turn memory, and incremental knowledge-base updates.

> **Works with any OpenAI-compatible endpoint** — OpenAI, Azure OpenAI, or local models (Ollama, vLLM, LM Studio). Defaults to `gpt-4o-mini` + `text-embedding-3-small`; point `OPENAI_BASE_URL` at any compatible provider to switch.

---

## Demo

![Aurora chat UI](docs/screenshot.png)

<!-- Optional: add a short screen recording (~10s: question -> streamed answer -> tool call) and uncomment:
![Aurora demo](docs/demo.gif)
-->

> Run it locally in under a minute with the Quick Start below.

---

## Overview

**Aurora** is an AI customer-support app for robot vacuum / vacuum-and-mop users. Users ask questions in the web UI, and a ReAct (Reasoning + Acting) Agent autonomously plans and calls tools (knowledge-base retrieval, weather, geolocation, usage reports, etc.), grounds its answer in retrieved knowledge, and streams the reply back token by token.

Key features:

- **RAG retrieval augmentation** — Product guides, FAQs, troubleshooting and maintenance docs are vectorized. Retrieval filters by relevance score to avoid stuffing irrelevant material into the prompt, with optional reranking.
- **ReAct multi-tool calling** — The agent reasons and calls tools over multiple rounds until the user's need is met.
- **Two-level memory** — Short-term conversation checkpoints are isolated by user + conversation. Stable user facts are extracted into a separate cross-conversation memory store.
- **Token-level streaming** — Answers are pushed token by token over SSE; the frontend renders incrementally with safe Markdown formatting.
- **Dynamic prompting** — Middleware detects "generate usage report" intent and switches the system prompt on the fly.
- **Internal user management** — Local login, administrator-provisioned accounts, RBAC, revocable sessions, audit records, and no public registration.
- **Automatic KB updates** — While the web service is running, local knowledge files are scanned every 10 minutes and incrementally synchronized to Chroma.

---

## Tech Stack

| Layer | Choice |
|---|---|
| Web framework | FastAPI + Uvicorn (SSE streaming) |
| Frontend | Vanilla HTML / CSS / JavaScript (no framework) |
| Agent | LangChain `create_agent` (ReAct) + LangGraph |
| LLM | Any OpenAI-compatible chat model (default `gpt-4o-mini`) via `langchain-openai` |
| Embedding | Any OpenAI-compatible embedding model (default `text-embedding-3-small`) |
| Vector store | Chroma (local persistence) |
| Memory stores | LangGraph SqliteSaver (`checkpoints.sqlite`) + application SQLite (`aurora.sqlite`) |
| External services | OpenWeatherMap (weather) · ip-api.com (IP geolocation, no key) |

---

## Architecture

```
                     Browser (web/ vanilla frontend)
                     EventSource ── SSE streaming
                              │
              ┌───────────────▼────────────────┐
              │        FastAPI (app.py)         │
│ login · RBAC · rate limit · logs│
│ POST /api/chat (SSE token flow) │
              └───────────────┬────────────────┘
                              │
              ┌───────────────▼────────────────┐
              │   ReactAgent (agent/react_agent)│
              │  Middleware:                    │
              │   · Summarization (memory)      │
              │   · ModelCallLimit (loop cap)   │
              │   · monitor_tool / logs / prompt│
│ Memory: user + conversation ID  │
              └──┬───────────┬────────────┬─────┘
                 │           │            │
                 ▼           ▼            ▼
        ┌──────────────┐ ┌─────────┐ ┌──────────────┐
        │  RAG service │ │ Weather │ │ usage records │
        │  rag/        │ │  + geo  │ │ data/external │
        └──────┬───────┘ └─────────┘ └──────────────┘
               ▼
        ┌──────────────────────────┐
        │ Chroma store (chroma_db/) │
        │ score filter + rerank     │
        └──────────────────────────┘
```

---

## Project Structure

```
Aurora/
├── app.py                     # FastAPI entrypoint (SSE / auth / rate limit / static)
├── web/                       # Vanilla frontend
│   ├── index.html
│   ├── styles.css
│   └── app.js
├── agent/
│   ├── react_agent.py         # ReAct agent, memory, streaming events
│   └── tools/
│       ├── agent_tools.py     # Tool definitions
│       └── middleware.py      # Custom middleware
├── rag/
│   ├── rag_service.py         # Retrieval + score filtering + optional rerank
│   └── vector_store.py        # Chroma management + incremental KB updates
├── services/
│   ├── auth.py                # Password hashing and revocable login sessions
│   ├── database.py            # Users, conversations, memory and audit storage
│   ├── long_term_memory.py    # Stable-fact extraction and recall
│   └── kb_scheduler.py        # Web-lifecycle knowledge scans
├── scripts/create_admin.py    # Bootstrap the first administrator
├── model/
│   └── factory.py             # LLM / Embedding factory (timeout, retries)
├── utils/
│   ├── config_handler.py      # YAML loading + .env + ${VAR} resolution
│   ├── settings.py            # Runtime settings (auth / rate limit / memory)
│   ├── logger_handler.py      # Logging
│   ├── prompt_loader.py       # Prompt loading
│   ├── file_handler.py        # Document loading & MD5
│   └── path_tool.py           # Path helpers
├── config/
│   ├── rag.yml                # Model names / access (references .env placeholders)
│   ├── chroma.yml             # Vector store & retrieval params
│   ├── agent.yml              # External service config (weather / geolocation)
│   └── prompts.yml            # Prompt file paths
├── prompts/                   # Prompt texts
├── data/                      # KB documents (txt/pdf) + external/records.csv
├── tests/                     # Unit tests
├── Dockerfile / .dockerignore
├── .env.example               # Env var template (copy to .env)
├── requirements.txt
└── README.md
```

> Generated at runtime and git-ignored: `.env`, `chroma_db/`, `checkpoints.sqlite*`, `aurora.sqlite*`, `md5.text`, `logs/`.

---

## Quick Start

### 1. Requirements

- Python 3.10+ (uses syntax such as `tuple[...]` and `str | None`)

### 2. Install dependencies

```bash
python -m pip install -r requirements.txt
```

### 3. Configure secrets

Copy the template and fill in real values (`.env` is not committed to git):

```bash
cp .env.example .env
```

Minimum required in `.env`:

```dotenv
OPENAI_API_KEY=replace-with-provider-api-key
# Optional — only if you want the live weather tool
OPENWEATHER_API_KEY=your-openweathermap-key
```

To use a provider other than OpenAI, set the endpoint and model names:

```dotenv
# Example: Azure OpenAI, a local model, or any OpenAI-compatible gateway
OPENAI_BASE_URL=http://localhost:11434/v1
LLM_MODEL=llama3.1
EMBEDDING_MODEL=nomic-embed-text
# Some endpoints cap embedding batch size:
# EMBEDDING_BATCH_SIZE=10
```

- OpenAI API keys: <https://platform.openai.com/api-keys>
- OpenWeatherMap (free tier): <https://openweathermap.org/api>

### 4. Build the knowledge base

Sample English docs are included under `data/`. Add your own `.txt` / `.pdf` files there, then ingest (required on first run):

```bash
python -m rag.vector_store
```

### 5. Choose the runtime mode

`APP_DEBUG=true` explicitly enables the no-login demo and maps each conversation
to a stable mock business user from `1001`–`1010`. Authentication is enabled by
default so a missing environment variable cannot silently expose admin APIs.

For internal production use, configure explicit origins, create the first administrator, then disable Debug mode:

```dotenv
APP_DEBUG=false
ALLOWED_ORIGINS=https://aurora.example.com
COOKIE_SECURE=true
```

```bash
python -m scripts.create_admin --username admin
```

Only administrators can create further accounts from `/admin`; Aurora exposes no registration endpoint. Set a business user ID (for example `1001`) when the account must read matching records from `data/external/records.csv`.

### 6. Run the server

```bash
python app.py
# or
python -m uvicorn app:app --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000> to start chatting.

---

## Configuration

### Environment variables (`.env`)

| Variable | Default | Description |
|---|---|---|
| `OPENAI_API_KEY` | — (required) | API key for the LLM / embedding provider |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | Any OpenAI-compatible endpoint |
| `LLM_MODEL` | `gpt-4o-mini` | Chat model name |
| `EMBEDDING_MODEL` | `text-embedding-3-small` | Embedding model name |
| `EMBEDDING_BATCH_SIZE` | `10` | Texts per embedding request (raise for OpenAI to speed up) |
| `OPENWEATHER_API_KEY` | empty | OpenWeatherMap key; empty disables the weather tool |
| `EXTERNAL_DATA_PATH` | `data/external/records.csv` | Usage-record CSV; use an ignored `*.local.csv` file for private data |
| `LLM_TIMEOUT` | `60` | Per model-call timeout (seconds) |
| `LLM_MAX_RETRIES` | `2` | Model-call retry count |
| `APP_DEBUG` | `false` | Explicitly enable the login-free demo; never use it in production |
| `APP_DB_PATH` | `aurora.sqlite` | Users, login sessions, conversation ownership and long-term memory |
| `AUTH_SESSION_HOURS` | `12` | Revocable login-session lifetime |
| `MIN_PASSWORD_LENGTH` | `10` | Minimum local account password length |
| `COOKIE_SECURE` | `false` | Require HTTPS for the login cookie; enable in production |
| `RATE_LIMIT_PER_MIN` | `20` | Per-user and client-IP chat requests per minute |
| `LOGIN_RATE_LIMIT_PER_MIN` | `10` | Per-IP login attempts per minute |
| `ALLOWED_ORIGINS` | local port 8000 origins | CORS allowed origins, comma-separated |
| `MEMORY_DB_PATH` | `checkpoints.sqlite` | Memory persistence file |
| `SESSION_TTL_MINUTES` | `180` | Idle time before a session's memory is cleared (≤0 = never) |
| `SUMMARY_TRIGGER_MESSAGES` | `30` | Summarize history once it exceeds this many messages |
| `SUMMARY_KEEP_MESSAGES` | `12` | Recent messages to keep after summarization |
| `MODEL_RUN_LIMIT` | `12` | Max model-call rounds within a single conversation |
| `LONG_TERM_MEMORY_ENABLED` | `true` | Extract stable user facts after successful turns |
| `KB_SCAN_ENABLED` | `true` | Run knowledge scans with the web service |
| `KB_SCAN_INTERVAL_SECONDS` | `600` | Local knowledge scan interval |

> Secrets live only in `.env`; `config/*.yml` reference them via `${VAR}` / `${VAR:-default}` — no plaintext keys in config.

### Vector store & retrieval (`config/chroma.yml`)

```yaml
k: 3                    # snippets finally passed to the model
candidate_k: 20         # initial candidate pool size
score_threshold: 0.3    # relevance threshold; below this is treated as irrelevant and dropped
rerank_enabled: false   # enable reranking (requires a rerank-capable endpoint)
chunk_size: 400         # text chunk size (requires re-ingest to apply)
chunk_overlap: 50
```

> After changing chunking params, rebuild the store to apply them to existing data: delete `chroma_db/` and `md5.text`, then run `python -m rag.vector_store`.

---

## API

| Method | Path | Description |
|---|---|---|
| GET | `/` | Frontend page |
| POST | `/api/auth/login` | Sign in with an administrator-provisioned account |
| POST | `/api/auth/logout` | Revoke the current login session |
| GET | `/api/auth/me` | Return the current user |
| POST | `/api/chat` | JSON body (`message`, `conversation_id`); streams SSE events |
| DELETE | `/api/conversations/current` | Clear one owned short-term conversation |
| GET/POST/PATCH | `/api/admin/users...` | Administrator-only user management |
| GET/POST | `/api/admin/knowledge/...` | Scan status and manual trigger |
| GET | `/api/health/live` | Process liveness |
| GET | `/api/health` | Database and knowledge-service readiness |

**SSE event format** (`data:` carries JSON):

| `type` | Fields | Meaning |
|---|---|---|
| `token` | `mid`, `content` | Answer text delta (grouped by message id) |
| `tool` | `mid`, `name`, `label` | Tool-call status hint |
| `error` | `content` | Error message |
| `done` | — | End of the turn |

---

## Conversational Memory

- Each short-term conversation is keyed internally by `user_id + conversation_id`; client IDs are checked against authenticated ownership and persisted to `checkpoints.sqlite`.
- Stable preferences, device details, home environment and lasting constraints are stored per user in `aurora.sqlite` and recalled across conversations. Credentials and low-confidence guesses are rejected.
- The frontend starts a new conversation on page refresh or when **New chat** is selected. Old short-term memory is cleaned up by TTL.
- When history exceeds the threshold, `SummarizationMiddleware` summarizes older messages and keeps only the most recent ones, bounding context length and cost.
- `SESSION_TTL_MINUTES` controls cleanup of idle sessions' memory.

> For multi-process / multi-worker deployments, SQLite's concurrency is limited — use a Redis / Postgres checkpointer instead.

---

## Tools

| Tool | Description |
|---|---|
| `rag_summarize` | Retrieve relevant material from the vector knowledge base |
| `get_weather` | Get real-time weather for a city (OpenWeatherMap) |
| `get_user_location` | Resolve the user's city via IP (ip-api.com) |
| `get_user_id` | Get the current user ID |
| `get_current_month` | Get the current month |
| `fetch_external_data` | Fetch the authenticated user's usage records for a given month |
| `fill_context_for_report` | Trigger report mode (middleware switches to the report prompt) |

---

## Knowledge Base Management

- Supports `.txt` / `.pdf`; place files in `data/` and run `python -m rag.vector_store` to ingest.
- **Incremental, self-cleaning updates**: tracked by "source file + MD5". Unchanged files are skipped; when a file changes, its old vectors are deleted by source before the new content is written, preventing stale/duplicate data.
- The web lifespan scans once at startup and every `KB_SCAN_INTERVAL_SECONDS` (default 10 minutes).
- The same mutually exclusive update path is available to administrators from `/admin`.
- Added, changed and deleted files are synchronized. The manifest is written atomically, source paths are relative, and nested knowledge directories are supported.

---

## Testing

```bash
python -m pytest -q
```

Covers authentication, user and conversation isolation, long-term memory filtering,
incremental vector updates, file utilities, configuration resolution, and the
repository-wide English-only rule. Tests do not call external model APIs.

---

## Deployment (Docker)

```bash
docker build -t aurora .
docker run -d -p 8000:8000 --env-file .env \
  -e APP_DB_PATH=runtime/aurora.sqlite \
  -e MEMORY_DB_PATH=runtime/checkpoints.sqlite \
  -e CHROMA_DB_PATH=runtime/chroma_db \
  -e KB_MANIFEST_PATH=runtime/kb_manifest.json \
  -v aurora-data:/app/runtime --name aurora aurora
```

The image includes a health check and runs a single worker by default (paired with SQLite memory). For multiple replicas in production, switch to an external memory store.

---

## Logging

- Stored under `logs/`, rotated daily, with 14 days retained.
- Console outputs INFO+, file outputs DEBUG+.
- Every `/api` request carries a `request_id` and logs method, path, status code, and latency.
- Passwords, cookies, full prompts, tool arguments and long-term memory values are not written to logs.

---

## Roadmap

- Migrate memory to Redis / Postgres for multi-replica deployment
- Hybrid retrieval (BM25 + vector) and query rewriting
- Frontend "new chat", message retry, and stop-generation
- External identity provider / SSO integration

---

## License

Released under the MIT License. See [LICENSE](LICENSE) for details. For learning and reference purposes.
