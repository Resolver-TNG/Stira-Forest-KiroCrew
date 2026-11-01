import { useAppApi } from '@kirocrew/app-sdk'
import { Card, CardTitle, PageHeader, StatCard } from '@kirocrew/app-sdk/ui'
import { type FormEvent, useCallback, useEffect, useMemo, useState } from 'react'

import {
  HarvestPanel,
  isHarvestDispatchInProgress,
  type HarvestLogResponse,
} from './HarvestPanel'
import { SchedulePanel } from './SchedulePanel'
import { LeafDetail } from './LeafDetail'
import { LeafGlyph } from './components/LeafGlyph'
import { buildScene } from './layout/engine'
import type { LayoutInput, LayoutLeaf } from './layout/types'

type DriftValue =
  | boolean
  | null
  | undefined
  | {
      drifted?: boolean
      score?: number
      magnitude?: number
      distance?: number
      violations?: unknown[]
      evaluated?: boolean
      requiresAdapterJudgment?: boolean
    }

type PendingCoreProposal = {
  version: number
  crewId: string
  baseHash: string
  candidateHash: string
  proposedAt: string
}

type Crew = {
  name: string
  stability: number | null
  growth: number | null
  fallenLeaves?: Leaf[]
  activeCount?: number
  archivedCount?: number
  coreHashHealthy: boolean | null
  onboardingRequired: boolean
  coreProposalPending: boolean
  pendingCoreProposal: PendingCoreProposal | null
  drift: {
    detected: boolean
    core: 'healthy' | 'changed' | 'undefined' | 'unknown'
    purpose: DriftValue
    means: DriftValue
  }
}

type Leaf = {
  id: string
  title: string
  summary: string
  score: number
  layer: 'core' | 'knowledge' | 'log'
  domain: string
  tags: string[]
  effectiveScore: number
  contentLength?: number
  life?: number
  archived?: boolean
  archivedAt?: string | null
  archivedReason?: 'faded' | 'pruned' | null
  content: string
  ui: {
    leafColorIntensity: number
    leafOpacity: number
    fadedFraction: number
  }
}

type ForestResponse = {
  crews: Crew[]
  onboarding: {
    required?: boolean
    question?: string
    crews?: string[]
    reason?: string | null
  } | null
  version?: string
  buildHash?: string
}

type AgentsResponse = {
  agents: string[]
}

type KnowledgeResponse = {
  crewId: string
  entries: Leaf[]
  totalCount: number
}

type SelectedLeaf = { crew: string; leaf: Leaf }

type CreateCrewResponse = {
  ok: boolean
  crewId: string
  created: boolean
  rebaselined: boolean
  coreHash: string
}

const API_ROOT = '/api/apps/stira'
const CUSTOM_CREW = '__custom__'

type HarvestDispatchResponse = {
  crewId: string
  status: string
  skip?: string
  runId?: string
  spawnId?: string
  coreHashHealthy?: boolean
  basisCoreHash?: string | null
  coreHashCurrent?: string | null
}

type HarvestUiState = {
  status: 'running' | 'success' | 'error' | 'info' | 'security'
  message: string
}

function isHarvestInProgressError(reason: unknown): boolean {
  if (reason instanceof Error && /(?:409|harvest_in_progress)/i.test(reason.message)) {
    return true
  }
  try {
    return /(?:"status"\s*:\s*409|harvest_in_progress)/i.test(JSON.stringify(reason))
  } catch {
    return false
  }
}

function clamp01(value: number | null | undefined, fallback = 0): number {
  if (typeof value !== 'number' || !Number.isFinite(value)) return fallback
  return Math.max(0, Math.min(1, value))
}

function driftStrength(value: DriftValue): number {
  if (value === true) return 1
  if (!value || value === false || typeof value !== 'object') return 0
  if (value.drifted === false) return 0
  for (const candidate of [value.score, value.magnitude, value.distance]) {
    if (typeof candidate === 'number' && Number.isFinite(candidate)) {
      return clamp01(candidate)
    }
  }
  if (Array.isArray(value.violations) && value.violations.length > 0) {
    return Math.min(1, 0.35 + value.violations.length * 0.15)
  }
  return value.drifted ? 0.7 : 0
}

function percent(value: number | null): string {
  return value === null ? '—' : `${Math.round(clamp01(value) * 100)}%`
}

function shortHash(value: string): string {
  return value.length <= 20 ? value : `${value.slice(0, 10)}…${value.slice(-8)}`
}

