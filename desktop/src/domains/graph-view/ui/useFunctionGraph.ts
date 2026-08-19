/**
 * The Function DB index, loaded once and re-read on demand.
 *
 * Not polled. The graph changes when a build runs, and the two things that run
 * one — the [Rebuild] button and a finished `graph build` command — both call
 * `reload()` themselves. A 2s poll over 1335 rows would buy nothing and cost a
 * SQLite open every two seconds.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import type { GraphIndexResult } from '@domains/graph-view/types'

export interface GraphBinding {
  /** null until the first read comes back. */
  index: GraphIndexResult | null
  loading: boolean
  reload: () => void
}

/**
 * Read `.aidev/graph/graph.db` through the bridge, and expose a re-read.
 *
 * @flow  load on mount -> `reload` loads again, ignoring an answer that arrives
 *        after the component is gone
 * 주요 내부 변수: alive(언마운트 후 응답 폐기)
 */
export function useFunctionGraph(): GraphBinding {
  const [index, setIndex] = useState<GraphIndexResult | null>(null)
  const [loading, setLoading] = useState(true)
  const alive = useRef(true)

  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
    }
  }, [])

  const reload = useCallback(() => {
    setLoading(true)
    window.aidev.getGraphIndex().then((next) => {
      if (!alive.current) return
      setIndex(next)
      setLoading(false)
    })
  }, [])

  useEffect(() => {
    reload()
  }, [reload])

  return { index, loading, reload }
}
