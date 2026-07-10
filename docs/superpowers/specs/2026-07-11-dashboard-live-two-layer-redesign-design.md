# Dashboard Live Two-Layer Redesign Design

## Goal

Rebuild the Dashboard Live page as a two-layer trading console. The default layer must answer the questions needed for live supervision within one viewport, while operational diagnostics and raw records remain available on demand without dominating the page.

## Approved Direction

Use a two-layer layout:

1. **Trading cockpit:** concise account, risk, prediction, market, PnL, and active-order information.
2. **Operations detail:** process health, risk rules, account activity, ledger records, readiness checks, and raw diagnostics in collapsed sections.

The redesign applies to both `Live Real` and `Paper Monitor`, with live trading receiving the richer account and order views.

## Safety And Scope

- Do not change prediction, settlement, sizing, risk, order submission, cancellation, repost, or mutation behavior.
- Do not modify any trading profile or live environment switch.
- Reuse the existing read-only Dashboard APIs and their current financial definitions.
- Do not invent missing PnL, win-rate, order, or account values. Missing and stale data must remain visibly marked.
- Keep the Strategies page and its comparison semantics out of scope.
- Do not introduce new frontend dependencies.
- A new read-only Dashboard calendar API is allowed. It must only read existing ledgers and must not call any order-mutation path.

## Information Architecture

### Layer 1: Trading Cockpit

The default view contains four areas in this order.

#### 1. Mode And Alert Bar

- Keep the `Live Real / Paper Monitor` selector.
- Show one authoritative mode badge: live enabled, live paused/locked, or simulation.
- Show blocking risk alerts directly below the selector.
- Do not repeat the same runtime or risk state in multiple cards.

#### 2. Decision Summary

Replace the current eight equal-weight KPI cards with six compact metrics:

- Account equity / CLOB portfolio value.
- Today realized PnL, with settled count.
- Total settled PnL and win rate in one result metric.
- Remaining daily risk budget, with the configured loss limit in secondary text.
- Active exposure: positions, open orders, and pending orders.
- Latest decision: action, side, market window, and time to settlement.

The summary must preserve full values on mobile. Labels or values may wrap, but important numbers must never be truncated.

#### 3. Core Charts

- BTC market chart is the primary chart.
- Equity history is the secondary chart.
- Desktop uses a responsive two-column layout; mobile stacks the charts.
- The two charts must begin inside the first desktop viewport under normal 1440 x 900 usage.
- On 390 x 844 mobile, the BTC chart heading and current/target values must be visible without more than one initial screen of scrolling.

#### 4. Current Action

- Show the latest prediction and current CLOB order state in a compact action strip.
- When there is no current order, show an explicit neutral empty state instead of a large empty table.
- Show an execution funnel summary: submitted, filled, no-fill, settled.

### Layer 2: Operations Detail

Place the following in independently collapsible sections, closed by default:

1. **Trading health:** merge Live Formal Process, Risk Rules, and Trading Status summaries. The collapsed header shows runtime, order sync, risk state, and warning count.
2. **Account and positions:** recent Polymarket activity, active positions, and redeemable positions. Show counts in the collapsed header and limit the first expanded view to recent records.
3. **Order ledger:** grouped live-order attempts and settlement records. Desktop may use a table; mobile must use stacked records or a contained horizontal scroller that cannot expand the document width.
4. **Diagnostics:** retain readiness, report freshness, market-data status, CLOB read-only audit, first-order rail, and recent raw signals.

If a detail section contains a warning or error, its collapsed header must show that state. Critical blockers remain visible in Layer 1 even when every detail section is closed.

## Chart Design

### BTC Market Chart

- Keep the existing market-data source and update cadence.
- Emphasize current price, target price, signed gap, direction, and countdown.
- Keep chart height stable: approximately 300 px on desktop and 220-240 px on mobile.
- Move recent settled outcomes from a large side column into a compact horizontal result strip.
- Use color only for semantic direction, result, warning, and stale states.

