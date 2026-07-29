import { describe, expect, it } from 'vitest'
import { resolveApiBase } from '../api'

// Regression test for a real bug (Stabilization Mode, 2026-07-28):
// API_BASE used to be `import.meta.env.VITE_API_BASE_URL ?? 'http://127.0.0.1:8000'`.
// scripts/start-team.ps1 tried to override the default to a relative path
// for its production build by setting `$env:VITE_API_BASE_URL = ""` before
// `npm run build`, but an empty-string env var doesn't reliably survive
// the PowerShell -> npm.cmd -> cmd.exe -> vite child-process chain on
// Windows -- the built bundle ended up with the loopback default baked
// in anyway, breaking every teammate's API calls in Team Mode (confirmed
// directly: inspecting the built dist bundle showed the literal
// 'http://127.0.0.1:8000' string despite the env var having been set).
// resolveApiBase() now bases its default on import.meta.env.DEV/PROD
// (compiled in by Vite as real booleans, not round-tripped through a
// child-process environment), which is immune to that failure mode.
describe('resolveApiBase', () => {
  it('defaults to the loopback backend in dev mode (Local Mode: separate Vite dev server + API origin)', () => {
    expect(resolveApiBase(undefined, true)).toBe('http://127.0.0.1:8000')
  })

  it('defaults to a relative path in a production build (Team Mode: one origin serves both)', () => {
    expect(resolveApiBase(undefined, false)).toBe('')
  })

  it('an explicit VITE_API_BASE_URL always wins, in dev mode', () => {
    expect(resolveApiBase('https://api.example.com', true)).toBe('https://api.example.com')
  })

  it('an explicit VITE_API_BASE_URL always wins, in a production build', () => {
    expect(resolveApiBase('https://api.example.com', false)).toBe('https://api.example.com')
  })

  it('an explicitly empty VITE_API_BASE_URL is honored as relative, not treated as unset', () => {
    // This is the exact case that broke on Windows: a real, present empty
    // string must NOT fall through to the dev default just because it's
    // falsy-looking.
    expect(resolveApiBase('', true)).toBe('')
    expect(resolveApiBase('', false)).toBe('')
  })
})
