import {
  Activity,
  AlertTriangle,
  ArrowDownRight,
  ArrowUpRight,
  CheckCircle2,
  ChevronDown,
  CircleDollarSign,
  Clock3,
  ListChecks,
  Lock,
  RadioTower,
  Target,
  Wallet,
} from "lucide-react";
import { Fragment, type ReactNode, useId, useMemo, useState } from "react";
import { usePolling } from "../hooks/usePolling";
import LiveCockpitSummary, { type CockpitMetric } from "./live/LiveCockpitSummary";
import LiveCurrentAction from "./live/LiveCurrentAction";
import LiveMarketSection from "./live/LiveMarketSection";
import LiveMonthlyPnlCalendar from "./live/LiveMonthlyPnlCalendar";
import LiveOperationsDetails from "./live/LiveOperationsDetails";

interface StatusData {
  balance: number;
  trades_count: number;
  wr: number;
  cooldown_left: number;
}

interface SignalStats {
  total: number;
  passed: number;
  blocked: number;
  pass_rate: number;
}

interface EventItem {
  id: number;
  kline_n: number;
  action: string;
  dir5: string;
  dir4: string;
  regime: string;
  filt_passed: number;
  reason: string;
  details?: string;
  created_at?: string;
}

interface TradeItem {
  id: number;
  pnl: number;
  won: number;
  regime: string;
  direction: string;
  size: number;
  entry_bar: number | string;
  settle_bar: number | string;
  details?: string;
  created_at?: string;
}

type EquitySummary = {
  points: number[];
  start: number;
  current: number;
  pnl_usdc: number;
  settled: number;
  source?: string;
};

type WeeklyPnlDay = {
  date: string;
  pnl_usdc: number;
  settled: number;
  wins: number;
  losses: number;
};

type WeeklyPnlCalendar = {
  start_date: string;
  end_date: string;
  days: WeeklyPnlDay[];
  total_pnl_usdc: number;
  settled: number;
  wins: number;
  losses: number;
  win_rate: number;
  source?: string;
};

type SettledStats = {
  settled: number;
  wins: number;
  losses: number;
  win_rate: number;
};

type SignalDetails = {
  action?: string;
  passed?: boolean;
  side?: string | null;
  reason?: string;
  reason_code?: string;
  failure_codes?: string[];
  p5_up?: number;
  p1_up?: number;
  p4_up?: number;
  long_score?: number;
  short_score?: number;
  created_at?: string;
  settled_at?: string;
  ts?: string;
  close?: number;
  balance?: number;
  entry_ts?: string;
  settle_ts?: string;
  market_slug?: string;
  maker_price?: number;
  max_price?: number;
  execution_price?: number;
  execution_price_tier?: string;
  execution_status?: string;
  execution_order_id?: string;
  execution_filled_size?: number;
  order_chain_count?: number;
  order_chain?: {
    order_id?: string;
    repost_parent_order_id?: string;
    status?: string;
    execution_result?: string;
    price?: number;
    price_tier?: string;
    size?: number;
    filled_size?: number;
    remaining_size?: number;
    created_at?: string;
    updated_at?: string;
  }[];
  entry_close?: number;
  settle_close?: number;
};

interface LiveIntel {
  health: {
    run_source?: string;
    state: "ok" | "warning";
    warnings: string[];
    checkpoint_age_seconds: number | null;
    latest_event_age_seconds: number | null;
    pending_count: number;
    open_order_count: number;
    trades_count: number;
    stale_pending_count: number;
    duplicated_pending_count: number;
  };
  funnel: Record<string, number>;
  maker: {
    open_orders: unknown[];
    pending_positions: unknown[];
    rejected_orders: Array<{ reason?: string; created_at?: string }>;
    avg_fill_delay_bars: number;
  };
  maker_quality: {
    summary?: {
      watch_count?: number;
      avg_buy_one_rate?: number;
      avg_target_price?: number;
      total_reposts?: number;
      total_blocks?: number;
      api_error_count?: number;
    };
  };
  order_lifecycle: {
    counts?: Record<string, number>;
    anomalies?: unknown[];
    recent?: Array<Record<string, unknown>>;
  };
  risk: {
    max_open_orders?: number;
    max_open_rejections_count?: number;
    missed_trades?: unknown[];
  };
  issues: {
    missing_orders: unknown[];
    inconsistent_events: unknown[];
    stale_pending: unknown[];
    duplicated_pending: unknown[];
    log_errors: Array<{ file: string; line: string }>;
  };
  logs: {
    out_log: string;
    err_log: string;
  };
}

interface PolymarketAccountActivityRow {
  type?: string;
  side?: string;
  outcome?: string;
  price?: number | null;
  size?: number | null;
  usdc_size?: number | null;
  slug?: string;
  event_slug?: string;
  title?: string;
  timestamp?: number | string | null;
  timestamp_iso?: string | null;
  transaction_hash?: string;
}

interface PolymarketAccountPosition {
  slug?: string;
  event_slug?: string;
  title?: string;
  outcome?: string;
  size?: number | null;
  avg_price?: number | null;
  current_value?: number | null;
  cash_pnl?: number | null;
  realized_pnl?: number | null;
  cur_price?: number | null;
  redeemable?: boolean;
  mergeable?: boolean;
}

interface PolymarketAccountActivity {
  available: boolean;
  ok: boolean;
  source?: string;
  report: string;
  report_mtime?: string | null;
  report_age_seconds?: number | null;
  created_at?: string | null;
  user?: string | null;
  reason?: string | null;
  summary: {
    activity_count: number;
    positions_count: number;
    active_positions_count?: number;
    redeemable_positions_count?: number;
    trade_count: number;
    redeem_count: number;
  };
  portfolio?: {
    cash_balance_usdc?: number | null;
    positions_value_usdc?: number | null;
    total_value_usdc?: number | null;
    positions_count?: number;
    source?: string;
  };
  recent_activity: PolymarketAccountActivityRow[];
  positions: PolymarketAccountPosition[];
  redeemable_positions?: PolymarketAccountPosition[];
}

interface LiveSafety {
  mode: string;
  run_source: string;
  source_label?: string;
  real_orders_enabled: boolean;
  kill_switch: { state: string };
  clob: {
    authenticated: boolean;
    account_read_ok: boolean;
    allowance_read_ok: boolean;
    open_orders_read_ok: boolean;
  };
  clob_readonly?: {
    available: boolean;
    ready: boolean;
    status_reason?: string;
    next_action?: string;
    report: string;
    report_source?: string;
    report_mtime?: string | null;
    report_age_seconds?: number | null;
    fresh: boolean;
    max_age_seconds: number;
    authenticated: boolean;
    account_read_ok: boolean;
    allowance_read_ok: boolean;
    open_orders_read_ok: boolean;
    open_orders_count: number;
    open_orders?: Array<{
      order_id?: string;
      market?: string;
      asset_id?: string;
      token_id?: string;
      side?: string;
      outcome?: string;
      price?: number | null;
      original_size?: number | null;
      matched_size?: number | null;
      remaining_size?: number | null;
      status?: string;
      created_at?: string;
      expiration?: string;
    }>;
    balance?: number | null;
    min_allowance?: number | null;
    allowance_count?: number | null;
    min_allowance_spender?: string;
    market_probe_count: number;
    quote_executable_count: number;
    quote_executable_rate: number;
    quote_probes?: Array<{
      direction?: string;
      ok?: boolean;
      quote_executable?: boolean;
      best_bid?: number | null;
      best_ask?: number | null;
      price?: number | null;
      token_id?: string;
      reason?: string;
      error?: string;
    }>;
    funding_ready?: boolean | null;
    balance_shortfall_usdc?: number | null;
    allowance_shortfall_usdc?: number | null;
    blockers?: string[];
  };
  funding: {
    balance_ok: boolean;
    allowance_ok: boolean;
    balance?: number;
    allowance_count?: number;
    min_allowance?: number;
    min_allowance_spender?: string;
    balance_expected?: string;
    allowance_expected?: string;
    required_min_balance_usdc?: number;
    required_smoke_notional_usdc?: number;
    required_min_allowance_usdc?: number;
    balance_shortfall_usdc?: number;
    smoke_notional_shortfall_usdc?: number;
    allowance_shortfall_usdc?: number;
    funding_ready?: boolean;
  };
  reports: {
    allowance: string;
    execution: string;
    live_gate?: string;
    live_preflight?: string;
  };
  report_refresh?: {
    ready: boolean;
    total: number;
    missing_count: number;
    stale_count: number;
    blocked_count: number;
    items: Array<{
      key: string;
      label: string;
      report: string;
      available: boolean;
      ok: boolean;
      fresh: boolean;
      age_seconds?: number | null;
      max_age_seconds?: number | null;
      status: string;
      next_action: string;
      blockers?: string[];
    }>;
  };
  legacy_report_refresh?: LiveSafety["report_refresh"];
  market_data?: {
    ready: boolean;
    price?: number | null;
    timestamp?: string;
    source: string;
    status: string;
    price_age_seconds?: number | null;
    received_at?: number | null;
    received_age_seconds?: number | null;
    max_price_age_seconds: number;
    max_received_age_seconds: number;
    next_action: string;
    error?: string | null;
  };
  dryrun?: {
    ledger: string;
    ledger_count: number;
    would_place_count: number;
    blocked_count: number;
    submitted_count: number;
    latest?: Record<string, unknown> | null;
  };
  live_gate?: {
    report: string;
    available: boolean;
    ok: boolean;
    ready_for_live_smoke: boolean;
    blockers: string[];
    market_probe_count: number;
    quote_executable_count: number;
  };
  preflight_chain?: {
    report: string;
    available: boolean;
    ok: boolean;
    submitted: boolean;
    created_at: string;
    age_seconds?: number | null;
    fresh: boolean;
    max_age_seconds: number;
    blockers: string[];
    gate_ready: boolean;
    smoke_mode: string;
    open_orders: number;
    settled: number;
    risk_ok: boolean;
    components?: Record<string, unknown>;
  };
  checklist?: ChecklistItem[];
  readiness_summary?: {
    ready: boolean;
    total: number;
    passed: number;
    blockers: number;
    critical_blockers: number;
    funding_blockers: number;
    risk_blockers: number;
    top_blockers?: Array<{
      key?: string;
      label?: string;
      severity?: string;
      value?: string | number | null;
      expected?: string | null;
      action?: string;
    }>;
    by_severity?: Record<string, number>;
  };
  operator_summary?: {
    ready: boolean;
    status: string;
    current_stage_key?: string;
    current_stage_label?: string;
    next_action?: string;
    primary_blocker_key?: string | null;
    primary_blocker?: string | null;
    readiness_passed: number;
    readiness_total: number;
    critical_blockers: number;
    funding_blockers: number;
    risk_blockers: number;
  };
  first_order_rail?: {
    current_key: string;
    ready_for_manual_confirmation: boolean;
    stages: Array<{
      key: string;
      label: string;
      status: string;
      action: string;
      blocker_keys?: string[];
      blockers?: string[];
      requires_confirmation?: boolean;
    }>;
  };
  live_trading_status?: {
    ok: boolean;
    status: string;
    label: string;
    next_action?: string;
    blockers?: Array<{
      key: string;
      label: string;
      severity?: string;
      value?: unknown;
      expected?: unknown;
      action?: string;
    }>;
    formal?: {
      available?: boolean;
      running?: boolean;
      supervisor_running?: boolean;
      child_running?: boolean;
      fresh?: boolean;
      age_seconds?: number | null;
      max_age_seconds?: number | null;
      latest_action?: string | null;
      latest_reason?: string | null;
      terminal_reason?: string | null;
    };
    sync?: {
      available?: boolean;
      running?: boolean;
      fresh?: boolean;
      ok?: boolean;
      age_seconds?: number | null;
      max_age_seconds?: number | null;
      reconcile_errors?: number | null;
      open_orders?: number | null;
      settlement_source?: string | null;
    };
    risk?: {
      ok?: boolean;
      reason?: string | null;
      blockers?: Array<{ key: string; label: string; value?: unknown; expected?: unknown; action?: string }>;
    };
    pause?: {
      paused?: boolean;
      reason?: string | null;
      age_seconds?: number | null;
    };
    orders?: {
      open_or_pending?: number;
      total?: number;
      filled_size?: number;
    };
  };
  risk?: {
    ok: boolean;
    metrics: {
      day?: string;
      day_tz?: string;
      day_start_utc?: string;
      day_end_utc?: string;
      day_utc: string;
      daily_pnl_usdc: number;
      daily_trades: number;
      wins?: number;
      losses?: number;
      win_rate?: number;
      consecutive_losses: number;
      open_or_pending_orders: number;
    };
    limits: {
      max_daily_loss_usdc: number;
      max_daily_trades: number;
      max_consecutive_losses: number;
      max_open_or_pending_orders: number;
    };
    resilience?: {
      ok: boolean;
      reason?: string;
      failures?: string[];
      warnings?: string[];
      source?: string;
      metrics?: {
        total_trades?: number;
        total_pnl_usdc?: number;
        wins?: number;
        losses?: number;
        win_rate?: number;
        profit_factor?: number | null;
        max_drawdown_usdc?: number;
        max_consecutive_losses?: number;
        same_direction_cooldowns?: Record<string, { losses?: number; active?: boolean; active_until?: string | null }>;
        limits?: {
          max_smoke_drawdown_usdc?: number;
          same_direction_loss_cooldown_count?: number;
          same_direction_loss_cooldown_minutes?: number;
        };
      };
    };
  };
  today?: {
    day?: string;
    day_tz?: string;
    day_start_utc?: string;
    day_end_utc?: string;
    day_utc: string;
    signals: {
      total: number;
      passed: number;
      blocked: number;
      pass_rate: number;
      buy_up: number;
      buy_down: number;
      hold: number;
      other: number;
      top_block_reason?: string;
      top_block_count?: number;
    };
    trades: {
      settled: number;
      wins: number;
      losses: number;
      win_rate: number;
      pnl_usdc: number;
      pending: number;
      open: number;
    };
    risk_usage: {
      daily_loss: number;
      daily_trades: number;
      loss_streak: number;
      open_or_pending: number;
    };
    maker: {
      target_price: number;
      observed_avg_target_price?: number | null;
      buy_one_rate: number;
      blocks: number;
      api_errors: number;
    };
    dryrun: {
      records: number;
      would_place: number;
      blocked: number;
      submitted: number;
      latest_status?: string;
      latest_action?: string;
      latest_block_reason?: string;
      latest_at?: string | null;
      latest_age_seconds?: number | null;
    };
    activity?: {
      latest_signal_at?: string | null;
      latest_signal_age_seconds?: number | null;
      latest_trade_at?: string | null;
      latest_trade_age_seconds?: number | null;
      latest_order_at?: string | null;
      latest_order_age_seconds?: number | null;
      latest_dryrun_at?: string | null;
      latest_dryrun_age_seconds?: number | null;
    };
  };
  live_real?: {
    ledger: string;
    ledger_exists: boolean;
    runtime: RuntimeSummary;
    formal?: {
      available: boolean;
      report: string;
      report_age_seconds?: number | null;
      ok: boolean;
      submitted: boolean;
      terminal_reason: string;
      reason?: string;
      attempts: number;
      latest_action?: string | null;
      latest_reason?: string | null;
      latest_decision_id?: string | null;
      latest_created_at?: string | null;
      decision_bar_ts?: string | null;
      entry_ts?: string | null;
      settle_ts?: string | null;
      target_market_entry_ts?: string | null;
      target_market_settle_ts?: string | null;
      execution_market_shift?: string | null;
    };
    soak: {
      available: boolean;
      report: string;
      report_age_seconds?: number | null;
      ok: boolean;
      submit_enabled: boolean;
      terminal_reason: string;
      attempts: number;
      submitted_count: number;
      started_at?: string | null;
      elapsed_seconds?: number | null;
      latest_action?: string | null;
      latest_reason?: string | null;
      latest_decision_id?: string | null;
      blockers: string[];
    };
    orders: {
      total: number;
      open_or_pending: number;
      settled: number;
      cancelled: number;
      no_fill_cancelled?: number;
      filled_orders?: number;
      filled_size: number;
      status_counts: Record<string, number>;
    };
    stats: SettledStats;
    market_results?: {
      resolved: number;
      wins: number;
      losses: number;
      win_rate: number;
    };
    risk: LiveSafety["risk"];
    risk_resilience?: NonNullable<LiveSafety["risk"]>["resilience"];
    risk_controls?: RiskControls;
    equity: EquitySummary;
    weekly_pnl_calendar?: WeeklyPnlCalendar;
    latest_order?: Record<string, unknown> | null;
    order_records?: LiveOrderRecord[];
    account_activity?: PolymarketAccountActivity;
  };
  paper_monitor?: {
    run_source: string;
    source_label?: string;
    checkpoint: string;
    ledger: string;
    checkpoint_exists: boolean;
    checkpoint_mtime?: string | null;
    checkpoint_age_seconds?: number | null;
    runtime: RuntimeSummary;
    balance?: number | null;
    orders: {
      open: number;
      pending: number;
      settled: number;
      total_trades: number;
    };
    stats: SettledStats;
    signals: SignalStats;
    equity: EquitySummary;
    risk: LiveSafety["risk"];
    today: LiveSafety["today"];
  };
}

type RuntimeSummary = {
  running: boolean;
  matches: Array<{ pid?: number; name?: string; started_at?: string | null; command?: string }>;
  started_at?: string | null;
  uptime_seconds?: number | null;
  patterns?: string[];
  role?: string;
  child_runtime?: RuntimeSummary;
  error?: string;
};

