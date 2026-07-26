import { useApi } from '../useApi'
import { generateReport, getOverfitVerdict, getPerformance, reportViewUrl } from '../api'
import { LoadingState, ErrorState, StatTile, VerdictBadge } from './UI'
import { EquityCurveChart, DrawdownChart } from './Charts'
import { money, num, pct, int, tone } from '../format'
import type { RunDetail } from '../types'
import { useState } from 'react'

export default function BacktestResults({ run }: { run: RunDetail }) {
  const perf = useApi(() => getPerformance(run.id), [run.id])
  const verdict = useApi(() => getOverfitVerdict(run.id), [run.id])
  const [reportUrl, setReportUrl] = useState<string | null>(null)
  const [reportError, setReportError] = useState<string | null>(null)

  async function handleGenerateReport() {
    setReportError(null)
    try {
      const report = await generateReport(run.id)
      setReportUrl(reportViewUrl(report.id))
    } catch (err) {
      setReportError(err instanceof Error ? err.message : 'Could not generate report.')
    }
  }

  if (run.status === 'failed') {
    return <ErrorState message={run.error_message ?? 'This run failed.'} />
  }

  return (
    <div>
      <div className="grid grid-stats" style={{ marginBottom: 16 }}>
        <StatTile label="Net P&L" value={money(run.net_pnl)} tone={tone(run.net_pnl)} />
        <StatTile label="Profit Factor" value={num(run.profit_factor)} />
        <StatTile label="Win Rate" value={pct(run.win_rate)} />
        <StatTile label="Expectancy" value={money(run.expectancy)} />
        <StatTile label="Sharpe" value={num(run.sharpe_ratio)} />
        <StatTile label="Sortino" value={num(run.sortino_ratio)} />
        <StatTile label="Max Drawdown" value={money(run.max_drawdown)} sub={pct(run.max_drawdown_pct, 0)} tone="bad" />
        <StatTile label="Trades" value={int(run.trade_count)} />
        {run.walk_forward && (
          <>
            <StatTile label="Validation Net P&L" value={money(run.validation_net_pnl)} tone={tone(run.validation_net_pnl)} />
            <StatTile label="Validation Trades" value={int(run.validation_trade_count)} />
            <StatTile label="Validation Profit Factor" value={num(run.validation_profit_factor)} />
          </>
        )}
      </div>

      {run.walk_forward && verdict.data && (
        <div className="panel">
          <h3>Overfit Verdict</h3>
          <div style={{ marginBottom: 8 }}>
            <VerdictBadge level={verdict.data.level} label={verdict.data.label} />
          </div>
          <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12.5, color: 'var(--text-dim)' }}>
            {verdict.data.reasons.map((r, i) => <li key={i}>{r}</li>)}
          </ul>
        </div>
      )}

      {run.caveats.length > 0 && (
        <div className="caveats" style={{ marginBottom: 16 }}>
          <h3>Read this before trusting the numbers above</h3>
          <ul>
            {run.caveats.map((c, i) => <li key={i}>{c}</li>)}
          </ul>
        </div>
      )}

      {perf.loading && <LoadingState label="Loading performance data…" />}
      {perf.error && <ErrorState message={perf.error} onRetry={perf.refetch} />}
      {perf.data && (
        <div className="grid grid-2">
          <div className="panel">
            <h3>Equity Curve</h3>
            <EquityCurveChart series={[{ name: run.strategy, color: 'var(--accent)', points: perf.data.equity_curve }]} />
          </div>
          <div className="panel">
            <h3>Drawdown</h3>
            <DrawdownChart points={perf.data.drawdown_curve} />
          </div>
        </div>
      )}

      {perf.data && (
        <div className="panel">
          <h3>Trade Statistics</h3>
          <div className="grid grid-stats">
            <StatTile label="Wins / Losses" value={`${perf.data.wins} / ${perf.data.losses}`} />
            <StatTile label="Average Win" value={money(perf.data.average_win)} tone="good" />
            <StatTile label="Average Loss" value={money(perf.data.average_loss)} tone="bad" />
            <StatTile label="Longest Drawdown" value={`${perf.data.longest_drawdown_trades} trades`} />
            <StatTile label="Max Consec. Losses" value={perf.data.max_consecutive_losses} />
            <StatTile label="Max Consec. Wins" value={perf.data.max_consecutive_wins} />
            <StatTile label="Avg Holding Time" value={perf.data.average_holding_minutes !== null ? `${perf.data.average_holding_minutes.toFixed(0)} min` : '—'} />
          </div>
        </div>
      )}

      <div className="panel">
        <h3>Report</h3>
        <button className="btn" onClick={handleGenerateReport}>Generate HTML report</button>
        {reportError && <p style={{ color: 'var(--bad)', marginTop: 8 }}>{reportError}</p>}
        {reportUrl && (
          <p style={{ marginTop: 8 }}>
            <a href={reportUrl} target="_blank" rel="noreferrer">Open report ↗</a>
          </p>
        )}
      </div>
    </div>
  )
}
