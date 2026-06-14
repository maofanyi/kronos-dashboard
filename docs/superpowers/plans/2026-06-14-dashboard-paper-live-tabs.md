# Dashboard Paper/Live Tab Isolation Plan

Date: 2026-06-14
Branch: `codex/live-trading-safety-dashboard`

## Goal

Before the 4-6 hour guarded live soak, split the dashboard's trading monitor into two clearly separated views:

- `Live Real`: real CLOB funding, real-order ledger, live soak process, real order lifecycle, and live safety gates.
- `Paper Monitor`: paper runner state, paper balance/equity curve, simulated completed trades, pending queue, and strategy diagnostics.

No real orders are submitted by this dashboard change.

## Implementation Notes

- Add backend `/api/live-safety` fields:
  - `live_real`: `live_real_orders.json`, live soak report, live soak process runtime, real-order risk summary.
  - `paper_monitor`: paper checkpoint/ledger, paper runner runtime, paper balance/orders/today summary.
- Keep the legacy top-level `risk` and `today` fields for compatibility, but make the UI use the separated views for primary monitoring.
- Add process runtime detection for:
  - `run_prediction_bound_live_soak.py`
  - `run_paper_aligned_prod.py`
- Make the frontend default to `Live Real`.
- Keep `Completed Trades` and `Pending Queue` out of the `Live Real` view.
- Put the paper equity curve next to the BTC 5m market chart inside the `Paper Monitor` view.
- Add a test fixture that disables the live RTDS worker during tests so BTC live cache assertions are deterministic.

## Verification

- `python -m pytest tests -q`
- `npm run build`
- Browser check against the built Flask page on a temporary port:
  - `Live Real` shows BTC chart, live soak process, live real orders, trading status, live diagnostics.
  - `Live Real` does not show paper completed/pending tables.
  - `Paper Monitor` shows BTC chart, equity curve, paper runtime, completed trades, pending queue.
  - No horizontal overflow at 1280x720.
