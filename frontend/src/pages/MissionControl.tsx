import { Link } from 'react-router-dom'
import AlertCenter from '../components/mission-control/AlertCenter'
import ActivityFeed from '../components/mission-control/ActivityFeed'
import AutomationPanel from '../components/mission-control/AutomationPanel'
import CollaborationWorkspace from '../components/mission-control/CollaborationWorkspace'
import HealthGrid from '../components/mission-control/HealthGrid'
import InfrastructurePanel from '../components/mission-control/InfrastructurePanel'
import IntegrationQueuePanel from '../components/mission-control/IntegrationQueuePanel'
import QuickActions from '../components/mission-control/QuickActions'
import RoadmapPanel from '../components/mission-control/RoadmapPanel'
import TeamPanel from '../components/mission-control/TeamPanel'
import WorkforcePanel from '../components/mission-control/WorkforcePanel'
import WorkRegistryPanel from '../components/mission-control/WorkRegistryPanel'
import { DatabaseSummaryCard, ResearchSummaryCard } from '../components/mission-control/SummaryCards'

// Mission Control is the platform's "operating system home screen" --
// the boot destination (index route). It answers five questions at a
// glance: is the platform healthy, is anything broken, what's running,
// what needs attention, what's next. Every other page is reached by
// branching outward from here (sidebar nav, or the summary cards'
// "View →" links). Every panel here reads real data from the API --
// infrastructure metrics, team/connected-user state, the Active Work
// Registry's full Collaboration Workspace, health/activity/alerts,
// research/database summaries (fixed 2026-07-29, KNOWN_ISSUES.md
// ISSUE-040 -- these five used to render hardcoded placeholders) --
// with one deliberate, clearly-labeled exception: RoadmapPanel is
// static reference content kept in sync with ROADMAP.md by hand, since
// there's no honest "% platform complete" this page could measure.
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
          <InfrastructurePanel />
          <TeamPanel />
        </div>
        <div className="mc-column">
          <WorkRegistryPanel />
          <CollaborationWorkspace />
        </div>
        <div className="mc-column">
          <QuickActions />
          <RoadmapPanel />
          <AutomationPanel />
        </div>
        <div className="mc-column">
          {/* SIL Phase 6 "Integration Coordinator" Milestone 1 -- Worker
              Registry + Integration Queue. See docs/ARCHITECTURE.md's
              "SIL Phase 6" section. */}
          <WorkforcePanel />
          <IntegrationQueuePanel />
        </div>
        <div className="mc-column">
          <ResearchSummaryCard />
          <DatabaseSummaryCard />
        </div>
      </div>
    </div>
  )
}