type LiveOrderRecord = {
  order_id?: string | null;
  market_slug?: string | null;
  status?: string;
  exchange_final_status?: string | null;
  risk_excluded?: boolean;
  direction?: string | null;
  token_outcome?: string | null;
  price?: number | null;
  average_fill_price?: number | null;
  size?: number | null;
  filled_size?: number | null;
  remaining_size?: number | null;
  pnl?: number | null;
  won?: boolean | null;
  hypothetical_pnl?: number | null;
  hypothetical_won?: boolean | null;
  hypothetical_pnl_basis?: string | null;
  entry_ts?: string | null;
  settle_ts?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  settled_at?: string | null;
  fill_source?: string | null;
  settlement_source?: string | null;
  market_result_source?: string | null;
  execution_result?: string | null;
  signal_id?: string | null;
  attempts?: number;
  order_chain_count?: number;
  order_chain?: Array<{
    order_id?: string | null;
    repost_parent_order_id?: string | null;
    status?: string | null;
    execution_result?: string | null;
    market_slug?: string | null;
    direction?: string | null;
    side?: string | null;
    price?: number | null;
    price_tier?: string | null;
    size?: number | null;
    filled_size?: number | null;
    remaining_size?: number | null;
    created_at?: string | null;
    entry_ts?: string | null;
    settle_ts?: string | null;
  }>;
};

type RiskControls = {
  source?: string;
  summary?: {
    order_size_shares?: number | null;
    min_price?: number | null;
    active_max_price?: number | null;
    hard_max_price?: number | null;
    max_notional_usdc?: number | null;
    max_daily_loss_usdc?: number | null;
    max_daily_trades?: number | null;
    max_consecutive_losses?: number | null;
    max_open_or_pending_orders?: number | null;
    max_smoke_drawdown_usdc?: number | null;
    same_direction_loss_cooldown_count?: number | null;
    same_direction_loss_cooldown_minutes?: number | null;
    signal_max_age_seconds?: number | null;
    reference_price_source?: string | null;
    execution_market_shift?: string | null;
  };
};

type ChecklistItem = {
  key: string;
  label: string;
  ok: boolean;
  value?: string | number | null;
  expected?: string | null;
  severity?: string;
};

const INITIAL_BALANCE = 500;

const money = (value: number, digits = 2) =>
  `$${value.toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })}`;

const compactAllowance = (value?: number | null, approved = false) => {
  if (value == null) return "-";
  if (approved && value >= 1_000_000) return "Approved";
  if (value >= 1_000_000) return "> $1M";
  return money(value);
};

const displayChecklistValue = (item: ChecklistItem) => {
  const key = item.key.toLowerCase();
  if (key.includes("allowance") && typeof item.value === "number") {
    return compactAllowance(item.value, item.ok);
  }
  return String(item.value ?? "-");
};

const signedMoney = (value: number) => `${value >= 0 ? "+" : ""}${money(value)}`;

const percent = (value?: number | null, digits = 1) =>
  value == null ? "-" : `${(value * 100).toFixed(digits)}%`;

const score = (value?: number) => (value == null ? "-" : value.toFixed(1));

const compactId = (value?: string) => {
  if (!value) return "-";
  if (value.length <= 14) return value;
  return `${value.slice(0, 6)}...${value.slice(-4)}`;
};

const orderSize = (value?: number | null) => {
  if (value == null) return "-";
  return Number.isInteger(value) ? value.toFixed(0) : value.toFixed(4);
};

const orderPrice = (value?: number | null) => (value == null ? "-" : value.toFixed(3));
const orderValue = (value?: number | null) => (value == null ? "-" : signedMoney(value));

const liveOrderStatusTone = (status?: string) => {
  const normalized = String(status || "").toUpperCase();
  if (normalized === "SETTLED" || normalized === "FILLED") return "border-emerald-500/30 bg-emerald-500/10 text-emerald-300";
  if (normalized.includes("OPEN") || normalized.includes("PENDING") || normalized.includes("PARTIAL")) return "border-amber-500/30 bg-amber-500/10 text-amber-300";
  if (normalized.includes("CANCEL") || normalized === "NO_FILL") return "border-zinc-700 bg-zinc-900 text-zinc-400";
  return "border-zinc-800 bg-zinc-950 text-zinc-400";
};

const liveOrderGroup = (order: LiveOrderRecord) => {
  const status = String(order.status || "").toUpperCase();
  const filled = order.filled_size ?? 0;
  if (status === "SETTLED") return "Settled";
  if (filled > 0 || status.includes("OPEN") || status.includes("PENDING") || status.includes("PARTIAL") || status === "FILLED") return "Position";
  if (status.includes("CANCEL") || status === "NO_FILL") return "Closed";
  return "Record";
};

const localTime = (value?: string | number) => {
  if (value == null) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
};

const shortDateTime = (value?: string | number) => {
  if (value == null) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
};

const ageLabel = (seconds?: number | null) => {
  if (seconds == null) return "-";
  if (seconds < 90) return `${Math.round(seconds)}s`;
  return `${Math.round(seconds / 60)}m`;
};

const ageBudgetLabel = (age?: number | null, max?: number | null) =>
  `${ageLabel(age)} / ${ageLabel(max)}`;

const sortValue = (value?: number | string) => {
  if (value == null) return 0;
  if (typeof value === "number") return value;
  if (/^-?\d+(\.\d+)?$/.test(value)) return Number(value);
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? 0 : date.getTime();
};

const timeOrBar = (value?: number | string) => {
  if (value == null) return "-";
  if (typeof value === "number" || /^-?\d+(\.\d+)?$/.test(String(value))) return `#${value}`;
  return localTime(String(value));
};

const rangeLabel = (entry?: number | string, settle?: number | string) =>
  `${timeOrBar(entry)} -> ${timeOrBar(settle)}`;

const pendingStatusLabel = (settle?: number | string) => {
  const settleMs = sortValue(settle);
  if (!settleMs) return "awaiting settlement";
  const seconds = Math.round((settleMs - Date.now()) / 1000);
  if (seconds <= 0) return "awaiting refresh";
  return `settles in ${ageLabel(seconds)}`;
};

const tradeRecordedAt = (trade: TradeItem, details: SignalDetails | null) =>
  details?.settled_at || details?.created_at || trade.created_at;

const parseJson = <T,>(text?: string): T | null => {
  if (!text) return null;
  try {
    return JSON.parse(text) as T;
  } catch {
    return null;
  }
};

const actionTone = (action?: string) => {
  if (action?.includes("BUY")) return "text-emerald-300";
  if (action === "HOLD") return "text-zinc-400";
  return "text-sky-300";
};

const directionLabel = (direction?: string) => {
  const normalized = direction?.toUpperCase();
  if (normalized === "LONG" || normalized === "UP" || normalized === "BUY_UP") return "UP";
  if (normalized === "SHORT" || normalized === "DOWN" || normalized === "BUY_DOWN") return "DOWN";
  return direction || "-";
};

const directionIcon = (direction?: string) => {
  const normalized = direction?.toUpperCase();
  if (normalized === "LONG" || normalized === "UP" || normalized === "BUY_UP") return <ArrowUpRight className="h-3.5 w-3.5" />;
  if (normalized === "SHORT" || normalized === "DOWN" || normalized === "BUY_DOWN") return <ArrowDownRight className="h-3.5 w-3.5" />;
  return null;
};

function Panel({ title, sub, children, right }: { title: string; sub?: string; children: ReactNode; right?: ReactNode }) {
  return (
    <section className="w-full max-w-full min-w-0 overflow-hidden rounded-md border border-zinc-800 bg-zinc-950/70">
      <div className="flex items-center justify-between gap-3 border-b border-zinc-800 px-4 py-3">
        <div>
          <h2 className="text-sm font-semibold text-zinc-100">{title}</h2>
          {sub && <p className="mt-0.5 text-xs text-zinc-500">{sub}</p>}
        </div>
        {right}
      </div>
      {children}
    </section>
  );
}

