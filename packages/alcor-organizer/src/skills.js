import { fileURLToPath } from 'node:url'
import * as FileSystemSkills from '@deepseek-ai/dsh-skill-filesystem'

export function registerBundledSkills(ctx, providerName, directory) {
  ctx.inject(['skills'], inner => {
    inner.plugin(FileSystemSkills, {
      providerName, bundledSkillDir: fileURLToPath(directory), includeDefaultRoots: false, watch: false,
    })
  })
}
