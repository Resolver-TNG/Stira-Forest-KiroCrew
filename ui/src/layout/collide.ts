import { hashUnit } from './hash'
import type { SceneLeaf } from './types'

export type CollisionOptions = {
  packing: number
  iterations: number
  maxAnchorDistance: number
  epsilon?: number
}

export type CollisionResult = {
  leaves: SceneLeaf[]
  overlapPairs: number
  relaxIterations: number
  maxLeafAnchorDistance: number
}

function clampToAnchor(leaf: SceneLeaf, maxDistance: number): void {
  if (!leaf.anchor) return
  const dx = leaf.x - leaf.anchor.x
  const dy = leaf.y - leaf.anchor.y
  const distance = Math.hypot(dx, dy)
  if (distance <= maxDistance || distance === 0) return
  const ratio = maxDistance / distance
  leaf.x = leaf.anchor.x + dx * ratio
  leaf.y = leaf.anchor.y + dy * ratio
}

function pairDirection(left: SceneLeaf, right: SceneLeaf): { x: number; y: number; distance: number } {
  const dx = right.x - left.x
  const dy = right.y - left.y
  const distance = Math.hypot(dx, dy)
  if (distance > 1e-9) return { x: dx / distance, y: dy / distance, distance }
  const first = left.id.localeCompare(right.id) <= 0 ? left.id : right.id
  const second = first === left.id ? right.id : left.id
  const angle = hashUnit(`${first}:${second}:collision`) * Math.PI * 2
  return { x: Math.cos(angle), y: Math.sin(angle), distance: 0 }
}

export function countOverlapPairs(leaves: readonly SceneLeaf[], packing: number, epsilon = 0): number {
  let overlaps = 0
  for (let leftIndex = 0; leftIndex < leaves.length; leftIndex += 1) {
    for (let rightIndex = leftIndex + 1; rightIndex < leaves.length; rightIndex += 1) {
      const left = leaves[leftIndex]
      const right = leaves[rightIndex]
      const minimumDistance = (left.hitRadius + right.hitRadius) * packing
      if (Math.hypot(right.x - left.x, right.y - left.y) + epsilon < minimumDistance) {
        overlaps += 1
      }
    }
  }
  return overlaps
}

export function resolveCollisions(
  source: readonly SceneLeaf[],
  options: CollisionOptions,
): CollisionResult {
  const epsilon = options.epsilon ?? 0.01
  const leaves = source.map((leaf) => ({
    ...leaf,
    anchor: leaf.anchor ? { ...leaf.anchor } : null,
  }))

  leaves.forEach((leaf) => clampToAnchor(leaf, options.maxAnchorDistance))
  let relaxIterations = 0

  for (let iteration = 0; iteration < options.iterations; iteration += 1) {
    let moved = false
    for (let leftIndex = 0; leftIndex < leaves.length; leftIndex += 1) {
      for (let rightIndex = leftIndex + 1; rightIndex < leaves.length; rightIndex += 1) {
        const left = leaves[leftIndex]
        const right = leaves[rightIndex]
        const minimumDistance = (left.hitRadius + right.hitRadius) * options.packing
        const direction = pairDirection(left, right)
        const overlap = minimumDistance - direction.distance
        if (overlap <= epsilon) continue

        const leftMass = Math.max(0.01, left.scale * left.scale)
        const rightMass = Math.max(0.01, right.scale * right.scale)
        const totalMass = leftMass + rightMass
        const leftMove = overlap * (rightMass / totalMass)
        const rightMove = overlap * (leftMass / totalMass)

        left.x -= direction.x * leftMove
        left.y -= direction.y * leftMove
        right.x += direction.x * rightMove
        right.y += direction.y * rightMove
        clampToAnchor(left, options.maxAnchorDistance)
        clampToAnchor(right, options.maxAnchorDistance)
        moved = true
      }
    }

    relaxIterations = iteration + 1
    const overlaps = countOverlapPairs(leaves, options.packing, epsilon)
    if (!moved || overlaps === 0) break
  }

  let maxLeafAnchorDistance = 0
  for (const leaf of leaves) {
    if (!leaf.anchor) continue
    maxLeafAnchorDistance = Math.max(
      maxLeafAnchorDistance,
      Math.hypot(leaf.x - leaf.anchor.x, leaf.y - leaf.anchor.y),
    )
  }

  return {
    leaves,
    overlapPairs: countOverlapPairs(leaves, options.packing, epsilon),
    relaxIterations,
    maxLeafAnchorDistance,
  }
}
