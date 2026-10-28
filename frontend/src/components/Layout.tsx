import { useEffect, useState, type ReactNode } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import StatusBar from './mission-control/StatusBar'
import { useTheme, type ThemePreference } from '../theme'

// App shell. Five primary destinations stay one click away; every other page
// lives under "More", grouped by job. Nothing is removed -- every route in
// App.tsx is reachable from here. "More" opens by itself whenever the current
// page is one of its links, so the active page is never hidden.

type NavItem = { to: string; label: string; icon?: ReactNode }

const svgProps = {
  width: 18, height: 18, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor',
  strokeWidth: 1.75, strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const, 'aria-hidden': true,
}

const PRIMARY: NavItem[] = [
  { to: '/', label: 'Home', icon: <svg {...svgProps}><path d="M4 11l8-6 8 6v8a1 1 0 0 1-1 1h-4v-6h-6v6H5a1 1 0 0 1-1-1z" /></svg> },
  { to: '/dashboard', label: 'Dashboard', icon: <svg {...svgProps}><path d="M4 19h16M7 15l4-4 3 3 5-6" /></svg> },
  { to: '/backtest', label: 'Backtest', icon: <svg {...svgProps}><path d="M6 4l13 8-13 8z" /></svg> },
  { to: '/trades', label: 'Trades', icon: <svg {...svgProps}><path d="M4 6h16M4 12h16M4 18h10" /></svg> },
  { to: '/live', label: 'Live', icon: <svg {...svgProps}><circle cx="12" cy="12" r="3" /><path d="M5.6 5.6a9 9 0 0 0 0 12.8M18.4 5.6a9 9 0 0 1 0 12.8" /></svg> },
]

const MORE_GROUPS: { label: string; links: NavItem[] }[] = [
  {
    label: 'Research',
    links: [
      { to: '/compare', label: 'Compare strategies' },
      { to: '/optimizer', label: 'Optimizer' },
      { to: '/experiments', label: 'Experiments' },
      { to: '/ml', label: 'ML research' },
      { to: '/regime', label: 'Market regime' },
      { to: '/reports', label: 'Reports' },
    ],
  },
  {
    label: 'Data & system',
    links: [
      { to: '/market-data', label: 'Market data' },
      { to: '/import', label: 'Import trades' },
      { to: '/jobs', label: 'Jobs' },
      { to: '/research-server', label: 'Research server' },
      { to: '/logs', label: 'System logs' },
    ],
  },
  {
    label: 'Account',
    links: [
      { to: '/profile', label: 'Profile' },
      { to: '/organization', label: 'Organization' },
      { to: '/team', label: 'Team members' },
    ],
  },
]

const THEME_OPTIONS: { value: ThemePreference; label: string }[] = [
  { value: 'system', label: 'System' },
  { value: 'light', label: 'Light' },
  { value: 'dark', label: 'Dark' },
]

function isUnder(pathname: string, to: string): boolean {
  return pathname === to || pathname.startsWith(`${to}/`)
}

function navClass({ isActive }: { isActive: boolean }): string {
  return `nav-link${isActive ? ' active' : ''}`
}

export default function Layout() {
  const { pathname } = useLocation()
  const [theme, setTheme] = useTheme()
  const onMorePage = MORE_GROUPS.some((g) => g.links.some((l) => isUnder(pathname, l.to)))
  const [moreOpen, setMoreOpen] = useState(onMorePage)
  const [mobileOpen, setMobileOpen] = useState(false)

  useEffect(() => {
    if (onMorePage) setMoreOpen(true)
    setMobileOpen(false)
  }, [pathname, onMorePage])

  return (
    <div className="app-root">
      <StatusBar />
      <div className="app-shell">
        <aside className={`sidebar${mobileOpen ? ' open' : ''}`}>
          <div className="sidebar-brand">
            <span className="brand-mark" aria-hidden="true" />
            <strong>futures-bot</strong>
            <button
              type="button"
              className="sidebar-menu-toggle"
              aria-expanded={mobileOpen}
              aria-controls="sidebar-nav"
              onClick={() => setMobileOpen((v) => !v)}
            >
              {mobileOpen ? 'Close' : 'Menu'}
            </button>
          </div>

          <nav id="sidebar-nav" className="sidebar-nav" aria-label="Main">
            {PRIMARY.map((link) => (
              <NavLink key={link.to} to={link.to} end={link.to === '/'} className={navClass}>
                {link.icon}
                <span>{link.label}</span>
              </NavLink>
            ))}

            <button
              type="button"
              className="nav-more-toggle"
              aria-expanded={moreOpen}
              aria-controls="nav-more"
              onClick={() => setMoreOpen((v) => !v)}
            >
              <svg {...svgProps} className="nav-more-chevron"><path d="M9 6l6 6-6 6" /></svg>
              <span>More</span>
            </button>

            {moreOpen && (
              <div id="nav-more" className="nav-more">
                {MORE_GROUPS.map((group) => (
                  <div className="nav-section" key={group.label}>
                    <div className="nav-label">{group.label}</div>
                    {group.links.map((link) => (
                      <NavLink key={link.to} to={link.to} className={navClass}>
                        {link.label}
                      </NavLink>
                    ))}
                  </div>
                ))}
              </div>
            )}
          </nav>

          <div className="theme-switch" role="radiogroup" aria-label="Color theme">
            {THEME_OPTIONS.map((option) => (
              <button
                key={option.value}
                type="button"
                role="radio"
                aria-checked={theme === option.value}
                className={theme === option.value ? 'active' : ''}
                onClick={() => setTheme(option.value)}
              >
                {option.label}
              </button>
            ))}
          </div>
        </aside>
        <main className="main">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
