"""HTML backtest report.

A single self-contained file: no CDN, no build step, no internet connection
needed to view it. Opens in any browser, attaches to an email, survives being
handed to someone on a machine with no Python installed. Charts are hand-drawn
inline SVG rather than a charting library, which is what makes "self-contained"
actually true.

The terminal report puts caveats at the end deliberately, so reading to the
finish means reading past them. A web page invites skimming in a way a
terminal scroll does not, so here the caveats get a second placement: a
banner right under the headline numbers, impossible to miss even on a
five-second glance, in addition to the full list further down.
"""

from __future__ import annotations

import html as html_module
from decimal import Decimal
from typing import Optional

from ..config import Settings
from .data import DataQualityReport
from .metrics import BacktestMetrics
from .report import plain_english_summary

_CHART_WIDTH = 760
_CHART_HEIGHT = 220
_CHART_PAD = 28


def _esc(value: object) -> str:
    return html_module.escape(str(value))


def _money(value: Optional[Decimal]) -> str:
    if value is None:
        return "n/a"
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value):,.2f}"


def _pct(value: Optional[Decimal], places: int = 1) -> str:
    return "n/a" if value is None else f"{value:.{places}f}%"


def _num(value: Optional[Decimal], places: int = 2) -> str:
    return "n/a" if value is None else f"{value:.{places}f}"


def _svg_line_chart(
    values: list[Decimal],
    *,
    positive_color: str,
    negative_color: str,
    fill: bool = True,
    zero_baseline: bool = False,
) -> str:
    """A minimal line/area chart as raw SVG. No JS, no library."""
    if len(values) < 2:
        return '<p class="chart-empty">Not enough data to chart.</p>'

    floats = [float(v) for v in values]
    v_min, v_max = min(floats), max(floats)
    if zero_baseline:
        v_min = min(v_min, 0.0)
        v_max = max(v_max, 0.0)
    span = (v_max - v_min) or 1.0

    w, h, pad = _CHART_WIDTH, _CHART_HEIGHT, _CHART_PAD
    plot_w, plot_h = w - 2 * pad, h - 2 * pad

    def x_at(i: int) -> float:
        return pad + (i / (len(floats) - 1)) * plot_w

    def y_at(v: float) -> float:
        return pad + plot_h - ((v - v_min) / span) * plot_h

    points = [(x_at(i), y_at(v)) for i, v in enumerate(floats)]
    line_path = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)

    zero_y = y_at(0.0) if zero_baseline else None
    end_color = positive_color if floats[-1] >= floats[0] else negative_color

    parts = [f'<svg viewBox="0 0 {w} {h}" class="chart" preserveAspectRatio="none" role="img">']

    # Gridlines at min/mid/max for a sense of scale without axis labels crowding it.
    for frac in (0.0, 0.5, 1.0):
        gy = pad + plot_h * frac
        val = v_max - frac * span
        parts.append(
            f'<line x1="{pad}" y1="{gy:.1f}" x2="{w - pad}" y2="{gy:.1f}" class="chart-grid" />'
            f'<text x="{pad - 6}" y="{gy + 3:.1f}" class="chart-tick" text-anchor="end">'
            f"{val:,.0f}</text>"
        )

    if zero_baseline and zero_y is not None:
        parts.append(
            f'<line x1="{pad}" y1="{zero_y:.1f}" x2="{w - pad}" y2="{zero_y:.1f}" class="chart-zero" />'
        )

    if fill:
        base_y = zero_y if zero_y is not None else pad + plot_h
        area = f"{pad:.1f},{base_y:.1f} " + line_path + f" {x_at(len(floats) - 1):.1f},{base_y:.1f}"
        parts.append(f'<polygon points="{area}" class="chart-fill" style="fill:{end_color}" />')

    parts.append(f'<polyline points="{line_path}" class="chart-line" style="stroke:{end_color}" />')
    parts.append(
        f'<circle cx="{points[-1][0]:.1f}" cy="{points[-1][1]:.1f}" r="3.5" style="fill:{end_color}" />'
    )
    parts.append("</svg>")
    return "\n".join(parts)


def _kpi_card(label: str, value: str, sub: str = "", tone: str = "") -> str:
    tone_class = f" kpi-{tone}" if tone else ""
    sub_html = f'<div class="kpi-sub">{_esc(sub)}</div>' if sub else ""
    return (
        f'<div class="kpi{tone_class}">'
        f'<div class="kpi-label">{_esc(label)}</div>'
        f'<div class="kpi-value">{_esc(value)}</div>'
        f"{sub_html}"
        f"</div>"
    )


