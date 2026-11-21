import { createAccount, registerAccount } from './account.js'
import { registerProvider } from './provider.js'

export const inject = ['llm', 'credentials', 'typert']

export async function apply(ctx) {
  const account = createAccount(ctx)
  await registerAccount(ctx, account)
  registerProvider(ctx, account)
}