function proposalTime(value: string): string {
  const timestamp = Date.parse(value)
  if (!Number.isFinite(timestamp)) return value || '時刻不明'
  return new Intl.DateTimeFormat('ja-JP', {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(new Date(timestamp))
}

function statusLabel(crew: Crew): string {
  if (crew.coreProposalPending) return '承認待ち'
  if (crew.onboardingRequired) return '未設定'
  if (crew.coreHashHealthy === false) return '要確認'
  if (crew.drift.detected) return 'Drift detected'
  if (crew.coreHashHealthy === true) return 'Aligned'
  return 'Unknown'
}

function statusTone(crew: Crew): string {
  if (crew.coreProposalPending) return 'proposal'
  if (crew.onboardingRequired) return 'undefined'
  if (crew.coreHashHealthy === false) return 'changed'
  return crew.drift.core
}

function statusDetail(crew: Crew): string {
  if (crew.coreProposalPending) return '芯の変更が人間の承認を待っている'
  if (crew.onboardingRequired) return '芯がまだ設定されていない'
  if (crew.coreHashHealthy === false) return '承認済みの芯と現在のIPDが一致していない'
  if (crew.drift.detected) return '目的または手段のドリフトを検出'
  if (crew.coreHashHealthy === true) return '承認済みの芯と一致'
  return '芯の状態を確認できない'
}

function toLayoutLeaf(leaf: Leaf): LayoutLeaf {
  return {
    id: leaf.id,
    domain: leaf.domain,
    layer: leaf.layer,
    effectiveScore: leaf.effectiveScore,
    life: leaf.life ?? leaf.effectiveScore,
    contentLength: leaf.contentLength,
    tags: leaf.tags,
    archived: leaf.archived,
    archivedReason: leaf.archivedReason,
  }
}

function TreeSvg({
  crew,
  leaves,
  fallenLeaves,
  onSelectLeaf,
}: {
  crew: Crew
  leaves: Leaf[]
  fallenLeaves: Leaf[]
  onSelectLeaf: (leaf: Leaf) => void
}) {
  const purposeDrift = driftStrength(crew.drift.purpose)
  const meansDrift = driftStrength(crew.drift.means)
  const input = useMemo<LayoutInput>(() => ({
    crew: {
      name: crew.name,
      stability: crew.stability,
      growth: crew.growth,
      purposeDrift,
      meansDrift,
    },
    leaves: leaves.map(toLayoutLeaf),
    fallenLeaves: fallenLeaves.map(toLayoutLeaf),
  }), [crew.growth, crew.name, crew.stability, fallenLeaves, leaves, meansDrift, purposeDrift])
  const scene = useMemo(() => buildScene(input), [input])
  const activeLeavesById = useMemo(
    () => new Map(leaves.map((leaf) => [leaf.id, leaf])),
    [leaves],
  )
  const fallenLeavesById = useMemo(
    () => new Map(fallenLeaves.map((leaf) => [leaf.id, leaf])),
    [fallenLeaves],
  )
  const gradientId = `trunk-${crew.name}`
  const glowId = `glow-${crew.name}`

  return (
    <svg
      className="stira-tree"
      viewBox={`0 0 ${scene.viewWidth} ${scene.viewHeight}`}
      role="img"
      aria-label={`${crew.name} knowledge tree with ${scene.branches.length} domain branches`}
    >
      <defs>
        <linearGradient id={gradientId} x1="0" x2="1">
          <stop offset="0" stopColor="var(--stira-trunk-shadow)" />
          <stop offset="0.5" stopColor="var(--stira-trunk)" />
          <stop offset="1" stopColor="var(--stira-trunk-highlight)" />
        </linearGradient>
        <filter id={glowId} x="-80%" y="-80%" width="260%" height="260%">
          <feGaussianBlur stdDeviation="3" result="blur" />
          <feMerge>
            <feMergeNode in="blur" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
      </defs>

      <ellipse
        cx={scene.trunk.rootX}
        cy={scene.trunk.rootY + 7}
        rx="112"
        ry="13"
        fill="var(--stira-ground)"
      />
      {scene.roots.map((root, index) => (
        <path
          key={`root-${index}`}
          d={root.path}
          fill="none"
          stroke="var(--stira-root)"
          strokeWidth={root.width}
          strokeLinecap="round"
        />
      ))}

      {scene.fallen.map((sceneLeaf) => {
        const leaf = fallenLeavesById.get(sceneLeaf.id)
        if (!leaf) return null
        const restorable = leaf.archivedReason === 'pruned'
        return (
          <g
            key={`fallen-${sceneLeaf.id}`}
            className={`stira-fallen-leaf${restorable ? ' stira-fallen-leaf--restorable' : ''}`}
            transform={`translate(${sceneLeaf.x} ${sceneLeaf.y}) rotate(${sceneLeaf.rotation})`}
            opacity={sceneLeaf.opacity}
            role={restorable ? 'button' : undefined}
            tabIndex={restorable ? 0 : undefined}
            aria-label={restorable ? `${leaf.title}を木に戻す` : undefined}
            aria-hidden={restorable ? undefined : true}
            onClick={restorable ? () => onSelectLeaf(leaf) : undefined}
            onKeyDown={restorable ? (event) => {
              if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault()
                onSelectLeaf(leaf)
              }
            } : undefined}
          >
            <circle r={sceneLeaf.hitRadius} fill="transparent" />
            <LeafGlyph shape={sceneLeaf.shape} color={sceneLeaf.color} scale={sceneLeaf.scale} />
          </g>
        )
      })}

      {scene.branches.map((branch) => (
        <path
          key={branch.key}
          className="stira-domain-branch"
          d={branch.path}
          fill="none"
          stroke={meansDrift > 0.2 ? 'var(--stira-branch-drift)' : 'var(--stira-branch)'}
          strokeWidth={branch.width}
          strokeLinecap="round"
        />
      ))}

      <path d={scene.trunk.path} fill={`url(#${gradientId})`} />

      {scene.layerLabels.map((label) => (
        <text key={label.layer} x={label.x} y={label.y} className="stira-svg-label">
          {label.layer.toUpperCase()}
        </text>
      ))}
      {scene.branches.filter((branch) => branch.label.visible).map((branch) => (
        <text
          key={`${branch.key}-label`}
          x={branch.label.x}
          y={branch.label.y}
          textAnchor={branch.label.anchor}
          className="stira-domain-label"
        >
          {branch.label.text}
        </text>
      ))}

      {scene.leaves.map((sceneLeaf) => {
        const leaf = activeLeavesById.get(sceneLeaf.id)
        if (!leaf) return null
        const life = leaf.life ?? leaf.effectiveScore
        return (
          <g
            key={sceneLeaf.id}
            className={`stira-leaf stira-leaf--${leaf.layer}${sceneLeaf.withering ? ' stira-leaf--withering' : ''}`}
            role="button"
            tabIndex={0}
            aria-label={`${leaf.title}, ${leaf.domain}, score ${Math.round(leaf.effectiveScore * 100)}%`}
            onClick={() => onSelectLeaf(leaf)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault()
                onSelectLeaf(leaf)
              }
            }}
          >
            {sceneLeaf.twigPath && (
              <path
                d={sceneLeaf.twigPath}
                fill="none"
                stroke="var(--stira-twig)"
                strokeWidth="1"
                strokeLinecap="round"
              />
            )}
            <g
              transform={`translate(${sceneLeaf.x} ${sceneLeaf.y}) rotate(${sceneLeaf.rotation})`}
              opacity={sceneLeaf.opacity}
              filter={life > 0.78 ? `url(#${glowId})` : undefined}
            >
              <circle r={sceneLeaf.hitRadius} fill="transparent" />
              <LeafGlyph shape={sceneLeaf.shape} color={sceneLeaf.color} scale={sceneLeaf.scale} />
            </g>
            <title>{`${leaf.title} · ${leaf.domain} · ${leaf.layer} · ${Math.round(leaf.effectiveScore * 100)}%`}</title>
          </g>
        )
      })}

      {purposeDrift > 0 && (
        <path d="M 42 34 L 62 34" stroke="var(--stira-danger)" strokeWidth="3" strokeLinecap="round" />
      )}
      {meansDrift > 0 && (
        <path d="M 42 46 L 62 46" stroke="var(--stira-warning)" strokeWidth="3" strokeLinecap="round" />
      )}
      <text x="68" y="39" className="stira-svg-label">
        {purposeDrift > 0 ? 'purpose drift' : meansDrift > 0 ? 'means drift' : ''}
      </text>
    </svg>
  )
}

