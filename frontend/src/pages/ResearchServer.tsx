import { useEffect, useState } from 'react'
import {
  getResearchServerStatus, startResearchServer, stopResearchServer, runNightlyBatchNow,
  getResearchServerFindings, ApiRequestError, deployFinding, rollbackConfigDeployment,
  listConfigDeployments, testFindingParams, listDatasets,
} from '../api'
import { useApi } from '../useApi'
import { useJobStream } from '../useJobStream'
import { LoadingState, ErrorState, EmptyState, StatTile, Badge, Panel } from '../components/UI'
import { JobProgressBar } from '../components/JobProgress'
import { dateTime, money, num, tone } from '../format'
import type { InsightOut, ParamsComparisonResult } from '../types'

function uptime(seconds: number | null): string {
  if (seconds === null) return '—'
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = Math.floor(seconds % 60)
  return `${h}h ${m}m ${s}s`
}

export default function ResearchServer() {
  const status = useApi(getResearchServerStatus)
  const findings = useApi(getResearchServerFindings)

  const [busy, setBusy] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [selectedFinding, setSelectedFinding] = useState<InsightOut | null>(null)

  async function run<T>(label: string, action: () => Promise<T>, describe: (r: T) => string) {
    setBusy(label)
    setError(null)
    setMessage(null)
    try {
      const result = await action()
      setMessage(describe(result))
      status.refetch()
      findings.refetch()
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.message : `${label} failed.`)
    } finally {
      setBusy(null)
    }
  }

  return (
    <div>
      <div className="page-header">
        <h1>Research Server</h1>
        <p>
          Phase 8B: the autonomous mode that keeps market data synced, paper trades every strategy
          in <code>research_server.paper_strategies</code>, and runs nightly research -- all without
          anyone clicking anything. Turned on via <code>research_server.enabled: true</code> in
          config.yaml (off by default); the controls below start/stop it for this session regardless
          of that setting.
        </p>
      </div>

      {status.loading && <LoadingState label="Loading research server status…" />}
      {status.error && <ErrorState message={status.error} onRetry={status.refetch} />}

      {status.data && (
        <>
          <div className="panel">
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 12 }}>
              <StatTile
                label="Server"
                value={<Badge tone={status.data.running ? 'good' : 'neutral'}>{status.data.running ? 'running' : 'stopped'}</Badge>}
              />
              <StatTile label="Uptime" value={uptime(status.data.uptime_seconds)} />
              <StatTile
                label="Market connection"
                value={<Badge tone={status.data.data_scheduler.running ? 'good' : 'neutral'}>{status.data.data_scheduler.running ? 'connected' : 'idle'}</Badge>}
                sub={status.data.data_scheduler.last_error ?? undefined}
              />
              <StatTile label="Active paper strategies" value={Object.keys(status.data.paper_trader.strategies).length} />
              <StatTile
                label="Nightly jobs"
                value={<Badge tone={status.data.nightly_jobs.running ? 'good' : 'neutral'}>{status.data.nightly_jobs.running ? 'scheduled' : 'stopped'}</Badge>}
                sub={status.data.nightly_jobs.last_run_date ? `last: ${status.data.nightly_jobs.last_run_date}` : 'never run'}
              />
            </div>

            <div style={{ display: 'flex', gap: 8, marginTop: 16 }}>
              {status.data.running ? (
                <button type="button" className="btn btn-secondary" disabled={busy !== null} onClick={() => run('stop', stopResearchServer, () => 'Research server stopped.')}>
                  {busy === 'stop' ? 'Stopping…' : 'Stop server'}
                </button>
              ) : (
                <button type="button" className="btn btn-primary" disabled={busy !== null} onClick={() => run('start', startResearchServer, () => 'Research server started.')}>
                  {busy === 'start' ? 'Starting…' : 'Start server'}
                </button>
              )}
              <button type="button" className="btn btn-secondary" disabled={busy !== null} onClick={() => run('nightly', runNightlyBatchNow, (r) => r.summary)}>
                {busy === 'nightly' ? 'Running…' : 'Run nightly batch now'}
              </button>
            </div>
            {message && <p style={{ marginTop: 12 }}>{message}</p>}
            {error && <p role="alert" style={{ marginTop: 12 }}>{error}</p>}
          </div>

          <div className="panel">
            <h3>Active paper strategies</h3>
            {Object.keys(status.data.paper_trader.strategies).length === 0 && (
              <EmptyState label="No strategies configured (research_server.paper_strategies is empty), or the server isn't running." />
            )}
            {Object.keys(status.data.paper_trader.strategies).length > 0 && (
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>Strategy</th><th>Status</th><th>Position</th><th>Session P&amp;L</th><th>Trades today</th><th>Halted</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.values(status.data.paper_trader.strategies).map((s) => (
                      <tr key={s.strategy}>
                        <td className="text-col">{s.strategy}</td>
                        <td><Badge tone={s.status === 'error' ? 'bad' : s.status === 'running' ? 'good' : 'neutral'}>{s.status}</Badge></td>
                        <td>{s.position ? `${s.position.side} × ${s.position.quantity}` : 'flat'}</td>
                        <td>{money(s.session_pnl)}</td>
                        <td>{s.trade_count_today ?? '—'}</td>
                        <td>{s.halted ? <Badge tone="bad">{s.halt_reason ?? 'halted'}</Badge> : '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {status.data.paper_trader.last_feed_error && (
              <p style={{ marginTop: 12 }}><Badge tone="warn">Feed warning</Badge> {status.data.paper_trader.last_feed_error}</p>
            )}
          </div>

          <Panel title="Recent discoveries">
            {findings.loading && <LoadingState label="Loading findings…" />}
            {findings.error && <ErrorState message={findings.error} onRetry={findings.refetch} />}
            {findings.data && findings.data.length === 0 && (
              <EmptyState label="No degradation, regime-drift, or parameter-recommendation findings yet." />
            )}
            {findings.data && findings.data.length > 0 && (
              <ul style={{ listStyle: 'none', padding: 0, margin: 0 }}>
                {findings.data.map((f, i) => (
                  <li key={i} style={{ marginBottom: 8 }}>
                    <button
                      type="button"
                      onClick={() => setSelectedFinding(f)}
                      style={{
                        all: 'unset', cursor: 'pointer', display: 'block', width: '100%',
                        padding: '6px 8px', borderRadius: 6,
                        background: selectedFinding === f ? 'var(--accent-dim)' : 'transparent',
                      }}
                    >
                      <Badge tone={f.severity === 'warning' ? 'warn' : 'neutral'}>{f.category}</Badge>{' '}
                      {f.message}
                      {f.details && <span style={{ marginLeft: 8, fontSize: 11, color: 'var(--text-faint)' }}>(details →)</span>}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </Panel>

          {selectedFinding && (
            <FindingDetail
              finding={selectedFinding}
              onClose={() => setSelectedFinding(null)}
              onChanged={() => { findings.refetch() }}
            />
          )}

          {status.data.nightly_jobs.last_run_summary && (
            <div className="panel">
              <h3>Nightly research</h3>
              <p>Last run: {dateTime(status.data.nightly_jobs.last_run_date)} -- {status.data.nightly_jobs.last_run_summary}</p>
              {status.data.nightly_jobs.last_error && <p role="alert">{status.data.nightly_jobs.last_error}</p>}
            </div>
          )}
        </>
      )}
    </div>
  )
}

/** Detail window for one finding -- every category shows its structured
 * `details` payload; a 'recommendation' additionally gets "Test More"
 * (a real backtest comparing current vs. recommended params, run as a
 * background job before anyone commits to anything) and "Deploy"
 * (rewrites config.yaml, with a confirm step and a rollback history --
 * mirrors Phase 9's model deploy/rollback pattern). */
function FindingDetail({
  finding, onClose, onChanged,
}: {
  finding: InsightOut
  onClose: () => void
  onChanged: () => void
}) {
  const details = (finding.details ?? {}) as Record<string, unknown>
  const isRecommendation = finding.category === 'recommendation'
  const isDeployable = isRecommendation && details.is_deployable === true

  const deployments = useApi(() => listConfigDeployments(finding.strategy ?? undefined), [finding.strategy])
  const datasets = useApi(listDatasets)
  const [datasetForTest, setDatasetForTest] = useState('')

  const [testJobId, setTestJobId] = useState<string | null>(null)
  const [testing, setTesting] = useState(false)
  const [testError, setTestError] = useState<string | null>(null)
  const testJob = useJobStream(testJobId)
  const testResult = testJob?.status === 'completed'
    ? (testJob.result_payload as unknown as ParamsComparisonResult | null)
    : null

  const [confirmingDeploy, setConfirmingDeploy] = useState(false)
  const [deploying, setDeploying] = useState(false)
  const [deployError, setDeployError] = useState<string | null>(null)
  const [deployedMessage, setDeployedMessage] = useState<string | null>(null)

  const [rollingBack, setRollingBack] = useState<string | null>(null)

  useEffect(() => {
    setTestJobId(null)
    setDeployedMessage(null)
    setConfirmingDeploy(false)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [finding])

  async function handleTestMore() {
    if (!finding.strategy || !datasetForTest) return
    setTesting(true)
    setTestError(null)
    try {
      const job = await testFindingParams({
        strategy: finding.strategy, dataset: datasetForTest,
        recommended_params: details.recommended_params as Record<string, unknown>,
      })
      setTestJobId(job.id)
    } catch (err) {
      setTestError(err instanceof Error ? err.message : 'Could not start the comparison.')
    } finally {
      setTesting(false)
    }
  }

  async function handleDeploy() {
    if (!finding.strategy) return
    setDeploying(true)
    setDeployError(null)
    try {
      await deployFinding({
        strategy: finding.strategy, params: details.recommended_params as Record<string, unknown>,
        run_id: details.run_id as string | undefined,
      })
      setDeployedMessage('Deployed -- config.yaml has been updated. A backup was taken automatically; roll back below if needed.')
      setConfirmingDeploy(false)
      deployments.refetch()
      onChanged()
    } catch (err) {
      setDeployError(err instanceof Error ? err.message : 'Deploy failed.')
    } finally {
      setDeploying(false)
    }
  }

  async function handleRollback(deploymentId: string) {
    setRollingBack(deploymentId)
    try {
      await rollbackConfigDeployment(deploymentId)
      deployments.refetch()
      onChanged()
    } finally {
      setRollingBack(null)
    }
  }

  return (
    <div className="panel">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 8 }}>
        <h3 style={{ marginTop: 0 }}>
          Finding Detail <Badge tone={finding.severity === 'warning' ? 'warn' : 'neutral'}>{finding.category}</Badge>
        </h3>
        <button className="btn btn-secondary" type="button" onClick={onClose}>Close</button>
      </div>
      <p>{finding.message}</p>

      {Object.keys(details).length > 0 && (
        <dl className="detail-list">
          {Object.entries(details).filter(([k]) => k !== 'is_deployable').map(([k, v]) => (
            <div key={k} style={{ display: 'flex', justifyContent: 'space-between', padding: '4px 0', borderBottom: '1px solid var(--border)', fontSize: 12.5, gap: 12 }}>
              <span style={{ color: 'var(--text-faint)' }}>{k}</span>
              <span className="mono" style={{ textAlign: 'right', wordBreak: 'break-word' }}>
                {typeof v === 'object' ? JSON.stringify(v) : String(v)}
              </span>
            </div>
          ))}
        </dl>
      )}

      {isRecommendation && !isDeployable && (
        <p style={{ fontSize: 12.5, color: 'var(--text-faint)', marginTop: 8 }}>
          {finding.strategy} is not config.yaml's active <code>strategy_name</code> -- only the active strategy has a
          config-file <code>strategy_params</code> slot, so this recommendation can't be deployed from here. See
          docs/RESEARCH_SERVER.md's "Known gaps" section.
        </p>
      )}

      {isDeployable && (
        <>
          <h4>Test More</h4>
          <p style={{ fontSize: 12.5, color: 'var(--text-dim)' }}>
            Runs a real backtest with the currently configured params and one with the recommended params, side by
            side, before you decide whether to deploy.
          </p>
          <div className="field-row" style={{ alignItems: 'flex-end' }}>
            <div className="field">
              <label htmlFor="test-dataset">Dataset</label>
              <select id="test-dataset" value={datasetForTest} onChange={(e) => setDatasetForTest(e.target.value)}>
                <option value="">Select…</option>
                {(datasets.data ?? []).map((d) => <option key={d.filename} value={d.filename}>{d.filename}</option>)}
              </select>
            </div>
            <button className="btn btn-secondary" type="button" disabled={!datasetForTest || testing} onClick={handleTestMore}>
              {testing ? 'Starting…' : 'Test More'}
            </button>
          </div>
          {testError && <ErrorState message={testError} />}
          {testJob && testJob.status !== 'completed' && <div style={{ marginTop: 8 }}><JobProgressBar job={testJob} /></div>}
          {testResult && (
            <div style={{ marginTop: 12 }}>
              <div className="grid grid-2">
                <div>
                  <h4 style={{ marginBottom: 4 }}>Current Params</h4>
                  <div className="grid grid-stats">
                    <StatTile label="Trades" value={testResult.current.trade_count ?? '—'} />
                    <StatTile label="Net P&L" value={money(testResult.current.net_pnl)} />
                    <StatTile label="Sharpe" value={testResult.current.sharpe_ratio ?? '—'} />
                  </div>
                </div>
                <div>
                  <h4 style={{ marginBottom: 4 }}>Recommended Params</h4>
                  <div className="grid grid-stats">
                    <StatTile label="Trades" value={testResult.recommended.trade_count ?? '—'} />
                    <StatTile label="Net P&L" value={money(testResult.recommended.net_pnl)} />
                    <StatTile label="Sharpe" value={testResult.recommended.sharpe_ratio ?? '—'} />
                  </div>
                </div>
              </div>
              <p style={{ fontSize: 11.5, color: 'var(--text-faint)', margin: '8px 0 0' }}>
                Net P&amp;L alone can look better while quietly trading a thinner sample, a worse profit
                factor, or a bigger drawdown — these numbers say whether it actually did.
              </p>
              <div className="grid grid-stats">
                <StatTile
                  label="P&L Improvement" value={money(testResult.pnl_improvement)}
                  tone={tone(testResult.pnl_improvement)}
                />
                <StatTile
                  label="Profit Factor Δ" value={num(testResult.profit_factor_improvement)}
                  tone={tone(testResult.profit_factor_improvement)}
                />
                <StatTile
                  label="Expectancy Δ" value={money(testResult.expectancy_improvement)}
                  tone={tone(testResult.expectancy_improvement)}
                />
                <StatTile
                  label="Drawdown Reduction" value={money(testResult.drawdown_reduction)}
                  tone={tone(testResult.drawdown_reduction)}
                />
                <StatTile
                  label="Trades Retained"
                  value={`${testResult.trade_count_retained} (${testResult.trade_count_retained_pct !== null ? testResult.trade_count_retained_pct.toFixed(0) : '—'}%)`}
                />
              </div>
            </div>
          )}

          <h4 style={{ marginTop: 16 }}>Deploy</h4>
          {!confirmingDeploy ? (
            <button className="btn" type="button" onClick={() => setConfirmingDeploy(true)}>Deploy Recommended Params</button>
          ) : (
            <div className="caveats">
              <h3>Confirm deploy</h3>
              <p>
                This rewrites <code>config.yaml</code>'s <code>strategy_params</code> for <strong>{finding.strategy}</strong> to{' '}
                <code className="mono">{JSON.stringify(details.recommended_params)}</code>. A full backup of the current
                file is taken first (comments included) and can be restored with one click below at any time.
              </p>
              <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
                <button className="btn" type="button" disabled={deploying} onClick={handleDeploy}>
                  {deploying ? 'Deploying…' : 'Confirm Deploy'}
                </button>
                <button className="btn btn-secondary" type="button" onClick={() => setConfirmingDeploy(false)}>Cancel</button>
              </div>
            </div>
          )}
          {deployError && <ErrorState message={deployError} />}
          {deployedMessage && <p style={{ marginTop: 8 }}>{deployedMessage}</p>}

          <h4 style={{ marginTop: 16 }}>Deployment History</h4>
          {deployments.loading && <LoadingState label="Loading deployment history…" />}
          {deployments.data && deployments.data.length === 0 && <EmptyState label="No deploys yet for this strategy." />}
          {deployments.data && deployments.data.length > 0 && (
            <table>
              <thead><tr><th>Action</th><th>Params</th><th>When</th><th /></tr></thead>
              <tbody>
                {deployments.data.map((d) => (
                  <tr key={d.id}>
                    <td><Badge tone={d.action === 'deploy' ? 'good' : 'neutral'}>{d.action}</Badge></td>
                    <td className="mono">{JSON.stringify(d.params)}</td>
                    <td>{dateTime(d.created_at)}</td>
                    <td>
                      <button
                        className="btn btn-secondary" type="button" disabled={rollingBack !== null}
                        onClick={() => handleRollback(d.id)}
                      >
                        {rollingBack === d.id ? 'Rolling back…' : 'Undo this change'}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}
    </div>
  )
}
