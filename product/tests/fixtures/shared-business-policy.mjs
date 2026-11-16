// Only mounted in the disposable test Profile. Exercise the signed host's own executor
// against a session created by session/create that has never received a prompt.
export const inject = ['tools', 'agents']

export function apply(ctx) {
  ctx.tools.guard(call => {
    if (call.name === 'alcor_business' && call.arguments?.method === 'categories') return 'Synthetic host veto'
  })
  ctx.tools.register({
    name: 'qa_unprompted_session',
    description: 'Probe business access on the empty synthetic session',
    parameters: { session_id: { type: 'string', required: true } },
    output: { schema: { type: 'string' }, render: (_args, value) => [{ type: 'text', text: value }] },
    async execute({ session_id }, { signal }) {
      const agent = ctx.agents.get(session_id)
      if (!agent) throw new Error('Missing synthetic session')
      const batch = await ctx.tools.execute({ agent, signal, calls: [{
        type: 'tool-call', id: 'unprompted', name: 'alcor_business',
        arguments: JSON.stringify({ domain: 'tasks', method: 'list_todos' }),
      }] })
      const { result } = batch.results[0]
      if (!result.isError) throw new Error('Unprompted business read succeeded')
      return JSON.stringify(result)
    },
  })
}