function CollapsiblePanel({
  title,
  sub,
  children,
  right,
  summary,
}: {
  title: string;
  sub?: string;
  children: ReactNode;
  right?: ReactNode;
  summary?: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const panelId = useId();
  return (
    <section className="w-full max-w-full min-w-0 overflow-hidden rounded-md border border-zinc-800 bg-zinc-950/70">
      <button
        type="button"
        className="flex w-full items-center justify-between gap-3 px-4 py-2.5 text-left"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        aria-controls={panelId}
      >
        <div className="min-w-0">
          <div className="flex min-w-0 items-center gap-2">
            <h2 className="truncate text-sm font-semibold text-zinc-100">{title}</h2>
            <ChevronDown className={`h-4 w-4 shrink-0 text-zinc-600 transition-transform ${open ? "rotate-180" : ""}`} />
          </div>
          {sub && <p className="mt-0.5 truncate text-xs text-zinc-500">{sub}</p>}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {right}
        </div>
      </button>
      {!open && summary && <div className="hidden border-t border-zinc-900 px-4 py-2 md:block">{summary}</div>}
      {open && <div id={panelId} className="border-t border-zinc-800">{children}</div>}
    </section>
  );
}

function StatCard({
  label,
  value,
  sub,
  icon: Icon,
  tone = "text-zinc-100",
}: {
  label: string;
  value: string;
  sub?: string;
  icon: typeof Activity;
  tone?: string;
}) {
  return (
    <div className="rounded-md border border-zinc-800 bg-zinc-950/70 p-4 min-w-0">
      <div className="flex items-center justify-between gap-3">
        <span className="truncate text-xs uppercase tracking-[0.16em] text-zinc-500">{label}</span>
        <Icon className="h-4 w-4 text-zinc-500" />
      </div>
      <div className={`mt-3 truncate font-mono text-2xl font-semibold ${tone}`}>{value}</div>
      {sub && <div className="mt-1 truncate text-xs text-zinc-500">{sub}</div>}
    </div>
  );
}

function StatusPill({ ok, label, danger = false }: { ok: boolean; label: string; danger?: boolean }) {
  const badTone = danger
    ? "border-rose-500/30 bg-rose-500/10 text-rose-300"
    : "border-amber-500/25 bg-amber-500/10 text-amber-300";
  return (
    <span className={`inline-flex items-center gap-1 rounded border px-2 py-1 text-xs ${ok ? "border-emerald-500/25 bg-emerald-500/10 text-emerald-300" : badTone}`}>
      {ok ? <CheckCircle2 className="h-3.5 w-3.5" /> : <AlertTriangle className="h-3.5 w-3.5" />}
      {label}
    </span>
  );
}

function SafetyStrip({ safety, health }: { safety?: LiveSafety | null; health?: LiveIntel["health"] | null }) {
  const liveEnabled = safety?.real_orders_enabled === true;
  const source = safety?.source_label ?? safety?.run_source ?? health?.run_source ?? "-";
  const dryrunClean = liveEnabled || (safety?.dryrun?.submitted_count ?? 0) === 0;
  const gateReady = safety?.live_gate?.ready_for_live_smoke === true;
  const preflightOk = safety?.preflight_chain?.ok === true;
  const preflightFresh = safety?.preflight_chain?.fresh === true;
  const preflightSubmitted = safety?.preflight_chain?.submitted === true;
  const preflightBlockers = safety?.preflight_chain?.blockers?.length ?? 0;
  const preflightReady = liveEnabled || (preflightOk && preflightFresh && !preflightSubmitted);
  const preflightAge = ageLabel(safety?.preflight_chain?.age_seconds);
  const fundingReady = safety?.funding?.funding_ready === true;
  const readiness = safety?.readiness_summary;
  const ignoredLiveReadinessKeys = new Set([
    "real_orders_locked",
    "dryrun_no_submitted_orders",
    "live_preflight_available",
    "live_preflight_chain_ok",
    "live_preflight_fresh",
    "live_preflight_no_submission",
  ]);
  const topBlockers = (readiness?.top_blockers ?? []).filter((blocker) => (
    !liveEnabled || !ignoredLiveReadinessKeys.has(String(blocker.key ?? ""))
  ));
  const ignoredCriticalBlockers = liveEnabled
    ? (readiness?.top_blockers ?? []).filter((blocker) => (
        blocker.severity === "critical" && ignoredLiveReadinessKeys.has(String(blocker.key ?? ""))
      )).length
    : 0;
  const criticalBlockers = Math.max(0, (readiness?.critical_blockers ?? 0) - ignoredCriticalBlockers);
  const fundingBlockers = readiness?.funding_blockers ?? 0;
  const riskBlockers = readiness?.risk_blockers ?? 0;
  const riskBlocked = riskBlockers > 0 || safety?.risk?.resilience?.ok === false;
  const fundingBalance = safety?.funding?.balance;
  const fundingAllowance = safety?.funding?.min_allowance;
  const fundingAllowanceLabel = compactAllowance(fundingAllowance, safety?.funding?.allowance_ok === true || fundingReady);
  const fundingGap = Math.max(
    safety?.funding?.balance_shortfall_usdc ?? 0,
    safety?.funding?.allowance_shortfall_usdc ?? 0,
  );
  return (
    <CollapsiblePanel
      title="Live Safety"
      sub={source}
      right={<StatusPill ok={health?.state === "ok" && dryrunClean && preflightReady && fundingReady && criticalBlockers === 0 && !riskBlocked} danger={riskBlocked} label={health?.state === "ok" && dryrunClean && preflightReady && fundingReady && criticalBlockers === 0 && !riskBlocked ? "Safe" : riskBlocked ? "Risk blocked" : "Review"} />}
      summary={
        <div className="flex min-w-0 flex-wrap gap-2">
          <StatusPill ok label={liveEnabled ? "Real orders enabled" : "Locked"} />
          <StatusPill ok={health?.state === "ok"} label={health?.state === "ok" ? "Health OK" : "Review"} />
          <StatusPill ok={criticalBlockers === 0} label={`Critical ${criticalBlockers}`} />
          <StatusPill ok={fundingBlockers === 0} label={`Funding ${fundingBlockers}`} />
          <StatusPill ok={!riskBlocked} danger={riskBlocked} label={riskBlocked ? `Risk blocked ${riskBlockers}` : `Risk ${riskBlockers}`} />
          <StatusPill ok={fundingReady} label={fundingReady ? "Funding ready" : `Funding gap ${money(fundingGap)}`} />
          {!liveEnabled && <StatusPill ok={dryrunClean} label={`Dry-run ${safety?.dryrun?.would_place_count ?? 0}/${safety?.dryrun?.ledger_count ?? 0}`} />}
          <StatusPill ok={gateReady} label={gateReady ? "Gate ready" : `${safety?.live_gate?.blockers?.length ?? 0} blockers`} />
          {!liveEnabled && <StatusPill ok={preflightReady} label={preflightReady ? `Preflight ${preflightAge}` : `Preflight ${preflightBlockers}`} />}
          <span className="min-w-0 truncate font-mono text-xs text-zinc-500">{source}</span>
        </div>
      }
    >
      <div className="p-4">
        <div className="flex flex-wrap gap-2">
          <StatusPill ok label={liveEnabled ? "REAL ORDERS ENABLED" : "Real orders locked"} />
          <StatusPill ok={safety?.clob?.authenticated === true} label="CLOB auth" />
          <StatusPill ok={criticalBlockers === 0} label={`Critical blockers ${criticalBlockers}`} />
          <StatusPill ok={fundingBlockers === 0} label={`Funding blockers ${fundingBlockers}`} />
          <StatusPill ok={!riskBlocked} danger={riskBlocked} label={riskBlocked ? `Risk blocked ${riskBlockers}` : `Risk blockers ${riskBlockers}`} />
          <StatusPill ok={safety?.funding?.balance_ok === true} label={`Balance ${fundingBalance == null ? "-" : money(fundingBalance)}`} />
          <StatusPill ok={safety?.funding?.allowance_ok === true} label={`Allowance ${fundingAllowanceLabel}`} />
          <StatusPill ok={fundingReady} label={`Balance Gap ${money(safety?.funding?.balance_shortfall_usdc ?? 0)}`} />
          <StatusPill ok={fundingReady} label={`Allowance Gap ${money(safety?.funding?.allowance_shortfall_usdc ?? 0)}`} />
          {!liveEnabled && <StatusPill ok={dryrunClean} label={`Dry-run submitted ${safety?.dryrun?.submitted_count ?? 0}`} />}
          <StatusPill ok={gateReady} label={`Gate blockers ${safety?.live_gate?.blockers?.length ?? 0}`} />
          {!liveEnabled && <StatusPill ok={preflightReady} label={preflightSubmitted ? "Preflight submitted" : `Preflight ${preflightAge}`} />}
          <StatusPill ok={health?.state === "ok"} label={health?.state === "ok" ? "Health OK" : "Review"} />
        </div>
        {topBlockers.length > 0 && (
          <div className="mt-3 rounded-md border border-zinc-900 bg-black/20 p-3">
            <div className="mb-2 text-[11px] uppercase tracking-[0.14em] text-zinc-600">Top Blockers</div>
            <div className="grid gap-2 md:grid-cols-3">
              {topBlockers.slice(0, 3).map((blocker) => (
                <div key={blocker.key ?? blocker.label} className="rounded border border-amber-500/20 bg-amber-500/5 px-2.5 py-2">
                  <div className="flex items-center justify-between gap-3">
                    <span className="truncate text-sm text-zinc-200">{blocker.label ?? blocker.key}</span>
                    <span className="shrink-0 font-mono text-[11px] uppercase text-amber-300">{blocker.severity ?? "check"}</span>
                  </div>
                  {blocker.expected && <div className="mt-1 truncate text-xs text-zinc-500">expected {blocker.expected}</div>}
                  {blocker.action && <div className="mt-1 truncate text-xs text-zinc-500">next {blocker.action}</div>}
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </CollapsiblePanel>
  );
}

function ReadinessChecklist({ safety, health, intel }: { safety?: LiveSafety | null; health?: LiveIntel["health"] | null; intel?: LiveIntel | null }) {
  const apiItems = safety?.checklist ?? [];
  const healthItems: ChecklistItem[] = [
    {
      key: "checkpoint_fresh",
      label: "Checkpoint fresh",
      ok: (health?.checkpoint_age_seconds ?? 9999) < 600,
      value: ageLabel(health?.checkpoint_age_seconds),
      severity: "runtime",
    },
    {
      key: "events_fresh",
      label: "Events fresh",
      ok: (health?.latest_event_age_seconds ?? 9999) < 600,
      value: ageLabel(health?.latest_event_age_seconds),
      severity: "runtime",
    },
    {
      key: "no_log_errors",
      label: "No log errors",
      ok: (intel?.issues.log_errors.length ?? 0) === 0,
      value: intel?.issues.log_errors.length ?? 0,
      severity: "runtime",
    },
  ];
  const items = [...apiItems, ...healthItems];
  const passed = items.filter((item) => item.ok).length;
  const total = items.length;
  const readyForSmoke = total > 0 && passed === total && safety?.real_orders_enabled === false;

  return (
    <CollapsiblePanel
      title="Live Readiness Checklist"
      sub="read-only gates before the first real maker smoke"
      right={<StatusPill ok={readyForSmoke} label={readyForSmoke ? "Ready" : `${passed}/${total}`} />}
      summary={
        <div className="flex items-center justify-between gap-3 text-xs">
          <span className="text-zinc-500">Checks</span>
          <span className={`font-mono ${readyForSmoke ? "text-emerald-300" : "text-amber-300"}`}>{passed}/{total}</span>
        </div>
      }
    >
      <div className="grid gap-2 p-4 md:grid-cols-2 xl:grid-cols-3">
        {items.map((item) => (
          <div key={item.key} className={`rounded border p-3 ${item.ok ? "border-emerald-500/20 bg-emerald-500/5" : "border-amber-500/20 bg-amber-500/5"}`}>
            <div className="flex items-center justify-between gap-3">
              <div className="flex min-w-0 items-center gap-2">
                {item.ok ? <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-300" /> : <AlertTriangle className="h-4 w-4 shrink-0 text-amber-300" />}
                <span className="truncate text-sm font-medium text-zinc-100">{item.label}</span>
              </div>
              <span className={`shrink-0 font-mono text-xs ${item.ok ? "text-emerald-300" : "text-amber-300"}`}>
                {displayChecklistValue(item)}
              </span>
            </div>
            {item.expected && <div className="mt-2 truncate text-xs text-zinc-500">expected {item.expected}</div>}
            <div className="mt-2 text-[11px] uppercase tracking-[0.14em] text-zinc-600">{item.severity ?? "check"}</div>
          </div>
        ))}
      </div>
    </CollapsiblePanel>
  );
}

function MetricChip({ label, value }: { label: string; value: string }) {
  return (
    <span className="rounded border border-zinc-900 bg-black/20 px-2 py-1 font-mono text-zinc-300">
      <span className="text-zinc-600">{label}</span> {value}
    </span>
  );
}

function conditionRows(details: SignalDetails | null) {
  const failures = new Set(details?.failure_codes ?? []);
  return [
    { side: "LONG", label: "5m long probability", value: percent(details?.p5_up), ok: !failures.has("long_micro_low") },
    { side: "LONG", label: "4h long gate", value: percent(details?.p4_up), ok: !failures.has("long_macro_low") },
    { side: "LONG", label: "1h+4h long score", value: score(details?.long_score), ok: !failures.has("long_score_low") },
    { side: "LONG", label: "Short macro veto", value: failures.has("long_macro_veto") ? "triggered" : "clear", ok: !failures.has("long_macro_veto") },
    { side: "SHORT", label: "5m short probability", value: percent(details?.p5_up == null ? undefined : 1 - details.p5_up), ok: !failures.has("short_micro_low") },
    { side: "SHORT", label: "4h short gate", value: percent(details?.p4_up == null ? undefined : 1 - details.p4_up), ok: !failures.has("short_macro_low") },
    { side: "SHORT", label: "1h+4h short score", value: score(details?.short_score), ok: !failures.has("short_score_low") },
    { side: "SHORT", label: "Long macro veto", value: failures.has("short_macro_veto") ? "triggered" : "clear", ok: !failures.has("short_macro_veto") },
  ];
}

function signalDirection(details: SignalDetails | null) {
  const raw = `${details?.side ?? ""} ${details?.action ?? ""}`.toUpperCase();
  if (raw.includes("SHORT") || raw.includes("DOWN")) return "DOWN";
  if (raw.includes("LONG") || raw.includes("UP")) return "UP";
  return "";
}

function signalProb(details: SignalDetails | null, upProbability?: number) {
  if (upProbability == null) return undefined;
  return signalDirection(details) === "DOWN" ? 1 - upProbability : upProbability;
}

function SignalCard({ event, expanded, onToggle }: { event: EventItem; expanded: boolean; onToggle: () => void }) {
  const details = parseJson<SignalDetails>(event.details);
  const signalArrow = signalDirection(details) === "DOWN" ? "↓" : signalDirection(details) === "UP" ? "↑" : "";
  return (
    <button type="button" onClick={onToggle} className="block w-full border-b border-zinc-900 px-4 py-3 text-left last:border-b-0 hover:bg-zinc-900/50">
      <div className="flex items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-2">
          <ChevronDown className={`h-3.5 w-3.5 shrink-0 text-zinc-600 transition-transform ${expanded ? "rotate-180" : ""}`} />
          <span className="font-mono text-xs text-zinc-500">{details?.ts ? localTime(details.ts) : `#${event.kline_n}`}</span>
          <span className="font-mono text-xs text-zinc-600">{localTime(event.created_at)}</span>
        </div>
        <span className={`font-mono text-xs font-semibold ${actionTone(event.action)}`}>{event.action}</span>
      </div>

      <div className="mt-2 grid grid-cols-4 gap-2 text-xs">
        <MetricChip label={`5m${signalArrow}`} value={percent(signalProb(details, details?.p5_up))} />
        <MetricChip label={`1h${signalArrow}`} value={percent(signalProb(details, details?.p1_up))} />
        <MetricChip label={`4h${signalArrow}`} value={percent(signalProb(details, details?.p4_up))} />
        <span className={event.filt_passed ? "text-emerald-300" : "text-rose-300"}>{event.filt_passed ? "passed" : "blocked"}</span>
      </div>

      <div className="mt-1 truncate text-xs text-zinc-600">{details?.reason ?? event.reason}</div>

      {expanded && (
        <div className="mt-3 space-y-3 rounded border border-zinc-900 bg-black/20 p-3">
          <div className="grid grid-cols-2 gap-2 text-[11px] text-zinc-500">
            <div>close <span className="font-mono text-zinc-300">{details?.close?.toFixed(2) ?? "-"}</span></div>
            <div>balance <span className="font-mono text-zinc-300">{details?.balance?.toFixed(2) ?? "-"}</span></div>
            <div>reason <span className="font-mono text-zinc-300">{details?.reason_code ?? "-"}</span></div>
            <div>side <span className="font-mono text-zinc-300">{details?.side ?? "-"}</span></div>
            <div>entry <span className="font-mono text-zinc-300">{shortDateTime(details?.entry_ts)}</span></div>
            <div>settle <span className="font-mono text-zinc-300">{shortDateTime(details?.settle_ts)}</span></div>
            <div>cap <span className="font-mono text-zinc-300">{details?.max_price?.toFixed(2) ?? "-"}</span></div>
            <div>exec <span className="font-mono text-zinc-300">{details?.execution_price?.toFixed(2) ?? "-"}</span></div>
            <div>tier <span className="font-mono text-zinc-300">{details?.execution_price_tier ?? "-"}</span></div>
            <div>orders <span className="font-mono text-zinc-300">{details?.order_chain_count ?? details?.order_chain?.length ?? 0}</span></div>
          </div>
          {(details?.order_chain?.length ?? 0) > 0 && (
            <div>
              <div className="mb-1.5 text-[11px] uppercase tracking-[0.14em] text-zinc-600">Order Chain</div>
              <div className="space-y-1.5">
                {(details?.order_chain ?? []).map((order) => (
                  <div key={order.order_id ?? `${order.created_at}-${order.price_tier}`} className="grid grid-cols-[68px_64px_58px_1fr] items-center gap-2 rounded border border-zinc-900 bg-zinc-950/70 px-2.5 py-1.5 text-[11px]">
                    <span className="font-mono text-zinc-300">{order.price == null ? "-" : order.price.toFixed(2)}</span>
                    <span className="font-mono text-zinc-500">{order.price_tier ?? "-"}</span>
                    <span className="font-mono text-zinc-400">{order.status ?? "-"}</span>
                    <span className="truncate font-mono text-zinc-600">{order.execution_result || order.order_id || "-"}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
          <div className="grid gap-1.5">
            {conditionRows(details).map((row) => (
              <div key={`${row.side}-${row.label}`} className="flex items-center justify-between gap-3 rounded border border-zinc-900 bg-zinc-950/70 px-2.5 py-1.5">
                <div className="flex min-w-0 items-center gap-2">
                  {row.ok ? <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-emerald-300" /> : <AlertTriangle className="h-3.5 w-3.5 shrink-0 text-rose-300" />}
                  <span className="font-mono text-[11px] text-zinc-500">{row.side}</span>
                  <span className="truncate text-xs text-zinc-300">{row.label}</span>
                </div>
                <span className={`shrink-0 font-mono text-xs ${row.ok ? "text-emerald-300" : "text-rose-300"}`}>{row.value}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </button>
  );
}

function FunnelPanel({ funnel }: { funnel?: Record<string, number> }) {
  const funnelRate = (numerator: number, denominator: number) => (denominator > 0 ? numerator / denominator : 0);
  const rows = [
    ["Signals", funnel?.total ?? 0],
    ["Passed", funnel?.filt_passed ?? 0],
    ["Executable", funnel?.executable ?? 0],
    ["Orders", funnel?.orders_generated ?? 0],
    ["Maker Opened", funnel?.maker_opened ?? 0],
    ["Filled", funnel?.filled ?? 0],
    ["Settled", funnel?.settled ?? 0],
    ["Rejected", funnel?.rejected ?? 0],
  ] as const;
  const rates = [
    { label: "Pass / Signal", value: funnelRate(funnel?.filt_passed ?? 0, funnel?.total ?? 0) },
    { label: "Orders / Exec", value: funnelRate(funnel?.orders_generated ?? 0, funnel?.executable ?? 0) },
    { label: "Filled / Orders", value: funnelRate(funnel?.filled ?? 0, funnel?.orders_generated ?? 0) },
    { label: "Settled / Filled", value: funnelRate(funnel?.settled ?? 0, funnel?.filled ?? 0) },
  ];
  const max = Math.max(...rows.map(([, value]) => value), 1);
  return (
    <Panel title="Execution Funnel" sub="signal to settlement, read-only">
      <div className="grid grid-cols-2 gap-2 border-b border-zinc-900 p-4 md:grid-cols-4">
        {rates.map((item) => (
          <div key={item.label} className="rounded border border-zinc-900 bg-black/20 px-3 py-2">
            <div className="truncate text-[11px] uppercase tracking-[0.14em] text-zinc-600">{item.label}</div>
            <div className="mt-1 font-mono text-sm font-semibold text-zinc-200">{percent(item.value)}</div>
          </div>
        ))}
      </div>
      <div className="space-y-2 p-4">
        {rows.map(([label, value]) => (
          <div key={label} className="grid grid-cols-[110px_1fr_48px] items-center gap-3 text-xs">
            <span className="text-zinc-500">{label}</span>
            <div className="h-2 rounded bg-zinc-900">
              <div className="h-2 rounded bg-emerald-400/70" style={{ width: `${Math.max(3, (value / max) * 100)}%` }} />
            </div>
            <span className="text-right font-mono text-zinc-300">{value}</span>
          </div>
        ))}
      </div>
    </Panel>
  );
}

function UsageBar({ label, value }: { label: string; value?: number }) {
  const clamped = Math.max(0, Math.min(1, value ?? 0));
  const ok = clamped < 0.8;
  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between gap-3 text-xs">
        <span className="text-zinc-500">{label}</span>
        <span className={ok ? "font-mono text-zinc-300" : "font-mono text-amber-300"}>{percent(clamped, 0)}</span>
      </div>
      <div className="h-2 rounded bg-zinc-900">
        <div
          className={`h-2 rounded ${ok ? "bg-emerald-400/70" : "bg-amber-300/80"}`}
          style={{ width: `${Math.max(2, clamped * 100)}%` }}
        />
      </div>
    </div>
  );
}

function TodayCockpit({ today }: { today?: LiveSafety["today"] | null }) {
  const pnl = today?.trades.pnl_usdc ?? 0;
  const winRate = today?.trades.win_rate ?? 0;
  const passRate = today?.signals.pass_rate ?? 0;
  const makerTarget = today?.maker.target_price ?? 0.49;
  const observedTarget = today?.maker.observed_avg_target_price;
  const makerClean = (today?.maker.blocks ?? 0) === 0 && (today?.maker.api_errors ?? 0) === 0;
  const dryrunRecords = today?.dryrun.records ?? 0;
  const dryrunWouldPlaceRate = dryrunRecords === 0 ? null : (today?.dryrun.would_place ?? 0) / dryrunRecords;
  const dryrunBlockedRate = dryrunRecords === 0 ? null : (today?.dryrun.blocked ?? 0) / dryrunRecords;
  const dayLabel = `${today?.day_tz ?? "Trading day"} ${today?.day ?? today?.day_utc ?? "-"}`;

  return (
    <Panel
      title="Today Cockpit"
      sub={dayLabel}
      right={<StatusPill ok={makerTarget <= 0.49} label={`Maker Target ${makerTarget.toFixed(2)}`} />}
    >
      <div className="grid gap-4 p-4 xl:grid-cols-[minmax(0,1fr)_minmax(300px,0.72fr)]">
        <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
          <HealthTile label="Day PnL" value={signedMoney(pnl)} ok={pnl >= 0} />
          <HealthTile
            label="Settled W/L"
            value={`${today?.trades.settled ?? 0} | ${today?.trades.wins ?? 0}W/${today?.trades.losses ?? 0}L`}
            ok={(today?.trades.settled ?? 0) === 0 ? undefined : winRate >= 0.5}
          />
          <HealthTile
            label="Signal Pass"
            value={`${today?.signals.passed ?? 0}/${today?.signals.total ?? 0} | ${percent(passRate)}`}
            ok={(today?.signals.total ?? 0) === 0 ? undefined : passRate > 0}
          />
          <HealthTile label="Buy Up/Down" value={`${today?.signals.buy_up ?? 0}/${today?.signals.buy_down ?? 0}`} />
          <HealthTile label="Signal Hold" value={`${today?.signals.hold ?? 0}`} ok={(today?.signals.hold ?? 0) === 0 ? undefined : false} />
          <HealthTile label="Top Signal Block" value={today?.signals.top_block_reason || "-"} ok={today?.signals.top_block_reason ? false : undefined} />
          <HealthTile
            label="Open/Pending"
            value={`${today?.trades.open ?? 0}/${today?.trades.pending ?? 0}`}
            ok={(today?.risk_usage.open_or_pending ?? 0) < 0.8}
          />
          <HealthTile label="Maker Target" value={makerTarget.toFixed(2)} ok={makerTarget <= 0.49} />
          <HealthTile
            label="Observed Target"
            value={observedTarget == null ? "-" : observedTarget.toFixed(3)}
            ok={observedTarget == null ? undefined : observedTarget <= makerTarget + 0.01}
          />
          <HealthTile label="Buy-One Rate" value={percent(today?.maker.buy_one_rate ?? 0)} />
          <HealthTile label="Maker Blocks" value={`${today?.maker.blocks ?? 0} / ${today?.maker.api_errors ?? 0} err`} ok={makerClean} />
          <HealthTile label="Dry-run Today" value={`${today?.dryrun.records ?? 0}`} />
          <HealthTile label="Dry Would Place" value={`${today?.dryrun.would_place ?? 0}`} />
          <HealthTile label="Dry Place Rate" value={percent(dryrunWouldPlaceRate)} />
          <HealthTile label="Dry Blocked" value={`${today?.dryrun.blocked ?? 0}`} ok={(today?.dryrun.blocked ?? 0) === 0 ? undefined : false} />
          <HealthTile label="Dry Block Rate" value={percent(dryrunBlockedRate)} ok={dryrunBlockedRate == null ? undefined : dryrunBlockedRate === 0} />
          <HealthTile label="Dry Submitted" value={`${today?.dryrun.submitted ?? 0}`} ok={(today?.dryrun.submitted ?? 0) === 0} />
          <HealthTile label="Dry Latest" value={today?.dryrun.latest_action || today?.dryrun.latest_status || "-"} />
          <HealthTile label="Last Block" value={today?.dryrun.latest_block_reason || "-"} ok={today?.dryrun.latest_block_reason ? false : undefined} />
          <HealthTile label="Latest Signal" value={ageLabel(today?.activity?.latest_signal_age_seconds)} ok={(today?.activity?.latest_signal_age_seconds ?? 9999) < 600} />
          <HealthTile label="Latest Trade" value={ageLabel(today?.activity?.latest_trade_age_seconds)} />
          <HealthTile label="Latest Order" value={ageLabel(today?.activity?.latest_order_age_seconds)} />
          <HealthTile label="Latest Dry-run" value={ageLabel(today?.activity?.latest_dryrun_age_seconds)} ok={(today?.activity?.latest_dryrun_age_seconds ?? 9999) < 600} />
        </div>

        <div className="grid gap-3 rounded-md border border-zinc-900 bg-black/20 p-3">
          <UsageBar label="Daily Loss" value={today?.risk_usage.daily_loss} />
          <UsageBar label="Daily Trades" value={today?.risk_usage.daily_trades} />
          <UsageBar label="Loss Streak" value={today?.risk_usage.loss_streak} />
          <UsageBar label="Open/Pending" value={today?.risk_usage.open_or_pending} />
        </div>
      </div>
    </Panel>
  );
}

function TradingStatusPanel({ safety }: { safety?: LiveSafety | null }) {
  const liveStatus = safety?.live_trading_status;
  const formal = liveStatus?.formal;
  const sync = liveStatus?.sync;
  const risk = liveStatus?.risk;
  const orders = liveStatus?.orders;
  const blockers = liveStatus?.blockers ?? [];
  const statusOk = liveStatus?.ok === true;
  const statusLabel = liveStatus?.label ?? "Waiting";
  const formalValue = formal?.running ? (formal?.fresh ? "running / fresh" : `running / stale ${ageLabel(formal?.age_seconds)}`) : "not running";
  const syncValue = sync?.running ? (sync?.fresh ? "running / fresh" : `running / stale ${ageLabel(sync?.age_seconds)}`) : "not running";
  const riskValue = risk?.ok === false ? risk.reason || risk.blockers?.map((item) => item.label).join(", ") || "blocked" : "clear";
  const openPending = orders?.open_or_pending ?? 0;

  return (
    <Panel
      title="Trading Status"
      sub="formal live, sync, and risk"
      right={<StatusPill ok={statusOk} danger={liveStatus?.status === "risk_blocked" || liveStatus?.status === "paused"} label={statusLabel} />}
    >
      <div className="grid gap-3 p-4">
        <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
          <HealthTile label="Formal Live" value={formalValue} ok={formal?.running === true && formal?.fresh === true} />
          <HealthTile label="Order Sync" value={syncValue} ok={sync?.running === true && sync?.fresh === true && sync?.ok === true} />
          <HealthTile label="Risk State" value={riskValue} ok={risk?.ok} />
          <HealthTile label="Open Orders" value={`${openPending}`} ok={openPending === 0} />
        </div>
        <div className="rounded-md border border-zinc-900 bg-black/20 p-3">
          <div className="text-[11px] uppercase tracking-[0.14em] text-zinc-600">Next Action</div>
          <div className="mt-1 text-sm font-medium text-zinc-100">{liveStatus?.next_action || "Waiting for live status"}</div>
          {blockers.length > 0 ? (
            <div className="mt-3 flex flex-wrap gap-2">
              {blockers.slice(0, 4).map((blocker) => (
                <StatusPill
                  key={blocker.key}
                  ok={false}
                  danger={blocker.severity === "risk" || blocker.key === "live_paused"}
                  label={`${blocker.label}${blocker.value == null ? "" : ` ${blocker.value}`}`}
                />
              ))}
            </div>
          ) : (
            <div className="mt-3 flex flex-wrap gap-2">
              <StatusPill ok label={formal?.latest_action ? `Signal ${formal.latest_action}` : "Formal monitoring"} />
              <StatusPill ok={sync?.ok === true} label={sync?.settlement_source ? `Sync ${sync.settlement_source}` : "Sync ready"} />
              <StatusPill ok={risk?.ok !== false} label={risk?.ok === false ? "Risk blocked" : "Risk clear"} />
            </div>
          )}
        </div>
      </div>
    </Panel>
  );
}

function FirstOrderRail({ rail }: { rail?: LiveSafety["first_order_rail"] | null }) {
  const stages = rail?.stages ?? [];
  const currentStage = stages.find((stage) => stage.key === rail?.current_key);
  const ready = rail?.ready_for_manual_confirmation === true;
  const statusLabel = ready ? "Manual confirmation" : currentStage?.label ?? "Waiting";
  const currentAction = currentStage?.action ?? "Review readiness";
  const primaryBlocker = currentStage?.blockers?.[0] ?? "-";

  const statusTone = (status: string, active: boolean) => {
    if (status === "complete") return "border-emerald-500/25 bg-emerald-500/5 text-emerald-300";
    if (status === "manual") return "border-sky-500/25 bg-sky-500/10 text-sky-300";
    if (status === "blocked") return "border-amber-500/25 bg-amber-500/10 text-amber-300";
    return active ? "border-zinc-500/25 bg-zinc-500/10 text-zinc-300" : "border-zinc-900 bg-black/20 text-zinc-500";
  };

  return (
    <Panel
      title="First Order Path"
      sub="dry-run to guarded smoke"
      right={<StatusPill ok={ready} label={statusLabel} />}
    >
      <div className="grid gap-3 p-4">
        <div className="grid gap-2 md:grid-cols-3">
          <HealthTile label="Current Stage" value={statusLabel} ok={ready ? true : currentStage?.status !== "blocked"} />
          <HealthTile label="Next Action" value={currentAction} ok={ready} />
          <HealthTile label="Primary Blocker" value={primaryBlocker} ok={primaryBlocker === "-"} />
        </div>
        <div className="grid gap-2 md:grid-cols-3 xl:grid-cols-6">
          {stages.map((stage, index) => {
            const active = stage.key === rail?.current_key;
            const Icon = stage.status === "complete" ? CheckCircle2 : stage.status === "manual" ? Lock : stage.status === "blocked" ? AlertTriangle : Clock3;
            return (
              <div
                key={stage.key}
                className={`min-w-0 rounded-md border px-3 py-2.5 ${statusTone(stage.status, active)}`}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="font-mono text-[11px] uppercase tracking-[0.14em] opacity-70">{String(index + 1).padStart(2, "0")}</span>
                  <Icon className="h-3.5 w-3.5 shrink-0" />
                </div>
                <div className="mt-2 truncate text-sm font-medium text-zinc-100">{stage.label}</div>
                <div className="mt-1 truncate font-mono text-[11px] uppercase">{stage.status}</div>
                {stage.requires_confirmation && <div className="mt-2 truncate text-xs text-sky-300">Manual confirmation</div>}
                <div className="mt-2 min-h-8 text-xs text-zinc-500">{stage.action}</div>
                {(stage.blockers?.length ?? 0) > 0 && (
                  <div className="mt-2 truncate text-[11px] text-amber-300">{stage.blockers?.slice(0, 2).join(" / ")}</div>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </Panel>
  );
}

function ClobReadonlyPanel({ audit }: { audit?: LiveSafety["clob_readonly"] | null }) {
  const quoteRate = audit?.quote_executable_rate ?? 0;
  const blockers = audit?.blockers ?? [];
  const quoteProbes = audit?.quote_probes ?? [];
  return (
    <Panel
      title="CLOB Read-only Audit"
      sub={audit?.report_source || audit?.report?.split(/[\\/]/).pop() || "live gate and allowance report"}
      right={<StatusPill ok={audit?.ready === true} label={audit?.ready ? "Ready" : audit?.available ? "Review" : "Missing"} />}
    >
      <div className="grid gap-4 p-4 xl:grid-cols-[minmax(0,1fr)_minmax(300px,0.55fr)]">
        <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
          <HealthTile
            label="Report Age"
            value={ageLabel(audit?.report_age_seconds)}
            ok={audit?.fresh}
          />
          <HealthTile
            label="Status Reason"
            value={audit?.status_reason?.replace(/_/g, " ") || "-"}
            ok={audit?.ready === true}
          />
          <HealthTile
            label="Next Action"
            value={audit?.next_action || "-"}
            ok={audit?.ready === true}
          />
          <HealthTile label="CLOB Auth" value={audit?.authenticated ? "OK" : "-"} ok={audit?.authenticated} />
          <HealthTile label="Account Read" value={audit?.account_read_ok ? "OK" : "-"} ok={audit?.account_read_ok} />
          <HealthTile label="Allowance Read" value={audit?.allowance_read_ok ? "OK" : "-"} ok={audit?.allowance_read_ok} />
          <HealthTile label="Open Orders Read" value={audit?.open_orders_read_ok ? "OK" : "-"} ok={audit?.open_orders_read_ok} />
          <HealthTile label="Balance" value={audit?.balance == null ? "-" : money(audit.balance)} ok={(audit?.balance ?? 0) > 0} />
          <HealthTile label="Min Allowance" value={audit?.min_allowance == null ? "-" : money(audit.min_allowance)} ok={(audit?.min_allowance ?? 0) > 0} />
          <HealthTile label="Allowance Count" value={`${audit?.allowance_count ?? 0}`} ok={(audit?.allowance_count ?? 0) > 0} />
          <HealthTile label="Open Orders" value={`${audit?.open_orders_count ?? 0}`} ok={(audit?.open_orders_count ?? 0) === 0} />
          <HealthTile
            label="Quote Executable"
            value={`${audit?.quote_executable_count ?? 0}/${audit?.market_probe_count ?? 0} | ${percent(quoteRate)}`}
            ok={(audit?.market_probe_count ?? 0) > 0 && audit?.quote_executable_count === audit?.market_probe_count}
          />
          <HealthTile label="Funding Ready" value={audit?.funding_ready ? "yes" : "no"} ok={audit?.funding_ready === true} />
          <HealthTile label="Balance Gap" value={money(audit?.balance_shortfall_usdc ?? 0)} ok={(audit?.balance_shortfall_usdc ?? 0) === 0} />
          <HealthTile label="Allowance Gap" value={money(audit?.allowance_shortfall_usdc ?? 0)} ok={(audit?.allowance_shortfall_usdc ?? 0) === 0} />
        </div>

        <div className="rounded-md border border-zinc-900 bg-black/20 p-3">
          <div className="mb-2 text-[11px] uppercase tracking-[0.14em] text-zinc-600">Read-only Blockers</div>
          {blockers.length === 0 ? (
            <div className="text-sm text-emerald-300">No live gate blockers</div>
          ) : (
            <div className="space-y-2">
              {blockers.slice(0, 4).map((blocker) => (
                <div key={blocker} className="rounded border border-amber-500/20 bg-amber-500/5 px-2.5 py-2 text-xs text-amber-200">
                  {blocker}
                </div>
              ))}
            </div>
          )}
          <div className="mt-3 truncate text-[11px] text-zinc-600">
            spender {audit?.min_allowance_spender || "-"}
          </div>
          <div className="mt-4 border-t border-zinc-900 pt-3">
            <div className="mb-2 text-[11px] uppercase tracking-[0.14em] text-zinc-600">Probe Details</div>
            {quoteProbes.length === 0 ? (
              <div className="text-xs text-zinc-500">No market probes yet</div>
            ) : (
              <div className="space-y-2">
                {quoteProbes.map((probe) => (
                  <div key={`${probe.direction}-${probe.token_id || probe.reason || probe.error}`} className="rounded border border-zinc-900 bg-zinc-950/60 px-2.5 py-2">
                    <div className="flex items-center justify-between gap-3">
                      <span className="font-mono text-xs text-zinc-200">{probe.direction || "-"}</span>
                      <span className={`font-mono text-[11px] uppercase ${probe.quote_executable ? "text-emerald-300" : "text-amber-300"}`}>
                        {probe.quote_executable ? "executable" : probe.ok ? "not executable" : "failed"}
                      </span>
                    </div>
                    <div className="mt-1 grid grid-cols-3 gap-2 font-mono text-[11px] text-zinc-500">
                      <span>bid {probe.best_bid == null ? "-" : probe.best_bid.toFixed(3)}</span>
                      <span>ask {probe.best_ask == null ? "-" : probe.best_ask.toFixed(3)}</span>
                      <span>px {probe.price == null ? "-" : probe.price.toFixed(3)}</span>
                    </div>
                    {(probe.reason || probe.error) && (
                      <div className="mt-1 truncate text-[11px] text-amber-300">{probe.reason || probe.error}</div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </Panel>
  );
}

function ReportFreshnessPanel({ refresh }: { refresh?: LiveSafety["report_refresh"] | null }) {
  const items = refresh?.items ?? [];
  const issueCount = (refresh?.missing_count ?? 0) + (refresh?.stale_count ?? 0) + (refresh?.blocked_count ?? 0);
  const statusTone = (status: string) => {
    if (status === "ready") return "border-emerald-500/25 bg-emerald-500/10 text-emerald-300";
    if (status === "blocked") return "border-rose-500/25 bg-rose-500/10 text-rose-300";
    if (status === "stale" || status === "missing") return "border-amber-500/25 bg-amber-500/10 text-amber-300";
    return "border-zinc-700 bg-zinc-900/70 text-zinc-300";
  };
  return (
    <Panel
      title="Report Freshness"
      sub="formal live, order sync, and account activity"
      right={<StatusPill ok={refresh?.ready === true} label={refresh?.ready ? "Fresh" : `${issueCount} review`} />}
    >
      <div className="grid gap-3 p-4 lg:grid-cols-4">
        {items.length === 0 ? (
          <div className="rounded-md border border-amber-500/20 bg-amber-500/5 p-3 lg:col-span-4">
            <div className="flex items-center gap-2 text-sm font-medium text-amber-200">
              <AlertTriangle className="h-4 w-4 shrink-0" />
              <span>No freshness reports</span>
            </div>
            <div className="mt-2 text-xs text-zinc-500">
              Waiting for formal live, order sync, and account activity reports
            </div>
          </div>
        ) : items.map((item) => (
          <div key={item.key} className="min-w-0 rounded-md border border-zinc-900 bg-black/20 p-3">
            <div className="flex items-center justify-between gap-2">
              <div className="truncate text-sm font-medium text-zinc-100">{item.label}</div>
              <span className={`rounded border px-2 py-0.5 font-mono text-[11px] uppercase ${statusTone(item.status)}`}>
                {item.status}
              </span>
            </div>
            <div className="mt-3 grid grid-cols-2 gap-2">
              <HealthTile label="Age" value={ageLabel(item.age_seconds)} ok={item.fresh} />
              <HealthTile label="Max Age" value={ageLabel(item.max_age_seconds)} />
            </div>
            <div className="mt-3 truncate text-xs text-zinc-400">{item.next_action}</div>
            <div className="mt-2 truncate font-mono text-[11px] text-zinc-600">
              {item.report?.split(/[\\/]/).pop() || "-"}
            </div>
            {(item.blockers?.length ?? 0) > 0 && (
              <div className="mt-2 truncate text-[11px] text-amber-300">
                {item.blockers?.slice(0, 2).join(" / ")}
              </div>
            )}
          </div>
        ))}
      </div>
    </Panel>
  );
}

function LegacyReportFreshnessPanel({ refresh }: { refresh?: LiveSafety["legacy_report_refresh"] | null }) {
  const items = refresh?.items ?? [];
  const issueCount = (refresh?.missing_count ?? 0) + (refresh?.stale_count ?? 0) + (refresh?.blocked_count ?? 0);
  const statusTone = (status: string) => {
    if (status === "ready") return "border-emerald-500/25 bg-emerald-500/10 text-emerald-300";
    if (status === "blocked") return "border-rose-500/25 bg-rose-500/10 text-rose-300";
    if (status === "stale" || status === "missing") return "border-amber-500/25 bg-amber-500/10 text-amber-300";
    return "border-zinc-700 bg-zinc-900/70 text-zinc-300";
  };
  return (
    <Panel
      title="Legacy Preflight Reports"
      sub="diagnostic-only legacy readiness reports"
      right={<StatusPill ok={refresh?.ready === true} label={refresh?.ready ? "Fresh" : `${issueCount} diagnostic`} />}
    >
      <div className="grid gap-3 p-4 lg:grid-cols-3">
        {items.length === 0 ? (
          <div className="rounded-md border border-zinc-800 bg-black/20 p-3 lg:col-span-3">
            <div className="flex items-center gap-2 text-sm font-medium text-zinc-200">
              <AlertTriangle className="h-4 w-4 shrink-0 text-amber-300" />
              <span>No freshness reports</span>
            </div>
            <div className="mt-2 text-xs text-zinc-500">Waiting for legacy preflight diagnostics</div>
          </div>
        ) : items.map((item) => (
          <div key={item.key} className="min-w-0 rounded-md border border-zinc-900 bg-black/20 p-3">
            <div className="flex items-center justify-between gap-2">
              <div className="truncate text-sm font-medium text-zinc-100">{item.label}</div>
              <span className={`rounded border px-2 py-0.5 font-mono text-[11px] uppercase ${statusTone(item.status)}`}>
                {item.status}
              </span>
            </div>
            <div className="mt-3 grid grid-cols-2 gap-2">
              <HealthTile label="Age" value={ageLabel(item.age_seconds)} ok={item.fresh} />
              <HealthTile label="Max Age" value={ageLabel(item.max_age_seconds)} />
            </div>
            <div className="mt-3 truncate text-xs text-zinc-400">{item.next_action}</div>
            <div className="mt-2 truncate font-mono text-[11px] text-zinc-600">
              {item.report?.split(/[\\/]/).pop() || "-"}
            </div>
          </div>
        ))}
      </div>
    </Panel>
  );
}

function MarketDataPanel({ data }: { data?: LiveSafety["market_data"] | null }) {
  const sourceLabel = data?.source?.replace(/_/g, " ") || "-";
  return (
    <Panel
      title="Market Data"
      sub="Chainlink live reference"
      right={<StatusPill ok={data?.ready === true} label={data?.ready ? "Fresh" : data?.status || "Waiting"} />}
    >
      <div className="grid grid-cols-2 gap-2 p-4 md:grid-cols-4 xl:grid-cols-6">
        <HealthTile label="Source" value={sourceLabel} ok={data?.source?.includes("chainlink")} />
        <HealthTile label="Status" value={data?.status || "-"} ok={data?.ready === true} />
        <HealthTile label="BTC Price" value={data?.price == null ? "-" : money(data.price)} ok={data?.ready === true} />
        <HealthTile label="Price Age" value={ageLabel(data?.price_age_seconds)} ok={(data?.price_age_seconds ?? 9999) <= (data?.max_price_age_seconds ?? 0)} />
        <HealthTile label="Received Age" value={ageLabel(data?.received_age_seconds)} ok={(data?.received_age_seconds ?? 9999) <= (data?.max_received_age_seconds ?? 0)} />
        <HealthTile label="Price SLA" value={ageBudgetLabel(data?.price_age_seconds, data?.max_price_age_seconds)} ok={(data?.price_age_seconds ?? 9999) <= (data?.max_price_age_seconds ?? 0)} />
        <HealthTile label="Received SLA" value={ageBudgetLabel(data?.received_age_seconds, data?.max_received_age_seconds)} ok={(data?.received_age_seconds ?? 9999) <= (data?.max_received_age_seconds ?? 0)} />
        <HealthTile label="Next Action" value={data?.next_action || "-"} ok={data?.ready === true} />
        {data?.error && <HealthTile label="Error" value={data.error} ok={false} />}
      </div>
    </Panel>
  );
}

function RiskPanel({ intel, safety }: { intel?: LiveIntel | null; safety?: LiveSafety | null }) {
  const risk = intel?.risk;
  const maker = intel?.maker;
  const liveRisk = safety?.risk;
  const metrics = liveRisk?.metrics;
  const limits = liveRisk?.limits;
  const resilience = liveRisk?.resilience;
  const resilienceMetrics = resilience?.metrics;
  const resilienceLimits = resilienceMetrics?.limits;
  const fundingReady = safety?.funding?.funding_ready === true;
  const fundingBalance = safety?.funding?.balance;
  const fundingAllowance = safety?.funding?.min_allowance;
  const fundingAllowanceLabel = compactAllowance(fundingAllowance, safety?.funding?.allowance_ok === true || fundingReady);
  return (
    <Panel title="Risk & Funding" sub="limits, exposure, readiness">
      <div className="grid grid-cols-2 gap-2 p-4">
        <HealthTile label="Balance" value={fundingBalance == null ? "-" : money(fundingBalance)} ok={safety?.funding?.balance_ok} />
        <HealthTile label="Allowance" value={fundingAllowanceLabel} ok={safety?.funding?.allowance_ok} />
        <HealthTile label="Balance Gap" value={money(safety?.funding?.balance_shortfall_usdc ?? 0)} ok={fundingReady || (safety?.funding?.balance_shortfall_usdc ?? 0) === 0} />
        <HealthTile label="Allowance Gap" value={money(safety?.funding?.allowance_shortfall_usdc ?? 0)} ok={fundingReady || (safety?.funding?.allowance_shortfall_usdc ?? 0) === 0} />
        <HealthTile
          label="Daily PnL"
          value={metrics ? signedMoney(metrics.daily_pnl_usdc) : "-"}
          ok={metrics && limits ? metrics.daily_pnl_usdc > -Math.abs(limits.max_daily_loss_usdc) : undefined}
        />
        <HealthTile
          label="Daily Trades"
          value={metrics && limits ? `${metrics.daily_trades}/${limits.max_daily_trades}` : "-"}
          ok={metrics && limits ? metrics.daily_trades < limits.max_daily_trades : undefined}
        />
        <HealthTile
          label="Loss Streak"
          value={metrics && limits ? `${metrics.consecutive_losses}/${limits.max_consecutive_losses}` : "-"}
          ok={metrics && limits ? metrics.consecutive_losses < limits.max_consecutive_losses : undefined}
        />
        <HealthTile
          label="Open/Pending"
          value={metrics && limits ? `${metrics.open_or_pending_orders}/${limits.max_open_or_pending_orders}` : `${(maker?.open_orders.length ?? 0) + (maker?.pending_positions.length ?? 0)}`}
          ok={metrics && limits ? metrics.open_or_pending_orders <= limits.max_open_or_pending_orders : undefined}
        />
        <HealthTile
          label="Smoke Drawdown"
          value={resilienceMetrics?.max_drawdown_usdc == null ? "-" : `${money(resilienceMetrics.max_drawdown_usdc)} / ${money(resilienceLimits?.max_smoke_drawdown_usdc ?? 0)}`}
          ok={resilience?.ok}
        />
        <HealthTile
          label="Risk State"
          value={resilience?.failures?.length ? resilience.failures.join(", ") : "clear"}
          ok={resilience?.ok}
        />
        <HealthTile label="Missed" value={`${risk?.missed_trades?.length ?? 0}`} ok={(risk?.missed_trades?.length ?? 0) === 0} />
        <HealthTile label="Rejections" value={`${risk?.max_open_rejections_count ?? 0}`} ok={(risk?.max_open_rejections_count ?? 0) === 0} />
      </div>
    </Panel>
  );
}

const riskPrice = (value?: number | null) => (value == null ? "-" : value.toFixed(2));
const riskNumber = (value?: number | null) => (value == null ? "-" : `${value}`);

function RiskRulesPanel({ controls }: { controls?: RiskControls | null }) {
  const rules = controls?.summary;
  const priceBand = rules
    ? `${riskPrice(rules.min_price)}-${riskPrice(rules.active_max_price)} / hard ${riskPrice(rules.hard_max_price)}`
    : "-";
  const cooldown =
    rules?.same_direction_loss_cooldown_count != null && rules?.same_direction_loss_cooldown_minutes != null
      ? `${rules.same_direction_loss_cooldown_count} losses -> ${rules.same_direction_loss_cooldown_minutes}m`
      : "-";
  const orderLimit =
    rules?.order_size_shares != null || rules?.max_notional_usdc != null
      ? `${riskNumber(rules?.order_size_shares)} shares / ${rules?.max_notional_usdc == null ? "-" : money(rules.max_notional_usdc)}`
      : "-";
  const source = controls?.source ? controls.source.replace(/_/g, " ") : "unavailable";
  return (
    <Panel
      title="Risk Rules"
      sub="current formal live settings"
      right={<StatusPill ok={controls?.source === "supervisor_log"} label={source} />}
    >
      <div className="grid grid-cols-2 gap-2 p-4 md:grid-cols-3">
        <HealthTile label="Order Size" value={orderLimit} ok={rules?.order_size_shares != null} />
        <HealthTile label="Price Band" value={priceBand} ok={rules?.min_price != null && rules?.active_max_price != null} />
        <HealthTile label="Daily Loss" value={rules?.max_daily_loss_usdc == null ? "-" : money(rules.max_daily_loss_usdc)} ok={rules?.max_daily_loss_usdc != null} />
        <HealthTile label="Daily Trades" value={riskNumber(rules?.max_daily_trades)} ok={rules?.max_daily_trades != null} />
        <HealthTile label="Loss Streak" value={riskNumber(rules?.max_consecutive_losses)} ok={rules?.max_consecutive_losses != null} />
        <HealthTile label="Open/Pending" value={riskNumber(rules?.max_open_or_pending_orders)} ok={rules?.max_open_or_pending_orders != null} />
        <HealthTile label="Drawdown" value={rules?.max_smoke_drawdown_usdc == null ? "-" : money(rules.max_smoke_drawdown_usdc)} ok={rules?.max_smoke_drawdown_usdc != null} />
        <HealthTile label="Cooldown" value={cooldown} ok={cooldown !== "-"} />
        <HealthTile label="Signal Age" value={rules?.signal_max_age_seconds == null ? "-" : `${rules.signal_max_age_seconds}s`} ok={rules?.signal_max_age_seconds != null} />
        <HealthTile label="Reference" value={rules?.reference_price_source || "-"} ok={rules?.reference_price_source === "chainlink"} />
        <HealthTile label="Market Shift" value={rules?.execution_market_shift || "-"} ok={rules?.execution_market_shift === "next_period"} />
      </div>
    </Panel>
  );
}

function MakerPanel({ intel }: { intel?: LiveIntel | null }) {
  const summary = intel?.maker_quality?.summary;
  return (
    <Panel title="Maker Monitor" sub="quote quality and execution health">
      <div className="grid grid-cols-2 gap-2 p-4">
        <HealthTile label="Watches" value={`${summary?.watch_count ?? 0}`} />
        <HealthTile label="Buy-One Rate" value={percent(summary?.avg_buy_one_rate ?? 0)} />
        <HealthTile label="Avg Target" value={summary?.avg_target_price ? summary.avg_target_price.toFixed(3) : "-"} />
        <HealthTile label="Reposts" value={`${summary?.total_reposts ?? 0}`} />
        <HealthTile label="Blocks" value={`${summary?.total_blocks ?? 0}`} ok={(summary?.total_blocks ?? 0) === 0} />
        <HealthTile label="API Errors" value={`${summary?.api_error_count ?? 0}`} ok={(summary?.api_error_count ?? 0) === 0} />
      </div>
    </Panel>
  );
}

function HealthTile({ label, value, ok }: { label: string; value: string; ok?: boolean }) {
  const tone = ok == null ? "text-zinc-200" : ok ? "text-emerald-300" : "text-amber-300";
  return (
    <div className="rounded border border-zinc-900 bg-black/20 px-3 py-2">
      <div className="text-[11px] uppercase tracking-[0.14em] text-zinc-600">{label}</div>
      <div className={`mt-1 truncate font-mono text-sm ${tone}`}>{value}</div>
    </div>
  );
}

function ResultPill({ won }: { won: number }) {
  const label = won === 1 ? "WIN" : won === 0 ? "LOSS" : "PENDING";
  const klass =
    won === 1
      ? "border-emerald-500/25 bg-emerald-500/10 text-emerald-300"
      : won === 0
        ? "border-rose-500/25 bg-rose-500/10 text-rose-300"
        : "border-amber-500/25 bg-amber-500/10 text-amber-300";
  return <span className={`inline-flex min-w-14 items-center justify-center rounded border px-2 py-0.5 text-xs font-medium ${klass}`}>{label}</span>;
}

function runtimeState(runtime?: RuntimeSummary | null) {
  if (!runtime) return { label: "-", ok: undefined as boolean | undefined };
  if (runtime.running) return { label: `running ${ageLabel(runtime.uptime_seconds)}`, ok: true };
  return { label: "not running", ok: false };
}

function LiveSoakPanel({ liveReal }: { liveReal?: LiveSafety["live_real"] | null }) {
  const runtime = runtimeState(liveReal?.runtime);
  const childRuntime = liveReal?.runtime?.child_runtime;
  const childRuntimeState = runtimeState(childRuntime);
  const formal = liveReal?.formal;
  const soak = liveReal?.soak;
  const blockers = soak?.blockers ?? [];
  const reportAge = formal?.report_age_seconds ?? soak?.report_age_seconds;
  return (
    <Panel
      title="Live Formal Process"
      sub="current-next real-order runner"
      right={<StatusPill ok={runtime.ok === true} label={runtime.label} />}
    >
      <div className="grid gap-3 p-4">
        <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
          <HealthTile label="Total Runtime" value={runtime.label} ok={runtime.ok} />
          <HealthTile label="Child Runtime" value={childRuntimeState.label} ok={childRuntimeState.ok} />
          <HealthTile label="Total Started" value={shortDateTime(liveReal?.runtime?.started_at || formal?.latest_created_at || soak?.started_at || undefined)} ok={runtime.ok} />
          <HealthTile label="Child Started" value={shortDateTime(childRuntime?.started_at || undefined)} ok={childRuntime?.running} />
          <HealthTile label="Attempts" value={`${formal?.attempts ?? soak?.attempts ?? 0}`} />
          <HealthTile label="Submissions" value={formal?.submitted ? "1+" : `${soak?.submitted_count ?? 0}`} ok={formal?.submitted ? true : (soak?.submitted_count ?? 0) >= 0} />
          <HealthTile label="Mode" value={formal?.submitted ? "armed" : "watching"} ok={formal?.available ? true : undefined} />
          <HealthTile label="Terminal" value={formal?.terminal_reason || soak?.terminal_reason || "-"} ok={formal?.terminal_reason ? formal.terminal_reason !== "submit_blocked" : soak?.terminal_reason ? soak.terminal_reason !== "submit_blocked" : undefined} />
          <HealthTile label="Latest Action" value={formal?.latest_action || soak?.latest_action || "-"} />
          <HealthTile label="Report Age" value={ageLabel(reportAge)} ok={(reportAge ?? 9999) < 420} />
        </div>
        <div className="rounded-md border border-zinc-900 bg-black/20 p-3">
          <div className="text-[11px] uppercase tracking-[0.14em] text-zinc-600">Latest Reason</div>
          <div className="mt-1 truncate text-sm text-zinc-300">{formal?.latest_reason || formal?.reason || soak?.latest_reason || "-"}</div>
          {blockers.length > 0 && (
            <div className="mt-3 flex flex-wrap gap-2">
              {blockers.slice(0, 4).map((blocker) => (
                <span key={blocker} className="rounded border border-amber-500/25 bg-amber-500/10 px-2 py-1 text-xs text-amber-300">{blocker}</span>
              ))}
            </div>
          )}
        </div>
      </div>
    </Panel>
  );
}

function LiveRealOrdersPanel({ liveReal }: { liveReal?: LiveSafety["live_real"] | null }) {
  const latest = liveReal?.latest_order ?? {};
  const statusCounts = Object.entries(liveReal?.orders.status_counts ?? {});
  const latestStatus = String(latest.status ?? "-");
  const latestDirection = String(latest.direction ?? latest.action ?? "-");
  const latestPrice = typeof latest.price === "number" ? latest.price.toFixed(2) : String(latest.price ?? "-");
  const latestSize = typeof latest.filled_size === "number" ? latest.filled_size : typeof latest.size === "number" ? latest.size : null;
  return (
    <Panel
      title="Live Real Orders"
      sub="current-next live ledger"
      right={<span className="font-mono text-xs text-zinc-500">{liveReal?.orders.total ?? 0} records</span>}
    >
      <div className="grid gap-4 p-4 lg:grid-cols-[minmax(0,1fr)_minmax(280px,0.55fr)]">
        <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
          <HealthTile label="Open/Pending" value={`${liveReal?.orders.open_or_pending ?? 0}`} ok={(liveReal?.orders.open_or_pending ?? 0) === 0} />
          <HealthTile label="Settled" value={`${liveReal?.orders.settled ?? 0}`} />
          <HealthTile label="Cancelled" value={`${liveReal?.orders.cancelled ?? 0}`} />
          <HealthTile label="No-fill" value={`${liveReal?.orders.no_fill_cancelled ?? 0}`} />
          <HealthTile label="Filled Size" value={`${liveReal?.orders.filled_size ?? 0}`} />
          <HealthTile label="Daily PnL" value={signedMoney(liveReal?.risk?.metrics.daily_pnl_usdc ?? 0)} ok={(liveReal?.risk?.metrics.daily_pnl_usdc ?? 0) >= 0} />
          <HealthTile label="Daily Trades" value={`${liveReal?.risk?.metrics.daily_trades ?? 0}/${liveReal?.risk?.limits.max_daily_trades ?? "-"}`} />
          <HealthTile label="Loss Limit" value={money(liveReal?.risk?.limits.max_daily_loss_usdc ?? 0)} />
        </div>
        <div className="rounded-md border border-zinc-900 bg-black/20 p-3">
          <div className="mb-2 text-[11px] uppercase tracking-[0.14em] text-zinc-600">Latest Real Order</div>
          <div className="grid grid-cols-2 gap-2">
            <HealthTile label="Status" value={latestStatus} ok={latestStatus === "-" ? undefined : latestStatus === "SETTLED" || latestStatus.includes("CANCEL")} />
            <HealthTile label="Direction" value={directionLabel(latestDirection)} />
            <HealthTile label="Price" value={latestPrice} />
            <HealthTile label="Filled" value={latestSize == null ? "-" : `${latestSize}`} />
          </div>
          <div className="mt-3 flex flex-wrap gap-2">
            {statusCounts.length === 0 ? (
              <span className="text-xs text-zinc-500">No live ledger records</span>
            ) : statusCounts.map(([status, count]) => (
              <span key={status} className="rounded border border-zinc-800 bg-zinc-950 px-2 py-1 font-mono text-[11px] text-zinc-400">{status} {count}</span>
            ))}
          </div>
        </div>
      </div>
    </Panel>
  );
}

function CurrentClobOrdersPanel({ audit }: { audit?: LiveSafety["clob_readonly"] | null }) {
  const orders = audit?.open_orders ?? [];
  const count = audit?.open_orders_count ?? orders.length;
  return (
    <Panel
      title="Current CLOB Orders"
      sub="Polymarket account open orders"
      right={<StatusPill ok={count === 0 && audit?.open_orders_read_ok === true} label={`${count} open`} />}
    >
      <div className="max-h-[360px] overflow-auto">
        {orders.length === 0 ? (
          <div className="px-4 py-8 text-center">
            <div className="text-sm text-zinc-400">No current CLOB open orders</div>
            <div className="mt-1 text-xs text-zinc-600">{audit?.open_orders_read_ok ? "account read ok" : "waiting for account read"}</div>
          </div>
        ) : (
          <table className="min-w-full text-left text-xs">
            <thead className="sticky top-0 bg-zinc-950 text-[11px] uppercase tracking-[0.12em] text-zinc-600">
              <tr>
                <th className="px-4 py-2 font-medium">Order</th>
                <th className="px-3 py-2 font-medium">Market</th>
                <th className="px-3 py-2 font-medium">Side</th>
                <th className="px-3 py-2 font-medium">Price</th>
                <th className="px-3 py-2 font-medium">Size</th>
                <th className="px-3 py-2 font-medium">Filled</th>
                <th className="px-3 py-2 font-medium">Remaining</th>
                <th className="px-3 py-2 font-medium">Status</th>
                <th className="px-3 py-2 font-medium">Created</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-900">
              {orders.map((order, index) => (
                <tr key={order.order_id || `${order.market}-${order.token_id}-${index}`} className="hover:bg-zinc-900/40">
                  <td className="px-4 py-2 font-mono text-zinc-300" title={order.order_id || ""}>{compactId(order.order_id)}</td>
                  <td className="max-w-[220px] truncate px-3 py-2 font-mono text-zinc-400" title={order.market || order.token_id || ""}>
                    {order.market || compactId(order.token_id || order.asset_id)}
                  </td>
                  <td className="px-3 py-2">
                    <span className="inline-flex items-center gap-1 font-mono text-zinc-200">
                      {directionIcon(order.outcome || order.side)}
                      {order.side || "-"} {order.outcome || ""}
                    </span>
                  </td>
                  <td className="px-3 py-2 font-mono text-zinc-100">{orderPrice(order.price)}</td>
                  <td className="px-3 py-2 font-mono text-zinc-300">{orderSize(order.original_size)}</td>
                  <td className="px-3 py-2 font-mono text-zinc-300">{orderSize(order.matched_size)}</td>
                  <td className="px-3 py-2 font-mono text-zinc-100">{orderSize(order.remaining_size)}</td>
                  <td className="px-3 py-2 font-mono text-zinc-400">{order.status || "-"}</td>
                  <td className="px-3 py-2 font-mono text-zinc-500">{shortDateTime(order.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </Panel>
  );
}

function PolymarketAccountActivityPanel({ account }: { account?: PolymarketAccountActivity | null }) {
  const activity = account?.recent_activity ?? [];
  const positions = account?.positions ?? [];
  const redeemablePositions = account?.redeemable_positions ?? [];
  const summary = account?.summary;
  const fresh = (account?.report_age_seconds ?? 9999) < 180;
  return (
    <Panel
      title="PM Account Activity"
      sub="Data API trades, active positions, redeemable"
      right={<StatusPill ok={account?.ok === true && fresh} label={account?.available ? ageLabel(account?.report_age_seconds) : "missing"} />}
    >
      <div className="border-b border-zinc-900 px-4 py-3">
        <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
          <HealthTile label="Trades" value={`${summary?.trade_count ?? 0}`} ok={(summary?.trade_count ?? 0) >= 0} />
          <HealthTile label="Redeems" value={`${summary?.redeem_count ?? 0}`} />
          <HealthTile label="Active" value={`${summary?.active_positions_count ?? positions.length}`} ok={(summary?.active_positions_count ?? positions.length) === positions.length} />
          <HealthTile label="Redeemable" value={`${summary?.redeemable_positions_count ?? redeemablePositions.length}`} />
          <HealthTile label="Report Age" value={ageLabel(account?.report_age_seconds)} ok={fresh} />
        </div>
        {account?.reason && <div className="mt-2 truncate text-xs text-amber-300">{account.reason}</div>}
      </div>

      <div className="max-h-[460px] overflow-auto">
        <div className="border-b border-zinc-900 px-4 py-2 text-[11px] uppercase tracking-[0.12em] text-zinc-600">Recent Activity</div>
        {activity.length === 0 ? (
          <div className="px-4 py-6 text-center">
            <div className="text-sm text-zinc-400">No PM account activity report rows</div>
            <div className="mt-1 text-xs text-zinc-600">{account?.available ? "waiting for Data API rows" : "waiting for account activity sync"}</div>
          </div>
        ) : (
          <table className="min-w-[760px] text-left text-xs">
            <thead className="sticky top-0 bg-zinc-950 text-[11px] uppercase tracking-[0.12em] text-zinc-600">
              <tr>
                <th className="px-4 py-2 font-medium">Time</th>
                <th className="px-3 py-2 font-medium">Type</th>
                <th className="px-3 py-2 font-medium">Market</th>
                <th className="px-3 py-2 font-medium">Side</th>
                <th className="px-3 py-2 font-medium">Price</th>
                <th className="px-3 py-2 font-medium">Size</th>
                <th className="px-3 py-2 font-medium">USDC</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-900">
              {activity.slice(0, 8).map((row, index) => (
                <tr key={`${row.transaction_hash || row.slug || "activity"}-${index}`} className="hover:bg-zinc-900/40">
                  <td className="px-4 py-2 font-mono text-zinc-500">{shortDateTime(row.timestamp_iso || row.timestamp || undefined)}</td>
                  <td className="px-3 py-2 font-mono text-zinc-300">{row.type || "-"}</td>
                  <td className="max-w-[260px] truncate px-3 py-2 font-mono text-zinc-400" title={row.title || row.slug || ""}>{row.slug || row.event_slug || "-"}</td>
                  <td className="px-3 py-2">
                    <span className="inline-flex items-center gap-1 font-mono text-zinc-200">
                      {directionIcon(row.outcome || row.side)}
                      {row.side || "-"} {row.outcome || ""}
                    </span>
                  </td>
                  <td className="px-3 py-2 font-mono text-zinc-100">{orderPrice(row.price)}</td>
                  <td className="px-3 py-2 font-mono text-zinc-300">{orderSize(row.size)}</td>
                  <td className="px-3 py-2 font-mono text-zinc-300">{orderValue(row.usdc_size)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        <div className="border-y border-zinc-900 px-4 py-2 text-[11px] uppercase tracking-[0.12em] text-zinc-600">Active Positions</div>
        {positions.length === 0 ? (
          <div className="px-4 py-6 text-center">
            <div className="text-sm text-zinc-400">No active PM positions</div>
            <div className="mt-1 text-xs text-zinc-600">Open account positions will appear here after Data API sync</div>
          </div>
        ) : (
          <table className="min-w-[760px] text-left text-xs">
            <thead className="sticky top-0 bg-zinc-950 text-[11px] uppercase tracking-[0.12em] text-zinc-600">
              <tr>
                <th className="px-4 py-2 font-medium">Market</th>
                <th className="px-3 py-2 font-medium">Outcome</th>
                <th className="px-3 py-2 font-medium">Size</th>
                <th className="px-3 py-2 font-medium">Avg</th>
                <th className="px-3 py-2 font-medium">Cash PnL</th>
                <th className="px-3 py-2 font-medium">Redeem</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-900">
              {positions.slice(0, 8).map((position, index) => (
                <tr key={`${position.slug || "position"}-${position.outcome || ""}-${index}`} className="hover:bg-zinc-900/40">
                  <td className="max-w-[280px] truncate px-4 py-2 font-mono text-zinc-400" title={position.title || position.slug || ""}>{position.slug || position.event_slug || "-"}</td>
                  <td className="px-3 py-2">
                    <span className="inline-flex items-center gap-1 font-mono text-zinc-200">
                      {directionIcon(position.outcome)}
                      {directionLabel(position.outcome || "-")}
                    </span>
                  </td>
                  <td className="px-3 py-2 font-mono text-zinc-300">{orderSize(position.size)}</td>
                  <td className="px-3 py-2 font-mono text-zinc-100">{orderPrice(position.avg_price)}</td>
                  <td className={`px-3 py-2 font-mono font-semibold ${position.cash_pnl == null ? "text-zinc-500" : position.cash_pnl >= 0 ? "text-emerald-300" : "text-rose-300"}`}>{orderValue(position.cash_pnl)}</td>
                  <td className="px-3 py-2 font-mono text-zinc-400">{position.redeemable ? "yes" : "no"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        <div className="border-y border-zinc-900 px-4 py-2 text-[11px] uppercase tracking-[0.12em] text-zinc-600">Redeemable Positions</div>
        {redeemablePositions.length === 0 ? (
          <div className="px-4 py-6 text-center">
            <div className="text-sm text-zinc-400">No redeemable positions</div>
            <div className="mt-1 text-xs text-zinc-600">Settled PM positions will appear here until redeemed</div>
          </div>
        ) : (
          <table className="min-w-[760px] text-left text-xs">
            <thead className="sticky top-0 bg-zinc-950 text-[11px] uppercase tracking-[0.12em] text-zinc-600">
              <tr>
                <th className="px-4 py-2 font-medium">Market</th>
                <th className="px-3 py-2 font-medium">Outcome</th>
                <th className="px-3 py-2 font-medium">Size</th>
                <th className="px-3 py-2 font-medium">Avg</th>
                <th className="px-3 py-2 font-medium">Cash PnL</th>
                <th className="px-3 py-2 font-medium">Redeem</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-900">
              {redeemablePositions.slice(0, 8).map((position, index) => (
                <tr key={`${position.slug || "redeemable"}-${position.outcome || ""}-${index}`} className="hover:bg-zinc-900/40">
                  <td className="max-w-[280px] truncate px-4 py-2 font-mono text-zinc-400" title={position.title || position.slug || ""}>{position.slug || position.event_slug || "-"}</td>
                  <td className="px-3 py-2">
                    <span className="inline-flex items-center gap-1 font-mono text-zinc-200">
                      {directionIcon(position.outcome)}
                      {directionLabel(position.outcome || "-")}
                    </span>
                  </td>
                  <td className="px-3 py-2 font-mono text-zinc-300">{orderSize(position.size)}</td>
                  <td className="px-3 py-2 font-mono text-zinc-100">{orderPrice(position.avg_price)}</td>
                  <td className={`px-3 py-2 font-mono font-semibold ${position.cash_pnl == null ? "text-zinc-500" : position.cash_pnl >= 0 ? "text-emerald-300" : "text-rose-300"}`}>{orderValue(position.cash_pnl)}</td>
                  <td className="px-3 py-2 font-mono text-zinc-400">{position.redeemable ? "yes" : "no"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </Panel>
  );
}

function LiveLedgerOrdersPanel({ liveReal }: { liveReal?: LiveSafety["live_real"] | null }) {
  const records = liveReal?.order_records ?? [];
  const visible = records.slice(0, 12);
  const positions = records.filter((record) => liveOrderGroup(record) === "Position").length;
  const settled = records.filter((record) => liveOrderGroup(record) === "Settled").length;
  const closed = records.filter((record) => liveOrderGroup(record) === "Closed").length;
  const attempts = records.reduce((total, record) => total + (record.attempts ?? record.order_chain_count ?? 1), 0);
  return (
    <Panel
      title="Live Ledger Orders"
      sub="signal executions with grouped order attempts"
      right={<span className="font-mono text-xs text-zinc-500">{records.length} signals</span>}
    >
      <div className="border-b border-zinc-900 px-4 py-3">
        <div className="grid grid-cols-4 gap-2">
          <HealthTile label="Position" value={`${positions}`} ok={positions === 0 ? true : undefined} />
          <HealthTile label="Settled" value={`${settled}`} />
          <HealthTile label="Closed" value={`${closed}`} />
          <HealthTile label="Attempts" value={`${attempts}`} />
        </div>
      </div>
      <div data-testid="live-ledger-mobile" className="space-y-2 p-3 md:hidden">
        {visible.length === 0 ? (
          <div className="py-6 text-center text-sm text-zinc-500">暂无实盘账本订单</div>
        ) : visible.map((order, index) => {
          const status = String(order.status || "-");
          const pnl = order.pnl ?? null;
          const pnlTone = pnl == null ? "text-zinc-500" : pnl >= 0 ? "text-emerald-300" : "text-rose-300";
          return (
            <article key={order.signal_id || order.order_id || `${order.market_slug}-${index}`} className="min-w-0 rounded border border-zinc-800 bg-black/20 p-3">
              <div className="flex min-w-0 items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="break-all font-mono text-xs text-zinc-300">{compactId(order.signal_id || order.order_id || undefined)}</div>
                  <div className="mt-1 break-words text-xs text-zinc-500">{order.market_slug || "未知市场"}</div>
                </div>
                <div className={`shrink-0 font-mono text-sm font-semibold ${pnlTone}`}>{orderValue(pnl)}</div>
              </div>
              <div className="mt-3 grid grid-cols-2 gap-2 text-xs">
                <div><span className="text-zinc-600">状态</span><div className="mt-0.5 text-zinc-300">{liveOrderGroup(order)}</div></div>
                <div><span className="text-zinc-600">方向</span><div className="mt-0.5 font-mono text-zinc-300">{directionLabel(order.token_outcome || order.direction || "-")}</div></div>
                <div><span className="text-zinc-600">成交份额</span><div className="mt-0.5 font-mono text-zinc-300">{orderSize(order.filled_size)}</div></div>
                <div><span className="text-zinc-600">均价</span><div className="mt-0.5 font-mono text-zinc-300">{orderPrice(order.average_fill_price ?? order.price)}</div></div>
                <div className="col-span-2"><span className="text-zinc-600">结算来源</span><div className="mt-0.5 break-words font-mono text-zinc-300">{order.settlement_source || order.market_result_source || order.fill_source || "-"}</div></div>
              </div>
              <div className="mt-2 text-[10px] text-zinc-600">{status} | {shortDateTime(order.settle_ts || order.settled_at || undefined)}</div>
            </article>
          );
        })}
      </div>
      <div className="hidden max-h-[420px] w-full min-w-0 max-w-full overflow-x-auto overflow-y-auto md:block">
        {visible.length === 0 ? (
          <div className="px-4 py-8 text-center">
            <div className="text-sm text-zinc-400">No live ledger orders</div>
            <div className="mt-1 text-xs text-zinc-600">Filled and settled real orders will appear here</div>
          </div>
        ) : (
          <table className="min-w-[980px] text-left text-xs">
            <thead className="sticky top-0 bg-zinc-950 text-[11px] uppercase tracking-[0.12em] text-zinc-600">
              <tr>
                <th className="px-4 py-2 font-medium">Signal</th>
                <th className="px-3 py-2 font-medium">Group</th>
                <th className="px-3 py-2 font-medium">Attempts</th>
                <th className="px-3 py-2 font-medium">Market</th>
                <th className="px-3 py-2 font-medium">Dir</th>
                <th className="px-3 py-2 font-medium">Price</th>
                <th className="px-3 py-2 font-medium">Filled</th>
                <th className="px-3 py-2 font-medium">Remaining</th>
                <th className="px-3 py-2 font-medium">PnL</th>
                <th className="px-3 py-2 font-medium">Settle</th>
                <th className="px-3 py-2 font-medium">Source</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-900">
              {visible.map((order, index) => {
                const status = String(order.status || "-");
                const group = liveOrderGroup(order);
                const attemptCount = order.attempts ?? order.order_chain_count ?? 1;
                const pnl = order.pnl ?? null;
                const hypotheticalPnl = order.hypothetical_pnl ?? null;
                const showHypothetical = pnl == null && hypotheticalPnl != null;
                const pnlTone = pnl == null ? "text-zinc-500" : pnl >= 0 ? "text-emerald-300" : "text-rose-300";
                const hypotheticalTone = hypotheticalPnl == null ? "text-zinc-500" : hypotheticalPnl >= 0 ? "text-emerald-300" : "text-rose-300";
                return (
                  <tr key={order.signal_id || order.order_id || `${order.market_slug}-${index}`} className="hover:bg-zinc-900/40">
                    <td className="px-4 py-2 font-mono text-zinc-300" title={order.signal_id || order.order_id || ""}>{compactId(order.signal_id || order.order_id || undefined)}</td>
                    <td className="px-3 py-2">
                      <span className={`inline-flex items-center rounded border px-2 py-0.5 font-mono text-[11px] ${liveOrderStatusTone(status)}`}>{group}</span>
                    </td>
                    <td className="px-3 py-2 font-mono text-zinc-300" title={(order.order_chain ?? []).map((item) => `${compactId(item.order_id || undefined)} ${item.status || ""} ${orderPrice(item.price)}`).join(" | ")}>
                      {attemptCount}
                    </td>
                    <td className="max-w-[230px] truncate px-3 py-2 font-mono text-zinc-400" title={order.market_slug || ""}>{order.market_slug || "-"}</td>
                    <td className="px-3 py-2">
                      <span className="inline-flex items-center gap-1 font-mono text-zinc-200">
                        {directionIcon(order.token_outcome || order.direction || undefined)}
                        {directionLabel(order.token_outcome || order.direction || "-")}
                      </span>
                    </td>
                    <td className="px-3 py-2 font-mono text-zinc-100">{orderPrice(order.average_fill_price ?? order.price)}</td>
                    <td className="px-3 py-2 font-mono text-zinc-300">{orderSize(order.filled_size)}</td>
                    <td className="px-3 py-2 font-mono text-zinc-300">{orderSize(order.remaining_size)}</td>
                    <td className="px-3 py-2 font-mono">
                      <div className={`font-semibold ${pnlTone}`}>{orderValue(pnl)}</div>
                      {showHypothetical && (
                        <div
                          className={`mt-0.5 text-[10px] font-medium ${hypotheticalTone}`}
                          title="Hypothetical result if this closed no-fill order had fully filled"
                        >
                          Would {orderValue(hypotheticalPnl)}
                        </div>
                      )}
                    </td>
                    <td className="px-3 py-2 font-mono text-zinc-500">{shortDateTime(order.settle_ts || order.settled_at || undefined)}</td>
                    <td className="px-3 py-2 font-mono text-zinc-500">{order.settlement_source || order.market_result_source || order.fill_source || "-"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </Panel>
  );
}

function PaperRuntimePanel({ paperMonitor }: { paperMonitor?: LiveSafety["paper_monitor"] | null }) {
  const runtime = runtimeState(paperMonitor?.runtime);
  return (
    <Panel
      title="Paper Runtime"
      sub={paperMonitor?.run_source || "paper runner"}
      right={<StatusPill ok={runtime.ok === true} label={runtime.label} />}
    >
      <div className="grid grid-cols-2 gap-2 p-4 md:grid-cols-4">
        <HealthTile label="Runner" value={runtime.label} ok={runtime.ok} />
        <HealthTile label="Started" value={shortDateTime(paperMonitor?.runtime?.started_at || undefined)} ok={runtime.ok} />
        <HealthTile label="Checkpoint Age" value={ageLabel(paperMonitor?.checkpoint_age_seconds)} ok={(paperMonitor?.checkpoint_age_seconds ?? 9999) < 600} />
        <HealthTile label="Balance" value={paperMonitor?.balance == null ? "-" : money(paperMonitor.balance)} />
        <HealthTile label="Open" value={`${paperMonitor?.orders.open ?? 0}`} ok={(paperMonitor?.orders.open ?? 0) === 0} />
        <HealthTile label="Pending" value={`${paperMonitor?.orders.pending ?? 0}`} ok={(paperMonitor?.orders.pending ?? 0) === 0} />
        <HealthTile label="Settled" value={`${paperMonitor?.orders.settled ?? 0}`} />
        <HealthTile label="Source" value={paperMonitor?.source_label || "-"} />
      </div>
    </Panel>
  );
}

export type TradingTab = "live-real" | "paper-monitor";

export default function Live({
  activeTradingTab,
  onTradingTabChange,
}: {
  activeTradingTab: TradingTab;
  onTradingTabChange?: (next: TradingTab) => void;
}) {
  const [expandedSignal, setExpandedSignal] = useState<number | null>(null);
  const [expandedTrade, setExpandedTrade] = useState<number | null>(null);
  const isLiveRealTab = activeTradingTab === "live-real";
  const isPaperMonitorTab = !isLiveRealTab;
  const eventSource = isLiveRealTab ? "live_real" : "paper";
  const { data: status } = usePolling<StatusData>("/api/status", 5000);
  const { data: events } = usePolling<EventItem[]>(`/api/events?source=${eventSource}&limit=80`, 5000);
  const { data: trades } = usePolling<TradeItem[]>("/api/trades?limit=200", 5000);
  const { data: intel } = usePolling<LiveIntel>("/api/live-intel?limit=80", 5000);
  const { data: safety } = usePolling<LiveSafety>("/api/live-safety", 30000);
  const { data: signalStats } = usePolling<SignalStats>(`/api/signal-stats?source=${eventSource}`, 10000);
  const liveReal = safety?.live_real;
  const paperMonitor = safety?.paper_monitor;

  const allTrades = trades ?? [];
  const settledDesc = useMemo(
    () => allTrades.filter((trade) => trade.won !== -1).sort((a, b) => sortValue(b.settle_bar) - sortValue(a.settle_bar)),
    [allTrades],
  );
  const pending = useMemo(
    () => allTrades.filter((trade) => trade.won === -1).sort((a, b) => sortValue(a.settle_bar) - sortValue(b.settle_bar)),
    [allTrades],
  );
  const latestSignal = events?.[0];
  const latestDetails = latestSignal ? parseJson<SignalDetails>(latestSignal.details) : null;
  const health = intel?.health;
  const isHealthy = health?.state === "ok";
  const todayStats = safety?.today;
  const todayPnl = todayStats?.trades.pnl_usdc ?? 0;
  const todaySettled = todayStats?.trades.settled ?? 0;
  const todayWins = todayStats?.trades.wins ?? 0;
  const todayLosses = todayStats?.trades.losses ?? 0;
  const todayWinRate = todayStats?.trades.win_rate ?? 0;
  const todayOpen = todayStats?.trades.open ?? 0;
  const todaySignalPassed = todayStats?.signals.passed ?? 0;
  const todaySignalTotal = todayStats?.signals.total ?? 0;
  const todayPassRate = todayStats?.signals.pass_rate ?? 0;
  const paperTotalSettled = paperMonitor?.stats.settled ?? 0;
  const paperTotalWinRate = paperMonitor?.stats.win_rate ?? 0;
  const paperTotalSignalPassed = paperMonitor?.signals.passed ?? signalStats?.passed ?? 0;
  const paperTotalSignalTotal = paperMonitor?.signals.total ?? signalStats?.total ?? 0;
  const paperTotalPassRate = paperMonitor?.signals.pass_rate ?? signalStats?.pass_rate ?? 0;
  const latestSignalKpiAge = todayStats?.activity?.latest_signal_age_seconds;
  const latestSignalFresh = (latestSignalKpiAge ?? 9999) < 600;
  const signalKpiTone = todaySignalTotal === 0 ? "text-zinc-100" : latestSignalFresh ? "text-emerald-300" : "text-amber-300";
  const readinessSummary = safety?.readiness_summary;
  const readinessPassed = readinessSummary?.passed ?? 0;
  const readinessTotal = readinessSummary?.total ?? 0;
  const readinessCritical = readinessSummary?.critical_blockers ?? 0;
  const readinessReady = readinessSummary?.ready === true;
  const readinessTone = readinessReady ? "text-emerald-300" : readinessCritical > 0 ? "text-rose-300" : "text-amber-300";
  const riskResilience = safety?.risk?.resilience ?? liveReal?.risk_resilience;
  const riskBlockerItems = (safety?.checklist ?? []).filter((item) => item.severity === "risk" && !item.ok);
  const riskLimits = safety?.risk?.limits;
  const riskOpenPendingHardBlocked = riskBlockerItems.some(
    (item) => item.key === "risk_open_or_pending" && Number(item.value ?? 0) > Number(riskLimits?.max_open_or_pending_orders ?? Number.POSITIVE_INFINITY),
  );
  const riskHardBlockerItems = riskBlockerItems.filter((item) => item.key !== "risk_open_or_pending" || riskOpenPendingHardBlocked);
  const riskControlTriggered = riskHardBlockerItems.length > 0 || riskResilience?.ok === false;
  const riskControlReason = riskResilience?.ok === false ? riskResilience?.reason : riskHardBlockerItems.map((item) => item.label).join(", ");
  const dryrun = safety?.dryrun;
  const dryrunSubmitted = dryrun?.submitted_count ?? 0;
  const dryrunValue = `${dryrun?.would_place_count ?? 0}/${dryrun?.ledger_count ?? 0}`;
  const dryrunSub = `${dryrunSubmitted} submitted / ${dryrun?.blocked_count ?? 0} blocked`;
  const dryrunTone = dryrunSubmitted === 0 ? "text-emerald-300" : "text-rose-300";
  const marketData = safety?.market_data;
  const marketReady = marketData?.ready === true;
  const marketValue = marketReady ? "Fresh" : marketData?.status ? marketData.status : "Waiting";
  const marketTone = marketReady ? "text-emerald-300" : marketData?.status === "stale" ? "text-rose-300" : "text-amber-300";
  const funding = safety?.funding;
  const fundingBalance = funding?.balance ?? status?.balance ?? INITIAL_BALANCE;
  const portfolio = liveReal?.account_activity?.portfolio;
  const clobPortfolioValue = portfolio?.total_value_usdc ?? fundingBalance;
  const fundingBalanceGap = funding?.balance_shortfall_usdc ?? 0;
  const fundingAllowanceGap = funding?.allowance_shortfall_usdc ?? 0;
  const fundingLargestGap = Math.max(fundingBalanceGap, fundingAllowanceGap);
  const fundingBalanceSub =
    fundingBalanceGap > 0
      ? `Balance gap ${money(fundingBalanceGap)}`
      : fundingAllowanceGap > 0
      ? `Allowance gap ${money(fundingAllowanceGap)}`
        : "CLOB account";
  const clobPortfolioSub = portfolio ? `cash ${money(portfolio.cash_balance_usdc ?? 0)} | positions ${money(portfolio.positions_value_usdc ?? 0)}` : fundingBalanceSub;
  const fundingBalanceTone = funding?.funding_ready === true || funding?.balance_ok === true ? "text-emerald-300" : fundingLargestGap > 0 ? "text-rose-300" : "text-zinc-100";
  const pendingCount = pending.length;
  const openPendingUsed = todayOpen + pendingCount;
  const openPendingLimit = riskLimits?.max_open_or_pending_orders;
  const openPendingValue = `${todayOpen}/${pendingCount}`;
  const openPendingSub = openPendingLimit == null ? "open / pending" : `open / pending | risk limit ${openPendingLimit}`;
  const openPendingTone = openPendingLimit == null ? "text-zinc-100" : openPendingUsed <= openPendingLimit ? "text-emerald-300" : "text-rose-300";
  const checkpointAge = health?.checkpoint_age_seconds;
  const latestEventAge = health?.latest_event_age_seconds;
  const checkpointFresh = (checkpointAge ?? 9999) < 600;
  const latestEventFresh = (latestEventAge ?? 9999) < 600;
  const runtimeFresh = checkpointFresh && latestEventFresh;
  const healthValue = isHealthy && runtimeFresh ? "OK" : "Review";
  const healthTone = healthValue === "OK" ? "text-emerald-300" : "text-amber-300";
  const allowanceApproved = funding?.allowance_ok === true || funding?.funding_ready === true;
  const allowanceValue = compactAllowance(funding?.min_allowance, allowanceApproved);
  const allowanceSub = fundingAllowanceGap > 0 ? `Allowance gap ${money(fundingAllowanceGap)}` : allowanceApproved ? `${funding?.allowance_count ?? 0} spenders approved` : "allowance review";
  const allowanceTone = funding?.funding_ready === true || funding?.allowance_ok === true ? "text-emerald-300" : fundingAllowanceGap > 0 ? "text-rose-300" : "text-zinc-100";
  const sourceLabel = safety?.source_label ?? safety?.run_source ?? "Source";
  const recentSettled = settledDesc.slice(0, 10);
  const liveRuntime = runtimeState(liveReal?.runtime);
  const paperRuntime = runtimeState(paperMonitor?.runtime);
  const liveEquity = liveReal?.equity;
  const paperEquity = paperMonitor?.equity;
  const liveEquityPoints = liveEquity?.points?.length ? liveEquity.points : [fundingBalance];
  const paperEquityPoints = paperEquity?.points?.length ? paperEquity.points : [paperMonitor?.balance ?? INITIAL_BALANCE];
  const paperEquityPnl = paperEquity?.pnl_usdc ?? 0;
  const liveOrderCount = liveReal?.orders.open_or_pending ?? 0;
  const liveTrades = liveReal?.risk?.metrics.daily_trades ?? 0;
  const livePnl = liveReal?.risk?.metrics.daily_pnl_usdc ?? 0;
  const liveTotalWins = liveReal?.stats.wins ?? 0;
  const liveTotalLosses = liveReal?.stats.losses ?? 0;
  const liveTotalWinRate = liveReal?.stats.win_rate ?? 0;
  const liveEquityPnl = liveEquity?.pnl_usdc ?? livePnl;
  const liveRiskLimits = liveReal?.risk?.limits;
  const liveSubmitted = liveReal?.orders.total ?? (liveReal?.formal?.submitted ? 1 : liveReal?.soak.submitted_count ?? 0);
  const liveNoFillCancelled = liveReal?.orders.no_fill_cancelled ?? 0;
  const paperOpenPending = (paperMonitor?.orders.open ?? todayOpen) + (paperMonitor?.orders.pending ?? pendingCount);
  const modeStatus = isPaperMonitorTab ? "Simulation only" : safety?.real_orders_enabled ? "REAL ORDERS ENABLED" : "Real orders locked";
  const modeStatusOk = isPaperMonitorTab ? true : !safety?.real_orders_enabled;
  const liveDailyLossLimit = liveRiskLimits?.max_daily_loss_usdc ?? 0;
  const liveRiskRemaining = Math.max(0, liveDailyLossLimit + livePnl);
  const liveActivePositions = liveReal?.account_activity?.summary.active_positions_count ?? 0;
  const liveOpenOrders = safety?.clob_readonly?.open_orders?.length ?? 0;
  const latestDecisionAction = liveReal?.formal?.latest_action || latestDetails?.action || latestSignal?.action || "HOLD";
  const latestDecisionSide = latestDetails?.side || latestSignal?.dir5 || "--";
  const latestDecisionMarket = latestDetails?.market_slug || rangeLabel(latestDetails?.entry_ts || "--", latestDetails?.settle_ts || "--");
  const latestDecisionSettle = latestDetails?.settle_ts || liveReal?.formal?.target_market_settle_ts || liveReal?.formal?.settle_ts || "";
  const liveCockpitMetrics: CockpitMetric[] = [
    {
      id: "equity",
      label: "账户权益",
      value: money(clobPortfolioValue),
      detail: clobPortfolioSub,
      tone: fundingBalanceTone,
      icon: Wallet,
    },
    {
      id: "today-pnl",
      label: "今日已实现 PnL",
      value: signedMoney(livePnl),
      detail: `${liveTrades} 笔结算`,
      tone: livePnl >= 0 ? "text-emerald-300" : "text-rose-300",
      icon: CircleDollarSign,
    },
    {
      id: "settled-result",
      label: "结算结果",
      value: signedMoney(liveEquityPnl),
      detail: `胜率 ${percent(liveTotalWinRate)} | ${liveTotalWins}胜/${liveTotalLosses}负 | ${liveEquity?.settled ?? 0}笔`,
      tone: liveEquityPnl >= 0 ? "text-emerald-300" : "text-rose-300",
      icon: Target,
    },
    {
      id: "risk-budget",
      label: "当前风险余量",
      value: money(liveRiskRemaining),
      detail: `日亏损线 ${money(liveDailyLossLimit)}`,
      tone: riskControlTriggered ? "text-rose-300" : "text-amber-300",
      icon: AlertTriangle,
    },
    {
      id: "exposure",
      label: "当前敞口",
      value: `${liveActivePositions} 持仓`,
      detail: `${liveOpenOrders} 挂单 | ${liveOrderCount} open/pending`,
      tone: liveOrderCount > 0 ? "text-amber-300" : "text-zinc-100",
      icon: ListChecks,
    },
    {
      id: "latest-decision",
      label: "最新决策",
      value: `${latestDecisionAction} ${latestDecisionSide}`,
      detail: latestDecisionSettle ? `结算 ${shortDateTime(latestDecisionSettle)}` : "等待新信号",
      tone: latestDecisionAction === "HOLD" ? "text-zinc-300" : "text-emerald-300",
      icon: Activity,
    },
  ];

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="inline-flex rounded-md border border-zinc-800 bg-zinc-950 p-1">
          {(["live-real", "paper-monitor"] as TradingTab[]).map((key) => (
            <button
              key={key}
              type="button"
              onClick={() => onTradingTabChange?.(key)}
              className={`rounded px-3 py-1.5 text-xs font-medium transition-colors ${
                activeTradingTab === key ? "bg-emerald-500/15 text-emerald-300" : "text-zinc-500 hover:text-zinc-200"
              }`}
            >
              {key === "live-real" ? "Live Real" : "Paper Monitor"}
            </button>
          ))}
        </div>
        <StatusPill ok={modeStatusOk} label={modeStatus} />
      </div>

      {activeTradingTab === "live-real" && (
        <div className="space-y-3 md:space-y-5">
          {riskControlTriggered && (
            <div className="rounded-md border border-rose-500/30 bg-rose-500/10 px-4 py-3">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <div className="text-sm font-semibold text-rose-200">Risk Control Triggered</div>
                  <div className="mt-1 text-xs text-rose-100/80">{riskControlReason || "Live risk controls are blocking new entries."}</div>
                </div>
                <div className="flex flex-wrap gap-2">
                  {riskBlockerItems.slice(0, 4).map((item) => (
                    <StatusPill key={item.key} ok={false} danger label={`${item.label}: ${item.value == null ? "-" : `${item.value}`}`} />
                  ))}
                </div>
              </div>
            </div>
          )}
          <LiveCockpitSummary metrics={liveCockpitMetrics} updatedAt={liveReal?.formal?.latest_created_at || safety?.market_data?.timestamp} />

          <LiveCurrentAction
            decision={{
              action: latestDecisionAction,
              side: latestDecisionSide,
              market: latestDecisionMarket,
              settleAt: latestDecisionSettle,
              reason: liveReal?.formal?.latest_reason || latestDetails?.reason || "等待下一次正式预测",
            }}
            openOrders={liveOpenOrders}
            funnel={{
              submitted: liveSubmitted,
              filled: liveReal?.orders.filled_orders ?? 0,
              noFill: liveNoFillCancelled,
              settled: liveReal?.orders.settled ?? 0,
            }}
          />

          <div className="grid min-w-0 gap-4 live-main-console">
            <LiveMarketSection
              equity={{ points: liveEquityPoints, pnlUsdc: liveEquityPnl, settled: liveEquity?.settled ?? 0, source: "实盘结算账本" }}
              weeklyCalendar={liveReal?.weekly_pnl_calendar}
            />
            <LiveMonthlyPnlCalendar
              source="live_real"
              initialMonth={(todayStats?.day ?? todayStats?.day_utc ?? new Date().toISOString().slice(0, 10)).slice(0, 7)}
            />
          </div>

          <LiveOperationsDetails
            health={{
              description: "进程、订单同步、风控规则与实盘执行汇总",
              summary: `${liveRuntime.label} | ${riskBlockerItems.length} 个风险提示`,
              status: liveRuntime.ok && !riskControlTriggered ? "正常" : "需检查",
              warning: !liveRuntime.ok || riskControlTriggered,
              content: (
                <div className="grid min-w-0 gap-4 xl:grid-cols-2">
                  <LiveSoakPanel liveReal={liveReal} />
                  <RiskRulesPanel controls={liveReal?.risk_controls} />
                  <TradingStatusPanel safety={safety} />
                  <LiveRealOrdersPanel liveReal={liveReal} />
                </div>
              ),
            }}
            account={{
              description: "Polymarket 账户活动、当前挂单与可赎回持仓",
              summary: `${liveActivePositions} 个持仓 | ${liveOpenOrders} 个挂单`,
              status: liveReal?.account_activity?.ok === false ? "需检查" : "正常",
              warning: liveReal?.account_activity?.ok === false,
              content: (
                <div className="grid min-w-0 gap-4 xl:grid-cols-2">
                  <CurrentClobOrdersPanel audit={safety?.clob_readonly} />
                  <PolymarketAccountActivityPanel account={liveReal?.account_activity} />
                </div>
              ),
            }}
            ledger={{
              description: "按信号聚合的订单尝试、成交与结算记录",
              summary: `${liveReal?.order_records?.length ?? 0} 个信号`,
              status: "只读",
              content: <LiveLedgerOrdersPanel liveReal={liveReal} />,
            }}
            diagnostics={{
              description: "安全门、数据新鲜度、只读审计与原始信号",
              summary: `就绪 ${readinessPassed}/${readinessTotal} | ${healthValue}`,
              status: readinessReady && runtimeFresh ? "正常" : "需检查",
              warning: !readinessReady || !runtimeFresh,
              content: (
                <div className="space-y-4">
                  <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-zinc-900 pb-3 text-xs">
                    <span className="text-zinc-500">Dry-run</span>
                    <span className={`font-mono ${dryrunTone}`}>{dryrunValue}</span>
                    <span className="text-zinc-600">{dryrunSub}</span>
                  </div>
                  <div className="grid gap-4 xl:grid-cols-[minmax(0,0.9fr)_minmax(420px,1.1fr)]">
                    <SafetyStrip safety={safety} health={health} />
                    <ReadinessChecklist safety={safety} health={health} intel={intel} />
                  </div>
                  <div className="grid gap-4 xl:grid-cols-4">
                    <MarketDataPanel data={safety?.market_data} />
                    <ReportFreshnessPanel refresh={safety?.report_refresh} />
                    <LegacyReportFreshnessPanel refresh={safety?.legacy_report_refresh} />
                    <RiskPanel intel={intel} safety={safety} />
                  </div>
                  <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(420px,0.8fr)]">
                    <ClobReadonlyPanel audit={safety?.clob_readonly} />
                    <FirstOrderRail rail={safety?.first_order_rail} />
                  </div>
                  <Panel title="最近信号" sub="正式实盘预测记录" right={<span className="font-mono text-xs text-zinc-500">{events?.length ?? 0}</span>}>
                    <div className="max-h-[280px] overflow-auto">
                      {(events?.length ?? 0) === 0 ? (
                        <div className="px-4 py-8 text-center text-sm text-zinc-500">等待正式实盘决策</div>
                      ) : (
                        (events ?? []).slice(0, 14).map((event) => (
                          <SignalCard key={event.id} event={event} expanded={expandedSignal === event.id} onToggle={() => setExpandedSignal(expandedSignal === event.id ? null : event.id)} />
                        ))
                      )}
                    </div>
                  </Panel>
                </div>
              ),
            }}
          />
        </div>
      )}

      {activeTradingTab === "paper-monitor" && (
        <div className="space-y-5">
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4 2xl:grid-cols-8">
            <StatCard label="Paper Balance" value={money(paperMonitor?.balance ?? status?.balance ?? INITIAL_BALANCE)} sub={`${paperMonitor?.source_label ?? sourceLabel} monitor`} icon={Wallet} tone="text-zinc-100" />
            <StatCard label="Open/Pending" value={openPendingValue} sub={openPendingSub} icon={Clock3} tone={openPendingTone} />
            <StatCard label="Today PnL" value={signedMoney(todayPnl)} sub={`${todaySettled} settled today`} icon={CircleDollarSign} tone={todayPnl >= 0 ? "text-emerald-300" : "text-rose-300"} />
            <StatCard label="Total Win Rate" value={percent(paperTotalWinRate)} sub={`${paperTotalSettled} settled total`} icon={Target} tone={paperTotalSettled === 0 ? "text-zinc-100" : paperTotalWinRate >= 0.51 ? "text-emerald-300" : "text-amber-300"} />
            <StatCard label="Today Win Rate" value={percent(todayWinRate)} sub={`${todayWins}W / ${todayLosses}L today`} icon={Target} tone={todaySettled === 0 ? "text-zinc-100" : todayWinRate >= 0.51 ? "text-emerald-300" : "text-amber-300"} />
            <StatCard label="Total Pass Rate" value={percent(paperTotalPassRate)} sub={`${paperTotalSignalPassed}/${paperTotalSignalTotal} signals`} icon={Activity} tone={paperTotalSignalTotal === 0 ? "text-zinc-100" : paperTotalPassRate > 0 ? "text-emerald-300" : "text-amber-300"} />
            <StatCard label="Today Pass Rate" value={percent(todayPassRate)} sub={`${todaySignalPassed}/${todaySignalTotal} signals today`} icon={Activity} tone={signalKpiTone} />
            <StatCard label="Runtime" value={paperRuntime.label} sub={`${paperOpenPending} open or pending`} icon={RadioTower} tone={paperRuntime.ok ? "text-emerald-300" : "text-amber-300"} />
          </div>

          <div className="grid min-w-0 gap-5 2xl:grid-cols-[minmax(0,1.6fr)_minmax(360px,0.45fr)]">
            <LiveMarketSection equity={{ points: paperEquityPoints, pnlUsdc: paperEquityPnl, settled: paperEquity?.settled ?? paperMonitor?.orders.settled ?? 0, source: "模拟结算账本" }} />
            <PaperRuntimePanel paperMonitor={paperMonitor} />
          </div>

          <LiveMonthlyPnlCalendar
            source="paper_monitor"
            initialMonth={(todayStats?.day ?? todayStats?.day_utc ?? new Date().toISOString().slice(0, 10)).slice(0, 7)}
          />

          <div className="grid min-w-0 gap-5 xl:grid-cols-[minmax(0,1.3fr)_minmax(380px,0.7fr)]">
            <Panel title="Completed Trades" sub="latest fills" right={<span className="font-mono text-xs text-zinc-500">latest {Math.min(10, settledDesc.length)} / {settledDesc.length}</span>}>
              <div className="max-h-[420px] w-full max-w-[calc(100vw-2rem)] overflow-x-auto overflow-y-auto">
                <table className="w-full table-fixed text-sm tabular-nums md:min-w-[760px]">
                  <thead className="sticky top-0 z-10 border-b border-zinc-800 bg-zinc-950/95 text-xs uppercase tracking-[0.12em] text-zinc-500">
                    <tr>
                      <th className="px-4 py-2.5 text-left">Window</th>
                      <th className="px-3 py-2.5 text-left">Dir</th>
                      <th className="px-3 py-2.5 text-left">Result</th>
                      <th className="px-3 py-2.5 text-left">Recorded</th>
                      <th className="px-3 py-2.5 text-right">Size</th>
                      <th className="px-3 py-2.5 text-right">PnL</th>
                      <th className="px-3 py-2.5" />
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-zinc-900">
                    {settledDesc.length === 0 ? (
                      <tr>
                        <td colSpan={7} className="px-4 py-10 text-center">
                          <div className="text-sm text-zinc-400">No completed trades</div>
                          <div className="mt-1 text-xs text-zinc-600">Settled trades will appear here</div>
                        </td>
                      </tr>
                    ) : recentSettled.map((trade) => {
                      const open = expandedTrade === trade.id;
                      const details = parseJson<SignalDetails>(trade.details);
                      return (
                        <Fragment key={trade.id}>
                          <tr className="cursor-pointer hover:bg-zinc-900/60" onClick={() => setExpandedTrade(open ? null : trade.id)}>
                            <td className="px-4 py-2.5 font-mono text-xs text-zinc-300">{rangeLabel(trade.entry_bar, trade.settle_bar)}</td>
                            <td className="px-3 py-2.5">
                              <span className="inline-flex items-center gap-1 font-mono text-xs text-zinc-300">{directionIcon(trade.direction)}{directionLabel(trade.direction)}</span>
                            </td>
                            <td className="px-3 py-2.5"><ResultPill won={trade.won} /></td>
                            <td className="px-3 py-2.5 font-mono text-xs text-zinc-400">{localTime(tradeRecordedAt(trade, details))}</td>
                            <td className="px-3 py-2.5 text-right font-mono text-zinc-300">{money(trade.size ?? 0)}</td>
                            <td className={`px-3 py-2.5 text-right font-mono font-semibold ${trade.pnl >= 0 ? "text-emerald-300" : "text-rose-300"}`}>{signedMoney(trade.pnl ?? 0)}</td>
                            <td className="px-3 py-2.5 text-right"><ChevronDown className={`ml-auto h-3.5 w-3.5 text-zinc-600 transition-transform ${open ? "rotate-180" : ""}`} /></td>
                          </tr>
                          {open && (
                            <tr>
                              <td colSpan={7} className="bg-black/20 px-4 py-3">
                                <div className="grid gap-3 text-xs text-zinc-500 md:grid-cols-4">
                                  <div>reason <span className="font-mono text-zinc-300">{details?.reason_code || trade.regime || "-"}</span></div>
                                  <div>5m/1h/4h <span className="font-mono text-zinc-300">{percent(details?.p5_up)} / {percent(details?.p1_up)} / {percent(details?.p4_up)}</span></div>
                                  <div>score <span className="font-mono text-zinc-300">L {score(details?.long_score)} / S {score(details?.short_score)}</span></div>
                                  <div>maker <span className="font-mono text-zinc-300">{details?.maker_price?.toFixed(2) ?? "-"}</span></div>
                                </div>
                              </td>
                            </tr>
                          )}
                        </Fragment>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </Panel>

            <div className="min-w-0 max-w-full space-y-5">
              <Panel title="Pending Queue" sub="awaiting settlement" right={<span className="font-mono text-xs text-zinc-500">{pending.length}</span>}>
                <div className="max-h-[220px] divide-y divide-zinc-900 overflow-auto">
                  {pending.length === 0 ? (
                    <div className="px-4 py-8 text-center text-sm text-zinc-500">No pending orders</div>
                  ) : (
                    pending.map((trade) => (
                      <div key={trade.id} className="px-4 py-3">
                        <div className="flex items-center justify-between gap-3">
                          <ResultPill won={trade.won} />
                          <span className="font-mono text-xs text-zinc-400">{rangeLabel(trade.entry_bar, trade.settle_bar)}</span>
                        </div>
                        <div className="mt-2 flex items-center justify-between text-xs text-zinc-500">
                          <span className="inline-flex items-center gap-1 text-zinc-300">{directionIcon(trade.direction)}{directionLabel(trade.direction)}</span>
                          <span className="font-mono">{money(trade.size ?? 0)}</span>
                        </div>
                        <div className="mt-2 font-mono text-[11px] uppercase tracking-[0.12em] text-amber-300">{pendingStatusLabel(trade.settle_bar)}</div>
                      </div>
                    ))
                  )}
                </div>
              </Panel>
            </div>
          </div>

          <CollapsiblePanel
            title="Paper Diagnostics"
            sub="paper runner, signals, and strategy monitor"
            right={<StatusPill ok={isHealthy && runtimeFresh} label={isHealthy && runtimeFresh ? "Clean" : "Review"} />}
            summary={
              <div className="grid gap-2 text-xs md:grid-cols-4">
                <div className="flex items-center justify-between gap-3">
                  <span className="text-zinc-500">Health</span>
                  <span className={`font-mono ${healthTone}`}>{healthValue}</span>
                </div>
                <div className="flex items-center justify-between gap-3">
                  <span className="text-zinc-500">Signals</span>
                  <span className={`font-mono ${signalKpiTone}`}>{todaySignalPassed}/{todaySignalTotal}</span>
                </div>
                <div className="flex items-center justify-between gap-3">
                  <span className="text-zinc-500">Market</span>
                  <span className={`font-mono ${marketTone}`}>{marketValue}</span>
                </div>
                <div className="flex items-center justify-between gap-3">
                  <span className="text-zinc-500">Readiness</span>
                  <span className={`font-mono ${readinessTone}`}>{readinessPassed}/{readinessTotal}</span>
                </div>
              </div>
            }
          >
            <div className="space-y-5 p-4">
              <div className="grid gap-5 xl:grid-cols-3">
                <TodayCockpit today={todayStats} />
                <FunnelPanel funnel={intel?.funnel} />
                <MakerPanel intel={intel} />
              </div>

              <Panel title="Recent Signals" sub="aligned-prod decisions" right={<span className="font-mono text-xs text-zinc-500">{events?.length ?? 0}</span>}>
                <div className="max-h-[280px] overflow-auto">
                  {(events?.length ?? 0) === 0 ? (
                    <div className="px-4 py-8 text-center">
                      <div className="text-sm text-zinc-400">No recent signals</div>
                      <div className="mt-1 text-xs text-zinc-600">Waiting for aligned-prod decisions</div>
                    </div>
                  ) : (
                    (events ?? []).slice(0, 14).map((event) => (
                      <SignalCard key={event.id} event={event} expanded={expandedSignal === event.id} onToggle={() => setExpandedSignal(expandedSignal === event.id ? null : event.id)} />
                    ))
                  )}
                </div>
              </Panel>

              <Panel title="System Health" sub="runner, checkpoint, events, logs" right={<StatusPill ok={isHealthy} label={isHealthy ? "OK" : "Review"} />}>
                <div className="space-y-3 p-4">
                  <div className="grid grid-cols-2 gap-2">
                    <HealthTile label="Checkpoint" value={ageLabel(health?.checkpoint_age_seconds)} ok={(health?.checkpoint_age_seconds ?? 9999) < 600} />
                    <HealthTile label="Latest Event" value={ageLabel(health?.latest_event_age_seconds)} ok={(health?.latest_event_age_seconds ?? 9999) < 600} />
                    <HealthTile label="Stale Pending" value={`${health?.stale_pending_count ?? 0}`} ok={(health?.stale_pending_count ?? 0) === 0} />
                    <HealthTile label="Log Errors" value={`${intel?.issues.log_errors.length ?? 0}`} ok={(intel?.issues.log_errors.length ?? 0) === 0} />
                  </div>
                  {(health?.warnings?.length ?? 0) > 0 && (
                    <div className="rounded border border-amber-500/20 bg-amber-500/5 p-3">
                      <div className="mb-2 flex items-center gap-2 text-xs font-medium text-amber-300">
                        <AlertTriangle className="h-3.5 w-3.5" /> Warnings
                      </div>
                      <div className="space-y-1 font-mono text-xs text-zinc-400">
                        {health?.warnings.map((item) => <div key={item}>{item}</div>)}
                      </div>
                    </div>
                  )}
                  <div className="rounded border border-zinc-900 bg-black/20 p-3">
                    <div className="mb-2 flex items-center gap-2 text-xs font-medium text-zinc-300">
                      <RadioTower className="h-3.5 w-3.5 text-zinc-500" /> Runtime
                    </div>
                    <div className="space-y-1 text-xs text-zinc-500">
                      <div>source <span className="font-mono text-zinc-300">{paperMonitor?.source_label ?? safety?.source_label ?? health?.run_source ?? "-"}</span></div>
                      <div className="truncate">stdout <span className="font-mono text-zinc-400">{intel?.logs.out_log?.split("\\").pop() ?? "-"}</span></div>
                      <div className="truncate">stderr <span className="font-mono text-zinc-400">{intel?.logs.err_log?.split("\\").pop() ?? "-"}</span></div>
                    </div>
                  </div>
                </div>
              </Panel>
            </div>
          </CollapsiblePanel>
        </div>
      )}
    </div>
  );
}
