import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import Layout from '../components/Layout'
import { THEME_STORAGE_KEY } from '../theme'

// StatusBar does its own API/session work and has its own tests
// (MissionControlPanels.test.tsx); here we only care about the nav shell.
vi.mock('../components/mission-control/StatusBar', () => ({ default: () => null }))

// Every authenticated route in App.tsx. The 2026-10 redesign moved most pages
// under "More" -- this list guards the "nothing removed" promise.
const ALL_ROUTES = [
  '/', '/dashboard', '/backtest', '/trades', '/live',
  '/compare', '/optimizer', '/experiments', '/ml', '/regime', '/reports',
  '/market-data', '/import', '/jobs', '/research-server', '/logs',
  '/profile', '/organization', '/team',
]

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route element={<Layout />}>
          <Route path="*" element={<p>page</p>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  )
}

function navHrefs(): string[] {
  const nav = screen.getByRole('navigation', { name: 'Main' })
  return Array.from(nav.querySelectorAll('a')).map((a) => a.getAttribute('href') ?? '')
}

afterEach(() => {
  window.localStorage.clear()
  delete document.documentElement.dataset.theme
})

describe('Layout navigation', () => {
  it('shows only the five primary destinations with More collapsed on the home page', () => {
    renderAt('/')
    expect(navHrefs()).toEqual(['/', '/dashboard', '/backtest', '/trades', '/live'])
    expect(screen.getByRole('button', { name: 'More' })).toHaveAttribute('aria-expanded', 'false')
  })

  it('reaches every app route once More is opened', () => {
    renderAt('/')
    fireEvent.click(screen.getByRole('button', { name: 'More' }))
    expect(new Set(navHrefs())).toEqual(new Set(ALL_ROUTES))
  })

  it('opens More automatically when the current page lives inside it', () => {
    renderAt('/optimizer')
    expect(screen.getByRole('button', { name: 'More' })).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByRole('link', { name: 'Optimizer' })).toHaveClass('active')
  })

  it('treats nested routes as inside their More group', () => {
    renderAt('/jobs/abc123')
    expect(screen.getByRole('button', { name: 'More' })).toHaveAttribute('aria-expanded', 'true')
  })
})

describe('Theme switch', () => {
  it('defaults to System with no data-theme attribute', () => {
    renderAt('/')
    expect(screen.getByRole('radio', { name: 'System' })).toHaveAttribute('aria-checked', 'true')
    expect(document.documentElement.dataset.theme).toBeUndefined()
  })

  it('pins dark, persists it, and returns to System cleanly', () => {
    renderAt('/')
    fireEvent.click(screen.getByRole('radio', { name: 'Dark' }))
    expect(document.documentElement.dataset.theme).toBe('dark')
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe('dark')

    fireEvent.click(screen.getByRole('radio', { name: 'System' }))
    expect(document.documentElement.dataset.theme).toBeUndefined()
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBeNull()
  })

  it('restores a stored preference on load', () => {
    window.localStorage.setItem(THEME_STORAGE_KEY, 'light')
    renderAt('/')
    expect(screen.getByRole('radio', { name: 'Light' })).toHaveAttribute('aria-checked', 'true')
    expect(document.documentElement.dataset.theme).toBe('light')
  })

  it('ignores a garbage stored value', () => {
    window.localStorage.setItem(THEME_STORAGE_KEY, 'neon')
    renderAt('/')
    expect(screen.getByRole('radio', { name: 'System' })).toHaveAttribute('aria-checked', 'true')
  })
})
