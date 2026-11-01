import React, { useMemo, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { buildScene } from '../src/layout/engine'
import type { LayoutInput, Scene, SceneLeaf } from '../src/layout/types'
import crowdedFixture from './fixtures/crowded.json'
import lifecycleFixture from './fixtures/lifecycle.json'
import sparseFixture from './fixtures/sparse.json'

const fixtures: ReadonlyArray<{ name: string; input: LayoutInput }> = [
  { name: 'Crowded', input: crowdedFixture as LayoutInput },
  { name: 'Sparse', input: sparseFixture as LayoutInput },
  { name: 'Lifecycle', input: lifecycleFixture as LayoutInput },
]

function LeafGlyph({ leaf }: { leaf: SceneLeaf }) {
  const fillProps = {
    fill: leaf.color,
    stroke: 'var(--leaf-edge)',
    strokeWidth: 0.7,
  }
  const veinProps = {
    fill: 'none',
    stroke: 'rgba(0, 0, 0, 0.32)',
    strokeWidth: 0.7,
  }

  return (
    <g transform={`scale(${leaf.scale})`}>
      {leaf.shape === 'maple' && (
        <>
          <path d="M 0 4.5 L -1.8 1.4 L -6 2.4 L -4 -1 L -8 -4 L -3 -3.8 L -2 -9 L 0 -5.4 L 2 -9 L 3 -3.8 L 8 -4 L 4 -1 L 6 2.4 L 1.8 1.4 Z" {...fillProps} />
          <path d="M 0 4.2 L 0 -5.4" {...veinProps} />
        </>
      )}
      {leaf.shape === 'broad' && (
        <>
          <path d="M 0 -6.5 C 6.3 -1.95 4.9 4.55 0 6.5 C -4.9 4.55 -6.3 -1.95 0 -6.5 Z" {...fillProps} />
          <path d="M 0 -5.5 L 0 5.5" {...veinProps} />
        </>
      )}
      {leaf.shape === 'needle' && [-22, -11, 0, 11, 22].map((angle) => {
        const radians = angle * Math.PI / 180
        return (
          <line
            key={angle}
            x1="0"
            y1="7.5"
            x2={Math.sin(radians) * 15}
            y2={7.5 - Math.cos(radians) * 15}
            stroke={leaf.color}
            strokeWidth="1.5"
            strokeLinecap="round"
          />
        )
      })}
      {leaf.shape === 'ginkgo' && (
        <>
          <path d="M 0 5.5 L -8.25 -3.85 A 11 11 0 0 1 8.25 -3.85 Z" {...fillProps} />
          <path d="M 0 5.5 L 0 -2.2" {...veinProps} />
        </>
      )}
      {leaf.shape === 'heart' && (
        <>
          <path d="M 0 4.95 C 9 0 5.4 -9 0 -3.15 C -5.4 -9 -9 0 0 4.95 Z" {...fillProps} />
          <path d="M 0 3.6 L 0 -2.25" {...veinProps} />
        </>
      )}
      {leaf.shape === 'slender' && (
        <>
          <path d="M 0 -8 C 3.4 -1.6 3.4 4.8 0 8 C -3.4 4.8 -3.4 -1.6 0 -8 Z" {...fillProps} />
          <path d="M 0 -6.7 L 0 6.7" {...veinProps} />
        </>
      )}
    </g>
  )
}

function SceneLeafGlyph({ leaf, showHitCircles }: { leaf: SceneLeaf; showHitCircles: boolean }) {
  return (
    <g className={leaf.withering ? 'preview-leaf preview-leaf--withering' : 'preview-leaf'}>
      {leaf.twigPath && (
        <path className="preview-twig" d={leaf.twigPath} />
      )}
      <g transform={`translate(${leaf.x} ${leaf.y}) rotate(${leaf.rotation})`} opacity={leaf.opacity}>
        {showHitCircles && (
          <circle className="preview-hit-circle" r={leaf.hitRadius} />
        )}
        <LeafGlyph leaf={leaf} />
      </g>
    </g>
  )
}

function ScenePreview({ name, scene, showHitCircles }: { name: string; scene: Scene; showHitCircles: boolean }) {
  const { diagnostics } = scene
  return (
    <article className="preview-card">
      <header>
        <div>
          <p className="preview-kicker">Layout fixture</p>
          <h2>{name}</h2>
        </div>
        <dl className="preview-diagnostics">
          <div>
            <dt>Overlap pairs</dt>
            <dd className={diagnostics.overlapPairs > 0 ? 'diagnostic-alert' : ''}>{diagnostics.overlapPairs}</dd>
          </div>
          <div>
            <dt>Max anchor distance</dt>
            <dd>{diagnostics.maxLeafAnchorDistance.toFixed(2)}</dd>
          </div>
          <div>
            <dt>Relax iterations</dt>
            <dd>{diagnostics.relaxIterations}</dd>
          </div>
        </dl>
      </header>
      <svg
        className="preview-tree"
        viewBox={`0 0 ${scene.viewWidth} ${scene.viewHeight}`}
        role="img"
        aria-label={`${name} layout scene`}
      >
        <defs>
          <linearGradient id={`trunk-${name}`} x1="0" x2="1">
            <stop offset="0" stopColor="#3f2a21" />
            <stop offset="0.5" stopColor="#79543a" />
            <stop offset="1" stopColor="#a4774f" />
          </linearGradient>
        </defs>
        <ellipse
          cx={scene.trunk.rootX}
          cy={scene.trunk.rootY + 7}
          rx="112"
          ry="13"
          fill="rgba(57, 42, 28, 0.14)"
        />
        {scene.roots.map((root, index) => (
          <path
            key={`root-${index}`}
            d={root.path}
            fill="none"
            stroke="#65472f"
            strokeWidth={root.width}
            strokeLinecap="round"
          />
        ))}
        {scene.fallen.map((leaf) => (
          <SceneLeafGlyph key={`fallen-${leaf.id}`} leaf={leaf} showHitCircles={showHitCircles} />
        ))}
        {scene.branches.map((branch) => (
          <path
            key={branch.key}
            d={branch.path}
            fill="none"
            stroke="#6f5337"
            strokeWidth={branch.width}
            strokeLinecap="round"
          />
        ))}
        <path d={scene.trunk.path} fill={`url(#trunk-${name})`} />
        {scene.layerLabels.map((label) => (
          <text key={label.layer} x={label.x} y={label.y} className="preview-layer-label">
            {label.layer.toUpperCase()}
          </text>
        ))}
        {scene.branches.filter((branch) => branch.label.visible).map((branch) => (
          <text
            key={`${branch.key}-label`}
            x={branch.label.x}
            y={branch.label.y}
            textAnchor={branch.label.anchor}
            className="preview-domain-label"
          >
            {branch.label.text}
          </text>
        ))}
        {scene.leaves.map((leaf) => (
          <SceneLeafGlyph key={leaf.id} leaf={leaf} showHitCircles={showHitCircles} />
        ))}
      </svg>
    </article>
  )
}

function PreviewApp() {
  const [showHitCircles, setShowHitCircles] = useState(false)
  const scenes = useMemo(
    () => fixtures.map((fixture) => ({ ...fixture, scene: buildScene(fixture.input) })),
    [],
  )

  return (
    <main>
      <section className="preview-heading">
        <div>
          <p className="preview-kicker">Stira UI refactor · Wave 1</p>
          <h1>Layout engine preview</h1>
          <p>All coordinates come directly from <code>buildScene</code>.</p>
        </div>
        <label className="preview-toggle">
          <input
            type="checkbox"
            checked={showHitCircles}
            onChange={(event) => setShowHitCircles(event.target.checked)}
          />
          <span>Show hit circles</span>
        </label>
      </section>
      <section className="preview-grid">
        {scenes.map(({ name, scene }) => (
          <ScenePreview key={name} name={name} scene={scene} showHitCircles={showHitCircles} />
        ))}
      </section>
    </main>
  )
}

const style = document.createElement('style')
style.textContent = `
  :root {
    color: #e9ecf2;
    background: #101318;
    font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    --leaf-edge: rgba(13, 19, 17, 0.7);
  }
  * { box-sizing: border-box; }
  body { margin: 0; min-width: 2160px; background: radial-gradient(circle at top, #202834 0, #101318 54%); }
  main { padding: 28px; }
  code { color: #9fd7bc; }
  .preview-heading { display: flex; align-items: end; justify-content: space-between; gap: 24px; margin-bottom: 20px; }
  .preview-heading h1, .preview-card h2 { margin: 0; font-weight: 670; letter-spacing: -0.025em; }
  .preview-heading h1 { font-size: 28px; }
  .preview-heading p:not(.preview-kicker) { margin: 6px 0 0; color: #9ea8b7; }
  .preview-kicker { margin: 0 0 4px; color: #75c9a0; font-size: 11px; font-weight: 750; letter-spacing: 0.13em; text-transform: uppercase; }
  .preview-toggle { display: flex; align-items: center; gap: 10px; padding: 10px 14px; border: 1px solid #394451; border-radius: 999px; background: #1a2028; cursor: pointer; }
  .preview-toggle input { accent-color: #5fc28f; }
  .preview-grid { display: grid; grid-template-columns: repeat(3, minmax(680px, 1fr)); gap: 18px; align-items: start; }
  .preview-card { overflow: hidden; border: 1px solid #303a46; border-radius: 14px; background: rgba(24, 29, 36, 0.94); box-shadow: 0 18px 55px rgba(0, 0, 0, 0.24); }
  .preview-card > header { display: flex; justify-content: space-between; gap: 20px; padding: 16px 18px; border-bottom: 1px solid #303a46; }
  .preview-card h2 { font-size: 20px; }
  .preview-diagnostics { display: flex; gap: 18px; margin: 0; }
  .preview-diagnostics div { min-width: 82px; }
  .preview-diagnostics dt { color: #8f9aaa; font-size: 10px; text-transform: uppercase; letter-spacing: 0.06em; }
  .preview-diagnostics dd { margin: 3px 0 0; color: #dfe8e2; font: 650 15px ui-monospace, SFMono-Regular, Consolas, monospace; }
  .preview-diagnostics .diagnostic-alert { color: #ff947d; }
  .preview-tree { display: block; width: 100%; height: auto; min-height: 480px; background: linear-gradient(#18211e 0%, #202820 72%, #27231d 100%); }
  .preview-layer-label { fill: #7f8c8b; font-size: 10px; letter-spacing: 0.14em; }
  .preview-domain-label { fill: #c4cec9; font-size: 10px; }
  .preview-twig { fill: none; stroke: #816246; stroke-width: 1; stroke-linecap: round; }
  .preview-leaf--withering { filter: saturate(0.78); }
  .preview-hit-circle { fill: rgba(255, 108, 94, 0.08); stroke: rgba(255, 126, 112, 0.72); stroke-width: 0.8; vector-effect: non-scaling-stroke; }
`
document.head.appendChild(style)

const root = document.getElementById('root')
if (!root) throw new Error('Preview root element was not found')
createRoot(root).render(<PreviewApp />)
