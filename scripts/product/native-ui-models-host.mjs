import { apply as upstreamApply } from './upstream-index.js'
export { Config } from './upstream-index.js'

export const inject = ['configEditor']

export function apply(ctx, config) {
  // Keep the owner's original onboarding choice when the bundle replaces its UI entry.
  const original = ctx.configEditor.configuration().find(row => row.entry.options.id === 'ui-settings-models')
  const credentialOnboarding = [original?.override.credentialOnboarding, original?.inherited.credentialOnboarding, config.credentialOnboarding]
    .find(value => typeof value === 'boolean')
  return upstreamApply(ctx, { ...config, credentialOnboarding })
}
