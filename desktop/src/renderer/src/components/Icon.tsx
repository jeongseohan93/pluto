import type { JSX } from 'react'

/**
 * Minimal stroke icons. Kept inline rather than pulled from an icon set: the
 * shell needs eight glyphs, and navigation must not fall back to emoji.
 */
const PATHS = {
  workspaces: 'M2 4h5v4H2zM9 4h5v8H9zM2 10h5v2H2z',
  graph:
    'M2.5 2.5h4v3h-4zM9.5 6.5h4v3h-4zM2.5 10.5h4v3h-4zM6.5 4h1.5a1.5 1.5 0 0 1 1.5 1.5v2.5M6.5 12h1.5a1.5 1.5 0 0 0 1.5-1.5V8',
  changes: 'M2.5 4.5h11M8 2v5M2.5 11.5h11',
  tests: 'M6 2v4L3 12a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1L10 6V2M5 2h6M5.5 8h5',
  telemetry: 'M2 12h12M4 12V8M7 12V4M10 12V9M13 12V6',
  settings: 'M2.5 4.5h11M2.5 11.5h11M6 2.5v4M10.5 9.5v4',
  chevron: 'M6 4l4 4-4 4',
  file: 'M4 2h5l3 3v9H4zM9 2v3h3',
  folder: 'M2 4h4l1.2 1.5H14V13H2z',
  fn: 'M5 13c1.6 0 2-1 2.2-2.4l1-6.2C8.4 3 9 2 10.6 2M4 6.6h5.4',
  test: 'M3 8a5 5 0 1 0 10 0A5 5 0 0 0 3 8zM6 8l1.5 1.6L10 6.6',
  play: 'M5 3l7 5-7 5z',
  split: 'M2 3h12v10H2zM8 3v10',
  search: 'M7 2.5a4.5 4.5 0 1 0 0 9 4.5 4.5 0 0 0 0-9zM10.4 10.4 14 14'
} as const

export type IconName = keyof typeof PATHS

export function Icon({
  name,
  size = 16,
  className = ''
}: {
  name: IconName
  size?: number
  className?: string
}): JSX.Element {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.2}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden="true"
    >
      <path d={PATHS[name]} />
    </svg>
  )
}
