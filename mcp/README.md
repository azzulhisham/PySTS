# MANTIS MCP (beginner guide)

This folder is a **small MCP server** for the MANTIS REST API in `PySTS/restapi`.

---

## What is MCP? (30-second version)

| Term | Meaning |
|------|---------|
| **MCP** | Model Context Protocol — a standard so AI apps can call **tools** |
| **MCP server** | A small program (this folder) that exposes tools |
| **MCP host** | The app that uses those tools (Cursor) |
| **Tool** | One action the AI can call, e.g. `get_dark_vessels` |

```
You ask Cursor in chat
        │
        ▼
Cursor (MCP host)  ──stdio──►  server.py (MCP server)
                                    │
                                    ▼
                              MANTIS REST API (Flask)
```

You do **not** call the MCP with curl. Cursor starts `server.py` and the AI picks tools when relevant.

---

## Folder layout

```
PySTS/mcp/
├── README.md                 ← you are here
├── requirements.txt          ← Python packages
├── .env.example              ← copy to .env
├── client.py                 ← talks to MANTIS HTTP API
├── server.py                 ← MCP server (7 tools)
└── cursor-mcp.example.json   ← paste into Cursor MCP settings
```

---

## Step 1 — Start the MANTIS API

The MCP only **calls** the API. The API must already be running.

```bash
cd PySTS/restapi
source venv/bin/activate
gunicorn -c gunicorn_config.py main:app
```

Default URL: `http://127.0.0.1:8080`  
Swagger: `http://127.0.0.1:8080/swagger`

---

## Step 2 — Install the MCP environment

```bash
cd PySTS/mcp
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

---

## Step 3 — Configure environment

```bash
cp .env.example .env
```

Edit `.env` only if your API URL or credentials differ.

| Variable | Purpose |
|----------|---------|
| `MANTIS_BASE_URL` | API base URL |
| `MANTIS_USER_ID` | Same as Swagger token `userId` |
| `MANTIS_ACCESS_KEY` | Same as Swagger token `accessKey` |

---

## Step 4 — Quick smoke test (optional, no Cursor yet)

This checks the HTTP client only:

```bash
cd PySTS/mcp
source venv/bin/activate
python - <<'PY'
from client import client
print(client.get_token())
print("polygons ok?", client.get_polygons().get("ok"))
PY
```

If this fails, fix API / `.env` before wiring Cursor.

---

## Step 5 — Connect Cursor to this MCP

### Option A — Cursor Settings UI

1. Open **Cursor Settings → MCP**
2. Click **Add new MCP server** (or edit MCP JSON)
3. Use the contents of `cursor-mcp.example.json`
4. **Important:** set `cwd` to your real absolute path, and prefer the venv Python:

```json
{
  "mcpServers": {
    "mantis": {
      "command": "/Users/zultan/sources/python/PySTS/mcp/venv/bin/python",
      "args": ["/Users/zultan/sources/python/PySTS/mcp/server.py"],
      "env": {
        "MANTIS_BASE_URL": "http://127.0.0.1:8080",
        "MANTIS_USER_ID": "user@sts.my",
        "MANTIS_ACCESS_KEY": "vZOODBrmB3cc0nvMiLwXtssAnchorageuj15dNSohbDgldkW_NI"
      }
    }
  }
}
```

5. Save, then confirm the server shows as connected (green / ready).

### Option B — Project file

You can also put the same JSON under:

`.cursor/mcp.json` in your project (if your Cursor version supports project MCP config).

---

## Step 6 — Try it in chat

With the API running and MCP connected, ask Cursor things like:

- “Use the mantis MCP to list dark vessels with coverage exit excluded.”
- “Get STS activities with min suspicion 4.5.”
- “How many illegal-anchoring candidates are there right now?”

Cursor should call tools such as `get_dark_vessels` or `get_sts_activities`.

---

## The seven tools

| Tool | MANTIS endpoint | What it does |
|------|-----------------|--------------|
| `get_token` | `POST /authentication/token` | Login / refresh JWT (other tools auto-login too) |
| `get_polygons` | `GET /mantis/polygons` | Anchorage + restricted polygons |
| `get_sts_activities` | `GET /mantis/sts-activities` | High-suspicion STS pairs |
| `get_illegal_anchoring` | `GET /mantis/illegal-anchoring` | Suspect stops in watch / restricted zones |
| `get_dark_vessels` | `GET /mantis/darkvessels` | Suspected AIS dark vessels |
| `get_identity_conflicts` | `GET /mantis/identity-conflict` | Re-flag / dual-MMSI identity groups |
| `get_sanctions_list` | `GET /mantis/sanctions` | OFAC vessel list (optional `imo` / `mmsi`) |

Optional tool args:

- `get_sts_activities(min_suspicion_score=4.5, max_distance_m=35)`
- `get_dark_vessels(include_coverage_exit=False)`
- `get_identity_conflicts(max_distance_m=50)`
- `get_sanctions_list(imo="9187631")`

---

## How the code fits together

1. **`client.py`** — plain HTTP with `httpx` (token cache + GET helpers).
2. **`server.py`** — registers seven `@mcp.tool()` functions and runs on **stdio**.
3. **Cursor** — starts `server.py`, discovers tools, calls them when useful.

You can read `server.py` top-to-bottom; it is intentionally short.

---

## Troubleshooting

| Problem | Check |
|---------|--------|
| MCP shows error / red | Absolute path to `venv/bin/python` and `server.py` |
| `Unauthorized` / 401 | `.env` credentials; API JWT settings |
| Connection refused | Is gunicorn/Flask running on 8080? |
| Tools not listed | Restart Cursor MCP / reload window |
| Timeout on dark/illegal | Those endpoints hit the DB — wait; raise timeout in `client.py` if needed |

Manual server start (for debugging only — Cursor normally does this):

```bash
cd PySTS/mcp
source venv/bin/activate
python server.py
```

(That process will sit waiting on stdin; Ctrl+C to stop.)

---

## Security note

Default keys match the local MANTIS README for development.  
For shared or production use, put real secrets only in `.env` / Cursor `env`, and do not commit them.
