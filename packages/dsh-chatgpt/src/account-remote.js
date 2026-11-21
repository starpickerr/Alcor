import { Service } from '@deepseek-ai/cordis'
import { Remote, TypertRemoteService } from '@deepseek-ai/dsh-typert-protocol'
import { methods, remote } from './account-contract.js'

export async function register(ctx, account) {
  class ChatgptRemote extends TypertRemoteService {
    static inject = ['typert']
    constructor(inner) { super(inner, 'alcorChatgpt', { namespace: 'alcorChatgpt' }) }
    [Service.init]() {
      this.ctx.typert.register({ package: '@alcor/chatgpt', face: 'host', schemas: [],
        invocations: remote.descriptors, model: { services: [], events: [], objects: [] } })
    }
    async invokeAccount(method, request) {
      const call = this.ctx.invocation
      if (call?.peer?.principal?.kind !== 'owner' || call.signal.aborted || call.peer.signal.aborted) {
        throw new Error('仅客户端所有者可管理 ChatGPT 账号。')
      }
      const input = methods[method][0].parse(request)
      return account[method](input)
    }
    @Remote accountState(input) { return this.invokeAccount('accountState', input) }
    @Remote beginLogin(input) { return this.invokeAccount('beginLogin', input) }
    @Remote cancelLogin(input) { return this.invokeAccount('cancelLogin', input) }
    @Remote signOut(input) { return this.invokeAccount('signOut', input) }
  }
  await ctx.plugin(ChatgptRemote).await()
}
