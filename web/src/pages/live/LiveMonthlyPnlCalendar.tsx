import { CalendarDays, ChevronLeft, ChevronRight, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import type { CalendarSource, DailyPnlOrder, DailyPnlOrders, MonthlyPnlCalendar, MonthlyPnlDay } from "./types";
import { useCachedJson } from "./useCachedJson";

type Props = {
  source: CalendarSource;
  initialMonth: string;
};

const WEEKDAYS = ["日", "一", "二", "三", "四", "五", "六"];

function money(value: number, signed = true) {
  const prefix = signed && value > 0 ? "+" : "";
  return `${prefix}${value.toFixed(2)} USDC`;
}

function monthLabel(month: string) {
  const [year, monthNumber] = month.split("-").map(Number);
  return `${year}年${monthNumber}月`;
}

function shiftMonth(month: string, amount: number) {
  const [year, monthNumber] = month.split("-").map(Number);
  const date = new Date(Date.UTC(year, monthNumber - 1 + amount, 1));
  return `${date.getUTCFullYear()}-${String(date.getUTCMonth() + 1).padStart(2, "0")}`;
}

function dayAccessibleLabel(day: MonthlyPnlDay) {
  const [year, month, date] = day.date.split("-").map(Number);
  const prefix = `${year}年${month}月${date}日`;
  if (day.settled === 0) return `${prefix}，无结算订单`;
  const result = day.pnl_usdc > 0
    ? `盈利 ${Math.abs(day.pnl_usdc).toFixed(2)} USDC`
    : day.pnl_usdc < 0
      ? `亏损 ${Math.abs(day.pnl_usdc).toFixed(2)} USDC`
      : "盈亏 0.00 USDC";
  return `${prefix}，${result}，${day.settled} 笔结算`;
}

function dayTone(day: MonthlyPnlDay, maxAbsPnl: number) {
  if (day.settled === 0) return "border-zinc-900 bg-zinc-950/30 text-zinc-600";
  const intensity = maxAbsPnl > 0 ? Math.abs(day.pnl_usdc) / maxAbsPnl : 0;
  if (day.pnl_usdc > 0) {
    return intensity > 0.66
      ? "border-emerald-400/45 bg-emerald-500/20 text-emerald-200"
      : "border-emerald-500/25 bg-emerald-500/10 text-emerald-300";
  }
  if (day.pnl_usdc < 0) {
    return intensity > 0.66
      ? "border-rose-400/45 bg-rose-500/20 text-rose-200"
      : "border-rose-500/25 bg-rose-500/10 text-rose-300";
  }
  return "border-zinc-700 bg-zinc-900/60 text-zinc-300";
}

function orderDirection(order: DailyPnlOrder) {
  return order.direction || order.outcome || "--";
}

function OrderDetail({ order }: { order: DailyPnlOrder }) {
  const pnlTone = order.pnl_usdc > 0 ? "text-emerald-300" : order.pnl_usdc < 0 ? "text-rose-300" : "text-zinc-300";
  return (
    <article className="rounded border border-zinc-800 bg-black/20 p-3">
      <div className="flex min-w-0 items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="break-all font-mono text-xs text-zinc-300">{order.order_id || order.signal_id || "--"}</div>
          <div className="mt-1 break-words text-xs text-zinc-500">{order.market_slug || "未知市场"}</div>
        </div>
        <div className={`shrink-0 font-mono text-sm font-semibold ${pnlTone}`}>{money(order.pnl_usdc)}</div>
      </div>
      <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2 text-xs">
        <div><dt className="text-zinc-600">方向</dt><dd className="mt-0.5 font-mono text-zinc-300">{orderDirection(order)}</dd></div>
        <div><dt className="text-zinc-600">结果</dt><dd className="mt-0.5 text-zinc-300">{order.won === true ? "获胜" : order.won === false ? "失败" : "--"}</dd></div>
        <div><dt className="text-zinc-600">成交份额</dt><dd className="mt-0.5 font-mono text-zinc-300">{order.filled_size?.toFixed(2) ?? "--"}</dd></div>
        <div><dt className="text-zinc-600">均价</dt><dd className="mt-0.5 font-mono text-zinc-300">{order.average_fill_price?.toFixed(3) ?? "--"}</dd></div>
        <div className="col-span-2"><dt className="text-zinc-600">结算来源</dt><dd className="mt-0.5 break-words font-mono text-zinc-300">{order.settlement_source || "--"}</dd></div>
      </dl>
    </article>
  );
}

export default function LiveMonthlyPnlCalendar({ source, initialMonth }: Props) {
  const [month, setMonth] = useState(initialMonth);
  const [selectedDay, setSelectedDay] = useState<MonthlyPnlDay | null>(null);

  useEffect(() => {
    setMonth(initialMonth);
    setSelectedDay(null);
  }, [initialMonth, source]);

  const monthUrl = `/api/live-pnl-calendar?source=${encodeURIComponent(source)}&month=${encodeURIComponent(month)}`;
  const { data: calendar, loading, error, refetch } = useCachedJson<MonthlyPnlCalendar>(monthUrl);
  const detailUrl = selectedDay && selectedDay.settled > 0
    ? `/api/live-pnl-calendar/orders?source=${encodeURIComponent(source)}&date=${encodeURIComponent(selectedDay.date)}`
    : null;
  const { data: detail, loading: detailLoading, error: detailError } = useCachedJson<DailyPnlOrders>(detailUrl);

  const maxAbsPnl = useMemo(
    () => Math.max(0, ...(calendar?.days ?? []).map((day) => Math.abs(day.pnl_usdc))),
    [calendar?.days],
  );
  const leadingDays = useMemo(() => {
    const [year, monthNumber] = month.split("-").map(Number);
    return new Date(Date.UTC(year, monthNumber - 1, 1)).getUTCDay();
  }, [month]);
  const earliestMonth = calendar?.available_months[0] ?? month;
  const previousMonth = shiftMonth(month, -1);
  const nextMonth = shiftMonth(month, 1);
  const canGoPrevious = previousMonth >= earliestMonth;
  const canGoNext = nextMonth <= initialMonth;

  function selectMonth(value: string) {
    if (!value) return;
    setMonth(value);
    setSelectedDay(null);
  }

  return (
    <section className="min-w-0 overflow-hidden rounded-md border border-zinc-800 bg-zinc-950/70" aria-labelledby="monthly-pnl-title">
      <header className="flex flex-col gap-3 border-b border-zinc-800 px-4 py-3 lg:flex-row lg:items-center lg:justify-between">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <CalendarDays className="h-4 w-4 shrink-0 text-zinc-500" />
            <h2 id="monthly-pnl-title" className="text-sm font-semibold text-zinc-100">月度收益日历</h2>
          </div>
          <p className="mt-1 text-xs text-zinc-500">按 {calendar?.day_tz || "交易时区"} 的已结算实盘账本统计</p>
        </div>
        <div className="flex max-w-full items-center gap-2">
          <button
            type="button"
            aria-label="上一个月"
            title="上一个月"
            disabled={!canGoPrevious}
            onClick={() => selectMonth(previousMonth)}
            className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded border border-zinc-800 text-zinc-400 hover:text-zinc-100 disabled:cursor-not-allowed disabled:opacity-30"
          >
            <ChevronLeft className="h-4 w-4" />
          </button>
          <label className="min-w-0">
            <span className="sr-only">选择月份</span>
            <input
              type="month"
              aria-label="选择月份"
              value={month}
              min={earliestMonth}
              max={initialMonth}
              onChange={(event) => selectMonth(event.target.value)}
              className="h-9 max-w-[170px] rounded border border-zinc-800 bg-zinc-950 px-3 font-mono text-sm text-zinc-200"
            />
          </label>
          <button
            type="button"
            aria-label="下一个月"
            title="下一个月"
            disabled={!canGoNext}
            onClick={() => selectMonth(nextMonth)}
            className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded border border-zinc-800 text-zinc-400 hover:text-zinc-100 disabled:cursor-not-allowed disabled:opacity-30"
          >
            <ChevronRight className="h-4 w-4" />
          </button>
        </div>
      </header>

      <div className="grid min-w-0 xl:grid-cols-[minmax(0,1fr)_360px]">
        <div className="min-w-0 p-3 sm:p-4">
          <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
            <div>
              <div className="text-lg font-semibold text-zinc-100">{monthLabel(month)}</div>
              <div className="mt-1 text-xs text-zinc-500">{calendar?.settled ?? 0} 笔结算 · {calendar?.wins ?? 0}胜/{calendar?.losses ?? 0}负</div>
            </div>
            <div className="text-right">
              <div className={`font-mono text-lg font-semibold ${(calendar?.total_pnl_usdc ?? 0) >= 0 ? "text-emerald-300" : "text-rose-300"}`}>
                {money(calendar?.total_pnl_usdc ?? 0)}
              </div>
              <div className="mt-1 text-xs text-zinc-500">胜率 {((calendar?.win_rate ?? 0) * 100).toFixed(1)}%</div>
            </div>
          </div>

          {error && (
            <div className="mb-3 flex items-center justify-between gap-3 rounded border border-amber-500/25 bg-amber-500/10 px-3 py-2 text-xs text-amber-200">
              <span>月度数据读取失败：{error}</span>
              <button type="button" className="font-medium text-amber-100 underline" onClick={refetch}>重试</button>
            </div>
          )}

          <div className="grid grid-cols-7 gap-1 text-center text-[11px] text-zinc-600">
            {WEEKDAYS.map((weekday) => <div key={weekday} className="py-1">{weekday}</div>)}
          </div>
          <div className="grid grid-cols-7 gap-1" aria-busy={loading}>
            {Array.from({ length: leadingDays }, (_, index) => <div key={`blank-${index}`} aria-hidden="true" />)}
            {(calendar?.days ?? []).map((day) => {
              const selected = selectedDay?.date === day.date;
              return (
                <button
                  key={day.date}
                  type="button"
                  aria-label={dayAccessibleLabel(day)}
                  aria-pressed={selected}
                  onClick={() => setSelectedDay(day)}
                  className={`min-h-[72px] min-w-0 rounded border p-1.5 text-left transition-colors sm:min-h-[82px] sm:p-2 ${dayTone(day, maxAbsPnl)} ${selected ? "ring-1 ring-zinc-300" : ""}`}
                >
                  <span className="block text-[11px] text-zinc-500">{Number(day.date.slice(-2))}</span>
                  <span className="mt-2 block break-words font-mono text-[10px] font-semibold leading-tight sm:text-xs">
                    {day.settled ? money(day.pnl_usdc).replace(" USDC", "") : "--"}
                  </span>
                  <span className="mt-1 block text-[9px] text-zinc-500 sm:text-[10px]">{day.settled ? `${day.settled}笔` : "无结算"}</span>
                </button>
              );
            })}
          </div>
          {loading && !calendar && <div className="py-8 text-center text-sm text-zinc-500">正在读取月度账本...</div>}
        </div>

        <aside className="min-w-0 border-t border-zinc-800 bg-black/20 p-4 xl:border-l xl:border-t-0" aria-live="polite">
          <div className="flex items-start justify-between gap-3">
            <div>
              <h3 className="text-sm font-semibold text-zinc-100">每日结算订单</h3>
              <p className="mt-1 text-xs text-zinc-500">{selectedDay?.date || "选择一个日期查看明细"}</p>
            </div>
            {selectedDay && (
              <button type="button" aria-label="关闭订单明细" title="关闭订单明细" onClick={() => setSelectedDay(null)} className="inline-flex h-8 w-8 items-center justify-center rounded text-zinc-500 hover:bg-zinc-900 hover:text-zinc-200">
                <X className="h-4 w-4" />
              </button>
            )}
          </div>

          {!selectedDay && <div className="py-10 text-center text-sm text-zinc-600">点击有结算记录的日期进行对账</div>}
          {selectedDay?.settled === 0 && <div className="py-10 text-center text-sm text-zinc-500">当日没有已结算订单</div>}
          {detailLoading && <div className="py-10 text-center text-sm text-zinc-500">正在读取订单...</div>}
          {detailError && <div className="mt-4 rounded border border-amber-500/25 bg-amber-500/10 px-3 py-2 text-xs text-amber-200">订单明细读取失败：{detailError}</div>}
          {detail && selectedDay?.settled ? (
            <div className="mt-4 space-y-3">
              <div className="flex items-center justify-between gap-3 border-b border-zinc-800 pb-3 text-xs">
                <span className="text-zinc-500">{detail.settled} 笔 · {detail.wins}胜/{detail.losses}负</span>
                <span className={`font-mono font-semibold ${detail.total_pnl_usdc >= 0 ? "text-emerald-300" : "text-rose-300"}`}>{money(detail.total_pnl_usdc)}</span>
              </div>
              {detail.orders.length === 0
                ? <div className="py-8 text-center text-sm text-zinc-500">当日没有已结算订单</div>
                : detail.orders.map((order, index) => <OrderDetail key={order.order_id || order.signal_id || index} order={order} />)}
            </div>
          ) : null}
        </aside>
      </div>
    </section>
  );
}
