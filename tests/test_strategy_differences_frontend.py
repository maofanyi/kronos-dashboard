from pathlib import Path


COMPONENT_PATH = Path("web/src/components/StrategyDifferences.tsx")
COMPARE_PATH = Path("web/src/pages/Compare.tsx")


def _component_source() -> str:
    assert COMPONENT_PATH.exists(), "StrategyDifferences component must be created"
    return COMPONENT_PATH.read_text(encoding="utf-8")


def test_strategy_difference_panel_is_lazy_and_never_polls():
    source = _component_source()

    assert "/api/strategy-comparison/differences" in source
    assert "if (!expanded || !candidateId)" in source
    assert "include_matched" in source
    assert "setInterval" not in source
    assert "usePolling" not in source
    assert "实盘与模拟差异" in source


def test_strategy_difference_panel_defaults_to_live_strategy_and_keeps_last_data():
    source = _component_source()

    assert "row.candidate_id === liveStrategyId" in source
    assert "setData((await response.json()) as DifferenceResponse)" in source
    assert "setData(null)" not in source
    assert "刷新失败，保留上次成功数据" in source


def test_strategy_difference_panel_has_filters_pagination_and_manual_refresh():
    source = _component_source()

    assert '<select' in source
    assert 'type="checkbox"' in source
    assert "包含完全匹配" in source
    assert "ChevronLeft" in source
    assert "ChevronRight" in source
    assert "RefreshCw" in source
    assert 'title="刷新差异数据"' in source
    assert "PAGE_SIZE" in source


def test_strategy_difference_panel_labels_money_and_causes_in_chinese():
    source = _component_source()

    assert "全窗口差额" in source
    assert "筛选后差额" in source
    assert "不可对账" in source
    assert 'side_mismatch: "方向不一致"' in source
    assert 'simulated_pre_submit_blocked: "提交前风控拦截"' in source
    assert 'simulated_no_fill: "实盘未成交"' in source
    assert 'simulated_strategy_only: "仅模拟策略信号"' in source
    assert 'simulated_only_unknown: "仅模拟（原因未知）"' in source


def test_strategy_difference_panel_scrolls_tables_without_nested_cards():
    source = _component_source()

    assert "overflow-x-auto" in source
    assert "rounded-md border border-zinc-800 bg-zinc-950/70" not in source
    assert "<Panel" not in source


def test_compare_page_uses_strategy_difference_component():
    source = COMPARE_PATH.read_text(encoding="utf-8")

    assert 'from "../components/StrategyDifferences"' in source
    assert "<StrategyDifferences" in source
    assert 'title="实盘匹配明细"' not in source
    assert "showLiveMatched" not in source
