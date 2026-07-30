// Mission Control's remaining static content.
//
// Everything that USED to live in this file as a hardcoded placeholder
// (component health, live activity, alerts, research/database/market-
// context summaries, performance) has been wired to real API calls
// instead (KNOWN_ISSUES.md ISSUE-040) -- see HealthGrid.tsx,
// ActivityFeed.tsx, AlertCenter.tsx, SummaryCards.tsx. Only `roadmap`
// remains here: forward-looking plan/priority text with no
// corresponding "live" API to wire to (it's curated, not measured) --
// RoadmapPanel.tsx labels it as static reference rather than implying
// it's a live reading, and it should be updated by hand alongside
// ROADMAP.md, not auto-generated.

export const roadmap = {
  currentMilestone: 'SIL Phase 6 "Integration Coordinator" -- Milestone 2 (Intelligent Review Pipeline) complete',
  nextPriority: 'SIL Research Engine 2.0 Phase 1 -- research/backtest audit findings (see KNOWN_ISSUES.md ISSUE-039/040 and open optimizer/ML findings)',
  upcoming: ['Walk-forward testing', 'Monte Carlo simulation', 'Parameter robustness analysis'],
}