// ── A-1 pending core proposal approval UI ────────────────────────────────
function CoreProposalCard({
  proposal,
  onReview,
}: {
  proposal: PendingCoreProposal | null
  onReview: () => void
}) {
  return (
    <section className="stira-core-proposal" aria-label="承認待ちの芯の変更">
      <div className="stira-core-proposal-heading">
        <div>
          <strong>これを植える？</strong>
          <p>芯の変更が提案されている。内容を確認し、人間が承認するまで収穫の基準には使われない。</p>
        </div>
        <span>承認待ち</span>
      </div>
      {proposal ? (
        <div className="stira-core-proposal-meta">
          <div className="stira-core-proposal-hashes" aria-label="芯のhash変更">
            <code title={proposal.baseHash}>{shortHash(proposal.baseHash)}</code>
            <span aria-hidden="true">→</span>
            <code title={proposal.candidateHash}>{shortHash(proposal.candidateHash)}</code>
          </div>
          <time dateTime={proposal.proposedAt}>{proposalTime(proposal.proposedAt)}</time>
        </div>
      ) : (
        <p className="stira-core-proposal-missing">提案の詳細を取得できない。更新して再確認してほしい。</p>
      )}
      <button type="button" onClick={onReview} disabled={!proposal}>
        内容を確認して植える
      </button>
    </section>
  )
}
// ── End A-1 pending core proposal approval UI ────────────────────────────

function CrewTreeCard({
  crew,
  leaves,
  fallenLeaves,
  leafError,
  harvestState,
  harvestLog,
  harvestLogError,
  onHarvest,
  onRefreshHarvestLog,
  onReviewCoreProposal,
  onSelectLeaf,
}: {
  crew: Crew
  leaves: Leaf[]
  fallenLeaves: Leaf[]
  leafError?: string
  harvestState?: HarvestUiState
  harvestLog?: HarvestLogResponse
  harvestLogError?: string
  onHarvest: () => void
  onRefreshHarvestLog: () => Promise<void>
  onReviewCoreProposal: () => void
  onSelectLeaf: (leaf: Leaf) => void
}) {
  const harvesting = isHarvestDispatchInProgress(harvestLog?.dispatch)
  const pollingRequested = harvestState?.status === 'running'

  return (
    <Card>
      <div className="stira-card-header">
        <div>
          <CardTitle>{crew.name}</CardTitle>
          <p className="stira-card-subtitle">{statusDetail(crew)}</p>
        </div>
        <span className={`stira-status stira-status--${statusTone(crew)}`}>
          {statusLabel(crew)}
        </span>
      </div>
      {/* ── A-1 pending core proposal approval UI ─────────────────────── */}
      {crew.coreProposalPending && (
        <CoreProposalCard
          proposal={crew.pendingCoreProposal}
          onReview={onReviewCoreProposal}
        />
      )}
      {/* ── End A-1 pending core proposal approval UI ─────────────────── */}
      <TreeSvg crew={crew} leaves={leaves} fallenLeaves={fallenLeaves} onSelectLeaf={onSelectLeaf} />
      <div className="stira-metrics" aria-label={`${crew.name} metrics`}>
        <span><strong>{percent(crew.stability)}</strong> stability</span>
        <span><strong>{percent(crew.growth)}</strong> growth</span>
        <span><strong>{leaves.length}</strong> leaves</span>
      </div>
      <div className="stira-harvest">
        <button
          className="stira-harvest-button"
          type="button"
          onClick={onHarvest}
          disabled={harvesting || crew.onboardingRequired}
        >
          {harvesting ? '収穫中…' : '今すぐ収穫'}
        </button>
        {harvestState && (
          <p
            className={`stira-harvest-status stira-harvest-status--${harvestState.status}`}
            role={harvestState.status === 'error' ? 'alert' : 'status'}
          >
            {harvestState.message}
          </p>
        )}
      </div>
      <HarvestPanel
        runs={harvestLog?.runs ?? []}
        dispatch={harvestLog?.dispatch ?? null}
        pollingRequested={pollingRequested}
        error={harvestLogError}
        onRefresh={onRefreshHarvestLog}
      />
      {leafError && <p className="stira-inline-warning">Leaves unavailable: {leafError}</p>}
      {!leafError && leaves.length === 0 && (
        <p className="stira-empty-leaves">No knowledge leaves yet.</p>
      )}
    </Card>
  )
}

