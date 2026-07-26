"""HTML report tests.

Checks structure and content rather than pixel output — a real browser render
is verified manually, not in CI. What matters here is that the generator
never crashes on edge-case data (empty trades, all wins, single trade) and
that the caveats actually appear in the markup, since that banner is the
point of the whole exercise.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from futures_bot.backtest.html_report import generate_html_report
from futures_bot.backtest.metrics import BacktestMetrics
from futures_bot.config import BrokerSettings, RiskSettings, SessionSettings, Settings
from futures_bot.contracts import CME_TZ
from futures_bot.models import Side, Trade


def make_settings() -> Settings:
    return Settings(
        contract="MES",
        mode="paper",
        risk=RiskSettings(
            stop_loss_points=Decimal("10"), take_profit_points=Decimal("20"),
            daily_max_loss=Decimal("200"), account_size=Decimal("2500"),
        ),
        session=SessionSettings(),
        broker=BrokerSettings(),
    )


def make_trade(net: Decimal, side: Side = Side.LONG, when: datetime | None = None) -> Trade:
    when = when or datetime(2026, 7, 21, 10, 0, tzinfo=CME_TZ)
    return Trade(
        side=side, quantity=1,
        entry_price=Decimal("7500"), exit_price=Decimal("7500") + net / Decimal("5"),
        entry_time=when, exit_time=when + timedelta(minutes=15),
        gross_pnl=net, commission=Decimal("1.24"), exit_reason="take_profit",
    )


class TestGenerateHtmlReport:
    def test_renders_with_trades(self):
        metrics = BacktestMetrics(
            trades=[make_trade(Decimal(x)) for x in ("50", "-30", "80", "-20")],
            starting_equity=Decimal("2500"),
            bars_processed=500,
            first_bar=datetime(2026, 6, 1, tzinfo=CME_TZ),
            last_bar=datetime(2026, 7, 1, tzinfo=CME_TZ),
        )
        html = generate_html_report(metrics, make_settings())

        assert html.startswith("<!DOCTYPE html>")
        assert "</html>" in html
        assert "MES" in html
        assert "<svg" in html
        assert "trades-table" in html

    def test_caveats_appear_in_both_banner_and_full_list(self):
        metrics = BacktestMetrics(trades=[make_trade(Decimal("5"))], starting_equity=Decimal("2500"))
        html = generate_html_report(metrics, make_settings())

        assert "caveat-banner" in html
        assert "caveats-full" in html
        # The low-trade-count caveat should appear (it fires below 30 trades).
        assert html.count("mostly noise") == 2  # once in the banner, once in the full list

    def test_handles_zero_trades_without_crashing(self):
        metrics = BacktestMetrics(trades=[])
        html = generate_html_report(metrics, make_settings())
        assert "<!DOCTYPE html>" in html
        assert "No trades taken" in html

    def test_handles_single_trade(self):
        metrics = BacktestMetrics(trades=[make_trade(Decimal("42"))], starting_equity=Decimal("1000"))
        html = generate_html_report(metrics, make_settings())
        assert "<!DOCTYPE html>" in html

    def test_handles_all_wins_no_losses(self):
        """Zero losing trades should not crash the drawdown or profit-factor math."""
        metrics = BacktestMetrics(
            trades=[make_trade(Decimal(x)) for x in ("10", "20", "15")],
            starting_equity=Decimal("1000"),
        )
        html = generate_html_report(metrics, make_settings())
        assert "<!DOCTYPE html>" in html
        # The "no losing trades" caveat should fire.
        assert "data or" in html

    def test_escapes_untrusted_strategy_name(self):
        """Strategy names flow from config.yaml, which a client may edit."""
        settings = make_settings()
        settings.strategy_name = "<script>alert(1)</script>"
        metrics = BacktestMetrics(trades=[make_trade(Decimal("10"))])
        html = generate_html_report(metrics, settings)
        assert "<script>alert(1)</script>" not in html
        assert "&lt;script&gt;" in html

    def test_long_and_short_trades_both_render(self):
        metrics = BacktestMetrics(
            trades=[make_trade(Decimal("10"), Side.LONG), make_trade(Decimal("-5"), Side.SHORT)],
            starting_equity=Decimal("1000"),
        )
        html = generate_html_report(metrics, make_settings())
        assert "side-long" in html
        assert "side-short" in html

    def test_custom_title_is_used(self):
        metrics = BacktestMetrics(trades=[make_trade(Decimal("1"))])
        html = generate_html_report(metrics, make_settings(), title="Custom Report Title")
        assert "<title>Custom Report Title</title>" in html

    def test_no_unclosed_tags(self):
        """Cheap structural sanity check without a full HTML parser dependency."""
        from html.parser import HTMLParser

        class Checker(HTMLParser):
            VOID = {"br", "meta", "link", "img", "circle", "line", "polygon", "polyline", "hr"}

            def __init__(self):
                super().__init__()
                self.stack: list[str] = []

            def handle_starttag(self, tag, attrs):
                if tag not in self.VOID:
                    self.stack.append(tag)

            def handle_endtag(self, tag):
                if self.stack and self.stack[-1] == tag:
                    self.stack.pop()

        metrics = BacktestMetrics(
            trades=[make_trade(Decimal(x)) for x in ("50", "-30", "80")],
            starting_equity=Decimal("2500"),
        )
        html = generate_html_report(metrics, make_settings())
        checker = Checker()
        checker.feed(html)
        assert checker.stack == [], f"unclosed tags: {checker.stack}"
