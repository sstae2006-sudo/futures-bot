import { useCallback, useEffect, useState } from 'react'

// Light/dark theme preference. "system" follows the OS (prefers-color-scheme);
// "light"/"dark" pin it. Applied as `data-theme` on <html>, which index.css's
// token blocks key off. index.html applies the stored preference with a tiny
// inline script before first paint so a pinned theme never flashes the wrong
// colors; this hook keeps it in sync afterwards. Storage is best-effort --
// a blocked/private-mode localStorage just means the choice isn't remembered.

export type ThemePreference = 'system' | 'light' | 'dark'

export const THEME_STORAGE_KEY = 'futures-bot.theme'

const PREFERENCES: ThemePreference[] = ['system', 'light', 'dark']

export function readStoredTheme(): ThemePreference {
  try {
    const stored = window.localStorage.getItem(THEME_STORAGE_KEY)
    return PREFERENCES.includes(stored as ThemePreference) ? (stored as ThemePreference) : 'system'
  } catch {
    return 'system'
  }
}

export function applyTheme(preference: ThemePreference): void {
  const root = document.documentElement
  if (preference === 'system') {
    delete root.dataset.theme
  } else {
    root.dataset.theme = preference
  }
}

export function useTheme(): [ThemePreference, (next: ThemePreference) => void] {
  const [preference, setPreference] = useState<ThemePreference>(readStoredTheme)

  useEffect(() => {
    applyTheme(preference)
  }, [preference])

  const update = useCallback((next: ThemePreference) => {
    setPreference(next)
    try {
      if (next === 'system') {
        window.localStorage.removeItem(THEME_STORAGE_KEY)
      } else {
        window.localStorage.setItem(THEME_STORAGE_KEY, next)
      }
    } catch {
      // Not persisted; the in-memory preference still applies for this session.
    }
  }, [])

  return [preference, update]
}
