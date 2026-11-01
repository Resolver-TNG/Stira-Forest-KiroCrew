import type { LeafShape } from '../layout/types'

type LeafGlyphProps = {
  shape: LeafShape
  color: string
  scale?: number
}

export function LeafGlyph({ shape, color, scale = 1 }: LeafGlyphProps) {
  const fillProps = {
    fill: color,
    stroke: 'var(--stira-leaf-edge)',
    strokeWidth: 0.7,
  }
  const veinProps = {
    fill: 'none',
    stroke: 'rgba(0,0,0,.32)',
    strokeWidth: 0.7,
  }

  return (
    <g className={`stira-leaf-glyph stira-leaf-glyph--${shape}`} transform={`scale(${scale})`}>
      {shape === 'maple' && (
        <>
          <path d="M 0 4.5 L -1.8 1.4 L -6 2.4 L -4 -1 L -8 -4 L -3 -3.8 L -2 -9 L 0 -5.4 L 2 -9 L 3 -3.8 L 8 -4 L 4 -1 L 6 2.4 L 1.8 1.4 Z" {...fillProps} />
          <path d="M 0 4.2 L 0 -5.4" {...veinProps} />
        </>
      )}
      {shape === 'broad' && (
        <>
          <path d="M 0 -6.5 C 6.3 -1.95 4.9 4.55 0 6.5 C -4.9 4.55 -6.3 -1.95 0 -6.5 Z" {...fillProps} />
          <path d="M 0 -5.5 L 0 5.5" {...veinProps} />
        </>
      )}
      {shape === 'needle' && [-22, -11, 0, 11, 22].map((angle) => {
        const radians = angle * Math.PI / 180
        return (
          <line
            key={angle}
            x1="0"
            y1="7.5"
            x2={Math.sin(radians) * 15}
            y2={7.5 - Math.cos(radians) * 15}
            stroke={color}
            strokeWidth="1.5"
            strokeLinecap="round"
          />
        )
      })}
      {shape === 'ginkgo' && (
        <>
          <path d="M 0 5.5 L -8.25 -3.85 A 11 11 0 0 1 8.25 -3.85 Z" {...fillProps} />
          <path d="M 0 5.5 L 0 -2.2" {...veinProps} />
        </>
      )}
      {shape === 'heart' && (
        <>
          <path d="M 0 4.95 C 9 0 5.4 -9 0 -3.15 C -5.4 -9 -9 0 0 4.95 Z" {...fillProps} />
          <path d="M 0 3.6 L 0 -2.25" {...veinProps} />
        </>
      )}
      {shape === 'slender' && (
        <>
          <path d="M 0 -8 C 3.4 -1.6 3.4 4.8 0 8 C -3.4 4.8 -3.4 -1.6 0 -8 Z" {...fillProps} />
          <path d="M 0 -6.7 L 0 6.7" {...veinProps} />
        </>
      )}
    </g>
  )
}
