import type { CodeGraph, GraphFileBox, GraphSymbol } from '@shared/ide'

export interface SymbolLocation {
  file: GraphFileBox
  symbol: GraphSymbol
  /** Index of the symbol within its file box, used to place graph rows. */
  row: number
}

export function findSymbol(graph: CodeGraph, symbolId: string | null): SymbolLocation | null {
  if (!symbolId) return null
  for (const file of graph.files) {
    const row = file.symbols.findIndex((s) => s.id === symbolId)
    if (row >= 0) return { file, symbol: file.symbols[row], row }
  }
  return null
}

export interface Relations {
  calls: SymbolLocation[]
  calledBy: SymbolLocation[]
  testedBy: SymbolLocation[]
}

export function relationsOf(graph: CodeGraph, symbolId: string): Relations {
  const calls: SymbolLocation[] = []
  const calledBy: SymbolLocation[] = []
  const testedBy: SymbolLocation[] = []

  for (const edge of graph.edges) {
    if (edge.from === symbolId) {
      const target = findSymbol(graph, edge.to)
      if (!target) continue
      if (edge.kind === 'tested-by') testedBy.push(target)
      else calls.push(target)
    } else if (edge.to === symbolId) {
      const source = findSymbol(graph, edge.from)
      if (!source) continue
      if (edge.kind === 'calls') calledBy.push(source)
    }
  }

  return { calls, calledBy, testedBy }
}
