import { Link } from 'react-router-dom'
import AlertCenter from '../components/mission-control/AlertCenter'
import ActivityFeed from '../components/mission-control/ActivityFeed'
import HealthGrid from '../components/mission-control/HealthGrid'
import QuickActions from '../components/mission-control/QuickActions'
import RoadmapPanel from '../components/mission-control/RoadmapPanel'
import { DatabaseSummaryCard, MarketContextSummaryCard, PerformanceCard, ResearchSummaryCard } from '../components/mission-control/SummaryCards'

// Mission Control is the platform's "operating system home screen" --
// the boot destination (index route). It answers five questions at a
// glance: is the platform healthy, is anything broken, what's running,
// what needs attention, what's next. Every other page is reached by
// branching outward from here (sidebar nav, or the summary cards'
// "View →" links). See the approved plan
// (C:\Users\sstae\.claude\plans\polymorphic-nibbling-lamport.md) for
// the full design rationale -- this is a design/scaffold pass: layout
// and component structure are real, the data behind it is placeholder
// (missionControlData.ts) until a system-health API exists.
export default function MissionControl() {
  return (
    <div className="mission-control">
      <div className="mc-cta">
        <div>
          <h2 style={{ marginBottom: 2 }}>Mission Control</h2>
          <p>Platform overview. Head to the research dashboard for backtests, the leaderboard, and system stats.</p>
        </div>
        <Link to="/dashboard" className="btn">Continue to Dashboard →</Link>
      </div>

      <HealthGrid />

      <div className="mc-body-grid">
        <div className="mc-column">
          <AlertCenter />
          <ActivityFeed />
        </div>
        <div className="mc-column">
          <QuickActions />
          <RoadmapPanel />
        </div>
        <div className="mc-column">
          <ResearchSummaryCard />
          <DatabaseSummaryCard />
          <MarketContextSummaryCard />
          <PerformanceCard />
        </div>
      </div>
    </div>
  )
}
