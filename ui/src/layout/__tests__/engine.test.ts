import { describe, expect, it } from 'vitest'

import crowdedFixture from '../../../preview/fixtures/crowded.json'
import lifecycleFixture from '../../../preview/fixtures/lifecycle.json'
import sparseFixture from '../../../preview/fixtures/sparse.json'
import { DEFAULT_LAYOUT } from '../config'
import { buildScene } from '../engine'
import { leafScale, lifeColor } from '../encoding'
import type { LayoutInput, LayoutLeaf, Scene } from '../types'

const DOMAINS = [
  'architecture',
  'deployment',
  'config',
  'security',
  'operations',
  'routing',
  'testing',
  'governance',
  'memory',
  'observability',
  'workflow',
]

function leaf(index: number, overrides: Partial<LayoutLeaf> = {}): LayoutLeaf {
  return {
    id: `leaf-${String(index).padStart(2, '0')}`,
    domain: DOMAINS[index % DOMAINS.length],
    layer: index < 18 ? 'knowledge' : index % 2 === 0 ? 'core' : 'log',
    effectiveScore: 0.18 + ((index * 29) % 78) / 100,
    life: 0.08 + ((index * 37) % 90) / 100,
    contentLength: 120 + ((index * 431) % 2600),
    tags: index % 9 === 0 ? ['drift'] : [],
    ...overrides,
  }
}

const crowded = crowdedFixture as LayoutInput
const fixtureInputs = [crowdedFixture, sparseFixture, lifecycleFixture] as LayoutInput[]

function distance(left: { x: number; y: number }, right: { x: number; y: number }): number {
  return Math.hypot(left.x - right.x, left.y - right.y)
}

function expectFinite(value: unknown): void {
  if (typeof value === 'number') {
    expect(Number.isFinite(value)).toBe(true)
    return
  }
  if (Array.isArray(value)) {
    value.forEach(expectFinite)
    return
  }
  if (value && typeof value === 'object') {
    Object.values(value).forEach(expectFinite)
  }
}

type LabelBounds = { left: number; right: number; top: number; bottom: number }

function labelBounds(
  x: number,
  y: number,
  text: string,
  anchor: 'start' | 'end',
): LabelBounds {
  const width = Math.max(DEFAULT_LAYOUT.labelCharWidth, text.length * DEFAULT_LAYOUT.labelCharWidth)
  const left = anchor === 'end' ? x - width : x
  return {
    left: left - DEFAULT_LAYOUT.labelGap,
    right: left + width + DEFAULT_LAYOUT.labelGap,
    top: y - DEFAULT_LAYOUT.labelHeight - DEFAULT_LAYOUT.labelGap,
    bottom: y + DEFAULT_LAYOUT.labelGap,
  }
}

function boundsOverlap(left: LabelBounds, right: LabelBounds): boolean {
  return left.left < right.right
    && left.right > right.left
    && left.top < right.bottom
    && left.bottom > right.top
}