export default function StiraTreePage() {
  const api = useAppApi()
  const [forest, setForest] = useState<ForestResponse>({ crews: [], onboarding: null })
  const [knowledge, setKnowledge] = useState<Record<string, Leaf[]>>({})
  const [knowledgeErrors, setKnowledgeErrors] = useState<Record<string, string>>({})
  const [harvestLogs, setHarvestLogs] = useState<Record<string, HarvestLogResponse>>({})
  const [harvestLogErrors, setHarvestLogErrors] = useState<Record<string, string>>({})
  const [harvestStates, setHarvestStates] = useState<Record<string, HarvestUiState>>({})
  const [selectedLeaf, setSelectedLeaf] = useState<SelectedLeaf | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [buildInfo, setBuildInfo] = useState<{ version?: string; buildHash?: string }>({})
  const [showCoreForm, setShowCoreForm] = useState(false)
  const [agentNames, setAgentNames] = useState<string[]>([])
  const [crewChoice, setCrewChoice] = useState<string>(CUSTOM_CREW)
  const [customCrew, setCustomCrew] = useState('')
  const [ipdContent, setIpdContent] = useState('')
  const [rebaseline, setRebaseline] = useState(false)
  const [submittingCore, setSubmittingCore] = useState(false)
  const [coreFormError, setCoreFormError] = useState<string | null>(null)
  const [coreSuccess, setCoreSuccess] = useState<string | null>(null)

  const loadForest = useCallback(async () => {
    setLoading(true)
    try {
      const nextForest = (await api.get(`${API_ROOT}/crews`)) as ForestResponse
      const crewResults = await Promise.all(
        nextForest.crews.map(async (crew) => {
          const [leafResult, logResult] = await Promise.all([
            api.get(`${API_ROOT}/crews/${encodeURIComponent(crew.name)}/knowledge`)
              .then((result) => ({ value: result as KnowledgeResponse, error: null as string | null }))
              .catch((reason) => ({
                value: { crewId: crew.name, entries: [], totalCount: 0 } as KnowledgeResponse,
                error: reason instanceof Error ? reason.message : 'request failed',
              })),
            api.get(`${API_ROOT}/crews/${encodeURIComponent(crew.name)}/harvest-log`)
              .then((result) => ({ value: result as HarvestLogResponse, error: null as string | null }))
              .catch((reason) => ({
                value: { crewId: crew.name, dispatch: null, runs: [] } as HarvestLogResponse,
                error: reason instanceof Error ? reason.message : 'request failed',
              })),
          ])
          return { crew: crew.name, leafResult, logResult }
        }),
      )
      setForest(nextForest)
      setBuildInfo({ version: nextForest.version, buildHash: nextForest.buildHash })
      setKnowledge(Object.fromEntries(crewResults.map((result) => [result.crew, result.leafResult.value.entries])))
      setKnowledgeErrors(Object.fromEntries(
        crewResults.filter((result) => result.leafResult.error).map((result) => [result.crew, result.leafResult.error!]),
      ))
      setHarvestLogs(Object.fromEntries(crewResults.map((result) => [result.crew, result.logResult.value])))
      setHarvestLogErrors(Object.fromEntries(
        crewResults.filter((result) => result.logResult.error).map((result) => [result.crew, result.logResult.error!]),
      ))
      setError(null)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Failed to load Stira forest')
    } finally {
      setLoading(false)
    }
  }, [api])

  const refreshHarvestLog = useCallback(async (crewName: string) => {
    try {
      const result = (await api.get(
        `${API_ROOT}/crews/${encodeURIComponent(crewName)}/harvest-log`,
      )) as HarvestLogResponse
      setHarvestLogs((current) => ({ ...current, [crewName]: result }))
      setHarvestLogErrors((current) => {
        const next = { ...current }
        delete next[crewName]
        return next
      })
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : 'request failed'
      setHarvestLogErrors((current) => ({ ...current, [crewName]: message }))
    }
  }, [api])

  const harvestCrew = useCallback(async (crewName: string) => {
    setHarvestStates((current) => ({
      ...current,
      [crewName]: { status: 'running', message: 'チャット履歴を収穫中…' },
    }))
    try {
      const dispatchResult = (await api.post(
        `${API_ROOT}/crews/${encodeURIComponent(crewName)}/harvest`,
        {},
      )) as HarvestDispatchResponse
      // ── SEC-3 core-integrity security state ────────────────────────────
      if (dispatchResult.status === 'skipped' && dispatchResult.skip === 'core_integrity') {
        setHarvestStates((current) => ({
          ...current,
          [crewName]: {
            status: 'security',
            message: '芯が承認と不一致のため収穫を停止',
          },
        }))
        await loadForest()
        return
      }
      // ── End SEC-3 core-integrity security state ────────────────────────
      const [knowledgeResult, logResult] = await Promise.all([
        api.get(`${API_ROOT}/crews/${encodeURIComponent(crewName)}/knowledge`) as Promise<KnowledgeResponse>,
        api.get(`${API_ROOT}/crews/${encodeURIComponent(crewName)}/harvest-log`) as Promise<HarvestLogResponse>,
      ])
      setKnowledge((current) => ({ ...current, [crewName]: knowledgeResult.entries }))
      setHarvestLogs((current) => ({ ...current, [crewName]: logResult }))
      setKnowledgeErrors((current) => {
        const next = { ...current }
        delete next[crewName]
        return next
      })
      setHarvestLogErrors((current) => {
        const next = { ...current }
        delete next[crewName]
        return next
      })
      const latest = logResult.runs[0]
      const written = latest?.writes?.written
      const topSkip = latest?.skips?.[0]?.reason ?? 'reason not reported'
      setHarvestStates((current) => ({
        ...current,
        [crewName]: {
          status: 'success',
          message: written === 0 ? `no-op: ${topSkip}` : `収穫完了。${written ?? 0}件を書いた。`,
        },
      }))
    } catch (reason) {
      if (isHarvestInProgressError(reason)) {
        setHarvestStates((current) => ({
          ...current,
          [crewName]: { status: 'info', message: '既に収穫中' },
        }))
        await refreshHarvestLog(crewName)
        return
      }
      const message = reason instanceof Error ? reason.message : 'request failed'
      setHarvestStates((current) => ({
        ...current,
        [crewName]: { status: 'error', message: `収穫に失敗: ${message}` },
      }))
    }
  }, [api, loadForest, refreshHarvestLog])

  useEffect(() => {
    void loadForest()
  }, [loadForest])

  useEffect(() => {
    let cancelled = false
    void api.get(`${API_ROOT}/agents`)
      .then((result) => {
        if (cancelled) return
        const response = result as AgentsResponse
        setAgentNames(Array.isArray(response.agents)
          ? response.agents.filter((agent): agent is string => typeof agent === 'string')
          : [])
      })
      .catch(() => {
        if (!cancelled) setAgentNames([])
      })
    return () => {
      cancelled = true
    }
  }, [api])

  const crewOptions = useMemo(
    () => [...new Set([...agentNames, ...forest.crews.map((crew) => crew.name)])],
    [agentNames, forest.crews],
  )

  const openCoreForm = useCallback(() => {
    const registered = new Set(forest.crews.map((crew) => crew.name))
    const availableAgent = agentNames.find((agent) => !registered.has(agent))
    setCrewChoice(availableAgent ?? CUSTOM_CREW)
    setCustomCrew('')
    setIpdContent('')
    setRebaseline(false)
    setCoreFormError(null)
    setCoreSuccess(null)
    setShowCoreForm(true)
  }, [agentNames, forest.crews])

  // ── A-1 proposal-to-approval form handoff ──────────────────────────────
  const openCoreProposalForm = useCallback((crewName: string) => {
    setCrewChoice(crewName)
    setCustomCrew('')
    setIpdContent('')
    setRebaseline(true)
    setCoreFormError(null)
    setCoreSuccess(null)
    setShowCoreForm(true)
    window.requestAnimationFrame(() => {
      document.getElementById('stira-core-form')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
      document.getElementById('stira-ipd-content')?.focus({ preventScroll: true })
    })
  }, [])
  // ── End A-1 proposal-to-approval form handoff ──────────────────────────

  const submitCore = useCallback(async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const crewId = crewChoice === CUSTOM_CREW ? customCrew.trim() : crewChoice
    if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(crewId)) {
      setCoreFormError('Crew name must use only letters, numbers, dot, underscore, or hyphen.')
      return
    }
    if (!ipdContent.trim()) {
      setCoreFormError('Define the crew core before growing its tree.')
      return
    }

    setSubmittingCore(true)
    setCoreFormError(null)
    try {
      const result = (await api.post(`${API_ROOT}/crews`, {
        crew_id: crewId,
        ipd_content: ipdContent,
        rebaseline,
      })) as CreateCrewResponse
      setShowCoreForm(false)
      setIpdContent('')
      setCustomCrew('')
      setRebaseline(false)
      await loadForest()
      setCoreSuccess(result.rebaselined
        ? `${result.crewId} の新しい芯を承認して植えた。`
        : `${result.crewId} core registered. Its tree is now growing.`)
    } catch (reason) {
      setCoreFormError(reason instanceof Error ? reason.message : 'Failed to register crew core')
    } finally {
      setSubmittingCore(false)
    }
  }, [api, crewChoice, customCrew, ipdContent, loadForest, rebaseline])

  // ── Manual pruning: active leaf -> archived fallen leaf ──────────────
  const pruneLeaf = useCallback(async (crewName: string, leaf: Leaf) => {
    await api.post(
      `${API_ROOT}/crews/${encodeURIComponent(crewName)}/leaves/${encodeURIComponent(leaf.id)}/prune`,
      {},
    )
    setKnowledge((current) => ({
      ...current,
      [crewName]: (current[crewName] ?? []).filter((entry) => entry.id !== leaf.id),
    }))
    setForest((current) => ({
      ...current,
      crews: current.crews.map((crew) => crew.name === crewName
        ? {
            ...crew,
            fallenLeaves: [
              {
                ...leaf,
                life: leaf.life ?? leaf.effectiveScore,
                archived: true,
                archivedAt: new Date().toISOString(),
                archivedReason: 'pruned',
              },
              ...(crew.fallenLeaves ?? []).filter((entry) => entry.id !== leaf.id),
            ].slice(0, 20),
          }
        : crew),
    }))
  }, [api])
  // ── End manual pruning ───────────────────────────────────────────────

  const unpruneLeaf = useCallback(async (crewName: string, leaf: Leaf) => {
    await api.post(
      `${API_ROOT}/crews/${encodeURIComponent(crewName)}/leaves/${encodeURIComponent(leaf.id)}/unprune`,
      {},
    )
    await loadForest()
  }, [api, loadForest])

  const totals = useMemo(() => {
    const leaves = Object.values(knowledge).reduce((sum, entries) => sum + entries.length, 0)
    const drifted = forest.crews.filter((crew) => crew.drift.detected).length
    const healthy = forest.crews.filter((crew) => crew.coreHashHealthy === true).length
    return { leaves, drifted, healthy }
  }, [forest.crews, knowledge])

  return (
    <div className="stira-page">
      <style>{STYLES}</style>
      <PageHeader
        title="Stira Forest"
        subtitle="Mission stability, mastery, knowledge, and drift — one living tree per crew."
      />
      <div className="stira-content">
        <div className="stira-toolbar">
          <div className="stira-stats">
            <StatCard label="Crew trees" value={String(forest.crews.length)} accent />
            <StatCard label="Healthy cores" value={String(totals.healthy)} />
            <StatCard label="Knowledge leaves" value={String(totals.leaves)} />
            <StatCard label="Drift signals" value={String(totals.drifted)} />
          </div>
          <div className="stira-toolbar-actions">
            <button className="stira-add" type="button" onClick={openCoreForm}>
              Add crew
            </button>
            <button className="stira-refresh" type="button" onClick={() => void loadForest()} disabled={loading}>
              {loading ? 'Loading…' : 'Refresh forest'}
            </button>
          </div>
        </div>

        {error && <div className="stira-error" role="alert">{error}</div>}
        {coreSuccess && <div className="stira-success" role="status">{coreSuccess}</div>}

        {forest.onboarding?.required && (
          <div className="stira-onboarding">
            <div>
              <strong>{forest.onboarding.question ?? 'Define each crew core before growth begins.'}</strong>
              <span>
                {(forest.onboarding.crews ?? []).length > 0
                  ? ` Waiting: ${(forest.onboarding.crews ?? []).join(', ')}`
                  : ' No crews are configured.'}
              </span>
            </div>
            <button type="button" onClick={openCoreForm}>Define a core</button>
          </div>
        )}

        {showCoreForm && (
          <Card>
            <form id="stira-core-form" className="stira-core-form" onSubmit={(event) => void submitCore(event)}>
              <div className="stira-form-heading">
                <div>
                  <CardTitle>{rebaseline ? 'Review and plant a core change' : 'Grow a crew tree'}</CardTitle>
                  <p className="stira-muted">
                    {rebaseline
                      ? 'Stiraは提案のhashだけを保持する。内容を確認し、完全なcandidate IPDを貼り付けて承認する。'
                      : 'Choose the crew and register its immutable IPD core.'}
                  </p>
                </div>
                <button
                  className="stira-form-close"
                  type="button"
                  aria-label="Close crew form"
                  onClick={() => setShowCoreForm(false)}
                >
                  ×
                </button>
              </div>

              <label>
                Crew
                <select value={crewChoice} onChange={(event) => setCrewChoice(event.target.value)}>
                  {crewOptions.map((agent) => (
                    <option key={agent} value={agent}>
                      {agent}
                      {agent === 'default' ? ' (conductor)' : ''}
                      {forest.crews.some((registered) => registered.name === agent) ? ' (registered)' : ''}
                    </option>
                  ))}
                  <option value={CUSTOM_CREW}>Custom crew name…</option>
                </select>
              </label>

              {crewChoice === CUSTOM_CREW && (
                <label>
                  Custom crew name
                  <input
                    value={customCrew}
                    onChange={(event) => setCustomCrew(event.target.value)}
                    placeholder="research-operator"
                    autoComplete="off"
                    maxLength={64}
                  />
                </label>
              )}

              <label>
                Core (IPD)
                <textarea
                  id="stira-ipd-content"
                  value={ipdContent}
                  onChange={(event) => setIpdContent(event.target.value)}
                  placeholder="# P0 — Mission\nDefine what this crew exists to protect and accomplish."
                  rows={9}
                />
              </label>

              {/* ── A-1 explicit human rebaseline approval ───────────── */}
              <label className="stira-rebaseline-option">
                <input
                  type="checkbox"
                  checked={rebaseline}
                  onChange={(event) => setRebaseline(event.target.checked)}
                />
                <span>
                  <strong>承認済みの芯として再設定する</strong>
                  <small>既存の承認基準を、この欄に貼り付けたIPDへ更新する。</small>
                </span>
              </label>
              {/* ── End A-1 explicit human rebaseline approval ───────── */}

              {coreFormError && <div className="stira-form-error" role="alert">{coreFormError}</div>}
              <div className="stira-form-actions">
                <button type="button" onClick={() => setShowCoreForm(false)} disabled={submittingCore}>
                  Cancel
                </button>
                <button className="stira-add" type="submit" disabled={submittingCore}>
                  {submittingCore
                    ? (rebaseline ? 'Planting…' : 'Growing…')
                    : (rebaseline ? '内容を確認して植える' : 'Register core and grow tree')}
                </button>
              </div>
            </form>
          </Card>
        )}

        <SchedulePanel />

        {!loading && !error && forest.crews.length === 0 && (
          <Card>
            <CardTitle>The forest is empty</CardTitle>
            <p className="stira-muted">Configure a crew and define its IPD core to grow the first tree.</p>
          </Card>
        )}

        <div className="stira-forest">
          {forest.crews.map((crew) => (
            <CrewTreeCard
              key={crew.name}
              crew={crew}
              leaves={knowledge[crew.name] ?? []}
              fallenLeaves={crew.fallenLeaves ?? []}
              leafError={knowledgeErrors[crew.name]}
              harvestState={harvestStates[crew.name]}
              harvestLog={harvestLogs[crew.name]}
              harvestLogError={harvestLogErrors[crew.name]}
              onHarvest={() => void harvestCrew(crew.name)}
              onRefreshHarvestLog={() => refreshHarvestLog(crew.name)}
              onReviewCoreProposal={() => openCoreProposalForm(crew.name)}
              onSelectLeaf={(leaf) => setSelectedLeaf({ crew: crew.name, leaf })}
            />
          ))}
        </div>

        {selectedLeaf && (
          <LeafDetail
            crew={selectedLeaf.crew}
            leaf={selectedLeaf.leaf}
            onPrune={selectedLeaf.leaf.archived ? undefined : () => pruneLeaf(selectedLeaf.crew, selectedLeaf.leaf)}
            onUnprune={selectedLeaf.leaf.archivedReason === 'pruned'
              ? () => unpruneLeaf(selectedLeaf.crew, selectedLeaf.leaf)
              : undefined}
            onClose={() => setSelectedLeaf(null)}
          />
        )}

        <div className="stira-build-info" data-testid="stira-build-info">
          v{buildInfo.version ?? '?'} · build {buildInfo.buildHash ?? '?'}
        </div>
      </div>
    </div>
  )
}

