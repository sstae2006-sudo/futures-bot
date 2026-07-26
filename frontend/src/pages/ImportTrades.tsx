import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  cancelImportStaging, confirmImport, createClientProfile, listClientProfiles, listImportHistory,
  uploadImportFile,
} from '../api'
import { useApi } from '../useApi'
import { useJobStream } from '../useJobStream'
import { LoadingState, ErrorState, EmptyState, StatTile, Badge } from '../components/UI'
import { JobProgressBar } from '../components/JobProgress'
import { dateTime } from '../format'
import type { ImportUploadResponse } from '../types'

const CANONICAL_FIELDS: { key: string; label: string; required: boolean }[] = [
  { key: 'timestamp', label: 'Timestamp', required: true },
  { key: 'symbol', label: 'Symbol / Contract', required: true },
  { key: 'side', label: 'Side (Buy/Sell)', required: true },
  { key: 'quantity', label: 'Quantity', required: true },
  { key: 'price', label: 'Price', required: true },
  { key: 'commission', label: 'Commission', required: false },
  { key: 'realized_pnl', label: 'Realized P&L', required: false },
  { key: 'account', label: 'Account', required: false },
  { key: 'fill_id', label: 'Order / Fill ID', required: false },
]

export default function ImportTrades() {
  const profiles = useApi(listClientProfiles)
  const [profileId, setProfileId] = useState('')
  const [newProfileName, setNewProfileName] = useState('')
  const [creatingProfile, setCreatingProfile] = useState(false)

  useEffect(() => {
    if (!profileId && profiles.data && profiles.data.length > 0) setProfileId(profiles.data[0].id)
  }, [profiles.data, profileId])

  const [upload, setUpload] = useState<ImportUploadResponse | null>(null)
  const [uploadError, setUploadError] = useState<string | null>(null)
  const [uploading, setUploading] = useState(false)
  const [mapping, setMapping] = useState<Record<string, string>>({})
  const fileInputRef = useRef<HTMLInputElement>(null)

  const [confirming, setConfirming] = useState(false)
  const [confirmError, setConfirmError] = useState<string | null>(null)
  const [jobId, setJobId] = useState<string | null>(null)
  const streamedJob = useJobStream(jobId)

  const history = useApi(() => listImportHistory({ profile_id: profileId || undefined }), [profileId])

  useEffect(() => {
    if (streamedJob && (streamedJob.status === 'completed' || streamedJob.status === 'failed')) {
      history.refetch()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [streamedJob?.status])

  async function handleCreateProfile(e: React.FormEvent) {
    e.preventDefault()
    setCreatingProfile(true)
    try {
      const created = await createClientProfile({ name: newProfileName })
      setNewProfileName('')
      await profiles.refetch()
      setProfileId(created.id)
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : 'Could not create the client profile.')
    } finally {
      setCreatingProfile(false)
    }
  }

  async function handleFileSelected(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    if (!file || !profileId) return
    setUploading(true)
    setUploadError(null)
    setUpload(null)
    setJobId(null)
    try {
      const result = await uploadImportFile(profileId, file)
      setUpload(result)
      setMapping(
        Object.fromEntries(
          Object.entries(result.suggested_mapping).filter((entry): entry is [string, string] => entry[1] !== null),
        ),
      )
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : 'Could not upload the file.')
    } finally {
      setUploading(false)
    }
  }

  async function handleCancelUpload() {
    if (upload) await cancelImportStaging(upload.import_id).catch(() => {})
    setUpload(null)
    setUploadError(null)
    if (fileInputRef.current) fileInputRef.current.value = ''
  }

  async function handleConfirm() {
    if (!upload) return
    setConfirming(true)
    setConfirmError(null)
    try {
      const job = await confirmImport(upload.import_id, mapping)
      setJobId(job.id)
    } catch (err) {
      setConfirmError(err instanceof Error ? err.message : 'Could not start the import.')
    } finally {
      setConfirming(false)
    }
  }

  const selectedProfile = profiles.data?.find((p) => p.id === profileId)
  const importComplete = streamedJob?.status === 'completed'

  return (
    <div>
      <div className="page-header">
        <h1>Import Trades</h1>
        <p>Import client trade history from Tradovate, NinjaTrader, generic CSV, or Excel — reconstructed from raw fills via FIFO position matching.</p>
      </div>

      <div className="panel">
        <h3 style={{ marginTop: 0 }}>Client Profile</h3>
        {profiles.loading && <LoadingState label="Loading profiles…" />}
        {profiles.error && <ErrorState message={profiles.error} onRetry={profiles.refetch} />}
        <div className="field-row">
          <div className="field">
            <label htmlFor="profile-select">Profile</label>
            <select id="profile-select" value={profileId} onChange={(e) => setProfileId(e.target.value)}>
              {(profiles.data ?? []).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
          </div>
        </div>
        <form className="field-row" onSubmit={handleCreateProfile} style={{ alignItems: 'flex-end' }}>
          <div className="field">
            <label htmlFor="new-profile-name">New profile name</label>
            <input id="new-profile-name" value={newProfileName} onChange={(e) => setNewProfileName(e.target.value)} placeholder="e.g. john-doe" />
          </div>
          <button className="btn btn-secondary" type="submit" disabled={!newProfileName || creatingProfile}>
            {creatingProfile ? 'Creating…' : 'Create Profile'}
          </button>
        </form>
      </div>

      {profileId && (
        <div className="panel">
          <h3 style={{ marginTop: 0 }}>Upload File</h3>
          <p style={{ fontSize: 12.5, color: 'var(--text-dim)' }}>
            CSV (Tradovate, NinjaTrader, or generic fill/execution export) or Excel (.xlsx). One row per fill --
            round-trip trades are reconstructed automatically.
          </p>
          <input ref={fileInputRef} type="file" accept=".csv,.xlsx,.xlsm" onChange={handleFileSelected} disabled={uploading} />
          {uploading && <LoadingState label="Parsing file…" />}
          {uploadError && <ErrorState message={uploadError} />}
        </div>
      )}

      {upload && (
        <div className="panel">
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
            <h3 style={{ margin: 0 }}>
              Detected format: <Badge tone={upload.detected_format === 'generic' ? 'warn' : 'good'}>{upload.detected_format}</Badge>
            </h3>
            <button className="btn btn-secondary" type="button" onClick={handleCancelUpload}>Cancel</button>
          </div>

          <div className="grid grid-stats" style={{ marginTop: 12 }}>
            <StatTile label="Total Rows" value={upload.total_rows} />
            <StatTile label="Duplicates" value={upload.duplicate_count} tone={upload.duplicate_count > 0 ? 'bad' : 'neutral'} />
            <StatTile label="Errors" value={upload.error_count} tone={upload.error_count > 0 ? 'bad' : 'neutral'} />
            <StatTile label="Trades That Will Be Created" value={upload.matched_trade_count} tone="good" />
          </div>

          {upload.warnings.length > 0 && (
            <div className="caveats" style={{ marginTop: 12 }}>
              <h3>Warnings</h3>
              <ul>{upload.warnings.map((w) => <li key={w}>{w}</li>)}</ul>
            </div>
          )}

          <h4>Column Mapping</h4>
          <p style={{ fontSize: 12.5, color: 'var(--text-dim)' }}>
            Confirm or correct how each field maps to a column in your file — always editable, even for a recognized format.
          </p>
          <div className="field-row">
            {CANONICAL_FIELDS.map((f) => (
              <div className="field" key={f.key}>
                <label htmlFor={`map-${f.key}`}>{f.label}{f.required ? ' *' : ''}</label>
                <select
                  id={`map-${f.key}`} value={mapping[f.key] ?? ''}
                  onChange={(e) => setMapping((prev) => ({ ...prev, [f.key]: e.target.value }))}
                >
                  <option value="">— none —</option>
                  {upload.raw_headers.map((h) => <option key={h} value={h}>{h}</option>)}
                </select>
              </div>
            ))}
          </div>

          {upload.error_count > 0 && (
            <div style={{ marginTop: 12 }}>
              <h4>Row Errors</h4>
              <div className="table-scroll">
                <table>
                  <thead><tr><th>Row</th><th>Message</th></tr></thead>
                  <tbody>
                    {upload.errors.slice(0, 20).map((e) => (
                      <tr key={e.row}><td>{e.row}</td><td className="text-col">{e.message}</td></tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          <div className="grid grid-2" style={{ marginTop: 12 }}>
            <div>
              <h4>Preview: Mapped Fills</h4>
              {upload.preview_fill_rows.length === 0 ? <EmptyState label="No valid fills to preview." /> : (
                <div className="table-scroll">
                  <table>
                    <thead><tr><th>Time</th><th>Symbol</th><th>Side</th><th>Qty</th><th>Price</th></tr></thead>
                    <tbody>
                      {upload.preview_fill_rows.map((r) => (
                        <tr key={r.row}>
                          <td>{dateTime(r.timestamp)}</td><td className="text-col">{r.symbol}</td>
                          <td>{r.side}</td><td>{r.quantity}</td><td>{r.price}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
            <div>
              <h4>Preview: Resulting Trades</h4>
              {upload.preview_trades.length === 0 ? <EmptyState label="No closed trades yet from these fills (position may carry forward)." /> : (
                <div className="table-scroll">
                  <table>
                    <thead><tr><th>Symbol</th><th>Side</th><th>Qty</th><th>Entry</th><th>Exit</th></tr></thead>
                    <tbody>
                      {upload.preview_trades.map((t, i) => (
                        <tr key={i}>
                          <td className="text-col">{t.symbol}</td><td>{t.side}</td><td>{t.quantity}</td>
                          <td>{t.entry_price}</td><td>{t.exit_price}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </div>

          <button className="btn" type="button" onClick={handleConfirm} disabled={confirming || importComplete} style={{ marginTop: 12 }}>
            {confirming ? 'Starting…' : 'Confirm Import'}
          </button>
          {confirmError && <ErrorState message={confirmError} />}

          {streamedJob && (
            <div style={{ marginTop: 12 }}>
              <JobProgressBar job={streamedJob} />
              {importComplete && selectedProfile && (
                <p style={{ marginTop: 8 }}>
                  Done — <Link to={`/trades?strategy=${encodeURIComponent(`import:${selectedProfile.name}`)}`}>
                    view these trades in Trade Explorer
                  </Link>.
                </p>
              )}
            </div>
          )}
        </div>
      )}

      <div className="panel">
        <h3 style={{ marginTop: 0 }}>Import History</h3>
        {history.loading && <LoadingState label="Loading import history…" />}
        {history.error && <ErrorState message={history.error} onRetry={history.refetch} />}
        {history.data && history.data.length === 0 && <EmptyState label="No imports yet for this profile." />}
        {history.data && history.data.length > 0 && (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Filename</th><th>Format</th><th>Status</th><th>Rows</th><th>Duplicates</th>
                  <th>Errors</th><th>Trades Created</th><th>When</th>
                </tr>
              </thead>
              <tbody>
                {history.data.map((h) => (
                  <tr key={h.id}>
                    <td className="text-col">{h.filename}</td>
                    <td>{h.detected_format}</td>
                    <td><Badge tone={h.status === 'completed' ? 'good' : 'bad'}>{h.status}</Badge></td>
                    <td>{h.total_fill_rows}</td>
                    <td>{h.duplicate_fill_count}</td>
                    <td>{h.error_count}</td>
                    <td>{h.trades_created}</td>
                    <td>{dateTime(h.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
