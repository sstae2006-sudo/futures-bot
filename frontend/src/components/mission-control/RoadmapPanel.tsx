import { roadmap } from './missionControlData'

// Static reference content, kept in sync with ROADMAP.md by hand -- not
// a live reading (there's no "% platform complete" this could honestly
// measure, so unlike every other Mission Control panel, this one isn't
// wired to an API call; see missionControlData.ts's module docstring).
export default function RoadmapPanel() {
  return (
    <div className="mc-panel">
      <div className="mc-panel-head">
        <h3>Roadmap</h3>
        <span style={{ fontSize: 10, opacity: 0.5 }}>static reference</span>
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
    </div>
  )
}