const STYLES = `
.stira-page {
  --stira-trunk: var(--accent, #8f744f);
  --stira-trunk-shadow: color-mix(in srgb, var(--accent, #8f744f) 45%, #18120d);
  --stira-trunk-highlight: color-mix(in srgb, var(--accent, #8f744f) 55%, #e5c891);
  --stira-root: color-mix(in srgb, var(--accent, #8f744f) 36%, #302319);
  --stira-branch: color-mix(in srgb, var(--accent, #8f744f) 52%, #38271b);
  --stira-branch-drift: var(--warning, #e4a853);
  --stira-twig: color-mix(in srgb, var(--accent, #8f744f) 42%, var(--border, #353a42));
  --stira-leaf-edge: color-mix(in srgb, var(--border, #3b4148) 68%, transparent);
  --stira-ground: color-mix(in srgb, var(--card, #171a1f) 75%, #0a0c0f);
  --stira-danger: var(--danger, #ef6b73);
  --stira-warning: var(--warning, #e4a853);
  min-height: 100%;
  color: var(--text, #d9dde3);
  background:
    radial-gradient(circle at 15% 0%, color-mix(in srgb, var(--accent, #7b9e78) 8%, transparent), transparent 34rem),
    var(--bg, #0d0f12);
}
.stira-content { padding: 0 1.5rem 2rem; }
.stira-build-info { margin-top: 1.25rem; text-align: right; color: var(--muted, #8f98a4); font-size: .68rem; letter-spacing: .04em; font-variant-numeric: tabular-nums; }
.stira-toolbar { display: flex; align-items: flex-end; gap: 1rem; margin-bottom: 1rem; }
.stira-stats { display: grid; grid-template-columns: repeat(4, minmax(8.5rem, 1fr)); gap: .75rem; flex: 1; }
.stira-toolbar-actions { display: flex; gap: .55rem; }
.stira-refresh, .stira-add, .stira-onboarding button, .stira-form-actions button {
  border: 1px solid var(--border, #353a42); border-radius: .55rem; padding: .62rem .9rem;
  background: var(--card, #171a1f); color: var(--text, #d9dde3); cursor: pointer; white-space: nowrap;
}
.stira-add { border-color: color-mix(in srgb, var(--accent, #79a676) 62%, transparent); background: color-mix(in srgb, var(--accent, #79a676) 14%, var(--card, #171a1f)); color: var(--text-strong, #fff); }
.stira-refresh:hover:not(:disabled), .stira-add:hover:not(:disabled), .stira-onboarding button:hover:not(:disabled), .stira-form-actions button:hover:not(:disabled) { border-color: var(--accent, #79a676); color: var(--text-strong, #fff); }
.stira-refresh:disabled, .stira-add:disabled, .stira-form-actions button:disabled { opacity: .55; cursor: wait; }
.stira-forest { display: grid; grid-template-columns: repeat(auto-fit, minmax(22rem, 1fr)); gap: 1rem; align-items: start; }
.stira-card-header { display: flex; justify-content: space-between; gap: 1rem; align-items: flex-start; }
.stira-card-subtitle { margin: .15rem 0 0; color: var(--muted, #8f98a4); font-size: .78rem; }
.stira-status { border: 1px solid var(--border, #353a42); border-radius: 999px; padding: .2rem .55rem; color: var(--muted, #8f98a4); font-size: .68rem; text-transform: uppercase; letter-spacing: .07em; }
.stira-status--healthy { border-color: color-mix(in srgb, var(--ok, #6ecf8d) 45%, transparent); color: var(--ok, #6ecf8d); }
.stira-status--proposal { border-color: color-mix(in srgb, var(--warning, #e4a853) 58%, transparent); background: color-mix(in srgb, var(--warning, #e4a853) 10%, transparent); color: var(--warning, #e4a853); }
.stira-status--changed { border-color: color-mix(in srgb, var(--danger, #ef6b73) 50%, transparent); color: var(--danger, #ef6b73); }
.stira-status--undefined { border-color: color-mix(in srgb, var(--warning, #e4a853) 50%, transparent); color: var(--warning, #e4a853); }
/* ── A-1 pending core proposal approval UI ───────────────────────────── */
.stira-core-proposal { display: grid; gap: .65rem; margin-top: .85rem; padding: .8rem; border: 1px solid color-mix(in srgb, var(--warning, #e4a853) 48%, transparent); border-radius: .65rem; background: color-mix(in srgb, var(--warning, #e4a853) 8%, var(--card, #171a1f)); }
.stira-core-proposal-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: .75rem; }
.stira-core-proposal-heading strong { color: var(--warning, #e4a853); font-size: .9rem; }
.stira-core-proposal-heading p, .stira-core-proposal-missing { margin: .2rem 0 0; color: var(--muted, #8f98a4); font-size: .72rem; line-height: 1.45; }
.stira-core-proposal-heading > span { border: 1px solid color-mix(in srgb, var(--warning, #e4a853) 50%, transparent); border-radius: 999px; padding: .16rem .45rem; color: var(--warning, #e4a853); font-size: .65rem; white-space: nowrap; }
.stira-core-proposal-meta { display: flex; align-items: center; justify-content: space-between; gap: .75rem; color: var(--muted, #8f98a4); font-size: .68rem; }
.stira-core-proposal-hashes { display: flex; align-items: center; gap: .35rem; min-width: 0; }
.stira-core-proposal-hashes code { color: var(--text, #d9dde3); overflow-wrap: anywhere; }
.stira-core-proposal time { white-space: nowrap; }
.stira-core-proposal > button { justify-self: start; border: 1px solid color-mix(in srgb, var(--warning, #e4a853) 58%, transparent); border-radius: .5rem; padding: .48rem .7rem; background: color-mix(in srgb, var(--warning, #e4a853) 12%, var(--card, #171a1f)); color: var(--text-strong, #fff); cursor: pointer; }
.stira-core-proposal > button:hover:not(:disabled) { border-color: var(--warning, #e4a853); }
.stira-core-proposal > button:disabled { opacity: .55; cursor: not-allowed; }
/* ── End A-1 pending core proposal approval UI ───────────────────────── */
.stira-tree { display: block; width: 100%; height: auto; margin: .25rem auto -.35rem; overflow: visible; }
.stira-svg-label { fill: var(--muted, #8f98a4); font-size: 10px; letter-spacing: .04em; }
.stira-domain-label { fill: var(--muted, #a4acb7); font-size: 10px; font-weight: 650; letter-spacing: .025em; paint-order: stroke; stroke: var(--card, #171a1f); stroke-width: 3px; stroke-linejoin: round; }
.stira-domain-branch { transition: stroke .15s ease, stroke-width .15s ease; }
.stira-domain-branch:hover { stroke: color-mix(in srgb, var(--accent, #79a676) 75%, var(--stira-branch)); }
.stira-leaf { cursor: pointer; outline: none; transition: opacity .15s ease; }
.stira-leaf--withering .stira-leaf-glyph { filter: saturate(.78); }
.stira-leaf:hover .stira-leaf-glyph path, .stira-leaf:focus .stira-leaf-glyph path,
.stira-leaf:hover .stira-leaf-glyph line, .stira-leaf:focus .stira-leaf-glyph line { stroke: var(--text-strong, #fff); stroke-width: 1.7; }
.stira-leaf-glyph path, .stira-leaf-glyph line { transition: stroke .15s ease, stroke-width .15s ease; }
.stira-fallen-leaf { pointer-events: none; }
.stira-fallen-leaf--restorable { pointer-events: auto; cursor: pointer; outline: none; }
.stira-fallen-leaf--restorable:hover .stira-leaf-glyph path,
.stira-fallen-leaf--restorable:focus .stira-leaf-glyph path,
.stira-fallen-leaf--restorable:hover .stira-leaf-glyph line,
.stira-fallen-leaf--restorable:focus .stira-leaf-glyph line { stroke: var(--text-strong, #fff); stroke-width: 1.7; }
.stira-metrics { display: grid; grid-template-columns: repeat(3, 1fr); border-top: 1px solid var(--border, #353a42); padding-top: .8rem; color: var(--muted, #8f98a4); font-size: .72rem; text-align: center; }
.stira-metrics strong { display: block; color: var(--text-strong, #fff); font-size: .95rem; }
.stira-harvest { display: grid; justify-items: center; gap: .35rem; margin-top: .8rem; }
.stira-harvest-button {
  border: 1px solid color-mix(in srgb, var(--accent, #79a676) 52%, var(--border, #353a42));
  border-radius: .55rem; padding: .55rem .9rem; background: color-mix(in srgb, var(--accent, #79a676) 10%, var(--card, #171a1f));
  color: var(--text-strong, #fff); cursor: pointer;
}
.stira-harvest-button:hover:not(:disabled) { border-color: var(--accent, #79a676); }
.stira-harvest-button:disabled { opacity: .55; cursor: wait; }
.stira-harvest-status { margin: 0; color: var(--muted, #8f98a4); font-size: .72rem; text-align: center; }
.stira-harvest-status--success { color: var(--ok, #6ecf8d); }
.stira-harvest-status--info { color: var(--accent, #79a676); }
.stira-harvest-status--security { padding: .4rem .55rem; border: 1px solid color-mix(in srgb, var(--warning, #e4a853) 45%, transparent); border-radius: .45rem; background: color-mix(in srgb, var(--warning, #e4a853) 8%, transparent); color: var(--warning, #e4a853); font-weight: 650; }
.stira-harvest-status--error { color: var(--danger, #ef6b73); }
.stira-empty-leaves, .stira-inline-warning { margin: .6rem 0 0; text-align: center; color: var(--muted, #8f98a4); font-size: .72rem; }
.stira-inline-warning { color: var(--warning, #e4a853); }
.stira-onboarding, .stira-error, .stira-success { margin-bottom: 1rem; padding: .8rem 1rem; border-radius: .65rem; border: 1px solid color-mix(in srgb, var(--warning, #e4a853) 45%, transparent); background: color-mix(in srgb, var(--warning, #e4a853) 9%, var(--card, #171a1f)); color: var(--text, #d9dde3); font-size: .82rem; }
.stira-onboarding { display: flex; align-items: center; justify-content: space-between; gap: 1rem; }
.stira-onboarding strong { color: var(--warning, #e4a853); }
.stira-onboarding span { margin-left: .25rem; }
.stira-onboarding button { padding-block: .45rem; }
.stira-error { border-color: color-mix(in srgb, var(--danger, #ef6b73) 45%, transparent); background: color-mix(in srgb, var(--danger, #ef6b73) 9%, var(--card, #171a1f)); color: var(--danger, #ef6b73); }
.stira-success { border-color: color-mix(in srgb, var(--ok, #6ecf8d) 45%, transparent); background: color-mix(in srgb, var(--ok, #6ecf8d) 9%, var(--card, #171a1f)); color: var(--ok, #6ecf8d); }
.stira-core-form { display: grid; gap: .85rem; margin-bottom: 1rem; }
.stira-form-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 1rem; }
.stira-form-close { border: 0; background: transparent; color: var(--muted, #8f98a4); font-size: 1.4rem; cursor: pointer; }
.stira-core-form label { display: grid; gap: .35rem; color: var(--muted, #8f98a4); font-size: .75rem; font-weight: 600; }
.stira-core-form select, .stira-core-form input, .stira-core-form textarea { width: 100%; box-sizing: border-box; border: 1px solid var(--border, #353a42); border-radius: .55rem; padding: .65rem .72rem; background: var(--bg-elevated, #22262d); color: var(--text, #d9dde3); font: inherit; }
.stira-core-form textarea { min-height: 10rem; resize: vertical; font-family: ui-monospace, SFMono-Regular, Consolas, monospace; line-height: 1.45; }
.stira-core-form select:focus, .stira-core-form input:focus, .stira-core-form textarea:focus { outline: 2px solid color-mix(in srgb, var(--accent, #79a676) 45%, transparent); border-color: var(--accent, #79a676); }
.stira-rebaseline-option { grid-template-columns: auto 1fr !important; align-items: start; padding: .7rem; border: 1px solid color-mix(in srgb, var(--warning, #e4a853) 40%, var(--border, #353a42)); border-radius: .55rem; background: color-mix(in srgb, var(--warning, #e4a853) 6%, transparent); cursor: pointer; }
.stira-rebaseline-option input { width: auto; margin: .15rem 0 0; padding: 0; border: 0; border-radius: 0; background: transparent; accent-color: var(--warning, #e4a853); }
.stira-rebaseline-option span { display: grid; gap: .2rem; }
.stira-rebaseline-option strong { color: var(--text, #d9dde3); }
.stira-rebaseline-option small { color: var(--muted, #8f98a4); font-weight: 400; line-height: 1.4; }
.stira-form-error { color: var(--danger, #ef6b73); font-size: .78rem; }
.stira-form-actions { display: flex; justify-content: flex-end; gap: .55rem; }
.stira-muted { color: var(--muted, #8f98a4); font-size: .85rem; }

@media (max-width: 760px) {
  .stira-content { padding-inline: .8rem; }
  .stira-toolbar { align-items: stretch; flex-direction: column; }
  .stira-toolbar-actions, .stira-form-actions { display: grid; grid-template-columns: 1fr; }
  .stira-onboarding { align-items: stretch; flex-direction: column; }
  .stira-core-proposal-meta { align-items: flex-start; flex-direction: column; }
  .stira-core-proposal-hashes { flex-wrap: wrap; }
  .stira-stats { grid-template-columns: repeat(2, 1fr); }
  .stira-forest { grid-template-columns: 1fr; }
}
`
