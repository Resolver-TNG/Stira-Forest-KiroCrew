import { useAppApi } from '@kirocrew/app-sdk'
import { Card, CardTitle } from '@kirocrew/app-sdk/ui'
import { useCallback, useEffect, useState } from 'react'

const API_ROOT = '/api/apps/stira'
const MIN_INTERVAL_SECONDS = 60
const MAX_INTERVAL_SECONDS = 2_678_400
const SUMMARY_LIMIT = 72

const INTERVAL_OPTIONS = [
  { value: 300, label: '5分' },
  { value: 900, label: '15分' },
  { value: 3600, label: '1時間' },
  { value: 21600, label: '6時間' },
  { value: 86400, label: '1日' },
  { value: 604800, label: '1週間' },
]

export type CronRunStatus = 'ok' | 'skip' | 'error'

export type CronLastRun = {
  status: CronRunStatus
  at: string
  summary: string
}

export type ScheduledCron = {
  name: string
  registeredName: string
  label: string
  available: boolean
  enabled: boolean
  intervalSeconds: number
  nextRunAt: string | null
  requiresCore: boolean
  guarded: boolean
  lastRun: CronLastRun | null
}

export type ScheduleResponse = {
  coreDefined: boolean
  crons: ScheduledCron[]
}

export type ScheduleUpdateResponse = {
  coreDefined: boolean
  cron: ScheduledCron
}

type ScheduleChange = {
  enabled?: boolean
  intervalSeconds?: number
}

type ScheduleApi = {
  put: (path: string, body: ScheduleChange) => Promise<unknown>
}

export type ScheduleErrorPresentation = {
  scope: 'row' | 'panel'
  message: string
}

type ScheduleViewProps = {
  schedule: ScheduleResponse | null
  busy: Record<string, boolean>
  panelError: string | null
  rowErrors: Record<string, string | undefined>
  onReload: () => void
  onUpdate: (cronName: string, change: ScheduleChange) => void
}

function timestampLabel(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '時刻不明'
  return new Intl.DateTimeFormat('ja-JP', {
    dateStyle: 'short',
    timeStyle: 'medium',
  }).format(date)
}

function nextRunLabel(value: string | null): string {
  return value ? `次回 ${timestampLabel(value)}` : '次回実行なし'
}

function summaryLabel(value: string): string {
  if (value.length <= SUMMARY_LIMIT) return value
  return `${value.slice(0, SUMMARY_LIMIT - 1)}…`
}

function statusLabel(status: CronRunStatus): string {
  return status
}

function errorText(reason: unknown): string {
  const parts: string[] = []
  if (reason instanceof Error) parts.push(reason.message)
  if (reason && typeof reason === 'object') {
    const candidate = reason as Record<string, unknown>
    for (const key of ['status', 'error', 'message']) {
      if (candidate[key] !== undefined) parts.push(String(candidate[key]))
    }
  }
  try {
    parts.push(JSON.stringify(reason))
  } catch {
    parts.push(String(reason))
  }
  return parts.join(' ')
}

export function classifyScheduleError(reason: unknown): ScheduleErrorPresentation {
  const text = errorText(reason)
  if (/core_required|\b409\b/i.test(text)) {
    return { scope: 'row', message: '芯が未定義のため、このスケジュールは有効化できない。' }
  }
  if (/invalid_request|intervalSeconds|\b400\b/i.test(text)) {
    return {
      scope: 'row',
      message: `間隔は${MIN_INTERVAL_SECONDS}〜${MAX_INTERVAL_SECONDS}秒で指定してほしい。`,
    }
  }
  if (/schedule_unavailable|schedule_update_failed|\b503\b/i.test(text)) {
    return { scope: 'panel', message: 'スケジュールサービスを利用できない。時間を置いて再試行してほしい。' }
  }
  return { scope: 'panel', message: reason instanceof Error ? reason.message : 'スケジュールを更新できなかった。' }
}

