import type { ReactNode } from 'react'
import { Navigate } from 'react-router-dom'
import { useSession } from '../session'

// Gates every "inside the app" route behind having registered/signed in
// at least once (session.tsx's frontend-only, no-auth "current user"
// concept) -- the success criteria this satisfies: "launch SIL, register,
// create/join an org, open Mission Control." A brand-new install with no
// session lands on /welcome instead of an empty, user-less Mission
// Control.
export default function RequireSession({ children }: { children: ReactNode }) {
  const { currentUser, loading } = useSession()

  if (loading) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100vh', fontSize: 14, opacity: 0.7 }}>
        Loading your session…
      </div>
    )
  }

  if (!currentUser) {
    return <Navigate to="/welcome" replace />
  }

  return <>{children}</>
}