def _trades_table(metrics: BacktestMetrics) -> str:
    if not metrics.trades:
        return '<p class="chart-empty">No trades taken.</p>'

    rows = []
    for i, t in enumerate(metrics.trades, start=1):
        net = t.net_pnl
        cls = "pnl-pos" if net > 0 else ("pnl-neg" if net < 0 else "pnl-flat")
        rows.append(
            "<tr>"
            f"<td>{i}</td>"
            f"<td>{_esc(t.entry_time.strftime('%Y-%m-%d %H:%M'))}</td>"
            f'<td class="side-{t.side.value}">{_esc(t.side.value.upper())}</td>'
            f"<td>{_esc(t.entry_price)}</td>"
            f"<td>{_esc(t.exit_price)}</td>"
            f"<td>{_esc(t.exit_reason)}</td>"
            f'<td class="{cls}">{_money(net)}</td>'
            "</tr>"
        )

    return (
        '<table class="trades" id="trades-table">'
        "<thead><tr>"
        '<th data-sort="num">#</th><th data-sort="text">Entry time</th>'
        '<th data-sort="text">Side</th><th data-sort="num">Entry</th>'
        '<th data-sort="num">Exit</th><th data-sort="text">Exit reason</th>'
        '<th data-sort="num">Net P&amp;L</th>'
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
    )


def _caveats_html(metrics: BacktestMetrics) -> tuple[str, str]:
    """Returns (banner_html, full_list_html)."""
    caveats = metrics.caveats()
    if not caveats:
        return "", ""

    banner = (
        '<div class="caveat-banner">'
        '<div class="caveat-banner-title">Before you trust these numbers</div>'
        "<ul>" + "".join(f"<li>{_esc(c)}</li>" for c in caveats) + "</ul>"
        "</div>"
    )
    full = (
        '<section class="caveats-full">'
        "<h2>Read this before believing the numbers above</h2>"
        "<ul>" + "".join(f"<li>{_esc(c)}</li>" for c in caveats) + "</ul>"
        "</section>"
    )
    return banner, full


_CSS = """
:root {
  --bg: #0b0e11; --panel: #12161c; --border: #1f252d; --text: #e6e8eb; --muted: #7c8798;
  --bull: #00d68f; --bear: #ff4d5e; --watch: #ffb020; --info: #4d9fff;
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--text);
  font-family: -apple-system, "Segoe UI", Helvetica, Arial, sans-serif;
  line-height: 1.5;
}
.wrap { max-width: 880px; margin: 0 auto; padding: 32px 20px 64px; }
header h1 { font-size: 1.4rem; margin: 0 0 4px; }
header p { color: var(--muted); margin: 0 0 4px; font-size: 0.9rem; }
.meta { color: var(--muted); font-size: 0.85rem; margin-bottom: 24px; }
.meta code { background: var(--panel); padding: 1px 5px; border-radius: 3px; }

.caveat-banner {
  border: 1px solid var(--watch); background: rgba(255,176,32,0.08);
  border-radius: 8px; padding: 14px 18px; margin: 20px 0 28px;
}
.caveat-banner-title { color: var(--watch); font-weight: 600; margin-bottom: 6px; }
.caveat-banner ul { margin: 0; padding-left: 20px; font-size: 0.88rem; }
.caveat-banner li { margin-bottom: 4px; }

.kpi-grid {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 10px; margin-bottom: 28px;
}
.kpi {
  background: var(--panel); border: 1px solid var(--border); border-radius: 8px;
  padding: 12px 14px;
}
.kpi-label { color: var(--muted); font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.03em; }
.kpi-value { font-size: 1.3rem; font-weight: 600; margin-top: 2px; font-variant-numeric: tabular-nums; }
.kpi-sub { color: var(--muted); font-size: 0.75rem; margin-top: 2px; }
.kpi-good .kpi-value { color: var(--bull); }
.kpi-bad .kpi-value { color: var(--bear); }

section { margin-bottom: 32px; }
h2 { font-size: 1rem; text-transform: uppercase; letter-spacing: 0.04em; color: var(--muted);
     border-bottom: 1px solid var(--border); padding-bottom: 8px; margin-bottom: 14px; }

.chart-panel { background: var(--panel); border: 1px solid var(--border); border-radius: 8px; padding: 12px; }
.chart { width: 100%; height: auto; display: block; }
.chart-grid { stroke: var(--border); stroke-width: 1; }
.chart-zero { stroke: var(--muted); stroke-width: 1; stroke-dasharray: 3 3; }
.chart-tick { fill: var(--muted); font-size: 9px; font-family: monospace; }
.chart-line { fill: none; stroke-width: 2; }
.chart-fill { opacity: 0.12; }
.chart-empty { color: var(--muted); font-size: 0.85rem; }

.two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
@media (max-width: 640px) { .two-col { grid-template-columns: 1fr; } }

.bars { display: flex; flex-direction: column; gap: 8px; }
.bar-row { display: flex; align-items: center; gap: 10px; font-size: 0.85rem; }
.bar-label { width: 210px; flex-shrink: 0; color: var(--muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.bar-track { flex: 1; background: var(--border); border-radius: 4px; height: 8px; overflow: hidden; }
.bar-fill { height: 100%; background: var(--info); }
.bar-count { width: 70px; text-align: right; flex-shrink: 0; font-variant-numeric: tabular-nums; }

table.trades { width: 100%; border-collapse: collapse; font-size: 0.82rem; }
table.trades th, table.trades td { padding: 6px 10px; text-align: right; border-bottom: 1px solid var(--border); }
table.trades th:first-child, table.trades td:first-child { text-align: left; }
table.trades th:nth-child(2), table.trades td:nth-child(2) { text-align: left; }
table.trades th:nth-child(6), table.trades td:nth-child(6) { text-align: left; }
table.trades th { color: var(--muted); font-weight: 500; cursor: pointer; user-select: none; white-space: nowrap; }
table.trades th:hover { color: var(--text); }
table.trades tbody tr:hover { background: rgba(255,255,255,0.03); }
.pnl-pos { color: var(--bull); } .pnl-neg { color: var(--bear); } .pnl-flat { color: var(--muted); }
.side-long { color: var(--bull); } .side-short { color: var(--bear); }
.table-scroll { max-height: 420px; overflow-y: auto; border: 1px solid var(--border); border-radius: 8px; }
.table-scroll table { border: none; }

.caveats-full ul { padding-left: 20px; font-size: 0.9rem; }
.caveats-full li { margin-bottom: 8px; }

footer { color: var(--muted); font-size: 0.78rem; border-top: 1px solid var(--border); padding-top: 16px; }
"""