export function replaceScheduledCron(
  schedule: ScheduleResponse,
  response: ScheduleUpdateResponse,
): ScheduleResponse {
  return {
    coreDefined: response.coreDefined,
    crons: schedule.crons.map((cron) => cron.name === response.cron.name ? response.cron : cron),
  }
}

export async function requestScheduleUpdate(
  api: ScheduleApi,
  cronName: string,
  change: ScheduleChange,
): Promise<ScheduleUpdateResponse> {
  if (change.intervalSeconds !== undefined && (
    !Number.isInteger(change.intervalSeconds)
    || change.intervalSeconds < MIN_INTERVAL_SECONDS
    || change.intervalSeconds > MAX_INTERVAL_SECONDS
  )) {
    throw new Error('400 invalid_request intervalSeconds')
  }
  return await api.put(
    `${API_ROOT}/schedule/${encodeURIComponent(cronName)}`,
    change,
  ) as ScheduleUpdateResponse
}

function LastRun({ lastRun }: { lastRun: CronLastRun | null }) {
  if (!lastRun) return <span className="stira-schedule-never">未実行</span>
  const localTime = timestampLabel(lastRun.at)
  return (
    <div className="stira-schedule-last-run">
      <span className={`stira-schedule-run-badge stira-schedule-run-badge--${lastRun.status}`}>
        {statusLabel(lastRun.status)}
      </span>
      <time dateTime={lastRun.at} title={localTime}>{localTime}</time>
      <span className="stira-schedule-summary" title={lastRun.summary}>
        {summaryLabel(lastRun.summary)}
      </span>
    </div>
  )
}

export function ScheduleView({
  schedule,
  busy,
  panelError,
  rowErrors,
  onReload,
  onUpdate,
}: ScheduleViewProps) {
  return (
    <section className="stira-schedule" aria-labelledby="stira-schedule-title">
      <style>{SCHEDULE_STYLES}</style>
      <Card>
        <div className="stira-schedule-heading">
          <div>
            <CardTitle><span id="stira-schedule-title">スケジュール</span></CardTitle>
            <p>Stiraの自動実行、間隔、直近の実行結果を管理する。</p>
          </div>
          <button type="button" onClick={onReload}>再読込</button>
        </div>

        {panelError && <p className="stira-schedule-error" role="alert">{panelError}</p>}
        {!schedule && !panelError && <p className="stira-schedule-muted">スケジュールを読込中…</p>}
        {schedule && !schedule.coreDefined && (
          <p className="stira-schedule-guard">
            芯が未定義のため、「芯必須」のスケジュールは有効化できない。
          </p>
        )}

        <div className="stira-schedule-list">
          {schedule?.crons.map((cron) => {
            const isBusy = busy[cron.name] === true
            const optionExists = INTERVAL_OPTIONS.some((option) => option.value === cron.intervalSeconds)
            const rowErrorId = `stira-schedule-error-${cron.name}`
            const guardReason = cron.guarded ? '芯未定義のため有効化できない' : undefined
            return (
              <div className="stira-schedule-row" key={cron.name} data-cron-name={cron.name}>
                <div className="stira-schedule-main">
                  <div className="stira-schedule-identity">
                    <div className="stira-schedule-name">
                      <strong>{cron.label}</strong>
                      {cron.requiresCore && (
                        <span className={`stira-schedule-core-badge${cron.guarded ? ' stira-schedule-core-badge--guarded' : ''}`}>
                          {cron.guarded ? '芯未定義' : '芯必須'}
                        </span>
                      )}
                    </div>
                    <span>{cron.available ? nextRunLabel(cron.nextRunAt) : '未登録'}</span>
                  </div>
                  <LastRun lastRun={cron.lastRun} />
                </div>

                <div className="stira-schedule-controls">
                  <label className="stira-schedule-toggle" title={guardReason}>
                    <input
                      type="checkbox"
                      aria-label={`${cron.label}を${cron.enabled ? '無効化' : '有効化'}`}
                      aria-describedby={rowErrors[cron.name] ? rowErrorId : undefined}
                      checked={cron.enabled}
                      disabled={isBusy || !cron.available || cron.guarded}
                      title={guardReason}
                      onChange={(event) => onUpdate(cron.name, { enabled: event.target.checked })}
                    />
                    <span>{cron.enabled ? 'ON' : 'OFF'}</span>
                  </label>
                  <label className="stira-schedule-interval">
                    <span>間隔</span>
                    <select
                      aria-label={`${cron.label}の実行間隔`}
                      aria-describedby={rowErrors[cron.name] ? rowErrorId : undefined}
                      value={cron.intervalSeconds}
                      disabled={isBusy || !cron.available}
                      onChange={(event) => onUpdate(cron.name, { intervalSeconds: Number(event.target.value) })}
                    >
                      {!optionExists && <option value={cron.intervalSeconds}>{cron.intervalSeconds}秒</option>}
                      {INTERVAL_OPTIONS.map((option) => (
                        <option key={option.value} value={option.value}>{option.label}</option>
                      ))}
                    </select>
                  </label>
                </div>

                {rowErrors[cron.name] && (
                  <p className="stira-schedule-row-error" id={rowErrorId} role="alert">
                    {rowErrors[cron.name]}
                  </p>
                )}
              </div>
            )
          })}
        </div>
      </Card>
    </section>
  )
}

