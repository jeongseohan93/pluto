export function tokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`
  return String(n)
}

export function usd(n: number): string {
  return `$${n.toFixed(2)}`
}

export function fileName(path: string): string {
  const parts = path.split('/')
  return parts[parts.length - 1]
}

export function dirName(path: string): string {
  const parts = path.split('/')
  parts.pop()
  return parts.join('/')
}
