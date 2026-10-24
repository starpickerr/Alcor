import type { LocalizedCopy } from '../types'
export type CapabilityKind = 'plugins' | 'mcp' | 'skills' | 'tools'
export type CapabilityCopy = string | LocalizedCopy
export interface CapabilityItem {
  kind: CapabilityKind; id: string; name: CapabilityCopy; description: CapabilityCopy
  enabled: boolean; state: string; source: string; version: string | null
}
export interface CapabilityFeature {
  id: string; name: CapabilityCopy; description: CapabilityCopy; type?: string
  source?: 'mcp' | 'plugin'; available?: boolean | null; pageId?: string
  detailDescription?: CapabilityCopy
  inputSchema?: Record<string, unknown>; outputSchema?: Record<string, unknown>
  target?: { kind: CapabilityKind; id: string }
}
export interface CapabilityDetail extends CapabilityItem {
  tabs: string[]; overview: CapabilityCopy; content?: string; reason?: CapabilityCopy | null
  features: CapabilityFeature[]; runtime: Record<string, string | number | string[]>
}
export interface CapabilityInventory { status: string; items: CapabilityItem[]; errors: Partial<Record<CapabilityKind, string>> }
