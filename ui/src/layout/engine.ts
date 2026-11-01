import { resolveCollisions } from './collide'
import { resolveLayoutConfig } from './config'
import { clamp01, importanceToDistance, leafScale, leafShapeForDomain, lifeColor } from './encoding'
import { hashUnit } from './hash'
import type {
  KnowledgeLayer,
  LayoutConfig,
  LayoutInput,
  LayoutLeaf,
  Point,
  Scene,
  SceneBranch,
  SceneLeaf,
} from './types'

const LAYERS: KnowledgeLayer[] = ['core', 'knowledge', 'log']
const LAYER_ORDER: Record<KnowledgeLayer, number> = { core: 0, knowledge: 1, log: 2 }

type DomainGroup = {
  domain: string
  leaves: LayoutLeaf[]
  closestLayer: KnowledgeLayer
  extent: number
  side: -1 | 1
  lane: number
}

type BranchSeed = {
  group: DomainGroup
  layer: KnowledgeLayer
  leaves: LayoutLeaf[]
  lane: number
  startY: number
}

function bezierPoint(start: Point, control: Point, end: Point, position: number): Point {
  const inverse = 1 - position
  return {
    x: inverse * inverse * start.x + 2 * inverse * position * control.x + position * position * end.x,
    y: inverse * inverse * start.y + 2 * inverse * position * control.y + position * position * end.y,
  }
}

function bezierTangent(start: Point, control: Point, end: Point, position: number): Point {
  return {
    x: 2 * (1 - position) * (control.x - start.x) + 2 * position * (end.x - control.x),
    y: 2 * (1 - position) * (control.y - start.y) + 2 * position * (end.y - control.y),
  }
}

function spreadLeafPositions(leaves: LayoutLeaf[], config: LayoutConfig): { leaf: LayoutLeaf; position: number }[] {
  const sorted = [...leaves].sort((left, right) =>
    clamp01(right.effectiveScore) - clamp01(left.effectiveScore)
    || left.id.localeCompare(right.id))
  if (sorted.length === 0) return []
  if (sorted.length === 1) {
    return [{ leaf: sorted[0], position: importanceToDistance(sorted[0].effectiveScore, config) }]
  }

  const gap = Math.max(
    config.leafGapMin,
    Math.min(config.leafGapMax, (config.tMax - config.tMin) / (sorted.length - 1)),
  )
  const positions = sorted.map((leaf) => importanceToDistance(leaf.effectiveScore, config))
  for (let index = 1; index < positions.length; index += 1) {
    positions[index] = Math.max(positions[index], positions[index - 1] + gap)
  }
  const overflow = Math.max(0, positions[positions.length - 1] - config.tMax)
  return sorted.map((leaf, index) => ({ leaf, position: positions[index] - overflow }))
}

function makeDomainGroups(leaves: LayoutLeaf[], config: LayoutConfig): DomainGroup[] {
  const grouped = new Map<string, LayoutLeaf[]>()
  for (const leaf of leaves) {
    const domain = leaf.domain?.trim() || 'general'
    grouped.set(domain, [...(grouped.get(domain) ?? []), leaf])
  }

  const groups = [...grouped.entries()]
    .map(([domain, entries]) => ({
      domain,
      leaves: [...entries].sort((left, right) =>
        LAYER_ORDER[left.layer] - LAYER_ORDER[right.layer]
        || clamp01(right.effectiveScore) - clamp01(left.effectiveScore)
        || left.id.localeCompare(right.id)),
      closestLayer: entries.reduce<KnowledgeLayer>(
        (closest, leaf) => LAYER_ORDER[leaf.layer] < LAYER_ORDER[closest] ? leaf.layer : closest,
        'log',
      ),
      extent: Math.min(
        config.domainExtentMax,
        config.domainExtentBase + Math.sqrt(entries.length) * config.domainExtentCount,
      ),
    }))
    .sort((left, right) =>
      LAYER_ORDER[left.closestLayer] - LAYER_ORDER[right.closestLayer]
      || right.leaves.length - left.leaves.length
      || left.domain.localeCompare(right.domain))

  const occupied: Record<'left' | 'right', number> = { left: 0, right: 0 }
  return groups.map((group) => {
    const sideName = occupied.left <= occupied.right ? 'left' : 'right'
    const side = sideName === 'left' ? -1 as const : 1 as const
    const lane = occupied[sideName]
    occupied[sideName] += 1
    return { ...group, side, lane }
  })
}

