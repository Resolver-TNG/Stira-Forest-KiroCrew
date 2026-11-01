import {
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react'

export type KnowledgeLeaf = {
  id: string
  title: string
  summary: string
  score: number
  layer: 'core' | 'knowledge' | 'log'
  domain: string
  tags: string[]
  effectiveScore: number
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

type Heading = { depth: number; text: string; id: string }
type MarkdownBlock =
  | { kind: 'heading'; depth: number; text: string; id: string }
  | { kind: 'paragraph'; text: string }
  | { kind: 'code'; language: string; text: string }
  | { kind: 'list'; ordered: boolean; items: string[] }
  | { kind: 'quote'; text: string }
  | { kind: 'rule' }

function headingId(text: string, used: Map<string, number>): string {
  const base = text
    .toLocaleLowerCase()
    .normalize('NFKC')
    .replace(/[^\p{L}\p{N}\s-]/gu, '')
    .trim()
    .replace(/[\s-]+/g, '-') || 'section'
  const count = used.get(base) ?? 0
  used.set(base, count + 1)
  return count === 0 ? base : `${base}-${count + 1}`
}

function isBlockStart(line: string): boolean {
  return /^(#{1,6})\s+/.test(line)
    || /^```/.test(line)
    || /^\s*(?:[-+*]|\d+\.)\s+/.test(line)
    || /^>\s?/.test(line)
    || /^\s*(?:---+|___+|\*\*\*+)\s*$/.test(line)
}

function parseMarkdown(content: string): { blocks: MarkdownBlock[]; headings: Heading[] } {
  const lines = content.replace(/\r\n?/g, '\n').split('\n')
  const blocks: MarkdownBlock[] = []
  const headings: Heading[] = []
  const usedIds = new Map<string, number>()

  for (let index = 0; index < lines.length;) {
    const line = lines[index]
    if (!line.trim()) {
      index += 1
      continue
    }

    const heading = /^(#{1,6})\s+(.+?)\s*#*\s*$/.exec(line)
    if (heading) {
      const depth = heading[1].length
      const text = heading[2]
      const id = headingId(text, usedIds)
      headings.push({ depth, text, id })
      blocks.push({ kind: 'heading', depth, text, id })
      index += 1
      continue
    }

    const fence = /^```\s*([^\s`]*)\s*$/.exec(line)
    if (fence) {
      const code: string[] = []
      index += 1
      while (index < lines.length && !/^```\s*$/.test(lines[index])) {
        code.push(lines[index])
        index += 1
      }
      if (index < lines.length) index += 1
      blocks.push({ kind: 'code', language: fence[1], text: code.join('\n') })
      continue
    }

    if (/^\s*(?:---+|___+|\*\*\*+)\s*$/.test(line)) {
      blocks.push({ kind: 'rule' })
      index += 1
      continue
    }

    const listItem = /^\s*(?:(\d+)\.|[-+*])\s+(.+)$/.exec(line)
    if (listItem) {
      const ordered = listItem[1] !== undefined
      const items: string[] = []
      while (index < lines.length) {
        const next = /^\s*(?:(\d+)\.|[-+*])\s+(.+)$/.exec(lines[index])
        if (!next || (next[1] !== undefined) !== ordered) break
        items.push(next[2])
        index += 1
      }
      blocks.push({ kind: 'list', ordered, items })
      continue
    }

    if (/^>\s?/.test(line)) {
      const quote: string[] = []
      while (index < lines.length && /^>\s?/.test(lines[index])) {
        quote.push(lines[index].replace(/^>\s?/, ''))
        index += 1
      }
      blocks.push({ kind: 'quote', text: quote.join('\n') })
      continue
    }

    const paragraph = [line.trim()]
    index += 1
    while (index < lines.length && lines[index].trim() && !isBlockStart(lines[index])) {
      paragraph.push(lines[index].trim())
      index += 1
    }
    blocks.push({ kind: 'paragraph', text: paragraph.join(' ') })
  }

  return { blocks, headings }
}

function safeHref(value: string): string | null {
  try {
    const url = new URL(value)
    return url.protocol === 'https:' || url.protocol === 'http:' ? url.href : null
  } catch {
    return null
  }
}

function inlineMarkdown(text: string): ReactNode[] {
  const token = /(`[^`]+`|\*\*[^*]+\*\*|\*[^*]+\*|\[[^\]]+\]\([^\s)]+\))/g
  const nodes: ReactNode[] = []
  let cursor = 0
  for (const match of text.matchAll(token)) {
    const position = match.index ?? 0
    if (position > cursor) nodes.push(text.slice(cursor, position))
    const value = match[0]
    if (value.startsWith('`')) {
      nodes.push(<code key={position}>{value.slice(1, -1)}</code>)
    } else if (value.startsWith('**')) {
      nodes.push(<strong key={position}>{value.slice(2, -2)}</strong>)
    } else if (value.startsWith('*')) {
      nodes.push(<em key={position}>{value.slice(1, -1)}</em>)
    } else {
      const link = /^\[([^\]]+)\]\(([^)]+)\)$/.exec(value)
      const href = link ? safeHref(link[2]) : null
      nodes.push(href
        ? <a key={position} href={href} target="_blank" rel="noreferrer">{link?.[1]}</a>
        : value)
    }
    cursor = position + value.length
  }
  if (cursor < text.length) nodes.push(text.slice(cursor))
  return nodes
}

