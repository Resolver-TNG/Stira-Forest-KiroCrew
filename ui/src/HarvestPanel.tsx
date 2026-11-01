import { useEffect, useRef } from 'react'

export type HarvestSkip = {
  reason: string
  count: number
  detail?: string
}

export type HarvestRun = {
  runId: string
  phase: 'started' | 'collected' | 'distilled' | 'completed' | 'failed'
  startedAt?: string
  finishedAt?: string
  trigger?: 'manual' | 'scheduled'
  model?: string
  stiraHome?: string
  crewsDiscovered?: number
  ipdStatus?: string
  counts?: {
    sessionsListed?: number
    sessionsMatched?: number
    sessionsFetched?: number
    sessionsFailed?: number
    cronRunsMatched?: number
    subagentsMatched?: number
    candidates?: number
    distilled?: number
    accepted?: number
    rejected?: number
  }
  agentsSeen?: Array<{ agent: string; count: number }>
  layerCounts?: { core?: number; knowledge?: number; log?: number }
  writes?: {
    written?: number
    duplicateFingerprint?: number
    duplicateSemantic?: number
    failed?: number
  }
  skips?: HarvestSkip[]
  errors?: Array<{ stage?: string; type?: string; message?: string }>
  notes?: string
}

export type HarvestDispatch = {
  runId?: string
  jobId?: string | null
  status?: string
  requestedAt?: string
  finishedAt?: string
  trigger?: string
  model?: string
}

export type HarvestLogResponse = {
  crewId: string
  dispatch: HarvestDispatch | null
  runs: HarvestRun[]
}

const POLL_INTERVAL_MS = 3500
const DISPATCH_STALE_MS = 10 * 60 * 1000
const TERMINAL_DISPATCH_STATUSES = new Set(['completed', 'failed', 'returned', 'start_failed'])
const HARVEST_PHASES: HarvestRun['phase'][] = ['started', 'collected', 'distilled', 'completed']

export function isHarvestDispatchInProgress(dispatch: HarvestDispatch | null | undefined): boolean {
  if (!dispatch) return false
  const status = dispatch.status?.toLowerCase()
  if (status && TERMINAL_DISPATCH_STATUSES.has(status)) return false
  if (dispatch.requestedAt) {
    const requestedAt = Date.parse(dispatch.requestedAt)
    if (Number.isFinite(requestedAt) && Date.now() - requestedAt >= DISPATCH_STALE_MS) return false
  }
  return true
}

type Props = {
  runs: HarvestRun[]
  dispatch: HarvestLogResponse['dispatch']
  pollingRequested: boolean
  error?: string
  onRefresh: () => Promise<void>
}

function value(value: number | undefined): number {
  return typeof value === 'number' ? value : 0
}

function PhaseProgress({ phase, active }: { phase?: HarvestRun['phase']; active: boolean }) {
  const visiblePhase = phase === 'failed' ? undefined : phase
  const currentIndex = visiblePhase ? HARVEST_PHASES.indexOf(visiblePhase) : -1
  return (
    <div className="stira-harvest-progress" aria-label="Harvest progress">
      {HARVEST_PHASES.map((item, index) => {
        const state = index < currentIndex || (!active && index === currentIndex)
          ? 'done'
          : index === currentIndex
            ? 'current'
            : 'pending'
        return <span key={item} className={`stira-harvest-step stira-harvest-step--${state}`}>{item}</span>
      })}
      {phase === 'failed' && <span className="stira-harvest-step stira-harvest-step--failed">failed</span>}
    </div>
  )
}

