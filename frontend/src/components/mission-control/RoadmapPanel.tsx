import { roadmap } from './missionControlData'

export default function RoadmapPanel() {
  return (
    <div className="mc-panel">
      <div className="mc-panel-head">
        <h3>Roadmap</h3>
      </div>
      <div className="mc-roadmap-milestone">
        <span className="k">Current Milestone</span>
        {roadmap.currentMilestone}
      </div>
      <div className="mc-roadmap-milestone">
        <span className="k">Next Priority</span>
        {roadmap.nextPriority}
      </div>
      <span className="k" style={{ display: 'block', marginBottom: 4 }}>Upcoming</span>
      <ul className="mc-roadmap-list">
        {roadmap.upcoming.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
      <div className="mc-roadmap-completion">
        <div className="bar-label">
          <span>Platform Completion</span>
          <span>{roadmap.completionPct}%</span>
        </div>
        <div className="job-progress-track">
          <div className="job-progress-fill tone-fill-good" style={{ width: `${roadmap.completionPct}%` }} />
        </div>
      </div>
    </div>
  )
}
