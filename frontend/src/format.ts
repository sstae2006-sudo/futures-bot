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
