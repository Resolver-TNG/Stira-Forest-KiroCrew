import { DEFAULT_LAYOUT } from './config'
import { hashUnit } from './hash'
import type { KnowledgeLayer, LayoutConfig, LeafShape } from './types'

const LIFE_COLOR_STOPS = [
  [1.00, [90, 175, 80]],
  [0.70, [150, 190, 60]],
  [0.52, [220, 190, 55]],
  [0.36, [224, 140, 50]],
  [0.20, [200, 70, 55]],
  [0.08, [120, 55, 45]],
  [0.00, [62, 42, 34]],
] as const

const LEAF_SHAPES: LeafShape[] = ['maple', 'broad', 'needle', 'ginkgo', 'heart', 'slender']
const DOMAIN_LEAF_SHAPES: Record<string, LeafShape> = {
  architecture: 'maple',
  deployment: 'broad',
  config: 'needle',
  security: 'ginkgo',
  operations: 'heart',
  routing: 'slender',
}

export function clamp01(value: number | null | undefined, fallback = 0): number {
  if (typeof value !== 'number' || !Number.isFinite(value)) return fallback
  return Math.max(0, Math.min(1, value))
}

export function lifeColor(value: number): string {
  const life = clamp01(value)
  for (let index = 0; index < LIFE_COLOR_STOPS.length - 1; index += 1) {
    const [high, highColor] = LIFE_COLOR_STOPS[index]
    const [low, lowColor] = LIFE_COLOR_STOPS[index + 1]
    if (life <= high && life >= low) {
      const position = (life - low) / (high - low)
      const color = lowColor.map((channel, channelIndex) =>
        Math.round(channel + (highColor[channelIndex] - channel) * position))
      return `rgb(${color[0]},${color[1]},${color[2]})`
    }
  }
  return 'rgb(62,42,34)'
}

export function leafShapeForDomain(domain: string): LeafShape {
  const normalized = domain.trim().toLocaleLowerCase()
  return DOMAIN_LEAF_SHAPES[normalized]
    ?? LEAF_SHAPES[Math.floor(hashUnit(normalized || 'general') * LEAF_SHAPES.length) % LEAF_SHAPES.length]
}

export function importanceToDistance(
  effectiveScore: number,
  config: Pick<LayoutConfig, 'tMin' | 'tSpan' | 'tMax'> = DEFAULT_LAYOUT,
): number {
  return Math.min(config.tMax, config.tMin + (1 - clamp01(effectiveScore)) * config.tSpan)
}

export function leafScale(
  layer: KnowledgeLayer,
  effectiveScore: number,
  contentLength = 0,
  config: Pick<LayoutConfig, 'layerScale' | 'scoreBase' | 'scoreWeight' | 'bulkWeight' | 'bulkChars'> = DEFAULT_LAYOUT,
): number {
  const score = clamp01(effectiveScore)
  const bulk = clamp01(contentLength / config.bulkChars)
  return config.layerScale[layer]
    * (config.scoreBase + score * config.scoreWeight)
    * (1 + bulk * config.bulkWeight)
}
