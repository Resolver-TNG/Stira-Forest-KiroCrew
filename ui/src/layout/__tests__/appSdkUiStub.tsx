import type { PropsWithChildren } from 'react'

export function Card({ children }: PropsWithChildren) {
  return <div data-testid="card">{children}</div>
}

export function CardTitle({ children }: PropsWithChildren) {
  return <h2>{children}</h2>
}
