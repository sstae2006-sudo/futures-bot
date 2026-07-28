import { NavLink, Outlet } from 'react-router-dom'
import StatusBar from './mission-control/StatusBar'

const NAV_SECTIONS: { label: string; links: { to: string; label: string }[] }[] = [
  {
    label: 'Home',
    links: [{ to: '/', label: 'Mission Control' }],
  },
  {
    label: 'Research',
    links: [
      { to: '/dashboard', label: 'Dashboard' },
      { to: '/backtest', label: 'Backtest Launcher' },
      { to: '/trades', label: 'Trade Explorer' },
      { to: '/import', label: 'Import Trades' },
      { to: '/compare', label: 'Strategy Comparison' },
      { to: '/optimizer', label: 'Optimizer' },
      { to: '/regime', label: 'Market Regime' },
      { to: '/jobs', label: 'Jobs' },
      { to: '/live', label: 'Live Session' },
      { to: '/market-data', label: 'Market Data' },
      { to: '/research-server', label: 'Research Server' },
    ],
  },
  {
    label: 'Insight',
    links: [
      { to: '/experiments', label: 'Experiments' },
      { to: '/ml', label: 'ML Research' },
      { to: '/reports', label: 'Reports' },
      { to: '/logs', label: 'System Logs' },
    ],
  },
  {
    label: 'Account',
    links: [
      { to: '/profile', label: 'Profile' },
      { to: '/organization', label: 'Organization' },
      { to: '/team', label: 'Team Members' },
    ],
  },
]

export default function Layout() {
  return (
    <div className="app-root">
      <StatusBar />
      <div className="app-shell">
        <aside className="sidebar">
          <div className="sidebar-brand">
            <strong>futures-bot</strong>
            <span>Research Workstation</span>
          </div>
          {NAV_SECTIONS.map((section) => (
            <div className="nav-section" key={section.label}>
              <div className="nav-label">{section.label}</div>
              {section.links.map((link) => (
                <NavLink
                  key={link.to}
                  to={link.to}
                  end={link.to === '/'}
                  className={({ isActive }) => `nav-link${isActive ? ' active' : ''}`}
                >
                  {link.label}
                </NavLink>
              ))}
            </div>
          ))}
        </aside>
        <main className="main">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