function separateBranches(seeds: BranchSeed[], minGap: number): void {
  for (const side of [-1, 1] as const) {
    const sameSide = seeds
      .filter((seed) => seed.group.side === side)
      .sort((left, right) => left.startY - right.startY || left.group.domain.localeCompare(right.group.domain))
    for (let index = 1; index < sameSide.length; index += 1) {
      sameSide[index].startY = Math.max(sameSide[index].startY, sameSide[index - 1].startY + minGap)
    }
  }
}

type LabelRect = { left: number; right: number; top: number; bottom: number }

function labelRect(
  x: number,
  y: number,
  text: string,
  anchor: 'start' | 'end',
  config: LayoutConfig,
): LabelRect {
  const width = Math.max(config.labelCharWidth, text.length * config.labelCharWidth)
  const left = anchor === 'end' ? x - width : x
  return {
    left: left - config.labelGap,
    right: left + width + config.labelGap,
    top: y - config.labelHeight - config.labelGap,
    bottom: y + config.labelGap,
  }
}

function labelsOverlap(left: LabelRect, right: LabelRect): boolean {
  return left.left < right.right
    && left.right > right.left
    && left.top < right.bottom
    && left.bottom > right.top
}

function resolveLabelCollisions(
  branches: SceneBranch[],
  layerLabels: Scene['layerLabels'],
  config: LayoutConfig,
): void {
  const occupied = layerLabels.map((label) =>
    labelRect(label.x, label.y, label.layer.toUpperCase(), 'start', config))
  const labels = branches
    .filter((branch) => branch.label.visible)
    .sort((left, right) => left.label.y - right.label.y || left.key.localeCompare(right.key))

  for (const branch of labels) {
    let placed = false
    const originalY = branch.label.y
    for (let candidate = 0; candidate <= config.labelShiftAttempts * 2; candidate += 1) {
      const rank = Math.ceil(candidate / 2)
      const direction = candidate === 0 ? 0 : candidate % 2 === 1 ? -1 : 1
      const y = originalY + direction * rank * config.labelShiftStep
      const rect = labelRect(branch.label.x, y, branch.label.text, branch.label.anchor, config)
      if (occupied.every((other) => !labelsOverlap(rect, other))) {
        branch.label.y = y
        occupied.push(rect)
        placed = true
        break
      }
    }
    if (!placed) branch.label.visible = false
  }
}

function twigPath(anchor: Point, leaf: SceneLeaf, bend: number): string {
  const dx = leaf.x - anchor.x
  const dy = leaf.y - anchor.y
  const distance = Math.max(1, Math.hypot(dx, dy))
  const normal = { x: -dy / distance, y: dx / distance }
  return `M ${anchor.x} ${anchor.y} Q ${(anchor.x + leaf.x) / 2 + normal.x * bend} ${(anchor.y + leaf.y) / 2 + normal.y * bend} ${leaf.x} ${leaf.y}`
}