export function SchedulePanel() {
  const api = useAppApi()
  const [schedule, setSchedule] = useState<ScheduleResponse | null>(null)
  const [busy, setBusy] = useState<Record<string, boolean>>({})
  const [panelError, setPanelError] = useState<string | null>(null)
  const [rowErrors, setRowErrors] = useState<Record<string, string | undefined>>({})

  const loadSchedule = useCallback(async () => {
    try {
      const response = (await api.get(`${API_ROOT}/schedule`)) as ScheduleResponse
      setSchedule(response)
      setPanelError(null)
    } catch (reason) {
      const detail = classifyScheduleError(reason)
      setPanelError(detail.scope === 'panel' ? detail.message : 'スケジュールを取得できなかった。')
    }
  }, [api])

  useEffect(() => {
    void loadSchedule()
  }, [loadSchedule])

  const updateCron = useCallback(async (cronName: string, change: ScheduleChange) => {
    setBusy((current) => ({ ...current, [cronName]: true }))
    setPanelError(null)
    setRowErrors((current) => ({ ...current, [cronName]: undefined }))
    try {
      const response = await requestScheduleUpdate(api as ScheduleApi, cronName, change)
      setSchedule((current) => current ? replaceScheduledCron(current, response) : current)
    } catch (reason) {
      const detail = classifyScheduleError(reason)
      if (detail.scope === 'row') {
        setRowErrors((current) => ({ ...current, [cronName]: detail.message }))
      } else {
        setPanelError(detail.message)
      }
    } finally {
      setBusy((current) => ({ ...current, [cronName]: false }))
    }
  }, [api])

  return (
    <ScheduleView
      schedule={schedule}
      busy={busy}
      panelError={panelError}
      rowErrors={rowErrors}
      onReload={() => void loadSchedule()}
      onUpdate={(cronName, change) => void updateCron(cronName, change)}
    />
  )
}

