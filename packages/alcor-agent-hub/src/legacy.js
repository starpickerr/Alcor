import { readFile } from 'node:fs/promises'
import { join } from 'node:path'
import { resolveBundleDir } from '@deepseek-ai/dsh-app-boot'

export async function assertLegacySecretaryDetached(profile) {
  for (const name of ['@stella/secretary', '@alcor/secretary']) {
    if (!profile?.startedBundles.includes(name)) continue
    const directory = resolveBundleDir('dsh', name, profile.installAnchor, profile.dir)
    const manifest = JSON.parse(await readFile(join(directory, 'package.json'), 'utf8'))
    const metadata = name.startsWith('@stella/') ? manifest.stella : manifest.alcor
    if (metadata?.agentHubDetached !== true) throw new Error('请先将 Stella 或 Alcor 更新到支持独立 AgentHub 的版本；协作数据尚未改动。')
  }
}
