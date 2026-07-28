// Every action here is visually real but functionally inert this pass
// (see the approved plan's "explicitly out of scope" section) -- no
// onClick calls a backend route yet. "ready" actions map to a script or
// command that genuinely exists today (captions are the real
// command/script, not invented); "planned" actions are the user's own
// named future placeholders (Train AI / Start Paper Trading / Start
// Live Trading) and are styled distinctly (dashed, muted, disabled) so
// the difference between "not wired up yet" and "doesn't exist yet" is
// visible at a glance.

interface Action {
  label: string
  caption: string
  planned?: boolean
}

const READY_ACTIONS: Action[] = [
  { label: 'Restart Backend', caption: 'scripts\\restart.ps1' },
  { label: 'Restart Frontend', caption: 'scripts\\restart.ps1' },
  { label: 'Restart Everything', caption: 'scripts\\restart.ps1' },
  { label: 'Run Doctor', caption: '/doctor' },
  { label: 'Validate Database', caption: 'futures_bot.cli --validate-db' },
  { label: 'Backup Database', caption: 'tools/ backup script' },
  { label: 'Refresh Status', caption: 'scripts\\status.ps1' },
]

const PLANNED_ACTIONS: Action[] = [
  { label: 'Train AI', caption: 'Coming soon', planned: true },
  { label: 'Start Paper Trading', caption: 'Coming soon', planned: true },
  { label: 'Start Live Trading', caption: 'Coming soon', planned: true },
]

function noop() {
  // Design/scaffold pass only -- no functionality wired yet.
}

export default function QuickActions() {
  return (
    <div className="mc-panel">
      <div className="mc-panel-head">
        <h3>Quick Actions</h3>
      </div>
      <div className="mc-actions-grid">
        {READY_ACTIONS.map((action) => (
          <button key={action.label} type="button" className="mc-action-btn" onClick={noop}>
            <span className="mc-action-label">{action.label}</span>
            <span className="mc-action-caption">{action.caption}</span>
          </button>
        ))}
        {PLANNED_ACTIONS.map((action) => (
          <button key={action.label} type="button" className="mc-action-btn planned" disabled>
            <span className="mc-action-label">{action.label}</span>
            <span className="mc-action-soon">Soon</span>
          </button>
        ))}
      </div>
    </div>
  )
}