function MarkdownContent({ content }: { content: string }) {
  const parsed = useMemo(() => parseMarkdown(content), [content])

  return (
    <>
      {parsed.headings.length > 0 && (
        <nav className="stira-leaf-toc" aria-label="本文のセクション">
          <strong>Sections</strong>
          <ol>
            {parsed.headings.map((heading) => (
              <li key={heading.id} style={{ paddingInlineStart: `${(heading.depth - 1) * 0.65}rem` }}>
                <a href={`#${heading.id}`}>{heading.text}</a>
              </li>
            ))}
          </ol>
        </nav>
      )}
      <div className="stira-markdown">
        {parsed.blocks.map((block, index) => {
          if (block.kind === 'heading') {
            const HeadingTag = `h${block.depth}` as keyof JSX.IntrinsicElements
            return <HeadingTag id={block.id} key={block.id}>{inlineMarkdown(block.text)}</HeadingTag>
          }
          if (block.kind === 'paragraph') return <p key={index}>{inlineMarkdown(block.text)}</p>
          if (block.kind === 'code') {
            return <pre key={index}><code data-language={block.language}>{block.text}</code></pre>
          }
          if (block.kind === 'quote') return <blockquote key={index}>{inlineMarkdown(block.text)}</blockquote>
          if (block.kind === 'rule') return <hr key={index} />
          const ListTag = block.ordered ? 'ol' : 'ul'
          return (
            <ListTag key={index}>
              {block.items.map((item, itemIndex) => <li key={itemIndex}>{inlineMarkdown(item)}</li>)}
            </ListTag>
          )
        })}
      </div>
    </>
  )
}