const SCHEDULE_STYLES = `
.stira-schedule { margin: 0 0 1rem; }
.stira-schedule-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 1rem; }
.stira-schedule-heading p { margin: .2rem 0 0; color: var(--muted, #8f98a4); font-size: .76rem; }
.stira-schedule-heading button { border: 1px solid var(--border, #353a42); border-radius: .5rem; padding: .42rem .68rem; background: var(--bg-elevated, #22262d); color: var(--text, #d9dde3); cursor: pointer; }
.stira-schedule-list { display: grid; gap: .55rem; margin-top: .85rem; }
.stira-schedule-row { display: grid; gap: .55rem; padding: .7rem .75rem; border: 1px solid var(--border, #353a42); border-radius: .6rem; background: color-mix(in srgb, var(--bg-elevated, #22262d) 55%, transparent); }
.stira-schedule-main { display: grid; grid-template-columns: minmax(10rem, 1fr) minmax(12rem, 1.4fr); align-items: center; gap: .85rem; }
.stira-schedule-identity { display: flex; flex-direction: column; gap: .18rem; }
.stira-schedule-name { display: flex; flex-wrap: wrap; align-items: center; gap: .4rem; }
.stira-schedule-identity strong { color: var(--text-strong, #fff); font-size: .82rem; }
.stira-schedule-identity > span, .stira-schedule-interval > span { color: var(--muted, #8f98a4); font-size: .68rem; }
.stira-schedule-core-badge { border: 1px solid color-mix(in srgb, var(--accent, #79a676) 42%, transparent); border-radius: 999px; padding: .08rem .32rem; color: var(--accent, #79a676); font-size: .61rem; font-weight: 700; }
.stira-schedule-core-badge--guarded { border-color: color-mix(in srgb, var(--warning, #e4a853) 48%, transparent); color: var(--warning, #e4a853); }
.stira-schedule-last-run { display: grid; grid-template-columns: auto auto minmax(0, 1fr); align-items: center; gap: .42rem; min-width: 0; color: var(--muted, #8f98a4); font-size: .68rem; }
.stira-schedule-run-badge { border: 1px solid currentColor; border-radius: 999px; padding: .08rem .34rem; font-size: .61rem; font-weight: 800; text-transform: uppercase; }
.stira-schedule-run-badge--ok { color: var(--ok, #6ecf8d); }
.stira-schedule-run-badge--skip { color: var(--muted, #8f98a4); }
.stira-schedule-run-badge--error { color: var(--danger, #ef6b73); }
.stira-schedule-summary { overflow: hidden; color: var(--text, #d9dde3); text-overflow: ellipsis; white-space: nowrap; }
.stira-schedule-never { color: var(--muted, #8f98a4); font-size: .68rem; }
.stira-schedule-controls { display: flex; align-items: center; justify-content: flex-end; gap: 1rem; padding-top: .5rem; border-top: 1px solid color-mix(in srgb, var(--border, #353a42) 72%, transparent); }
.stira-schedule-toggle, .stira-schedule-interval { display: flex; align-items: center; gap: .42rem; }
.stira-schedule-toggle { min-width: 3.8rem; color: var(--muted, #8f98a4); font-size: .7rem; font-weight: 700; }
.stira-schedule-toggle input { width: 1.05rem; height: 1.05rem; accent-color: var(--accent, #79a676); }
.stira-schedule-interval select { border: 1px solid var(--border, #353a42); border-radius: .45rem; padding: .35rem .48rem; background: var(--bg, #0d0f12); color: var(--text, #d9dde3); }
.stira-schedule-toggle input:disabled, .stira-schedule-interval select:disabled { cursor: not-allowed; opacity: .55; }
.stira-schedule-error, .stira-schedule-guard { margin: .7rem 0 0; padding: .55rem .7rem; border-radius: .5rem; font-size: .74rem; }
.stira-schedule-error, .stira-schedule-row-error { color: var(--danger, #ef6b73); background: color-mix(in srgb, var(--danger, #ef6b73) 9%, transparent); }
.stira-schedule-guard { color: var(--warning, #e4a853); background: color-mix(in srgb, var(--warning, #e4a853) 9%, transparent); }
.stira-schedule-row-error { margin: 0; padding: .4rem .5rem; border-radius: .4rem; font-size: .68rem; }
.stira-schedule-muted { color: var(--muted, #8f98a4); font-size: .75rem; }
@media (max-width: 760px) {
  .stira-schedule-main { grid-template-columns: 1fr; }
  .stira-schedule-controls { justify-content: space-between; }
}
@media (max-width: 480px) {
  .stira-schedule-heading { align-items: center; }
  .stira-schedule-last-run { grid-template-columns: auto 1fr; }
  .stira-schedule-summary { grid-column: 1 / -1; }
}
`
