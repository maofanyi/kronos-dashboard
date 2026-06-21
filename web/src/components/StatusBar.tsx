import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  CircleAlert,
  Lock,
  Radio,
  ShieldCheck,
  Wallet,
} from "lucide-react";
import { useMemo, useState } from "react";
import { usePolling } from "../hooks/usePolling";

interface StatusData {
  balance: number;
  trades_count: number;
  wr: number;
  cooldown_left: number;
}

interface SafetyData {
  mode?: string;
  run_source?: string;
  source_label?: string;
  real_orders_enabled?: boolean;
  kill_switch?: { state?: string };
  clob?: {
    authenticated?: boolean;
    account_read_ok?: boolean;
    allowance_read_ok?: boolean;
    open_orders_read_ok?: boolean;
  };
  funding?: {
    balance_ok?: boolean;
    allowance_ok?: boolean;
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
  dryrun?: {
    ledger?: string;
    ledger_count?: number;
    would_place_count?: number;
    blocked_count?: number;
    submitted_count?: number;
    latest?: Record<string, unknown> | null;
  };
  live_gate?: {
    report?: string;
    available?: boolean;
    ok?: boolean;
    ready_for_live_smoke?: boolean;
    blockers?: string[];
    market_probe_count?: number;
    quote_executable_count?: number;
  };
  preflight_chain?: {
    report?: string;
    available?: boolean;
    ok?: boolean;
    submitted?: boolean;
    created_at?: string;
    age_seconds?: number | null;
    fresh?: boolean;
    max_age_seconds?: number;
    blockers?: string[];
    gate_ready?: boolean;
    smoke_mode?: string;
    open_orders?: number;
    settled?: number;
    risk_ok?: boolean;
    components?: Record<string, unknown>;
  };
  market_data?: {
    ready?: boolean;
    price?: number | null;
    timestamp?: string;
    source?: string;
    status?: string;
    price_age_seconds?: number | null;
    received_age_seconds?: number | null;
    next_action?: string;
    error?: string | null;
  };
  alerts?: {
    available?: boolean;
    active_count?: number;
    selected_count?: number;
    critical_count?: number;
    warning_count?: number;
    active?: Array<{ key?: string; severity?: string; title?: string; body?: string }>;
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
    ready?: boolean;
    status?: string;
    current_stage_key?: string;
    current_stage_label?: string;
    next_action?: string;
    primary_blocker_key?: string | null;
    primary_blocker?: string | null;
  };
  risk?: {
    ok: boolean;
    metrics: {
      day_utc: string;
      daily_pnl_usdc: number;
      daily_trades: number;
      consecutive_losses: number;
      open_or_pending_orders: number;
    };
    limits: {
      max_daily_loss_usdc: number;
      max_daily_trades: number;
      max_consecutive_losses: number;
      max_open_or_pending_orders: number;
    };
  };
  today?: {
    signals: {
      total: number;
      passed: number;
      blocked: number;
      pass_rate: number;
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
    activity?: {
      latest_signal_age_seconds?: number | null;
      latest_dryrun_age_seconds?: number | null;
      latest_trade_age_seconds?: number | null;
      latest_order_age_seconds?: number | null;
    };
  };
}

interface LiveIntel {
  health?: {
    run_source?: string;
    state?: "ok" | "warning";
    warnings?: string[];
    checkpoint_age_seconds?: number | null;
    latest_event_age_seconds?: number | null;
    pending_count?: number;
    open_order_count?: number;
    trades_count?: number;
    stale_pending_count?: number;
  };
  issues?: {
    log_errors?: Array<{ file: string; line: string }>;
  };
}

type ChecklistItem = {
  key: string;
  label: string;
  ok: boolean;
  value?: string | number | null;
  expected?: string | null;
  severity?: string;
};

const money = (value?: number | null) =>
  value == null
    ? "-"
    : `$${value.toLocaleString(undefined, {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
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

const signedMoney = (value?: number | null) => {
  if (value == null) return "-";
  return `${value >= 0 ? "+" : ""}${money(value)}`;
};

const ageLabel = (seconds?: number | null) => {
  if (seconds == null) return "-";
  if (seconds < 90) return `${Math.round(seconds)}s`;
  return `${Math.round(seconds / 60)}m`;
};

function StatusChip({
  ok,
  label,
  icon,
}: {
  ok?: boolean;
  label: string;
  icon?: "lock" | "shield" | "wallet";
}) {
  const Icon = icon === "lock" ? Lock : icon === "wallet" ? Wallet : ok ? CheckCircle2 : AlertTriangle;
  const tone =
    ok == null
      ? "border-zinc-800 bg-zinc-950 text-zinc-400"
      : ok
        ? "border-emerald-500/25 bg-emerald-500/10 text-emerald-300"
        : "border-amber-500/25 bg-amber-500/10 text-amber-300";
  return (
    <span className={`inline-flex min-h-8 max-w-full items-center gap-1.5 rounded-md border px-2.5 text-xs ${tone}`}>
      <Icon className="h-3.5 w-3.5 shrink-0" />
      <span className="truncate">{label}</span>
    </span>
  );
}

function DetailTile({
  label,
  value,
  ok,
}: {
  label: string;
  value: string;
  ok?: boolean;
}) {
  const tone = ok == null ? "text-zinc-200" : ok ? "text-emerald-300" : "text-amber-300";
  return (
    <div className="rounded-md border border-zinc-800 bg-black/20 px-3 py-2">
      <div className="text-[11px] uppercase tracking-[0.14em] text-zinc-600">{label}</div>
      <div className={`mt-1 truncate font-mono text-sm ${tone}`}>{value}</div>
    </div>
  );
}

function summarizeChecklistReadiness(items: ChecklistItem[]) {
  const severityOrder: Record<string, number> = {
    critical: 0,
    funding: 1,
    risk: 2,
    runtime: 3,
  };
  const passed = items.filter((item) => item.ok).length;
  const failedItems = items
    .map((item, original_index) => ({ item, original_index }))
    .filter(({ item }) => !item.ok);
  const by_severity = failedItems.reduce<Record<string, number>>((counts, { item }) => {
    const severity = item.severity ?? "check";
    counts[severity] = (counts[severity] ?? 0) + 1;
    return counts;
  }, {});
  const top_blockers = failedItems
    .map(({ item, original_index }) => ({
      key: item.key,
      label: item.label,
      severity: item.severity ?? "check",
      value: item.value,
      expected: item.expected,
      action: readinessAction(item.key),
      original_index,
    }))
    .sort((a, b) => {
      const rankA = severityOrder[a.severity] ?? 9;
      const rankB = severityOrder[b.severity] ?? 9;
      if (rankA !== rankB) return rankA - rankB;
      return a.original_index - b.original_index;
    })
    .slice(0, 6);

  return {
    ready: items.length > 0 && passed === items.length,
    total: items.length,
    passed,
    blockers: items.length - passed,
    by_severity,
    critical_blockers: by_severity.critical ?? 0,
    funding_blockers: by_severity.funding ?? 0,
    risk_blockers: by_severity.risk ?? 0,
    top_blockers,
  };
}

function readinessAction(key: string) {
  const actions: Record<string, string> = {
    clob_authenticated: "Run CLOB read-only audit",
    account_read_ok: "Check CLOB account read access",
    allowance_read_ok: "Check CLOB allowance read access",
    minimum_balance: "Fund USDC balance",
    minimum_allowance: "Approve USDC allowance",
    open_orders_clear: "Cancel or reconcile open orders",
    dryrun_no_submitted_orders: "Keep dry-run from submitting orders",
    market_data_fresh: "Refresh Chainlink live price feed",
    live_trade_gate_available: "Generate live trade gate report",
    live_trade_gate_ready: "Clear live gate blockers",
    live_preflight_available: "Run live preflight chain",
    live_preflight_chain_ok: "Clear preflight blockers",
    live_preflight_fresh: "Refresh live preflight chain",
    live_preflight_no_submission: "Use preview-only preflight",
    risk_daily_loss: "Reset or lower daily loss exposure",
    risk_daily_trades: "Wait for daily trade limit reset",
    risk_consecutive_losses: "Pause after loss streak",
    risk_open_or_pending: "Clear open or pending orders",
    checkpoint_fresh: "Check runner checkpoint freshness",
    events_fresh: "Check event stream freshness",
    no_log_errors: "Inspect runtime logs",
  };
  return actions[key] ?? "Review readiness check";
}

const LIVE_MODE_NON_OPERATIONAL_CHECKS = new Set([
  "real_orders_locked",
  "dryrun_no_submitted_orders",
  "live_preflight_available",
  "live_preflight_chain_ok",
  "live_preflight_fresh",
  "live_preflight_no_submission",
]);

export default function StatusBar() {
  const [open, setOpen] = useState(false);
  const { data, error } = usePolling<StatusData>("/api/status", 5000);
  const { data: safety } = usePolling<SafetyData>("/api/live-safety", 5000);
  const { data: intel } = usePolling<LiveIntel>("/api/live-intel?limit=80", 5000);
  const now = new Date();
  const cooling = (data?.cooldown_left ?? 0) > 0;
  const liveEnabled = safety?.real_orders_enabled === true;
  const orderModeOk = liveEnabled || (!liveEnabled && safety?.kill_switch?.state !== "armed");
  const health = intel?.health;
  const metrics = safety?.risk?.metrics;
  const limits = safety?.risk?.limits;
  const today = safety?.today;
  const todayPnl = today?.trades.pnl_usdc ?? 0;
  const todayWins = today?.trades.wins ?? 0;
  const todayLosses = today?.trades.losses ?? 0;
  const todaySettled = today?.trades.settled ?? 0;
  const todaySignalsPassed = today?.signals.passed ?? 0;
  const todaySignalsTotal = today?.signals.total ?? 0;
  const todaySignalsOk = todaySignalsTotal === 0 || todaySignalsPassed > 0;
  const latestSignalAge = ageLabel(today?.activity?.latest_signal_age_seconds);
  const latestDryrunAge = ageLabel(today?.activity?.latest_dryrun_age_seconds);
  const todayActivityFresh = (today?.activity?.latest_signal_age_seconds ?? 9999) < 600;
  const latestDryrunFresh = (today?.activity?.latest_dryrun_age_seconds ?? 9999) < 600;
  const healthOk = !error && health?.state !== "warning";
  const checklist = useMemo(() => {
    const apiItems = liveEnabled
      ? (safety?.checklist ?? []).filter((item) => !LIVE_MODE_NON_OPERATIONAL_CHECKS.has(item.key))
      : safety?.checklist ?? [];
    const runtimeItems: ChecklistItem[] = [
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
        ok: (intel?.issues?.log_errors?.length ?? 0) === 0,
        value: intel?.issues?.log_errors?.length ?? 0,
        severity: "runtime",
      },
    ];
    return [...apiItems, ...runtimeItems];
  }, [health?.checkpoint_age_seconds, health?.latest_event_age_seconds, intel?.issues?.log_errors?.length, liveEnabled, safety?.checklist]);
  const localReadiness = summarizeChecklistReadiness(checklist);
  const checksOk = localReadiness.ready;
  const criticalBlockers = localReadiness.critical_blockers;
  const fundingBlockers = localReadiness.funding_blockers;
  const riskBlockers = localReadiness.risk_blockers;
  const topBlockers = localReadiness.top_blockers;
  const readinessPassed = localReadiness.passed;
  const readinessTotal = localReadiness.total;
  const riskOk = safety?.risk?.ok ?? true;
  const dryrunOk = (safety?.dryrun?.submitted_count ?? 0) === 0;
  const liveGateReady = safety?.live_gate?.ready_for_live_smoke === true;
  const preflightOk = safety?.preflight_chain?.ok === true;
  const preflightFresh = safety?.preflight_chain?.fresh === true;
  const preflightSubmitted = safety?.preflight_chain?.submitted === true;
  const preflightBlockers = safety?.preflight_chain?.blockers?.length ?? 0;
  const preflightReady = liveEnabled || (preflightOk && preflightFresh && !preflightSubmitted);
  const preflightAge = ageLabel(safety?.preflight_chain?.age_seconds);
  const marketDataReady = safety?.market_data?.ready === true;
  const alertCritical = safety?.alerts?.critical_count ?? 0;
  const alertActive = safety?.alerts?.active_count ?? 0;
  const fundingReady = safety?.funding?.funding_ready === true;
  const fundingGap = Math.max(
    safety?.funding?.balance_shortfall_usdc ?? 0,
    safety?.funding?.allowance_shortfall_usdc ?? 0,
  );
  const fundingAllowanceLabel = compactAllowance(safety?.funding?.min_allowance, safety?.funding?.allowance_ok === true || fundingReady);
  const needsReview = Boolean(error) || cooling || alertCritical > 0 || !healthOk || !checksOk || !riskOk || !preflightReady || !fundingReady || !marketDataReady;
  const operator = safety?.operator_summary;
  const operatorStage = operator?.current_stage_label ?? "Live safety";
  const operatorNextAction = operator?.next_action ?? "Review readiness";
  const operatorBlocked = operator?.status === "blocked";
  const operatorReady = operator?.ready === true;
  const pnlOpenLabel =
    metrics && limits
      ? `PnL ${signedMoney(metrics.daily_pnl_usdc)} | ${metrics.open_or_pending_orders}/${limits.max_open_or_pending_orders} open`
      : `PnL ${signedMoney(todayPnl)}`;
  const riskLabel = riskOk ? "Risk OK" : "Risk review";
  const alertLabel = alertCritical > 0 ? `Alerts ${alertCritical}` : "Alerts 0";
  const runtimeLabel = healthOk ? "Runtime OK" : "Runtime review";

  return (
    <div className="border-b border-zinc-800 bg-[#090a0f] text-xs">
      <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-2">
        <button
          type="button"
          onClick={() => setOpen((value) => !value)}
          className="flex min-w-0 flex-1 flex-wrap items-center gap-2 text-left"
          aria-expanded={open}
        >
          <span className="flex items-center gap-1.5 text-zinc-400">
            <span className={`h-2 w-2 rounded-full ${error ? "bg-rose-400" : "animate-pulse bg-emerald-400"}`} />
            <Radio className="h-3.5 w-3.5" />
            Live
          </span>
          <StatusChip ok={orderModeOk} label={liveEnabled ? "Real Enabled" : safety?.kill_switch?.state ?? "Locked"} icon="lock" />
          <StatusChip ok={healthOk} label={runtimeLabel} icon="shield" />
          <StatusChip ok={riskOk} label={pnlOpenLabel} />
          <StatusChip ok={riskOk && riskBlockers === 0} label={riskLabel} />
          <StatusChip ok={alertCritical === 0} label={alertLabel} />
          {!checksOk && <StatusChip ok={false} label={`Checks ${readinessPassed}/${readinessTotal}`} />}
          {!fundingReady && <StatusChip ok={false} label={`Funding gap ${money(fundingGap)}`} icon="wallet" />}
          <ChevronDown className={`h-4 w-4 shrink-0 text-zinc-600 transition-transform ${open ? "rotate-180" : ""}`} />
        </button>

        <div className="flex shrink-0 items-center gap-3 text-zinc-500">
          <span>{now.toLocaleTimeString("zh-CN", { hour12: false })}</span>
          <span className={`flex items-center gap-1.5 ${needsReview ? "text-amber-300" : "text-emerald-300"}`}>
            {needsReview && <CircleAlert className="h-3.5 w-3.5" />}
            {error ? "API review" : cooling ? `Cooldown ${data?.cooldown_left} bar` : needsReview ? "Review" : "Normal"}
          </span>
        </div>
      </div>

      {open && (
        <div className="border-t border-zinc-800 px-4 py-3">
          <div className="grid gap-3 lg:grid-cols-[minmax(260px,0.8fr)_minmax(360px,1.2fr)]">
            <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
              <DetailTile label="Mode" value={`${safety?.mode?.toUpperCase() ?? "PAPER"} / ${safety?.kill_switch?.state ?? "locked"}`} ok={orderModeOk} />
              <DetailTile label="CLOB Auth" value={safety?.clob?.authenticated ? "OK" : "-"} ok={safety?.clob?.authenticated} />
              <DetailTile label="Balance" value={money(safety?.funding?.balance)} ok={safety?.funding?.balance_ok} />
              <DetailTile label="Allowance" value={fundingAllowanceLabel} ok={safety?.funding?.allowance_ok} />
              <DetailTile label="Balance Gap" value={money(safety?.funding?.balance_shortfall_usdc)} ok={(safety?.funding?.balance_shortfall_usdc ?? 0) === 0} />
              <DetailTile label="Allowance Gap" value={money(safety?.funding?.allowance_shortfall_usdc)} ok={(safety?.funding?.allowance_shortfall_usdc ?? 0) === 0} />
              <DetailTile label="Operator Stage" value={operatorStage} ok={operatorBlocked ? false : operatorReady ? true : undefined} />
              <DetailTile label="Next Action" value={operatorNextAction} ok={operatorBlocked ? false : operatorReady ? true : undefined} />
              <DetailTile label="Alerts" value={`${alertCritical}/${alertActive}`} ok={alertCritical === 0} />
              {!liveEnabled && <DetailTile label="Paper Today PnL" value={signedMoney(todayPnl)} ok={todayPnl >= 0} />}
              {!liveEnabled && <DetailTile label="Paper Today Signals" value={`${todaySignalsPassed}/${todaySignalsTotal}`} ok={todaySignalsOk} />}
              {!liveEnabled && <DetailTile label="Paper Today W/L" value={`${todayWins}/${todayLosses}`} ok={todaySettled === 0 ? undefined : todayWins >= todayLosses} />}
              <DetailTile label="Latest Signal" value={latestSignalAge} ok={todayActivityFresh} />
              <DetailTile label="Latest Dry-run" value={latestDryrunAge} ok={latestDryrunFresh} />
              <DetailTile
                label="Daily Trades"
                value={metrics && limits ? `${metrics.daily_trades}/${limits.max_daily_trades}` : "-"}
                ok={metrics && limits ? metrics.daily_trades < limits.max_daily_trades : undefined}
              />
              <DetailTile
                label="Loss Streak"
                value={metrics && limits ? `${metrics.consecutive_losses}/${limits.max_consecutive_losses}` : "-"}
                ok={metrics && limits ? metrics.consecutive_losses < limits.max_consecutive_losses : undefined}
              />
              <DetailTile label="Checkpoint" value={ageLabel(health?.checkpoint_age_seconds)} ok={(health?.checkpoint_age_seconds ?? 9999) < 600} />
              <DetailTile label="Latest Event" value={ageLabel(health?.latest_event_age_seconds)} ok={(health?.latest_event_age_seconds ?? 9999) < 600} />
              <DetailTile label="Open/Pending" value={`${metrics?.open_or_pending_orders ?? health?.pending_count ?? 0}`} ok={riskOk} />
              <DetailTile
                label="Market Data"
                value={`${safety?.market_data?.status ?? "-"} / ${safety?.market_data?.source ?? "-"}`}
                ok={marketDataReady}
              />
              {!liveEnabled && (
                <DetailTile
                  label="Dry-run Ledger"
                  value={`${safety?.dryrun?.would_place_count ?? 0}/${safety?.dryrun?.ledger_count ?? 0}`}
                  ok={dryrunOk}
                />
              )}
              <DetailTile
                label="Live Gate"
                value={liveGateReady ? "ready" : `${safety?.live_gate?.blockers?.length ?? 0} blockers`}
                ok={liveGateReady}
              />
              {!liveEnabled && (
                <DetailTile
                  label="Preflight"
                  value={preflightSubmitted ? "submitted" : preflightReady ? preflightAge : `${preflightBlockers} blockers`}
                  ok={preflightReady}
                />
              )}
            </div>

            <div className="rounded-md border border-zinc-800 bg-black/20">
              <div className="flex items-center justify-between border-b border-zinc-800 px-3 py-2">
                <span className="font-medium text-zinc-200">Readiness Details</span>
                <span className={checksOk ? "font-mono text-emerald-300" : "font-mono text-amber-300"}>{readinessPassed}/{readinessTotal}</span>
              </div>
              <div className="grid grid-cols-3 gap-2 border-b border-zinc-800 p-3">
                <DetailTile label="Critical" value={`${criticalBlockers}`} ok={criticalBlockers === 0} />
                <DetailTile label="Funding" value={`${fundingBlockers}`} ok={fundingBlockers === 0} />
                <DetailTile label="Risk" value={`${riskBlockers}`} ok={riskBlockers === 0} />
              </div>
              {topBlockers.length > 0 && (
                <div className="border-b border-zinc-800 p-3">
                  <div className="mb-2 text-[11px] uppercase tracking-[0.14em] text-zinc-600">Top Blockers</div>
                  <div className="grid gap-2">
                    {topBlockers.slice(0, 3).map((blocker) => (
                      <div key={blocker.key ?? blocker.label} className="rounded border border-amber-500/20 bg-amber-500/5 px-2.5 py-2">
                        <div className="flex items-center justify-between gap-3">
                          <span className="truncate text-zinc-200">{blocker.label ?? blocker.key}</span>
                          <span className="shrink-0 font-mono text-[11px] uppercase text-amber-300">{blocker.severity ?? "check"}</span>
                        </div>
                        {blocker.expected && <div className="mt-1 truncate text-[11px] text-zinc-500">expected {blocker.expected}</div>}
                        {blocker.action && <div className="mt-1 truncate text-[11px] text-zinc-500">next {blocker.action}</div>}
                      </div>
                    ))}
                  </div>
                </div>
              )}
              <div className="grid max-h-[260px] gap-2 overflow-auto p-3 md:grid-cols-2">
                {checklist.map((item) => (
                  <div
                    key={item.key}
                    className={`rounded border px-2.5 py-2 ${
                      item.ok ? "border-emerald-500/20 bg-emerald-500/5" : "border-amber-500/20 bg-amber-500/5"
                    }`}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <div className="flex min-w-0 items-center gap-2">
                        {item.ok ? <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-emerald-300" /> : <AlertTriangle className="h-3.5 w-3.5 shrink-0 text-amber-300" />}
                        <span className="truncate text-zinc-200">{item.label}</span>
                      </div>
                      <span className={`shrink-0 font-mono ${item.ok ? "text-emerald-300" : "text-amber-300"}`}>{displayChecklistValue(item)}</span>
                    </div>
                    {item.expected && <div className="mt-1 truncate text-[11px] text-zinc-600">expected {item.expected}</div>}
                  </div>
                ))}
              </div>
            </div>
          </div>
          {(health?.warnings?.length ?? 0) > 0 && (
            <div className="mt-3 rounded-md border border-amber-500/20 bg-amber-500/5 px-3 py-2 font-mono text-xs text-amber-200">
              {health?.warnings?.join(" · ")}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