describe('buildScene', () => {
  it('resolves all active-leaf overlaps in a crowded 27-leaf fixture', () => {
    const scene = buildScene(crowded)
    expect(scene.leaves).toHaveLength(27)
    expect(scene.diagnostics.overlapPairs).toBe(0)
  })

  it('keeps every preview fixture overlap-free and within the anchor constraint', () => {
    const maximum = DEFAULT_LAYOUT.twigLengthMax * DEFAULT_LAYOUT.maxAnchorDistanceFactor
    for (const fixture of fixtureInputs) {
      const scene = buildScene(fixture)
      expect(scene.diagnostics.overlapPairs).toBe(0)
      expect(scene.diagnostics.maxLeafAnchorDistance).toBeLessThanOrEqual(maximum + 1e-8)
      for (const item of scene.leaves) {
        expect(item.anchor).not.toBeNull()
        expect(distance(item, item.anchor!)).toBeLessThanOrEqual(maximum + 1e-8)
      }
    }
  })

  it('keeps every active leaf within the maximum anchor distance', () => {
    const scene = buildScene(crowded)
    const maximum = DEFAULT_LAYOUT.twigLengthMax * DEFAULT_LAYOUT.maxAnchorDistanceFactor
    for (const item of scene.leaves) {
      expect(item.anchor).not.toBeNull()
      expect(distance(item, item.anchor!)).toBeLessThanOrEqual(maximum + 1e-8)
    }
    expect(scene.diagnostics.maxLeafAnchorDistance).toBeLessThanOrEqual(maximum + 1e-8)
  })

  it('places lower-importance leaves monotonically farther along one branch', () => {
    const scores = [0.96, 0.78, 0.55, 0.31]
    const scene = buildScene({
      crew: { name: 'importance', stability: 0.7, growth: 0.5 },
      leaves: scores.map((score, index) => leaf(index, {
        id: `importance-${index}`,
        domain: 'architecture',
        layer: 'knowledge',
        effectiveScore: score,
        tags: [],
      })),
    })
    const branch = scene.branches[0]
    const distances = scores.map((_, index) => {
      const item = scene.leaves.find((candidate) => candidate.id === `importance-${index}`)!
      return distance(branch.start, item.anchor!)
    })
    for (let index = 1; index < distances.length; index += 1) {
      expect(distances[index]).toBeGreaterThanOrEqual(distances[index - 1] - 1e-8)
    }
  })

  it('is byte-for-byte deterministic for identical input', () => {
    expect(JSON.stringify(buildScene(crowded))).toBe(JSON.stringify(buildScene(crowded)))
  })

  it('separates adjacent branch starts on each side by minBranchGap', () => {
    const scene = buildScene(crowded)
    for (const side of [-1, 1] as const) {
      const starts = scene.branches
        .filter((branch) => branch.side === side)
        .map((branch) => branch.start.y)
        .sort((left, right) => left - right)
      for (let index = 1; index < starts.length; index += 1) {
        expect(starts[index] - starts[index - 1]).toBeGreaterThanOrEqual(DEFAULT_LAYOUT.minBranchGap - 1e-8)
      }
    }
  })

  it('keeps every visible domain label clear of layer and domain labels', () => {
    const scene = buildScene(crowded)
    const bounds = scene.layerLabels.map((label) =>
      labelBounds(label.x, label.y, label.layer.toUpperCase(), 'start'))
    for (const branch of scene.branches.filter((item) => item.label.visible)) {
      const current = labelBounds(
        branch.label.x,
        branch.label.y,
        branch.label.text,
        branch.label.anchor,
      )
      expect(bounds.every((other) => !boundsOverlap(current, other))).toBe(true)
      bounds.push(current)
    }
  })

  it('returns finite, bounded scenes for crowded, empty, and fallen-only input', () => {
    const empty = buildScene({ crew: { name: 'empty', stability: null, growth: null }, leaves: [] })
    const fallenOnly = buildScene({
      crew: { name: 'fallen', stability: 0.5, growth: 0.2 },
      leaves: [],
      fallenLeaves: [leaf(90, { id: 'fallen-1', archived: true, archivedReason: 'faded' })],
    })
    const scenes: Scene[] = [buildScene(crowded), empty, fallenOnly]
    scenes.forEach((scene) => {
      expectFinite(scene)
      expect(scene.viewHeight).toBeGreaterThanOrEqual(400)
      expect(scene.viewHeight).toBeLessThan(1400)
    })
    expect(empty.leaves).toHaveLength(0)
    expect(fallenOnly.leaves).toHaveLength(0)
    expect(fallenOnly.fallen).toHaveLength(1)
  })

  it('encodes score and content into scale, and low life into brown', () => {
    const large = leafScale('knowledge', 0.95, 3000)
    const small = leafScale('knowledge', 0.2, 50)
    expect(large).toBeGreaterThan(small)

    const channels = lifeColor(0.05).match(/\d+/g)!.map(Number)
    expect(channels[1]).toBeLessThan(70)
    expect(channels[0]).toBeGreaterThan(channels[1])
  })
})