function RunDetails({ run, current, active }: { run: HarvestRun; current: boolean; active: boolean }) {
  const counts = run.counts ?? {}
  const writes = run.writes ?? {}
  const layers = run.layerCounts ?? {}
  const phaseLabel = active || run.phase === 'completed' || run.phase === 'failed'
    ? run.phase
    : `interrupted: ${run.phase}`
  const body = (
    <div className="stira-harvest-run-body">
      <div className="stira-harvest-diagnostics">
        <span><strong>stiraHome</strong><code>{run.stiraHome ?? 'not reported'}</code></span>
        <span><strong>crewsDiscovered</strong>{run.crewsDiscovered ?? 'not reported'}</span>
      </div>
      <div className="stira-harvest-run-grid">
        <span><strong>拾った</strong>{value(counts.sessionsMatched)}/{value(counts.sessionsListed)} sessions · {value(counts.cronRunsMatched)} cron · {value(counts.subagentsMatched)} subagent</span>
        <span><strong>通した</strong>{value(counts.candidates)} → {value(counts.distilled)} → {value(counts.accepted)}</span>
        <span><strong>層</strong>{value(layers.core)} core · {value(layers.knowledge)} knowledge · {value(layers.log)} log</span>
        <span><strong>書いた</strong>{value(writes.written)} · fingerprint重複 {value(writes.duplicateFingerprint)} · semantic重複 {value(writes.duplicateSemantic)} · 失敗 {value(writes.failed)}</span>
      </div>
      {(run.skips?.length ?? 0) > 0 && (
        <div className="stira-harvest-skips"><strong>skip理由</strong>{run.skips!.map((skip, index) => <span key={`${skip.reason}-${index}`}>{skip.reason} × {skip.count}{skip.detail ? ` — ${skip.detail}` : ''}</span>)}</div>
      )}
      <div className="stira-harvest-agents"><strong>agentsSeen</strong>{run.agentsSeen?.length ? run.agentsSeen.map((item) => `${item.agent} × ${item.count}`).join(', ') : 'none reported'}</div>
      {(run.errors?.length ?? 0) > 0 && (
        <div className="stira-harvest-run-errors" role="alert">
          {run.errors!.map((item, index) => <span key={index}>{item.stage ?? 'unknown'} / {item.type ?? 'error'}: {item.message ?? 'no detail'}</span>)}
        </div>
      )}
    </div>
  )
  const heading = <><span className={`stira-harvest-phase stira-harvest-phase--${run.phase}`}>{phaseLabel}</span><code>{run.runId}</code><small>{run.finishedAt ?? run.startedAt ?? 'time not reported'}{run.model ? ` · ${run.model}` : ''}</small></>
  if (current) return <section className="stira-harvest-current"><header>{heading}</header>{body}</section>
  return <details className="stira-harvest-history"><summary>{heading}</summary>{body}</details>
}

export function HarvestPanel({ runs, dispatch, pollingRequested, error, onRefresh }: Props) {
  const refreshRef = useRef(onRefresh)
  refreshRef.current = onRefresh
  const dispatchActive = isHarvestDispatchInProgress(dispatch)
  const shouldPoll = pollingRequested || dispatchActive
  const dispatchRun = dispatch?.runId
    ? runs.find((run) => run.runId === dispatch.runId)
    : undefined
  const currentRun = dispatchRun ?? runs[0]
  const visiblePhase = currentRun?.phase
    ?? (dispatchActive ? 'started' : undefined)

  useEffect(() => {
    let mounted = true
    const refresh = () => {
      if (mounted) void refreshRef.current()
    }
    refresh()
    if (!shouldPoll) {
      return () => {
        mounted = false
      }
    }
    const intervalId = window.setInterval(refresh, POLL_INTERVAL_MS)
    return () => {
      mounted = false
      window.clearInterval(intervalId)
    }
  }, [shouldPoll])

  return (
    <section className="stira-harvest-panel" aria-label="Harvest observability">
      <style>{STYLES}</style>
      <div className="stira-harvest-panel-heading">
        <strong>直近の収穫</strong>
        <button type="button" onClick={() => void onRefresh()} disabled={dispatchActive}>
          {dispatchActive ? '収穫中…' : 'ログ更新'}
        </button>
      </div>
      <PhaseProgress phase={visiblePhase} active={dispatchActive} />
      {dispatchActive && (
        <p className="stira-harvest-live" role="status">
          収穫中… {visiblePhase ?? dispatch?.status ?? 'started'}
        </p>
      )}
      {error && <p className="stira-harvest-log-error" role="alert">Harvest log unavailable: {error}</p>}
      {!error && runs.length === 0 && <p className="stira-harvest-log-empty">実行ログなし{dispatch?.status ? ` · dispatch ${dispatch.status}` : ''}</p>}
      {runs.map((run, index) => (
        <RunDetails
          key={run.runId}
          run={run}
          current={index === 0}
          active={dispatchActive && run.runId === dispatch?.runId}
        />
      ))}
    </section>
  )
}

