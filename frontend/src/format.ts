// Every money/ratio figure crosses the wire as a JSON string (the backend
// serializes Decimal that way on purpose -- see api.ts's module comment).
// These helpers are the one place that turns those strings into display
// text, so a formatting change never has to be repeated per page.

export function money(value: string | null | undefined): string {
  if (value === null || value === undefined) return '—'
  const n = Number(value)
  const sign = n < 0 ? '-' : ''
  return `${sign}$${Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

export function pct(value: string | null | undefined, places = 1): string {
  if (value === null || value === undefined) return '—'
  return `${Number(value).toFixed(places)}%`
}

export function num(value: string | null | undefined, places = 2): string {
  if (value === null || value === undefined) return '—'
  return Number(value).toFixed(places)
}

export function int(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  return value.toLocaleString()
}

export function dateTime(value: string | null | undefined): string {
  if (!value) return '—'
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return value
  return d.toLocaleString(undefined, {
    year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  })
}

export function tone(value: string | null | undefined): 'good' | 'bad' | 'neutral' {
  if (value === null || value === undefined) return 'neutral'
  const n = Number(value)
  if (n > 0) return 'good'
  if (n < 0) return 'bad'
  return 'neutral'
}

//: How far back a user's `last_active_at` heartbeat still counts as
//: "online" -- deliberately short (there's no real presence/websocket
//: system, just whatever a client's own heartbeat last reported; see
//: `accounts/store.py`'s docstring). Explicitly does NOT reuse `dateTime`
//: above -- that helper has a known bug (KNOWN_ISSUES.md ISSUE-021)
//: parsing SQLite's timezone-less local-mode timestamps as local time
//: instead of UTC; this normalizes the same way `TeamPanel.tsx`'s
//: `timeAgo` already does, rather than inheriting that bug.
const ONLINE_WINDOW_MS = 2 * 60 * 1000

export function isOnline(lastActiveAt: string | null | undefined): boolean {
  if (!lastActiveAt) return false
  const hasTimezone = lastActiveAt.endsWith('Z') || /[+-]\d\d:\d\d$/.test(lastActiveAt)
  const normalized = hasTimezone ? lastActiveAt.replace(' ', 'T') : `${lastActiveAt.replace(' ', 'T')}Z`
  const then = new Date(normalized).getTime()
  if (Number.isNaN(then)) return false
  return Date.now() - then < ONLINE_WINDOW_MS
}
