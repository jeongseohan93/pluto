import type { AidevBridge } from '../shared/ide'

declare global {
  interface Window {
    aidev: AidevBridge
  }
}
