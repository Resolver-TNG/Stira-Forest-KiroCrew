import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'

import {
  ScheduleView,
  classifyScheduleError,
  replaceScheduledCron,
  requestScheduleUpdate,
  type ScheduledCron,
  type ScheduleResponse,
} from '../../SchedulePanel'

function cron(overrides: Partial<ScheduledCron> = {}): ScheduledCron {
  return {
    name: 'core-hash-monitor',
    registeredName: 'stira/core-hash-monitor',
    label: '芯ハッシュ監視',
    available: true,
    enabled: false,
    intervalSeconds: 300,
    nextRunAt: null,
    requiresCore: false,
    guarded: false,
    lastRun: null,
    ...overrides,
  }
}

function renderSchedule(
  schedule: ScheduleResponse,
  rowErrors: Record<string, string | undefined> = {},
  panelError: string | null = null,
): string {
  return renderToStaticMarkup(
    <ScheduleView
      schedule={schedule}
      busy={{}}
      panelError={panelError}
      rowErrors={rowErrors}
      onReload={() => undefined}
      onUpdate={() => undefined}
    />,
  )
}

describe('SchedulePanel presentation', () => {
  it('renders ok, skip, error, and never-run states with timestamps and summaries', () => {
    const longSummary = '長い実行結果の概要'.repeat(12)
    const html = renderSchedule({
      coreDefined: true,
      crons: [
        cron({
          name: 'core-hash-monitor',
          lastRun: { status: 'ok', at: '2026-09-18T05:00:00Z', summary: 'healthy' },
          nextRunAt: '2026-09-18T05:05:00Z',
        }),
        cron({
          name: 'purpose-drift-check',
          label: '目的drift',
          lastRun: { status: 'skip', at: '2026-09-18T05:01:00Z', summary: 'no activity' },
        }),
        cron({
          name: 'knowledge-integrity',
          label: 'ナレッジ整合性',
          lastRun: { status: 'error', at: '2026-09-18T05:02:00Z', summary: longSummary },
        }),
        cron({ name: 'knowledge-harvest', label: '収穫' }),
      ],
    })

    expect(html).toContain('stira-schedule-run-badge--ok')
    expect(html).toContain('stira-schedule-run-badge--skip')
    expect(html).toContain('stira-schedule-run-badge--error')
    expect(html).toContain('未実行')
    expect(html).toContain('dateTime="2026-09-18T05:00:00Z"')
    expect(html).toContain('次回 ')
    expect(html).toContain(`title="${longSummary}"`)
    expect(html).not.toContain(`>${longSummary}</span>`)
  })

  it('disables a guarded core-dependent toggle and explains why', () => {
    const guarded = cron({
      name: 'purpose-drift-check',
      label: '目的drift',
      requiresCore: true,
      guarded: true,
    })
    const html = renderSchedule({ coreDefined: false, crons: [guarded] })

    expect(html).toContain('芯未定義')
    expect(html).toMatch(/aria-label="目的driftを有効化"[^>]*disabled=""[^>]*title="芯未定義のため有効化できない"/)
  })
})

describe('SchedulePanel updates', () => {
  it('uses PUT and replaces only the row returned by the server', async () => {
    const original = cron({ enabled: false, intervalSeconds: 300 })
    const unchanged = cron({ name: 'knowledge-integrity', label: 'ナレッジ整合性' })
    const confirmed = { ...original, enabled: true, intervalSeconds: 900 }
    const api = {
      put: vi.fn().mockResolvedValue({ coreDefined: true, cron: confirmed }),
    }

    const response = await requestScheduleUpdate(api, original.name, {
      enabled: true,
      intervalSeconds: 900,
    })
    const next = replaceScheduledCron({ coreDefined: true, crons: [original, unchanged] }, response)

    expect(api.put).toHaveBeenCalledWith(
      '/api/apps/stira/schedule/core-hash-monitor',
      { enabled: true, intervalSeconds: 900 },
    )
    expect(next.crons).toEqual([confirmed, unchanged])
  })

  it('maps 409 and 400 to inline errors and 503 to a panel error', () => {
    const guarded = classifyScheduleError(new Error('409 core_required'))
    const invalid = classifyScheduleError({ status: 400, error: 'invalid_request' })
    const unavailable = classifyScheduleError(new Error('503 schedule_unavailable'))
    const base = { coreDefined: true, crons: [cron()] }

    expect(guarded.scope).toBe('row')
    expect(invalid.scope).toBe('row')
    expect(unavailable.scope).toBe('panel')
    expect(renderSchedule(base, { 'core-hash-monitor': guarded.message })).toContain(guarded.message)
    expect(renderSchedule(base, { 'core-hash-monitor': invalid.message })).toContain(invalid.message)
    expect(renderSchedule(base, {}, unavailable.message)).toContain(unavailable.message)
  })

  it('rejects an out-of-range interval before sending PUT', async () => {
    const api = { put: vi.fn() }

    await expect(requestScheduleUpdate(api, 'core-hash-monitor', { intervalSeconds: 30 }))
      .rejects.toThrow('400 invalid_request intervalSeconds')
    expect(api.put).not.toHaveBeenCalled()
  })
})