export function buildScene(input: LayoutInput, overrides: Partial<LayoutConfig> = {}): Scene {
  const config = resolveLayoutConfig(overrides)
  const activeLeaves = input.leaves
    .filter((leaf) => !leaf.archived)
    .slice(0, config.activeLeafLimit)
  const groups = makeDomainGroups(activeLeaves, config)
  const viewWidth = Math.max(320, input.viewWidth ?? config.viewWidth)
  const growth = clamp01(input.crew.growth)
  const stability = clamp01(input.crew.stability, config.stabilityFallback)
  const purposeDrift = clamp01(input.crew.purposeDrift)
  const meansDrift = clamp01(input.crew.meansDrift)
  const crownY = config.crownYBase - growth * config.crownGrowthLift
  const maxAnchorDistance = config.twigLengthMax * config.maxAnchorDistanceFactor
  const denseLanePitch = Math.max(
    config.lanePitch,
    maxAnchorDistance * 2 + config.denseBranchMargin,
  )
  const branchDrafts = groups.flatMap((group) => LAYERS.flatMap((layer) => {
    const leaves = group.leaves.filter((leaf) => leaf.layer === layer)
    return leaves.length > 0 ? [{ group, layer, leaves }] : []
  }))
  const layerSideCounts: Record<KnowledgeLayer, Record<'left' | 'right', number>> = {
    core: { left: 0, right: 0 },
    knowledge: { left: 0, right: 0 },
    log: { left: 0, right: 0 },
  }
  for (const draft of branchDrafts) {
    layerSideCounts[draft.layer][draft.group.side < 0 ? 'left' : 'right'] += 1
  }
  const layerSpans = Object.fromEntries(LAYERS.map((layer) => [
    layer,
    Math.max(0, Math.max(layerSideCounts[layer].left, layerSideCounts[layer].right) - 1) * denseLanePitch,
  ])) as Record<KnowledgeLayer, number>
  const requiredTrunkHeights = [
    config.minViewHeight - config.groundOffset - crownY,
    (layerSpans.core / 2 + config.branchJitterY + config.layerBandGap) / config.bands.core,
    (layerSpans.log / 2 + config.branchJitterY + config.layerBandGap) / (1 - config.bands.log),
  ]
  for (let index = 1; index < LAYERS.length; index += 1) {
    const upper = LAYERS[index - 1]
    const lower = LAYERS[index]
    const bandDistance = config.bands[lower] - config.bands[upper]
    if (bandDistance > 0) {
      requiredTrunkHeights.push(
        (layerSpans[upper] / 2
          + layerSpans[lower] / 2
          + config.branchJitterY * 2
          + config.layerBandGap) / bandDistance,
      )
    }
  }
  const trunkHeight = Math.max(...requiredTrunkHeights)
  const rootY = crownY + trunkHeight
  const viewHeight = Math.ceil(rootY + config.groundOffset)
  const rootX = viewWidth / 2
  const direction = hashUnit(input.crew.name) >= 0.5 ? 1 : -1
  const crownX = rootX + purposeDrift * config.purposeDriftTilt * direction
  const baseWidth = config.trunkBaseMin + stability * config.trunkBaseStability
  const topWidth = config.trunkTopMin + stability * config.trunkTopStability

  const trunkProgressAt = (y: number) => clamp01((y - crownY) / trunkHeight)
  const trunkCenterAt = (y: number) => {
    const progress = trunkProgressAt(y)
    const eased = progress * progress * (3 - 2 * progress)
    return crownX + (rootX - crownX) * eased
  }
  const trunkWidthAt = (y: number) => {
    const progress = trunkProgressAt(y)
    return topWidth + (baseWidth - topWidth) * Math.pow(progress, 0.86)
  }

  const trunkPath = [
    `M ${rootX - baseWidth / 2} ${rootY}`,
    `C ${rootX - baseWidth * 0.48} ${rootY - trunkHeight * 0.29}`,
    `${crownX - topWidth * 0.72} ${crownY + trunkHeight * 0.42}`,
    `${crownX - topWidth / 2} ${crownY}`,
    `L ${crownX + topWidth / 2} ${crownY}`,
    `C ${crownX + topWidth * 0.72} ${crownY + trunkHeight * 0.42}`,
    `${rootX + baseWidth * 0.48} ${rootY - trunkHeight * 0.29}`,
    `${rootX + baseWidth / 2} ${rootY}`,
    'Z',
  ].join(' ')

  const laneCursors: Record<KnowledgeLayer, Record<'left' | 'right', number>> = {
    core: { left: 0, right: 0 },
    knowledge: { left: 0, right: 0 },
    log: { left: 0, right: 0 },
  }
  const seeds: BranchSeed[] = branchDrafts.map((draft) => {
    const sideName = draft.group.side < 0 ? 'left' : 'right'
    const lane = laneCursors[draft.layer][sideName]
    laneCursors[draft.layer][sideName] += 1
    const centeredLane = lane - (layerSideCounts[draft.layer][sideName] - 1) / 2
    const jitter = (hashUnit(`${draft.group.domain}:by`) * 2 - 1) * config.branchJitterY
    return {
      ...draft,
      lane,
      startY: crownY + trunkHeight * config.bands[draft.layer] + centeredLane * denseLanePitch + jitter,
    }
  })
  separateBranches(seeds, denseLanePitch)

  const branches: SceneBranch[] = []
  const initialLeaves: SceneLeaf[] = []

  for (const seed of seeds) {
    const { group, layer } = seed
    const trunkCenter = trunkCenterAt(seed.startY)
    const start = {
      x: trunkCenter + group.side * Math.max(2, trunkWidthAt(seed.startY) / 2 - 1),
      y: seed.startY,
    }
    const reachJitter = (hashUnit(`${group.domain}:${layer}:reach`) * 2 - 1) * config.reachJitter
    const reach = config.reachBase
      + group.extent * config.reachExtent
      + seed.lane * config.reachLane
      + growth * config.growthReach
      + reachJitter
    const liftFactor = config.liftJitter.min
      + hashUnit(`${group.domain}:${layer}:lift`) * (config.liftJitter.max - config.liftJitter.min)
    const lift = config.liftByLayer[layer] * liftFactor
    const end = {
      x: trunkCenter + group.side * (reach + meansDrift * config.meansDriftReach),
      y: seed.startY - lift,
    }
    const control = {
      x: start.x + group.side * (reach * config.controlReachRatio - meansDrift * config.meansDriftControl),
      y: seed.startY - lift * config.controlLiftRatio,
    }
    const key = `${group.domain}:${layer}`
    const branch: SceneBranch = {
      key,
      domain: group.domain,
      layer,
      side: group.side,
      width: config.branchWidth[layer] + growth * config.branchGrowthWidth,
      path: `M ${start.x} ${start.y} Q ${control.x} ${control.y} ${end.x} ${end.y}`,
      start,
      control,
      end,
      label: {
        x: end.x + group.side * 14,
        y: end.y - 14,
        anchor: group.side < 0 ? 'end' : 'start',
        text: group.domain,
        visible: layer === group.closestLayer,
      },
    }
    branches.push(branch)

    for (const { leaf, position } of spreadLeafPositions(seed.leaves, config)) {
      const anchor = bezierPoint(start, control, end, position)
      const tangent = bezierTangent(start, control, end, position)
      const tangentLength = Math.max(1, Math.hypot(tangent.x, tangent.y))
      const tangentUnit = { x: tangent.x / tangentLength, y: tangent.y / tangentLength }
      const normalUnit = { x: -tangentUnit.y, y: tangentUnit.x }
      const score = clamp01(leaf.effectiveScore)
      const life = clamp01(leaf.life, score)
      const withering = life < config.witheringThreshold
      const twigDirection = withering ? 1 : (hashUnit(`${leaf.id}:twig-side`) >= 0.5 ? 1 : -1)
      const twigLength = config.twigLengthMin
        + hashUnit(`${leaf.id}:twig-length`) * (config.twigLengthMax - config.twigLengthMin)
      const alongOffset = (hashUnit(`${leaf.id}:along-offset`) - 0.5) * config.alongAmp
      const taggedDrift = leaf.tags?.some((tag) => /drift|deviation|逸脱/i.test(tag))
        ? config.taggedDriftOffset
        : 0
      const outwardOffset = taggedDrift + meansDrift * config.meansLeafOffset
      const x = anchor.x
        + tangentUnit.x * alongOffset
        + normalUnit.x * twigDirection * twigLength
        + group.side * outwardOffset
      const y = anchor.y
        + tangentUnit.y * alongOffset
        + normalUnit.y * twigDirection * twigLength
        + (withering
          ? config.witheringDropMin + hashUnit(`${leaf.id}:wither-drop`) * config.witheringDropRange
          : 0)
      const scale = leafScale(layer, score, leaf.contentLength, config)
      const rotation = Math.atan2(normalUnit.y * twigDirection, normalUnit.x * twigDirection) * 180 / Math.PI
        + 90
        + (hashUnit(`${leaf.id}:r`) * 2 - 1) * config.rotJitter
        + (withering ? group.side * 25 + config.witheringRotation : 0)
      initialLeaves.push({
        id: leaf.id,
        branchKey: key,
        x,
        y,
        rotation,
        scale,
        hitRadius: config.hitRadiusBase + scale * config.hitRadiusScale,
        color: lifeColor(life),
        shape: leafShapeForDomain(leaf.domain),
        opacity: withering ? config.opacityWithering : config.opacityFreshMin + life * (1 - config.opacityFreshMin),
        withering,
        twigPath: null,
        anchor,
      })
    }
  }

  const collision = resolveCollisions(initialLeaves, {
    packing: config.collisionPacking,
    iterations: config.collisionIterations,
    maxAnchorDistance,
    epsilon: config.collisionEpsilon,
  })
  const leaves = collision.leaves.map((leaf) => ({
    ...leaf,
    twigPath: leaf.anchor
      ? twigPath(leaf.anchor, leaf, 2 + hashUnit(`${leaf.id}:twig-bend`) * config.bendAmp)
      : null,
  }))

  const fallen = (input.fallenLeaves ?? []).slice(0, config.fallenLeafLimit).map((leaf): SceneLeaf => {
    const life = Math.min(clamp01(leaf.life, leaf.effectiveScore), 0.04)
    const scale = 0.62 + hashUnit(`${leaf.id}:scale`) * 0.3
    return {
      id: leaf.id,
      branchKey: null,
      x: config.fallenSidePadding
        + hashUnit(`${leaf.id}:x`) * (viewWidth - config.fallenSidePadding * 2),
      y: rootY + 2 + hashUnit(`${leaf.id}:y`) * config.fallenVerticalRange,
      rotation: 30 + hashUnit(`${leaf.id}:rotation`) * 300,
      scale,
      hitRadius: config.hitRadiusBase + scale * config.hitRadiusScale,
      color: lifeColor(life),
      shape: leafShapeForDomain(leaf.domain),
      opacity: 0.34 + hashUnit(`${leaf.id}:opacity`) * 0.2,
      withering: true,
      twigPath: null,
      anchor: null,
    }
  })

  const roots = Array.from({ length: config.rootCount }, (_, root) => {
    const rootDirection = root % 2 === 0 ? -1 : 1
    const rank = Math.floor(root / 2)
    const length = 54 + rank * 22 + growth * 18
    return {
      path: `M ${rootX + rootDirection * (2 + rank * 2)} ${rootY - rank} Q ${rootX + rootDirection * length * 0.52} ${rootY + 8 + rank * 2} ${rootX + rootDirection * length} ${rootY + 4 + rank * 3}`,
      width: Math.max(2.5, baseWidth * 0.15 - rank * 0.8),
    }
  })

  const layerLabels: Scene['layerLabels'] = LAYERS.map((layer) => ({
    layer,
    x: 18,
    y: crownY + trunkHeight * config.bands[layer] - 7,
  }))
  resolveLabelCollisions(branches, layerLabels, config)

  const measuredViewHeight = Math.ceil(Math.max(
    viewHeight,
    ...branches.map((branch) => Math.max(branch.start.y, branch.control.y, branch.end.y, branch.label.y) + config.viewPadding),
    ...leaves.map((leaf) => leaf.y + leaf.hitRadius + config.viewPadding),
    ...fallen.map((leaf) => leaf.y + leaf.hitRadius + config.viewPadding),
  ))

  return {
    viewWidth,
    viewHeight: measuredViewHeight,
    trunk: { path: trunkPath, rootX, rootY, crownY, baseWidth, topWidth },
    roots,
    branches,
    leaves,
    fallen,
    layerLabels,
    diagnostics: {
      overlapPairs: collision.overlapPairs,
      relaxIterations: collision.relaxIterations,
      maxLeafAnchorDistance: collision.maxLeafAnchorDistance,
    },
  }
}

export type {
  KnowledgeLayer,
  LayoutConfig,
  LayoutCrew,
  LayoutInput,
  LayoutLeaf,
  Scene,
  SceneBranch,
  SceneLeaf,
} from './types'