### Equity History

- Remove the nested `Start / Current / Move` cards.
- Put current equity, total PnL, and settled count in the panel header.
- Add a readable time axis when timestamps are available. If the API only supplies numeric points, label the view as the current ledger sequence rather than implying wall-clock time.
- Preserve the zero/reference line and add drawdown emphasis when it can be derived from the existing series.
- Do not add range controls that the existing data cannot honestly support.

### Seven-Day Result View

- Replace seven individual PnL cards with one compact seven-bar or heat-strip visualization.
- Show date, PnL, and W/L details through direct labels or hover/focus details.
- Keep the seven-day total and settled count in the section header.

### Monthly PnL Calendar

- Add a natural-month calendar below the compact seven-day result view.
- Default to the current trading month and provide previous/next month controls plus a month picker.
- Disable navigation beyond the current month or before the earliest month present in the selected ledger.
- Each calendar day shows settled PnL and settled-order count. Color intensity communicates profit or loss magnitude; zero and no-data days remain neutral and visually distinct.
- The month header shows total settled PnL, settled count, wins, losses, and win rate.
- Selecting a populated day opens its contributing settled orders in a right-side drawer on desktop and an inline/bottom detail region on mobile.
- Each daily order row shows settlement time, market, direction, filled size, average fill price, realized PnL, result, and settlement source.
- Empty days do not request order details and show a neutral `No settled orders` state.
- Keyboard users can focus and select calendar days. Every color-coded day also includes a text or accessible-label result.

The calendar uses natural calendar months in the configured Dashboard trading-day timezone. It is not a rolling 30-day view.

## Language And Visual Style

- Use Chinese for primary UI labels and explanatory copy.
- Retain established technical terms where clearer: `PnL`, `CLOB`, `Chainlink`, `Live`, and strategy names.
- Reduce uppercase microcopy and excessive letter spacing.
- Use full-width unframed sections for major page regions. Cards are reserved for compact repeated metrics and records; do not nest decorative cards.
- Use emerald only for healthy/profitable states, rose for losses/blockers, amber for warnings/staleness, and neutral zinc for static information.
- Use Lucide icons already included in the project. Icon-only controls require tooltips and accessible labels.

## Responsive Rules

- The page must have no document-level horizontal scrolling at 390, 768, 1024, or 1440 px widths.
- KPI values and operational states must not use truncation where it can hide meaning.
- Tables with more than five columns must transform into mobile records or be contained inside an explicitly bounded horizontal scroller.
- Fixed-format chart and metric areas must have stable dimensions so loading and updates do not shift the layout.
- Collapsible section headers must remain readable without relying on hover.

## Data Flow And Polling

- Keep the BTC chart on its existing approximately 3-second cadence.
- Keep lightweight trading status and active-order summaries on a 5-second cadence.
- Signal statistics may remain on a 10-second cadence.
- Account activity and detailed safety data use a 30-second cadence.
- Detailed diagnostics should not create additional high-volume requests while collapsed.
- Consolidate the Live page onto one `live-intel` payload size and derive compact/detail views client-side; do not poll both `limit=80` and `limit=260` for the same visible page.
- Display a shared `last updated` indicator and stale warning so independently refreshed payloads do not appear to be one atomic snapshot.
- Monthly calendar data is fetched on initial display and when the user changes month. It is cached by source and month and is not part of the periodic Live polling loop.
- Daily order detail is fetched only when the user selects a populated day and is cached by source and date.

## Monthly Calendar API

Add two read-only endpoints backed by the complete selected ledger rather than the 60-row `order_records` summary.

### Month Summary

`GET /api/live-pnl-calendar?source=live_real&month=YYYY-MM`

The response contains:

- `source`, `month`, `day_tz`, `start_date`, and `end_date`.
- `available_months` in ascending `YYYY-MM` order.
- `total_pnl_usdc`, `settled`, `wins`, `losses`, and `win_rate`.
- `days`, containing one entry for every natural date in the requested month with `date`, `pnl_usdc`, `settled`, `wins`, and `losses`.

