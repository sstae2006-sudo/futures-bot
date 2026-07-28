import { useEffect } from 'react'
import { getInfrastructure } from '../../api'
import { useApi } from '../../useApi'

// Real process/host metrics (src/futures_bot/api/services.py::infrastructure_status)
// -- CPU/memory/disk via psutil, job queue depth from the actual `jobs`
// table. Polls every 15s, independent of StatusBar's 30s health poll --
// this panel is meant to feel "live" the way a resource monitor should.
function Bar({ label, percent, detail }: { label: string; percent: number; detail: string }) {
  const tone = percent >= 90 ? 'bad' : percent >= 75 ? 'warn' : 'good'
  return (
    <div style={{ marginBottom: 12 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13, marginBottom: 4 }}>
        <span>{label}</span>
        <span className={`tone-${tone}`}>{detail}</span>
      </div>
      <div style={{ height: 6, borderRadius: 3, background: 'var(--border, rgba(128,128,128,0.25))', overflow: 'hidden' }}>
        <div
          className={`tone-${tone}-bg`}
          style={{ height: '100%', width: `${Math.min(100, Math.max(0, percent))}%`, background: 'currentColor' }}
        />
      </div>
    </div>
  )
}

export default function InfrastructurePanel() {
  const { data, refetch } = useApi(getInfrastructure, [])

  useEffect(() => {
    const id = setInterval(refetch, 15_000)
    return () => clearInterval(id)
  }, [refetch])

  return (
    <div className="mc-panel">
      <div className="mc-panel-head">
        <h3>Infrastructure</h3>
      </div>
      {!data && <p style={{ fontSize: 13, opacity: 0.7 }}>Loading…</p>}
      {data && (
        <>
          <Bar
            label="CPU"
            percent={data.cpu_percent}
            detail={`${data.cpu_percent.toFixed(1)}%`}
          />
          <Bar
            label="Memory"
            percent={data.memory_percent}
            detail={`${(data.memory_used_mb / 1024).toFixed(1)} / ${(data.memory_total_mb / 1024).toFixed(1)} GB`}
          />
          <Bar
            label="Disk"
            percent={data.disk_percent}
            detail={`${data.disk_used_gb.toFixed(0)} / ${data.disk_total_gb.toFixed(0)} GB`}
          />
          <div style={{ marginTop: 8, fontSize: 13, opacity: 0.8 }}>
            Job queue: {data.jobs_running} running · {data.jobs_queued} queued
          </div>
        </>
      )}
    </div>
  )
}
