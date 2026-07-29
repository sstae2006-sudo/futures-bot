import { useEffect, useState } from 'react'
import { generateIntegrationReview, getIntegrationQueue, getIntegrationReviews } from '../../api'
import { useApi } from '../../useApi'
import { Badge } from '../UI'
import type { IntegrationReview, MergeReadinessLevel, RiskLevel } from '../../types'

// SIL Phase 6 "Integration Coordinator" Milestone 1 -- work items in
// testing/ready_for_review, ordered by merge-readiness confidence score
// (a *view* over work_items, not a separate persisted queue -- see
// api/collaboration_service.py::get_integration_queue's docstring).
// Deliberately not called "Merge Queue" -- CollaborationWorkspace's
// existing tab of that name is a plain client-side filter with no
// scoring/ordering behind it; this panel is the real thing, so the two
// must read as clearly different at a glance.
//
// Milestone 2 extends this same row-expansion area with the persistent
// Integration Review (generate one, see the latest one's architecture
// impact/validation recommendation/conflict resolutions, see the
// confidence trend across history) rather than adding a third,
// overlapping dashboard -- see this project's own "avoid duplicate
// dashboards" guidance for that milestone.
const LEVEL_TONE: Record<MergeReadinessLevel, 'good' | 'warn' | 'bad' | 'neutral'> = {
  ready: 'good',
  needs_review: 'warn',
  risky: 'bad',
  not_ready: 'bad',
}

const RISK_TONE: Record<RiskLevel, 'good' | 'warn' | 'bad' | 'neutral'> = {
  no_risk: 'good',
  low: 'neutral',
  medium: 'warn',
  high: 'bad',
  critical: 'bad',
}

export default function IntegrationQueuePanel() {
  const { data: entries, refetch } = useApi(() => getIntegrationQueue(), [])
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [reviewsByItem, setReviewsByItem] = useState<Record<string, IntegrationReview[]>>({})
  const [generatingId, setGeneratingId] = useState<string | null>(null)

  useEffect(() => {
    const id = setInterval(refetch, 15_000)
    return () => clearInterval(id)
  }, [refetch])

  const loadReviews = (itemId: string) => {
    getIntegrationReviews(itemId, 5).then((reviews) => {
      setReviewsByItem((prev) => ({ ...prev, [itemId]: reviews }))
    })
  }

  const toggleExpanded = (itemId: string) => {
    if (expandedId === itemId) {
      setExpandedId(null)
      return
    }
    setExpandedId(itemId)
    if (!reviewsByItem[itemId]) loadReviews(itemId)
  }

  const handleGenerateReview = (itemId: string) => {
    setGeneratingId(itemId)
    generateIntegrationReview(itemId)
      .then(() => loadReviews(itemId))
      .finally(() => setGeneratingId(null))
  }

  return (
    <div className="mc-panel">
      <div className="mc-panel-head">
        <h3>Integration Queue</h3>
      </div>
      <p style={{ fontSize: 11, opacity: 0.6, marginTop: -4, marginBottom: 8 }}>
        Ordered by merge-readiness confidence score
      </p>
      {!entries && <p style={{ fontSize: 13, opacity: 0.7 }}>Loading…</p>}
      {entries && entries.length === 0 && (
        <p style={{ fontSize: 13, opacity: 0.7 }}>Nothing in testing or ready for review.</p>
      )}
      {entries && entries.length > 0 && (
        <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
          {entries.map((e, i) => {
            const expanded = expandedId === e.work_item.id
            const reviews = reviewsByItem[e.work_item.id]
            const latest = reviews && reviews.length > 0 ? reviews[0] : null
            return (
              <li
                key={e.work_item.id}
                style={{ padding: '6px 0', borderBottom: '1px solid var(--border, rgba(128,128,128,0.15))', fontSize: 13 }}
              >
                <button
                  type="button"
                  onClick={() => toggleExpanded(e.work_item.id)}
                  style={{
                    all: 'unset', cursor: 'pointer', display: 'flex', justifyContent: 'space-between',
                    alignItems: 'center', width: '100%',
                  }}
                >
                  <span>
                    <span style={{ opacity: 0.5, marginRight: 6 }}>#{i + 1}</span>
                    {e.work_item.title}
                  </span>
                  <Badge tone={LEVEL_TONE[e.merge_readiness.level]}>{e.merge_readiness.score}/100</Badge>
                </button>
                {e.readiness_note && (
                  <div style={{ opacity: 0.6, fontSize: 11, marginTop: 2 }}>{e.readiness_note}</div>
                )}
                {expanded && (
                  <div style={{ marginTop: 4 }}>
                    <ul style={{ listStyle: 'none', margin: '6px 0 0', padding: '0 0 0 12px', fontSize: 12 }}>
                      {e.merge_readiness.factors.map((f) => (
                        <li key={f.name} style={{ marginBottom: 2 }}>
                          <strong>{f.name}</strong>{f.penalty !== 0 ? ` (${f.penalty})` : ''}: {f.explanation}
                        </li>
                      ))}
                    </ul>

                    <div style={{ marginTop: 8, paddingLeft: 12 }}>
                      <button
                        type="button"
                        onClick={() => handleGenerateReview(e.work_item.id)}
                        disabled={generatingId === e.work_item.id}
                        style={{ fontSize: 11, padding: '2px 8px', cursor: 'pointer' }}
                      >
                        {generatingId === e.work_item.id ? 'Generating…' : 'Generate Integration Review'}
                      </button>

                      {reviews && reviews.length === 0 && (
                        <div style={{ marginTop: 6, fontSize: 11, opacity: 0.6 }}>No Integration Reviews generated yet.</div>
                      )}

                      {latest && (
                        <div style={{ marginTop: 6, fontSize: 12 }}>
                          <div>{latest.summary}</div>
                          <div style={{ fontWeight: 600, marginTop: 2 }}>{latest.recommendation}</div>

                          {latest.affected_subsystems.length > 0 && (
                            <div style={{ marginTop: 4, opacity: 0.85 }}>
                              <strong>Affects:</strong> {latest.affected_subsystems.join(', ')}
                            </div>
                          )}

                          <div style={{ marginTop: 4, opacity: 0.85 }}>
                            {latest.validation_recommendation.recommend_full_suite
                              ? 'Recommends running the full backend suite (some changed files have no direct test mapping).'
                              : latest.validation_recommendation.recommended_tests.length > 0
                                ? `Recommended tests: ${latest.validation_recommendation.recommended_tests.join(', ')}`
                                : 'No backend test files mapped to these changes.'}
                          </div>

                          {latest.conflict_resolutions.length > 0 && (
                            <ul style={{ listStyle: 'none', margin: '6px 0 0', padding: 0 }}>
                              {latest.conflict_resolutions.map((c) => (
                                <li key={c.work_item_id} style={{ marginTop: 4 }}>
                                  <Badge tone={RISK_TONE[c.risk]}>{c.risk}</Badge>{' '}
                                  <span style={{ opacity: 0.85 }}>{c.suggested_resolution}</span>
                                </li>
                              ))}
                            </ul>
                          )}

                          {reviews.length > 1 && (
                            <div style={{ marginTop: 6, opacity: 0.6, fontSize: 11 }}>
                              Confidence trend (oldest → newest): {reviews.slice().reverse().map((r) => r.confidence_score).join(' → ')}
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  </div>
                )}
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}