Invalid month values return HTTP 400. A valid month with no ledger records returns a complete zero-valued month and an empty-data indicator rather than HTTP 404.

### Daily Settled Orders

`GET /api/live-pnl-calendar/orders?source=live_real&date=YYYY-MM-DD`

The response contains `source`, `date`, `day_tz`, day totals, and `orders`. Each order exposes only dashboard-safe fields required by the UI: signal/order identifier, market slug, direction/outcome, settlement timestamp, filled size, average fill price, realized PnL, won/lost result, status, and settlement source.

Both endpoints must use the same existing helpers and inclusion rules as live daily and weekly PnL: `_is_equity_settled_record`, `_record_ts`, `_dashboard_day_key`, `_record_pnl`, and `_record_won`. The sum of returned daily-order PnL must equal both the selected day total and that day's month-summary PnL. Repost attempts or reconciliation records must not be double counted.

The same contract may serve `paper_monitor` when its ledger exists. Unsupported sources return HTTP 400 and never silently fall back to live data.

## Component Boundaries

Split the current monolithic Live page by responsibility while retaining existing data types and helper behavior:

- `Live.tsx`: data orchestration, tab selection, and top-level error states.
- `LiveCockpitSummary.tsx`: six decision metrics and critical alert presentation.
- `LiveMarketSection.tsx`: BTC chart, equity history, and seven-day result strip.
- `LiveMonthlyPnlCalendar.tsx`: month navigation, daily PnL grid, and selected-day detail presentation.
- `LiveCurrentAction.tsx`: latest prediction, active-order state, and execution funnel.
- `LiveOperationsDetails.tsx`: collapsed health, account, ledger, and diagnostics groups.
- Shared compact metric and collapsible-section primitives remain local to the Live feature unless another page already uses an equivalent component.

The split changes no existing API schema. The monthly calendar is provided through the new isolated read-only endpoints described above.

## Loading, Missing, And Error States

- Loading placeholders preserve final dimensions.
- Missing values render as `--` with a short source/status explanation.
- Stale data uses amber and includes its age.
- API failures remain local to the affected section; the rest of the cockpit stays usable.
- A critical safety blocker always overrides neutral or healthy presentation.

## Verification

- Add Playwright coverage for the default collapsed cockpit, expansion of each operations section, and critical-alert visibility.
- Add a 390 x 844 test proving `document.documentElement.scrollWidth <= document.documentElement.clientWidth`.
- Add mobile assertions proving the main KPI values are not clipped and the BTC section is reachable within the first screen of scrolling.
- Add desktop visual checks at 1440 x 900 confirming both primary chart headings are visible in the first viewport.
- Add API tests for month boundaries, leap years, invalid input, empty months, earliest/latest available month, and the configured `Asia/Shanghai` trading-day boundary.
- Add an API invariant test proving each day's order-detail PnL equals its month-summary PnL without duplicate repost attempts.
- Add Playwright coverage for changing months, selecting a profitable/loss/empty day, opening daily orders, keyboard selection, and the mobile detail layout.
- Verify empty, stale, warning, and populated API fixtures.
- Run `npm run build` in `web`.
- Run the existing dashboard Python tests relevant to live safety and status payloads.
- Perform an in-app browser review at desktop and mobile sizes after the automated checks pass.

## Success Criteria

- A user can determine live mode, account result, risk state, latest action, and active exposure without opening details.
- The default page is materially shorter and no longer repeats runtime, risk, and order summaries across multiple visible panels.
- Charts explain current market and account movement without duplicated metric cards.
- Users can switch across every ledger-backed historical month and reconcile any populated day to its settled orders.
- Mobile has no page-level overflow and never truncates a critical amount or status.
- The redesign performs no order mutation and changes no trading configuration.
