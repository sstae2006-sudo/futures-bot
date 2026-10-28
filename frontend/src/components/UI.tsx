import type { ReactNode } from 'react'

/** Shape-of-content placeholder shown while data loads (2026-10 design audit
 * F11B). `variant` picks the silhouette so the page doesn't jump when the
 * real content arrives; `label` stays as screen-reader text. `text` keeps the
 * label visible instead -- for multi-second steps (connecting, parsing,
 * starting a session) where saying what is happening matters more. */
export function LoadingState({
  label = 'Loading…',
  variant = 'table',
}: {
  label?: string
  variant?: 'table' | 'tiles' | 'chart' | 'lines' | 'text'
}) {
  if (variant === 'text') {
    return (
      <div className="state-msg" role="status">
        {label}
      </div>
    )
  }
  return (
    <div className="skeleton" role="status" aria-live="polite">
      <span className="sr-only">{label}</span>
      {variant === 'tiles' && (
        <div className="skeleton-tiles" aria-hidden="true">
          {[0, 1, 2, 3].map((i) => <div key={i} className="skeleton-block" />)}
        </div>
      )}
      {variant === 'chart' && <div className="skeleton-block skeleton-chart" aria-hidden="true" />}
      {variant === 'table' &&
        [0, 1, 2, 3, 4].map((i) => <div key={i} className="skeleton-block skeleton-row" aria-hidden="true" />)}
      {variant === 'lines' &&
        [100, 82, 64].map((w) => (
          <div key={w} className="skeleton-block skeleton-line" style={{ width: `${w}%` }} aria-hidden="true" />
        ))}
    </div>
  )
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="state-msg error" role="alert">
      <p>{message}</p>
      {onRetry && (
        <button className="btn btn-secondary mt-2" onClick={onRetry}>
          Retry
        </button>
      )}
    </div>
  )
}

/** Slim banner for a failed refresh while the last good response stays on
 * screen underneath (audit F13C) -- blanking a page is worse than showing
 * numbers that are a minute old, as long as the age is stated. */
export function StaleBanner({
  message,
  updatedAt,
  onRetry,
}: {
  message: string
  updatedAt: Date | null
  onRetry?: () => void
}) {
  const when = updatedAt
    ? updatedAt.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    : 'an earlier load'
  return (
    <div className="stale-banner" role="alert">
      <strong>Showing data from {when}</strong>
      <span className="stale-detail">Refresh failed: {message}</span>
      {onRetry && <button type="button" onClick={onRetry}>Retry</button>}
    </div>
  )
}

/** The error slot for a `useApi` result: a full ErrorState when there is
 * nothing to show, a StaleBanner when earlier data is still rendered. */
export function ApiErrorState({
  api,
}: {
  api: { data: unknown; error: string | null; updatedAt: Date | null; refetch: () => void }
}) {
  if (!api.error) return null
  if (api.data !== null && api.data !== undefined) {
    return <StaleBanner message={api.error} updatedAt={api.updatedAt} onRetry={api.refetch} />
  }
  return <ErrorState message={api.error} onRetry={api.refetch} />
}

/** `hint` is an optional quiet next step (audit F12C) -- one line, usually
 * with an inline link, never a button. */
export function EmptyState({ label, hint }: { label: string; hint?: ReactNode }) {
  return (
    <div className="state-msg">
      <div>{label}</div>
      {hint && <div className="state-hint">{hint}</div>}
    </div>
  )
}

export function StatTile({
  label,
  value,
  sub,
  tone = 'neutral',
  featured = false,
}: {
  label: string
  value: ReactNode
  sub?: ReactNode
  tone?: 'good' | 'bad' | 'neutral'
  /** Lead figure of its row (audit F7B): spans the row at a larger size.
   * Give the containing `.grid-stats` the `has-featured` class so the
   * supporting tiles step down. */
  featured?: boolean
}) {
  return (
    <div className={featured ? 'stat-tile featured' : 'stat-tile'}>
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