_SORT_JS = """
document.querySelectorAll('#trades-table th').forEach(function (th, idx) {
  th.addEventListener('click', function () {
    var table = th.closest('table');
    var tbody = table.querySelector('tbody');
    var rows = Array.prototype.slice.call(tbody.querySelectorAll('tr'));
    var type = th.getAttribute('data-sort');
    var asc = th.getAttribute('data-asc') !== 'true';
    table.querySelectorAll('th').forEach(function (h) { h.removeAttribute('data-asc'); });
    th.setAttribute('data-asc', asc ? 'true' : 'false');

    rows.sort(function (a, b) {
      var av = a.children[idx].innerText.trim();
      var bv = b.children[idx].innerText.trim();
      if (type === 'num') {
        av = parseFloat(av.replace(/[^0-9.-]/g, '')) || 0;
        bv = parseFloat(bv.replace(/[^0-9.-]/g, '')) || 0;
        return asc ? av - bv : bv - av;
      }
      return asc ? av.localeCompare(bv) : bv.localeCompare(av);
    });
    rows.forEach(function (r) { tbody.appendChild(r); });
  });
});
"""


def generate_html_report(
    metrics: BacktestMetrics,
    settings: Settings,
    data_report: Optional[DataQualityReport] = None,
    title: Optional[str] = None,
) -> str:
    """Renders a full HTML report as a single string. Write it to a file yourself."""
    spec = settings.contract_spec
    page_title = title or f"{spec.symbol} Backtest — {settings.strategy_name}"

    period = ""
    if metrics.first_bar and metrics.last_bar:
        span_days = (metrics.last_bar - metrics.first_bar).days
        period = (
            f"{metrics.first_bar:%Y-%m-%d} to {metrics.last_bar:%Y-%m-%d} "
            f"({span_days} days, {metrics.bars_processed:,} bars)"
        )

    banner_html, caveats_full_html = _caveats_html(metrics)

    net_tone = "good" if metrics.net_pnl > 0 else ("bad" if metrics.net_pnl < 0 else "")
    kpis = [
        _kpi_card("Net P&L", _money(metrics.net_pnl), _pct(metrics.return_pct) + " of equity", net_tone),
        _kpi_card("Trades", str(metrics.trade_count), _pct(metrics.win_rate) + " win rate"),
        _kpi_card(
            "Profit factor", _num(metrics.profit_factor),
            "below 1.00 loses money",
            "good" if (metrics.profit_factor or 0) > 1 else "bad",
        ),
        _kpi_card("Expectancy / trade", _money(metrics.expectancy)),
        _kpi_card(
            "Max drawdown", _money(metrics.max_drawdown),
            _pct(metrics.max_drawdown_pct, 0) + " of equity", "bad" if metrics.max_drawdown > 0 else ""
        ),
        _kpi_card("Max consec. losses", str(metrics.max_consecutive_losses)),
    ]
    if metrics.strategy_errors:
        kpis.append(
            _kpi_card(
                "Strategy errors", str(metrics.strategy_errors),
                "forced to HOLD -- this is a bug", "bad",
            )
        )

    equity_chart = _svg_line_chart(
        metrics.equity_curve, positive_color="#00d68f", negative_color="#ff4d5e", fill=True
    )

    drawdown_chart = _svg_line_chart(
        metrics.drawdown_curve, positive_color="#ff4d5e", negative_color="#ff4d5e", fill=True, zero_baseline=True
    )

    exit_bars = ""
    if metrics.exit_reasons:
        max_count = max(metrics.exit_reasons.values())
        rows = []
        for reason, count in metrics.exit_reasons.items():
            pct_width = (count / max_count * 100) if max_count else 0
            rows.append(
                '<div class="bar-row">'
                f'<div class="bar-label" title="{_esc(reason)}">{_esc(reason)}</div>'
                f'<div class="bar-track"><div class="bar-fill" style="width:{pct_width:.0f}%"></div></div>'
                f'<div class="bar-count">{count}</div>'
                "</div>"
            )
        exit_bars = f'<div class="bars">{"".join(rows)}</div>'

    direction_html = ""
    if metrics.by_side:
        rows = []
        for side, stats in metrics.by_side.items():
            rows.append(
                '<div class="bar-row">'
                f'<div class="bar-label side-{side}">{side.upper()}</div>'
                f'<div class="bar-track"><div class="bar-fill" style="width:100%"></div></div>'
                f'<div class="bar-count">{stats["count"]} @ {stats["win_rate"]:.0f}%</div>'
                "</div>"
            )
        direction_html = (
            f'<div class="bars">{"".join(rows)}</div>'
            f'<p style="color:var(--muted);font-size:0.85rem;margin-top:8px">'
            f'{"  &nbsp; ".join(f"{s.upper()}: {_money(st["net_pnl"])}" for s, st in metrics.by_side.items())}'
            f"</p>"
        )

    plain_english_html = ""
    if metrics.trades:
        items = "".join(f"<li>{_esc(line)}</li>" for line in plain_english_summary(metrics, settings))
        plain_english_html = (
            '<section><h2>What these numbers mean</h2>'
            f'<ul style="font-size:0.92rem;line-height:1.5">{items}</ul></section>'
        )

    data_quality_html = ""
    if data_report and data_report.warnings():
        items = "".join(f"<li>{_esc(w)}</li>" for w in data_report.warnings())
        data_quality_html = (
            '<section><h2>Data quality</h2>'
            f'<ul style="font-size:0.88rem;color:var(--watch)">{items}</ul></section>'
        )

    doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>{_esc(page_title)}</title>
