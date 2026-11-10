export function adaptQQ(file, input) {
  let text = input
  function replace(from, to) {
    if (text.split(from).length !== 2) throw Error('Unexpected QQ source in ' + file + ': ' + from.slice(0, 60))
    text = text.replace(from, to)
  }
  if (file === 'lib/group-participation.js') {
    replace("    let delta = '', block = '';", `    const signalWithTimeout=AbortSignal.any([signal,AbortSignal.timeout(25000)]);
    const config={provider:route.provider,model:route.model,maxTokens:1400,temperature:0};
    const prepared=await ctx.llm.prepareCall?.(config,signalWithTimeout,(controls,model)=>{
        const efforts=model.reasoning?.efforts ?? [];
        const effort=['off','low'].find(id=>efforts.some(row=>row.id===id));
        return effort?{...controls,reasoningEffort:effort}:controls;
    });
    const options={...(prepared?.config ?? config),messages:[{role:'user',content:[{type:'text',text:prompt}]}],signal:signalWithTimeout};
    let delta = '', block = '';`)
    replace("ctx.llm.stream({ provider: route.provider, model: route.model,\n        messages: [{ role: 'user', content: [{ type: 'text', text: prompt }] }],\n        maxTokens: 256, temperature: 0, signal: AbortSignal.any([signal, AbortSignal.timeout(20000)]) })", "(prepared?prepared.stream(options):ctx.llm.stream(options))")
  }
  if (file === 'lib/inbound.js') {
    replace('        nickname,\n    };','        nickname,\n        at:Number(event.time)*1000,\n    };')
    replace("{ qqUserId: userId, qqGroupId: groupId,", "{ qqMessageId: inbound.messageId, qqUserId: userId, qqGroupId: groupId,")
    replace("        let mentioned = detectMention", "        await this.ctx.recordGroup?.(inbound);\n        let mentioned = detectMention")
    replace("            if (!text || (!directAt && text.startsWith('/'))) return;", "            if ((!text && !directAt) || (!directAt && text.startsWith('/'))) return;")
  }
  if (file === 'lib/index.js') replace("export const inject = ['tools',", "export const inject = ['alcorSecretary', 'tools',")
  if (file === 'lib/index.js') {
    text = "import { installConversationInfo } from './alcor-conversation-info.js';\n" + text
    replace("    // Lifecycle: start the bridge", "    ctx.effect(() => installConversationInfo(ctx, connection, bridge, config, policy));\n    // Lifecycle: start the bridge")
  }
  if (file === 'lib/bridge.js') {
    replace("return { ...source, qqUserId: identity.qqUserId,", "return { ...source, qqMessageId:identity.qqMessageId, qqUserId: identity.qqUserId,")
    replace("import { createGroupMemory } from 'dsh-lan-memory/group-memory';\n", '')
    replace("const ref = splitChatId(chatId);\n    if (ref.kind", "const ref = splitChatId(chatId);\n    if (ref.kind === 'private') return { ...source, qqUserId: ref.target };\n    if (ref.kind")
    replace("memory = createGroupMemory({ groupId, dataRoot: join(this.deps.config.mediaDir, 'group-memory'),\n                    llm: this.deps.ctx.llm, getDefaultModel: this.deps.defaultModel, logger });", "memory = this.deps.ctx.get('alcorSecretary').memory.group(groupId);")
    replace("            considerGroup: (inbound, direct) =>", "            recordGroup: inbound => this.alcorRecordGroup?.(inbound),\n            considerGroup: (inbound, direct) =>")
    replace("reason.kind === 'blocked' && !deferredTracked", "reason.kind === 'blocked' && !this.deps.ctx.get('alcorSecretary').memory.desktopBlocked(session.id) && !deferredTracked")
    replace("const source = event.data.source;\n            if (source?.kind", `const source = event.data.source;
            if (source?.kind === 'plugin:dsh-onebot' && !chat.alcorLabelPending && (!chat.alcorLabelAt || Date.now() - chat.alcorLabelAt > 60000)) {
                chat.alcorLabelPending = true;
                const ref = splitChatId(chatId);
                const group = ref.kind === 'group';
                this.deps.connection.call(group ? 'get_group_info' : 'get_stranger_info', group ? {group_id:Number(ref.target)} : {user_id:Number(ref.target)})
                    .then(data => this.deps.ctx.get('alcorSecretary').productSpaces.recordQQLabel(session.id, group ? data.group_name : data.nickname))
                    .catch(() => undefined).finally(() => {chat.alcorLabelPending = false; chat.alcorLabelAt = Date.now();});
            }
            if (source?.kind`)
  }
  if (file === 'lib/group-authority.js') replace("['qq_group_remember', 'qq_group_recall']", "['qq_group_remember', 'qq_group_recall', 'qq_group_correct', 'qq_group_forget', 'qq_group_followup']")
  if (file === 'lib/group-chat.js') {
    replace("export const inject = ['tools', 'systemPrompt'];", "export const inject = ['alcorSecretary', 'tools', 'systemPrompt'];")
    replace('text: () => memory.contextText()', 'text: ({ agent }) => memory.contextText(agent)')
    replace('execute: args => memory.remember(args)', 'execute: (args, execution) => memory.remember(args, execution.agent)')
    replace('execute: args => memory.recall(args.query)', 'execute: (args, execution) => memory.recall(args.query, execution.agent)')
    replace("    const output = {", `    ctx.tools.register({
        name:'qq_group_followup',description:'主人开启主动跟进后，依据本群有效记忆安排后续交流。实际发送由本群独立投递授权控制。',
        parameters:{evidence:{type:'string',required:true},content:{type:'string',required:true},dueAt:{type:'string',required:true}},
        output:{schema:{type:'object'},render:(_args,value)=>[{type:'text',text:JSON.stringify(value)}]},
        execute:(args,execution)=>memory.followup(args,execution.agent),
    });
    for (const action of ['correct', 'forget']) ctx.tools.register({
        name: 'qq_group_' + action,
        description: action === 'correct' ? '按 ID 更正自己在本群的明确记忆。' : '按 ID 忘记自己在本群的明确记忆。',
        parameters: { id: { type: 'string', required: true }, ...(action === 'correct' ? { content: { type: 'string', required: true } } : {}) },
        output: { schema: { type: 'object' }, render: (_args, value) => [{ type: 'text', text: JSON.stringify(value) }] },
        execute: (args, execution) => memory.change(args, execution.agent),
    });
    const output = {`)
    replace("text: '你是 Alcor，一个友善、自然、有分寸的 QQ 群聊伙伴。", "text: () => ctx.alcorSecretary.memory.publicPersona() + '\\n\\n当前在 QQ 群聊中。")
  }
  return text
}
export const adaptedFiles = ['lib/group-participation.js', 'lib/inbound.js', 'lib/index.js', 'lib/bridge.js', 'lib/group-authority.js', 'lib/group-chat.js']
