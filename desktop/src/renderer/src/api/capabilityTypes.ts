import type { LocalizedCopy } from '../types'
export type CapabilityKind = 'plugins' | 'mcp' | 'skills' | 'tools'
export type CapabilityCopy = string | LocalizedCopy
export interface CapabilityItem {
  kind: CapabilityKind; id: string; name: CapabilityCopy; description: CapabilityCopy
  retained?: boolean
  enabled: boolean; state: string; source: string; version: string | null
}
export interface CapabilityFeature {
  id: string; name: CapabilityCopy; description: CapabilityCopy; type?: string
  source?: 'mcp' | 'plugin'; available?: boolean | null; pageId?: string
  enabled?: boolean; toggleId?: string
  userSummary?: CapabilityCopy; userDetails?: CapabilityCopy
  detailDescription?: CapabilityCopy
  inputSchema?: Record<string, unknown>; outputSchema?: Record<string, unknown>
  target?: { kind: CapabilityKind; id: string }
}
export interface CapabilityDetail extends CapabilityItem {
  configuration?: { settingsUrl?: string }
  tabs: string[]; overview: CapabilityCopy; content?: string; reason?: CapabilityCopy | null
  technical?: { transport?: string; generation?: number; services?: string[] }
  features: CapabilityFeature[]; runtime: Record<string, string | number | string[]>
}
export interface CapabilityInventory { status: string; items: CapabilityItem[]; connectionSettings?: string[]; errors: Partial<Record<CapabilityKind, string>> }