<style>{_CSS}</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>{_esc(spec.symbol)} — {_esc(spec.name)}</h1>
    <p>Strategy: {_esc(settings.strategy_name)} &nbsp;|&nbsp; Mode: {_esc(settings.mode)}</p>
  </header>
  <div class="meta">
    {_esc(period)}<br/>
    Risk: <code>{_esc(settings.risk.stop_loss_points)} pt stop / {_esc(settings.risk.take_profit_points)} pt target</code>,
    {_esc(settings.risk.contracts_per_trade)} contract, <code>${_esc(settings.risk.daily_max_loss)}</code> daily limit &nbsp;|&nbsp;
    Costs: <code>${_esc(settings.broker.commission_per_side)}/side, {_esc(settings.broker.slippage_ticks)} tick slippage</code>
  </div>

  {banner_html}

  <div class="kpi-grid">
    {"".join(kpis)}
  </div>

  {data_quality_html}

  <section>
    <h2>Equity curve</h2>
    <div class="chart-panel">{equity_chart}</div>
  </section>

  <section>
    <h2>Drawdown</h2>
    <div class="chart-panel">{drawdown_chart}</div>
  </section>

  <div class="two-col">
    <section>
      <h2>Exits</h2>
      {exit_bars or '<p class="chart-empty">No trades.</p>'}
    </section>
    <section>
      <h2>By direction</h2>
      {direction_html or '<p class="chart-empty">No trades.</p>'}
    </section>
  </div>

  <section>
    <h2>Trades ({metrics.trade_count})</h2>
    <div class="table-scroll">{_trades_table(metrics)}</div>
  </section>

  {plain_english_html}

  {caveats_full_html}

  <footer>
    Generated by the futures-bot backtest runner. Educational tool, not financial advice.
    Past performance in a backtest does not indicate future results.
  </footer>
</div>
<script>{_SORT_JS}</script>
</body>
</html>"""
    return doc
