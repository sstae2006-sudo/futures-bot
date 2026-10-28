/** Chart series colours (2026-10 design audit F2B). Values are the
 * `--series-*` tokens in index.css: brass-led, muted, retuned per theme, with
 * no pure green or red so a series never reads as profit/loss. Kept out of
 * components/Charts.tsx so that file only exports components (fast refresh). */

/** Default colour for a chart's single (non-P&L) series. */
export const SERIES_COLOR = 'var(--series-1)'

/** Ordered categorical palette for multi-series charts. */
export const SERIES_PALETTE = [
  'var(--series-1)', 'var(--series-2)', 'var(--series-3)', 'var(--series-4)',
  'var(--series-5)', 'var(--series-6)', 'var(--series-7)', 'var(--series-8)',
]
