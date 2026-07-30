import { createContext, useContext, useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { getOrganization, getUserMe, sendUserHeartbeat } from './api'
import type { Capability, Organization, UserMe, UserRole } from './types'
import { can as roleCan } from './types'

// KNOWN_ISSUES.md ISSUE-043 -- `sendUserHeartbeat` (POST /api/users/{id}/heartbeat,
// which sets `last_active_at`) existed as a real, working backend route
// and a real frontend api.ts function, but nothing ever CALLED it -- the
// route's own docstring says it's meant to be "an explicit heartbeat from
// an active client session," but that wiring was never built. Every
// signed-in user's `last_active_at` was set once (at registration, via
// `create_user`) and never touched again, so `format.ts::isOnline`'s
// 2-minute window meant everyone showed "offline" within minutes of
// signing in, including the person actively looking at the page. Half
// the online window is a conventional heartbeat cadence for a TTL-style
// presence check like this one -- frequent enough that a missed tick or
// two never flips someone to "offline" while they're genuinely still
// here.
const HEARTBEAT_INTERVAL_MS = 60_000

// Frontend-only "current user" concept -- there is no backend session/
// auth (see accounts/store.py's docstring: no password, no token, no
// login endpoint). This remembers which *registered* user you are
// (a user id in localStorage) purely as a UX convenience -- "don't make
// me re-pick myself from a list every time I open the app" -- not a
// security boundary. Anyone with access to this browser/machine can open
// devtools and change the stored id; that's an accepted, documented
// limitation of a no-auth internal tool, not an oversight. See
// accounts/permissions.py's docstring for the matching note about
// `can()` below being advisory only.
const USER_ID_KEY = 'futures-bot:session:user-id'

interface SessionContextValue {
  currentUser: UserMe | null
  organization: Organization | null
  loading: boolean
  error: string | null
  login: (userId: string) => void
  logout: () => void
  refresh: () => void
  can: (capability: Capability) => boolean
}

const SessionContext = createContext<SessionContextValue | undefined>(undefined)

export function SessionProvider({ children }: { children: ReactNode }) {
  const [userId, setUserId] = useState<string | null>(() => window.localStorage.getItem(USER_ID_KEY))
  const [currentUser, setCurrentUser] = useState<UserMe | null>(null)
  const [organization, setOrganization] = useState<Organization | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [version, setVersion] = useState(0)

  useEffect(() => {
    let cancelled = false

    if (!userId) {
      setCurrentUser(null)
      setOrganization(null)
      setLoading(false)
      return undefined
    }

    setLoading(true)
    setError(null)
    getUserMe(userId)
      .then(async (user) => {
        if (cancelled) return
        setCurrentUser(user)
        const org = await getOrganization(user.org_id)
        if (!cancelled) setOrganization(org)
      })
      .catch(() => {
        if (cancelled) return
        // A stale/invalid session (the user was removed, or this points at
        // a different database than last time) -- clear it rather than
        // leaving the app stuck believing it's logged in as someone who
        // doesn't resolve to anything.
        window.localStorage.removeItem(USER_ID_KEY)
        setUserId(null)
        setCurrentUser(null)
        setOrganization(null)
        setError('Your saved session no longer resolves to a real user -- please register or sign in again.')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [userId, version])

  // KNOWN_ISSUES.md ISSUE-043 -- keeps `last_active_at` fresh for as long
  // as this tab is open with a signed-in user, so TeamPanel.tsx's
  // isOnline() check (a 2-minute TTL window) reflects reality instead of
  // permanently showing "offline" a couple minutes after sign-in. Fires
  // once immediately (so a fresh sign-in shows online right away, not
  // after the first interval tick) and then on HEARTBEAT_INTERVAL_MS.
  // Swallows failures silently -- a missed heartbeat should degrade to
  // "looks offline a bit early," never surface as a user-facing error for
  // what's a background presence signal, not a real action.
  useEffect(() => {
    if (!userId) return undefined

    sendUserHeartbeat(userId).catch(() => {})
    const id = setInterval(() => {
      sendUserHeartbeat(userId).catch(() => {})
    }, HEARTBEAT_INTERVAL_MS)
    return () => clearInterval(id)
  }, [userId])

  function login(id: string) {
    window.localStorage.setItem(USER_ID_KEY, id)
    setError(null)
    setUserId(id)
  }

  function logout() {
    window.localStorage.removeItem(USER_ID_KEY)
    setUserId(null)
    setCurrentUser(null)
    setOrganization(null)
  }

  function refresh() {
    setVersion((v) => v + 1)
  }

  function can(capability: Capability): boolean {
    if (!currentUser) return false
    return roleCan(currentUser.role as UserRole, capability)
  }

  return (
    <SessionContext.Provider value={{ currentUser, organization, loading, error, login, logout, refresh, can }}>
      {children}
    </SessionContext.Provider>
  )
}

export function useSession(): SessionContextValue {
  const ctx = useContext(SessionContext)
  if (!ctx) throw new Error('useSession must be used within a SessionProvider')
  return ctx
}
