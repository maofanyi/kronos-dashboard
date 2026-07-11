export type CalendarSource = "live_real" | "paper_monitor";

export type MonthlyPnlDay = {
  date: string;
  pnl_usdc: number;
  settled: number;
  wins: number;
  losses: number;
};

export type MonthlyPnlCalendar = {
  source: CalendarSource;
  month: string;
  day_tz: string;
  start_date: string;
  end_date: string;
  available_months: string[];
  total_pnl_usdc: number;
  settled: number;
  wins: number;
  losses: number;
  win_rate: number;
  empty: boolean;
  days: MonthlyPnlDay[];
};

export type DailyPnlOrder = {
  signal_id?: string | null;
  order_id?: string | null;
  market_slug?: string | null;
  direction?: string | null;
  outcome?: string | null;
  settle_ts?: string | null;
  filled_size?: number | null;
  average_fill_price?: number | null;
  pnl_usdc: number;
  won?: boolean | null;
  status?: string | null;
  settlement_source?: string | null;
};

export type DailyPnlOrders = {
  source: CalendarSource;
  date: string;
  day_tz: string;
  total_pnl_usdc: number;
  settled: number;
  wins: number;
  losses: number;
  orders: DailyPnlOrder[];
};