export function LeafDetail({
  crew,
  leaf,
  onPrune,
  onUnprune,
  onClose,
}: {
  crew: string
  leaf: KnowledgeLeaf
  onPrune?: () => Promise<void>
  onUnprune?: () => Promise<void>
  onClose: () => void
}) {
  const closeButton = useRef<HTMLButtonElement>(null)
  const [confirmingPrune, setConfirmingPrune] = useState(false)
  const [pruning, setPruning] = useState(false)
  const [pruneError, setPruneError] = useState<string | null>(null)
  const [restoring, setRestoring] = useState(false)
  const [restoreError, setRestoreError] = useState<string | null>(null)

  useEffect(() => {
    closeButton.current?.focus()
    const closeOnEscape = (event: globalThis.KeyboardEvent) => {
      if (event.key !== 'Escape') return
      if (confirmingPrune && !pruning) {
        setConfirmingPrune(false)
        setPruneError(null)
        return
      }
      if (!pruning) onClose()
    }
    window.addEventListener('keydown', closeOnEscape)
    return () => window.removeEventListener('keydown', closeOnEscape)
  }, [confirmingPrune, onClose, pruning])

  useEffect(() => {
    setConfirmingPrune(false)
    setPruning(false)
    setPruneError(null)
    setRestoring(false)
    setRestoreError(null)
  }, [leaf.id])

  const prune = async () => {
    if (!onPrune) return
    setPruning(true)
    setPruneError(null)
    try {
      await onPrune()
      onClose()
    } catch (reason) {
      setPruneError(reason instanceof Error ? reason.message : '剪定に失敗した')
    } finally {
      setPruning(false)
    }
  }

  const restore = async () => {
    if (!onUnprune) return
    setRestoring(true)
    setRestoreError(null)
    try {
      await onUnprune()
      onClose()
    } catch (reason) {
      setRestoreError(reason instanceof Error ? reason.message : '葉を戻せなかった')
    } finally {
      setRestoring(false)
    }
  }

  const stopPropagation = (event: ReactKeyboardEvent<HTMLElement>) => event.stopPropagation()

  return (
    <div className="stira-leaf-overlay" role="presentation" onMouseDown={onClose}>
      <style>{LEAF_DETAIL_STYLES}</style>
      <aside
        className="stira-leaf-detail"
        role="dialog"
        aria-modal="true"
        aria-labelledby="stira-leaf-title"
        onMouseDown={(event) => event.stopPropagation()}
        onKeyDown={stopPropagation}
      >
        <header className="stira-leaf-detail-header">
          <div>
            <p className="stira-leaf-eyebrow">{crew} / {leaf.domain}</p>
            <h2 id="stira-leaf-title">{leaf.title}</h2>
          </div>
          <button ref={closeButton} type="button" aria-label="Close knowledge detail" onClick={onClose}>×</button>
        </header>

        <p className="stira-leaf-summary">{leaf.summary || 'No summary.'}</p>
        <div className="stira-leaf-meta" aria-label="Knowledge metadata">
          <span>{leaf.layer}</span>
          <span>score {Math.round(leaf.score * 100)}%</span>
          <span>effective {Math.round(leaf.effectiveScore * 100)}%</span>
          {leaf.tags.map((tag) => <span key={tag}>#{tag}</span>)}
        </div>

        <div className="stira-leaf-body">
          <MarkdownContent content={leaf.content || '_No content available._'} />
        </div>

        {/* ── Manual pruning confirmation ───────────────────────────── */}
        {(onPrune || onUnprune) && <footer className="stira-leaf-actions">
          {onUnprune ? (
            <div className="stira-restore-action">
              {restoreError && <p className="stira-prune-error" role="alert">{restoreError}</p>}
              <button className="stira-restore-button" type="button" onClick={() => void restore()} disabled={restoring}>
                {restoring ? '戻している…' : '木に戻す'}
              </button>
            </div>
          ) : !confirmingPrune ? (
            <button className="stira-prune-button" type="button" onClick={() => setConfirmingPrune(true)}>
              剪定する
            </button>
          ) : (
            <div className="stira-prune-confirm" role="alertdialog" aria-labelledby="stira-prune-title" aria-describedby="stira-prune-description">
              <div>
                <strong id="stira-prune-title">この葉を剪定する？</strong>
                <p id="stira-prune-description">木から外して落ち葉にする。剪定した葉は、あとで木に戻せる。</p>
                {pruneError && <p className="stira-prune-error" role="alert">{pruneError}</p>}
              </div>
              <div className="stira-prune-confirm-actions">
                <button type="button" onClick={() => setConfirmingPrune(false)} disabled={pruning}>戻る</button>
                <button className="stira-prune-button" type="button" onClick={() => void prune()} disabled={pruning}>
                  {pruning ? '剪定中…' : '剪定する'}
                </button>
              </div>
            </div>
          )}
        </footer>}
        {/* ── End manual pruning confirmation ──────────────────────── */}
      </aside>
    </div>
  )
}

const LEAF_DETAIL_STYLES = `
.stira-leaf-overlay { position: fixed; inset: 0; z-index: 50; display: grid; place-items: center; padding: 1rem; background: color-mix(in srgb, var(--bg, #0d0f12) 72%, transparent); backdrop-filter: blur(6px); }
.stira-leaf-detail { display: grid; grid-template-rows: auto auto auto minmax(0, 1fr) auto; width: min(52rem, 96vw); max-height: min(90vh, 58rem); overflow: hidden; border: 1px solid var(--border, #353a42); border-radius: .9rem; background: var(--card, #171a1f); color: var(--text, #d9dde3); box-shadow: 0 1.4rem 5rem rgba(0,0,0,.58); }
.stira-leaf-detail-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 1rem; padding: 1rem 1.15rem .7rem; border-bottom: 1px solid var(--border, #353a42); }
.stira-leaf-detail-header h2 { margin: .18rem 0 0; color: var(--text-strong, #fff); font-size: 1.18rem; line-height: 1.3; }
.stira-leaf-detail-header button { flex: 0 0 auto; border: 1px solid transparent; border-radius: .4rem; padding: .12rem .48rem; background: transparent; color: var(--muted, #8f98a4); font-size: 1.45rem; cursor: pointer; }
.stira-leaf-detail-header button:hover, .stira-leaf-detail-header button:focus-visible { border-color: var(--border, #353a42); color: var(--text-strong, #fff); outline: none; }
.stira-leaf-eyebrow { margin: 0; color: var(--accent, #79a676); font-size: .68rem; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; }
.stira-leaf-summary { margin: 0; padding: .75rem 1.15rem .45rem; color: var(--muted, #a4acb7); font-size: .84rem; line-height: 1.55; }
.stira-leaf-meta { display: flex; flex-wrap: wrap; gap: .35rem; padding: .2rem 1.15rem .8rem; }
.stira-leaf-meta span { border: 1px solid color-mix(in srgb, var(--border, #353a42) 72%, transparent); border-radius: 999px; padding: .2rem .5rem; background: var(--bg-elevated, #22262d); color: var(--muted, #a4acb7); font-size: .68rem; }
.stira-leaf-body { overflow: auto; padding: 0 1.15rem 1.3rem; scroll-behavior: smooth; overscroll-behavior: contain; }
.stira-leaf-actions { display: flex; justify-content: flex-end; padding: .75rem 1.15rem; border-top: 1px solid var(--border, #353a42); background: color-mix(in srgb, var(--bg-elevated, #22262d) 54%, transparent); }
.stira-prune-button, .stira-restore-button, .stira-prune-confirm-actions button { border: 1px solid var(--border, #353a42); border-radius: .5rem; padding: .5rem .75rem; background: var(--card, #171a1f); color: var(--text, #d9dde3); cursor: pointer; }
.stira-restore-action { display: flex; align-items: center; gap: .65rem; }
.stira-restore-action p { margin: 0; color: var(--danger, #ef6b73); font-size: .72rem; }
.stira-restore-button { border-color: color-mix(in srgb, var(--accent, #79a676) 58%, var(--border, #353a42)); color: var(--accent, #79a676); }
.stira-restore-button:hover:not(:disabled) { border-color: var(--accent, #79a676); color: var(--text-strong, #fff); }
.stira-restore-button:disabled { opacity: .55; cursor: wait; }
.stira-prune-button { border-color: color-mix(in srgb, var(--danger, #ef6b73) 58%, var(--border, #353a42)); color: var(--danger, #ef6b73); }
.stira-prune-button:hover:not(:disabled) { border-color: var(--danger, #ef6b73); background: color-mix(in srgb, var(--danger, #ef6b73) 9%, var(--card, #171a1f)); }
.stira-prune-confirm { display: flex; width: 100%; align-items: center; justify-content: space-between; gap: 1rem; }
.stira-prune-confirm strong { color: var(--text-strong, #fff); font-size: .82rem; }
.stira-prune-confirm p { margin: .2rem 0 0; color: var(--muted, #a4acb7); font-size: .72rem; line-height: 1.45; }
.stira-prune-confirm .stira-prune-error { color: var(--danger, #ef6b73); }
.stira-prune-confirm-actions { display: flex; flex: 0 0 auto; gap: .45rem; }
.stira-prune-confirm-actions button:disabled { opacity: .55; cursor: wait; }
.stira-leaf-toc { margin: .2rem 0 1rem; padding: .75rem .85rem; border: 1px solid var(--border, #353a42); border-radius: .65rem; background: color-mix(in srgb, var(--bg-elevated, #22262d) 72%, transparent); }
.stira-leaf-toc strong { color: var(--text-strong, #fff); font-size: .72rem; letter-spacing: .06em; text-transform: uppercase; }
.stira-leaf-toc ol { display: grid; gap: .2rem; margin: .45rem 0 0; padding: 0; list-style: none; }
.stira-leaf-toc a { color: var(--accent, #8fc88b); font-size: .76rem; text-decoration: none; }
.stira-leaf-toc a:hover { text-decoration: underline; }
.stira-markdown { color: var(--text, #d9dde3); font-size: .86rem; line-height: 1.7; overflow-wrap: anywhere; }
.stira-markdown h1, .stira-markdown h2, .stira-markdown h3, .stira-markdown h4, .stira-markdown h5, .stira-markdown h6 { scroll-margin-top: 1rem; margin: 1.35em 0 .45em; color: var(--text-strong, #fff); line-height: 1.35; }
.stira-markdown h1 { font-size: 1.35rem; } .stira-markdown h2 { font-size: 1.18rem; } .stira-markdown h3 { font-size: 1.04rem; } .stira-markdown h4, .stira-markdown h5, .stira-markdown h6 { font-size: .92rem; }
.stira-markdown p { margin: .65rem 0; }
.stira-markdown a { color: var(--accent, #8fc88b); }
.stira-markdown code { border-radius: .28rem; padding: .08rem .3rem; background: var(--bg, #0d0f12); color: var(--text-strong, #fff); font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: .82em; }
.stira-markdown pre { overflow: auto; margin: .8rem 0; padding: .8rem; border: 1px solid var(--border, #353a42); border-radius: .55rem; background: var(--bg, #0d0f12); }
.stira-markdown pre code { padding: 0; background: transparent; font-size: .78rem; white-space: pre; }
.stira-markdown blockquote { margin: .8rem 0; padding: .15rem .85rem; border-left: 3px solid var(--accent, #79a676); color: var(--muted, #a4acb7); }
.stira-markdown ul, .stira-markdown ol { padding-inline-start: 1.35rem; }
.stira-markdown hr { border: 0; border-top: 1px solid var(--border, #353a42); margin: 1.2rem 0; }
@media (max-width: 620px) { .stira-leaf-overlay { padding: .45rem; } .stira-leaf-detail { width: 100%; max-height: 96vh; } .stira-prune-confirm { align-items: stretch; flex-direction: column; } .stira-prune-confirm-actions { display: grid; grid-template-columns: 1fr 1fr; width: 100%; } }
`