const STYLES = `
.stira-harvest-panel { margin-top: .8rem; padding-top: .75rem; border-top: 1px solid var(--border, #353a42); font-size: .7rem; color: var(--muted, #8f98a4); }
.stira-harvest-panel-heading { display: flex; align-items: center; justify-content: space-between; gap: .5rem; color: var(--text, #d9dde3); }
.stira-harvest-panel-heading button { border: 1px solid var(--border, #353a42); border-radius: .4rem; padding: .25rem .45rem; background: transparent; color: var(--muted, #8f98a4); cursor: pointer; }
.stira-harvest-panel-heading button:disabled { opacity: .55; cursor: wait; }
.stira-harvest-progress { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: .3rem; margin-top: .55rem; }
.stira-harvest-step { position: relative; padding-top: .45rem; color: var(--muted, #8f98a4); text-align: center; }
.stira-harvest-step::before { content: ''; position: absolute; inset: 0 0 auto; height: 2px; border-radius: 999px; background: var(--border, #353a42); }
.stira-harvest-step--done, .stira-harvest-step--current { color: var(--text, #d9dde3); }
.stira-harvest-step--done::before { background: var(--ok, #6ecf8d); }
.stira-harvest-step--current::before { background: var(--accent, #79a676); box-shadow: 0 0 .4rem color-mix(in srgb, var(--accent, #79a676) 55%, transparent); }
.stira-harvest-step--failed { color: var(--danger, #ef6b73); }
.stira-harvest-step--failed::before { background: var(--danger, #ef6b73); }
.stira-harvest-live { margin: .45rem 0 0; color: var(--accent, #79a676); }
.stira-harvest-current, .stira-harvest-history { margin-top: .55rem; border: 1px solid var(--border, #353a42); border-radius: .5rem; padding: .55rem; background: color-mix(in srgb, var(--card, #171a1f) 82%, var(--bg, #0d0f12)); }
.stira-harvest-current header, .stira-harvest-history summary { display: flex; flex-wrap: wrap; align-items: center; gap: .4rem; cursor: default; }
.stira-harvest-history summary { cursor: pointer; }
.stira-harvest-current code, .stira-harvest-history code, .stira-harvest-diagnostics code { overflow-wrap: anywhere; color: var(--text, #d9dde3); }
.stira-harvest-current small, .stira-harvest-history small { margin-left: auto; }
.stira-harvest-phase { border: 1px solid var(--border, #353a42); border-radius: 999px; padding: .1rem .35rem; }
.stira-harvest-phase--completed { color: var(--ok, #6ecf8d); border-color: color-mix(in srgb, var(--ok, #6ecf8d) 50%, transparent); }
.stira-harvest-phase--failed { color: var(--danger, #ef6b73); border-color: color-mix(in srgb, var(--danger, #ef6b73) 50%, transparent); }
.stira-harvest-run-body { display: grid; gap: .5rem; margin-top: .5rem; }
.stira-harvest-diagnostics { display: grid; gap: .25rem; padding: .4rem; border-radius: .35rem; background: color-mix(in srgb, var(--warning, #e4a853) 7%, transparent); }
.stira-harvest-diagnostics span, .stira-harvest-run-grid span { display: grid; gap: .1rem; }
.stira-harvest-run-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: .4rem; }
.stira-harvest-skips, .stira-harvest-run-errors { display: grid; gap: .15rem; }
.stira-harvest-run-errors { padding: .4rem; border: 1px solid color-mix(in srgb, var(--danger, #ef6b73) 45%, transparent); border-radius: .35rem; color: var(--danger, #ef6b73); }
.stira-harvest-log-error { color: var(--danger, #ef6b73); }
.stira-harvest-log-empty { margin: .5rem 0 0; }
@media (max-width: 560px) { .stira-harvest-run-grid { grid-template-columns: 1fr; } }
`
