export type KnowledgeLayer = 'core' | 'knowledge' | 'log'

export type LeafShape = 'maple' | 'broad' | 'needle' | 'ginkgo' | 'heart' | 'slender'

export type Point = { x: number; y: number }

export type LayoutLeaf = {
  id: string
  domain: string
  layer: KnowledgeLayer
  effectiveScore: number
  life?: number
  contentLength?: number
  tags?: string[]
  archived?: boolean
  archivedReason?: 'faded' | 'pruned' | null
}

export type LayoutCrew = {
  name: string
  stability: number | null
  growth: number | null
  purposeDrift?: number
  meansDrift?: number
}

export type LayoutInput = {
  crew: LayoutCrew
  leaves: LayoutLeaf[]
  fallenLeaves?: LayoutLeaf[]
  viewWidth?: number
}

export type SceneBranch = {
  key: string
  domain: string
  layer: KnowledgeLayer
  side: -1 | 1
  width: number
  path: string
  start: Point
  control: Point
  end: Point
  label: { x: number; y: number; anchor: 'start' | 'end'; text: string; visible: boolean }
}

export type SceneLeaf = {
  id: string
  branchKey: string | null
  x: number
  y: number
  rotation: number
  scale: number
  hitRadius: number
  color: string
  shape: LeafShape
  opacity: number
  withering: boolean
  twigPath: string | null
  anchor: Point | null
}

export type Scene = {
  viewWidth: number
  viewHeight: number
  trunk: { path: string; rootX: number; rootY: number; crownY: number; baseWidth: number; topWidth: number }
  roots: { path: string; width: number }[]
  branches: SceneBranch[]
  leaves: SceneLeaf[]
  fallen: SceneLeaf[]
  layerLabels: { layer: KnowledgeLayer; x: number; y: number }[]
  diagnostics: {
    overlapPairs: number
    relaxIterations: number
    maxLeafAnchorDistance: number
  }
}

export type LayoutConfig = {
  viewWidth: number
  minViewHeight: number
  viewPadding: number
  groundOffset: number
  crownYBase: number
  crownGrowthLift: number
  bands: Record<KnowledgeLayer, number>
  lanePitch: number
  branchJitterY: number
  minBranchGap: number
  branchWidth: Record<KnowledgeLayer, number>
  branchGrowthWidth: number
  reachBase: number
  reachExtent: number
  reachLane: number
  reachJitter: number
  growthReach: number
  meansDriftReach: number
  meansDriftControl: number
  liftByLayer: Record<KnowledgeLayer, number>
  liftJitter: { min: number; max: number }
  controlReachRatio: number
  controlLiftRatio: number
  tMin: number
  tSpan: number
  tMax: number
  leafGapMin: number
  leafGapMax: number
  twigLengthMin: number
  twigLengthMax: number
  alongAmp: number
  bendAmp: number
  rotJitter: number
  layerScale: Record<KnowledgeLayer, number>
  scoreWeight: number
  scoreBase: number
  bulkWeight: number
  bulkChars: number
  hitRadiusBase: number
  hitRadiusScale: number
  witheringThreshold: number
  witheringDropMin: number
  witheringDropRange: number
  witheringRotation: number
  collisionPacking: number
  collisionIterations: number
  collisionEpsilon: number
  maxAnchorDistanceFactor: number
  denseBranchMargin: number
  layerBandGap: number
  labelCharWidth: number
  labelHeight: number
  labelGap: number
  labelShiftStep: number
  labelShiftAttempts: number
  stabilityFallback: number
  trunkBaseMin: number
  trunkBaseStability: number
  trunkTopMin: number
  trunkTopStability: number
  purposeDriftTilt: number
  domainExtentBase: number
  domainExtentCount: number
  domainExtentMax: number
  taggedDriftOffset: number
  meansLeafOffset: number
  activeLeafLimit: number
  fallenLeafLimit: number
  fallenSidePadding: number
  fallenVerticalRange: number
  opacityFreshMin: number
  opacityWithering: number
  rootCount: number
}
