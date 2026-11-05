import { appendFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { defineTool } from '@deepseek-ai/dsh-tools'
export const inject = ['tools']
export function apply(ctx) {
  const record = value => appendFileSync(fileURLToPath(new URL('./reads.jsonl', import.meta.url)), JSON.stringify(value) + '\n')
  const output = { schema: { type: 'object', additionalProperties: true }, render: (_args, value) => [{ type: 'text', text: JSON.stringify(value) }] }
  ctx.tools.register(defineTool({ name: 'synthetic_briefing_sources', description: 'List synthetic briefing source metadata without reading content', parameters: {}, output,
    async execute() { record({ type: 'catalog' }); return { sources: [{ id: 'inbox', account_id: 'test-account', label: 'Synthetic plugin inbox' }, { id: 'private', account_id: 'test-account', label: 'Synthetic private source' }] } },
  }))
  ctx.tools.register(defineTool({ name: 'synthetic_briefing_fetch', description: 'Read selected synthetic briefing content', parameters: {
    source_id: { type: 'string', required: true }, account_id: { type: 'string', required: true }, since: { type: 'string', required: true }, until: { type: 'string', required: true }, offset: { type: 'number', required: true }, limit: { type: 'number', required: true },
  }, output, async execute(args) {
    record({ type: 'fetch', ...args })
    if (args.source_id !== 'inbox' || args.account_id !== 'test-account') throw new Error('Unselected source accessed')
    return { source_id: args.source_id, account_id: args.account_id, items: [{ source_id: `item-${args.offset}`, title: `Plugin item ${args.offset}`, occurred_at: new Date(Date.now() - 1000).toISOString(), snippet: 'Synthetic plugin deadline' }], next_offset: args.offset === 0 ? 1 : null }
  } }))
}
