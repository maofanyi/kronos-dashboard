# AGENTS.md

This repository contains the Kronos dashboard API and web UI. On the trading
machine, Codex should treat this as a read-only monitoring service for the
Kronos runtime data.

## First Read

Before changing or running anything, inspect:

- `api/server.py`
- `start.bat`
- `requirements.txt`
- `web/package.json`
- relevant tests under `tests/`

Use PowerShell examples on Windows. Prefer `rg` for search.

## Branch

The trading machine should normally run:

```powershell
git fetch origin
git checkout codex/live-trading-safety-dashboard
git pull --ff-only
```

The current dashboard release branch is expected to contain commit `d2eb06f`.

## Safety Rules

- The dashboard is read-only monitoring. Do not submit, cancel, or mutate orders.
- Do not print secrets, API keys, tokens, or `.env` contents.
- Do not edit local credential or machine-specific config files unless the user
  explicitly asks for that exact edit.
- If a requested action can affect a shared trading account, stop and ask first.
- Do not run trading loops from this repository.

## Setup

Python:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Web:

```powershell
cd web
npm install
cd ..
```

If dependencies already exist, check first and avoid unnecessary reinstalls.

## Verification

Run the focused dashboard safety tests:

```powershell
python -m pytest tests/test_live_safety_readiness.py -q
```

Build the production web assets served by the Flask API:

```powershell
cd web
npm run build
cd ..
```

The build writes static files into `api/static/`.

## Start Dashboard

The default API host and port are `0.0.0.0:8090`, controlled by
`DASHBOARD_HOST` and `DASHBOARD_PORT`.

Start the local sync service plus API:

```powershell
start.bat
```

Or start only the API after the web build:

```powershell
python api\server.py
```

Then open:

```text
http://<trading-machine-lan-ip>:8090
```

## Runtime Data

The dashboard reads reports, checkpoints, ledgers, and logs produced by the
trading runtime. If panels are empty or stale, inspect configured paths and
report freshness before changing code.

Common environment variables include:

- `DASHBOARD_PORT`
- `DASHBOARD_HOST`
- `DASHBOARD_DB_SOURCE`
- `DASHBOARD_RUN_SOURCE`
- `DASHBOARD_LIVE_REAL_LEDGER`
- `DASHBOARD_POLYMARKET_ACCOUNT_ACTIVITY_REPORT`

Do not invent missing production data. Report which file or source is missing.
