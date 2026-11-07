import { readFile } from 'node:fs/promises'
import { join } from 'node:path'
import { resolveBundleDir } from '@deepseek-ai/dsh-app-boot'

export async function assertStellaDetached(profile) {
  if (!profile?.startedBundles.includes('@stella/secretary')) return
  const directory = resolveBundleDir('dsh', '@stella/secretary', profile.installAnchor, profile.dir)
  const manifest = JSON.parse(await readFile(join(directory, 'package.json'), 'utf8'))
  if (manifest.stella?.agentHubDetached !== true) throw new Error('请先将 Stella 更新到支持独立 AgentHub 的版本；协作数据尚未改动。')
}
