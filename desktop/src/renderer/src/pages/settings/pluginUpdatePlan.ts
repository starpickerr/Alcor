import type apiClient from '../../api/client'
import { t } from '../../i18n'

export type UpdatePlan = {
  updates: { pluginId: string; fromVersion: string; toVersion: string }[]
  blocked: { pluginId: string; reason: string }[]
}

export async function checkPluginUpdates(client: typeof apiClient): Promise<UpdatePlan> {
  const data = await client.pluginRequest<UpdatePlan>('dual-update-plan')
  if (!Array.isArray(data.updates) || !Array.isArray(data.blocked)) throw new Error(t('settings_plugins_invalid_plan'))
  return data
}
