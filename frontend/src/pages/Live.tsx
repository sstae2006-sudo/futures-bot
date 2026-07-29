import { useState } from 'react'
import { Link } from 'react-router-dom'
import { startLiveSession, stopLiveSession, ApiRequestError } from '../api'
import { useLiveStream } from '../useLiveStream'
import { LoadingState, Badge } from '../components/UI'
import { money, dateTime } from '../format'

const STATUS_TONE: Record<string, 'good' | 'bad' | 'warn' | 'neutral'> = {
  running: 'good',
  starting: 'warn',
  stopping: 'warn',
  stopped: 'neutral',
  error: 'bad',
}

export default function Live() {
  const status = useLiveStream()
  // No hardcoded default here on purpose (found the hard way, 2026-07-28:
  // this used to default to 'MESH6' -- futures contract tickers expire
  // every quarter, H6 = March 2026, long expired by the time anyone
  // actually clicks "Start a session" -- the session started, showed
  // "running," and silently never received a single bar, since the feed
  // was polling a ticker with no live data at all. There's no
  // active-contract-resolution endpoint exposed to the frontend yet
  // (only used internally by the CLI/research server today) -- until
  // there is, requiring the user to type the real, current front-month
  // ticker themselves is safer than guessing wrong silently.
  const [liveSymbol, setLiveSymbol] = useState('')
  const [resolution, setResolution] = useState('5min')
  const [pollSeconds, setPollSeconds] = useState('30')
  const [submitting, setSubmitting] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)

  const busy = status?.status === 'starting' || status?.status === 'stopping'
  const canStart = !status || status.status === 'stopped' || status.status === 'error'
  const canStop = status?.status === 'running' || status?.status === 'starting'

  async function handleStart(e: React.FormEvent) {
    e.preventDefault()
    setSubmitting(true)
    setFormError(null)
    try {
      await startLiveSession({
        live_symbol: liveSymbol,
        resolution,
        poll_seconds: Number(pollSeconds) || 30,
      })
    } catch (err) {
      setFormError(err instanceof ApiRequestError ? err.message : 'Could not start the live session.')
    } finally {
      setSubmitting(false)
    }
  }

  async function handleStop() {
    setSubmitting(true)
    setFormError(null)
    try {
      await stopLiveSession()
    } catch (err) {
      setFormError(err instanceof ApiRequestError ? err.message : 'Could not stop the live session.')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div>
      <div className="page-header">
        <h1>Live Session</h1>
        <p>
          Starts and watches the existing paper-trading engine (<code>cli.cmd_live</code>) from the
          dashboard. Paper broker only, enforced at runtime — see{' '}
          <code>api/live_session.py</code>. Real trading remains terminal-only
          (<code>python -m futures_bot.cli --live</code>). Trades close the same way a
          backtest's do and are persisted to the research database as they happen — a session's
          fills show up in <Link to="/trades">Trade Explorer</Link> and{' '}
          <Link to="/regime">Market Regime</Link> under its strategy.
        </p>
      </div>

      {!status && <LoadingState label="Connecting to live session…" />}

      {status && (
        <>
          <div className="panel">
            <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
              <Badge tone={STATUS_TONE[status.status] ?? 'neutral'}>{status.status}</Badge>
              {status.strategy && <span>{status.strategy} on {status.contract} ({status.broker})</span>}
              {status.live_symbol && <span>· {status.live_symbol} @ {status.resolution}, polling every {status.poll_seconds}s</span>}
            </div>
            {status.run_id && <p style={{ marginTop: 8, fontSize: 12, opacity: 0.7 }}>Run: <code>{status.run_id}</code></p>}
            {status.halted && (
              <p style={{ marginTop: 8 }}>
                <Badge tone="bad">Halted</Badge> {status.halt_reason}
              </p>
            )}
            {status.error_message && (
              <p style={{ marginTop: 8 }} role="alert">{status.error_message}</p>
            )}
            {status.last_feed_error && (
              <p style={{ marginTop: 8 }}>
                <Badge tone="warn">Feed warning</Badge> {status.last_feed_error}
              </p>
            )}
            {status.warnings.length > 0 && (
              <ul style={{ marginTop: 8 }}>
                {status.warnings.map((w, i) => <li key={i}>{w}</li>)}
              </ul>
            )}
          </div>

          {status.status === 'starting' && (
            <div className="panel">
              <LoadingState label="Starting the live session… (detecting the active contract, connecting the feed)" />
            </div>
          )}

          {(status.status === 'running' || status.status === 'stopping') && (
            <div className="panel">
              <h3>Session</h3>
              <div className="field-row">
                <div className="field">
                  <label>Session P&amp;L</label>
                  <div>{money(status.session_pnl)}</div>
                </div>
                <div className="field">
                  <label>Trades today</label>
                  <div>{status.trade_count_today ?? '—'}</div>
                </div>
                <div className="field">
                  <label>Last bar</label>
                  <div>{dateTime(status.last_bar_time)} @ {status.last_bar_close ?? '—'}</div>
                </div>
              </div>

              <h3 style={{ marginTop: 16 }}>Position</h3>
              {!status.position && <p>Flat.</p>}
              {status.position && (
                <div className="field-row">
                  <div className="field">
                    <label>Side / Qty</label>
                    <div>{status.position.side} × {status.position.quantity}</div>
                  </div>
                  <div className="field">
                    <label>Entry</label>
                    <div>{status.position.entry_price}</div>
                  </div>
                  <div className="field">
                    <label>Stop / Target</label>
                    <div>{status.position.stop_loss ?? '—'} / {status.position.take_profit ?? '—'}</div>
                  </div>
                  <div className="field">
                    <label>Unrealized P&amp;L</label>
                    <div>{money(status.position.unrealized_pnl)}</div>
                  </div>
                </div>
              )}

              <button type="button" className="btn btn-secondary" style={{ marginTop: 16 }} disabled={!canStop || submitting || busy} onClick={handleStop}>
                {status.status === 'stopping' ? 'Stopping…' : 'Stop session'}
              </button>
            </div>
          )}

          {canStart && (
            <form className="panel" onSubmit={handleStart}>
              <h3>Start a session</h3>
              <div className="field-row">
                <div className="field">
                  <label htmlFor="live-symbol">Live symbol</label>
                  <input
                    id="live-symbol" value={liveSymbol} onChange={(e) => setLiveSymbol(e.target.value)}
                    placeholder="e.g. MESU6 -- the CURRENT front-month contract" required
                  />
                  <p style={{ fontSize: 11, opacity: 0.6, marginTop: 2, maxWidth: 260 }}>
                    Must be the currently active contract, not a past/expired one -- an expired ticker
                    starts the session fine but never receives any live data. Quarterly codes: H=Mar,
                    M=Jun, U=Sep, Z=Dec (e.g. MESU6 = September 2026).
                  </p>
                </div>
                <div className="field">
                  <label htmlFor="live-resolution">Resolution</label>
                  <input id="live-resolution" value={resolution} onChange={(e) => setResolution(e.target.value)} placeholder="5min" />
                </div>
                <div className="field">
                  <label htmlFor="live-poll">Poll seconds</label>
                  <input id="live-poll" type="number" value={pollSeconds} onChange={(e) => setPollSeconds(e.target.value)} />
                </div>
              </div>
              {formError && <p role="alert" style={{ marginTop: 8 }}>{formError}</p>}
              <button type="submit" className="btn btn-primary" style={{ marginTop: 16 }} disabled={submitting || busy}>
                {submitting || status.status === 'starting' ? 'Starting…' : 'Start session'}
              </button>
            </form>
          )}
        </>
      )}
    </div>
  )
}
