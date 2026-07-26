import type { ReactNode } from 'react'

export function LoadingState({ label = 'Loading…' }: { label?: string }) {
  return (
    <div className="state-msg" role="status">
      {label}
    </div>
  )
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="state-msg error" role="alert">
      <p>{message}</p>
      {onRetry && (
        <button className="btn btn-secondary" onClick={onRetry} style={{ marginTop: 8 }}>
          Retry
        </button>
      )}
    </div>
  )
}

export function EmptyState({ label }: { label: string }) {
  return <div className="state-msg">{label}</div>
}

export function StatTile({
  label,
  value,
  sub,
  tone = 'neutral',
}: {
  label: string
  value: ReactNode
  sub?: ReactNode
  tone?: 'good' | 'bad' | 'neutral'
}) {
  return (
    <div className="stat-tile">
      <div className="label">{label}</div>
      <div className={`value tone-${tone}`}>{value}</div>
      {sub !== undefined && <div className="sub">{sub}</div>}
    </div>
  )
}

export function Badge({ tone, children }: { tone: 'good' | 'bad' | 'warn' | 'neutral'; children: ReactNode }) {
  return <span className={`badge badge-${tone}`}>{children}</span>
}

export function VerdictBadge({ level, label }: { level: 'green' | 'yellow' | 'red'; label: string }) {
  const tone = level === 'green' ? 'good' : level === 'yellow' ? 'warn' : 'bad'
  return <Badge tone={tone}>{label}</Badge>
}

export function Panel({ title, children }: { title?: string; children: ReactNode }) {
  return (
    <div className="panel">
      {title && <h3>{title}</h3>}
      {children}
    </div>
  )
}
