import {readFileSync} from 'node:fs'

export function adaptQQ(file, input) {
  let text = input
  function replace(from, to) {
    if (text.split(from).length !== 2) throw Error('Unexpected QQ source in ' + file + ': ' + from.slice(0, 60))
    text = text.replace(from, to)
  }
  function replaceBlock(start,end,replacement) {
    const from=text.indexOf(start), until=text.indexOf(end,from+start.length)
    if (from<0 || until<0) throw Error('Unexpected QQ source in '+file+': '+start)
    replace(text.slice(from,until),replacement)
  }
  if (file === 'lib/group-participation.js') return readFileSync(new URL('./qq-participation.mjs',import.meta.url),'utf8')
  if (file === 'lib/prompt.js') {
    replace('- 长回复会自动分段发送，无需自行截断。', '- 日常聊天按当前人格简短回应，一次只聊眼前的一两件事，不默认写长篇分析、列清单或追问。需要几条消息时，用空行分隔，每段表达一个完整意思；短回复保持一条即可。通道会依次发送并稍作停顿。用户要求详细解释或完整任务结果时，保留必要信息和代码、列表的完整性。')
  }
  if (file === 'lib/outbound.js') {
    text="import {setTimeout as pause} from 'node:timers/promises';\nimport {splitQQReply,replyDelay} from './alcor-replies.js';\n"+text
    replace('this.now = options.now ?? (() => Date.now());', 'this.now = options.now ?? (() => Date.now());\n        this.pause = options.pause ?? pause;')
    const start=text.indexOf('            let sentCard = false;')
    const end=text.indexOf("            this.ctx.trace?.(chatId, { stage: 'outbound', ok: true, reason: '回复已发送'",start)
    if(start<0 || end<0)throw Error('Unexpected QQ outbound text pipeline')
    const block=text.slice(start,end)
    replace(block,`            const parts = splitQQReply(clean);
            for (let index = 0; index < parts.length; index++) {
                if (index) await this.pause(replyDelay(parts[index - 1]));
                if (this.ctx.isStopping() || !this.ctx.connected()) throw new OneBotNotConnectedError();
                const part = parts[index];
                const partOptions = index === 0 ? options : { ...options, replyTo: undefined };
${block.replace(/\bclean\b/g,'part').replace(/\boptions\b/g,'partOptions')}            }
`)
  }
  if (file === 'lib/inbound.js') {
    replace('        nickname,\n    };','        nickname,\n        at:Number(event.time)*1000,\n    };')
    replace("{ qqUserId: userId, qqGroupId: groupId,", "{ qqMessageId: inbound.messageId, qqUserId: userId, qqGroupId: groupId,")
    replace("        let mentioned = detectMention", "        await this.ctx.recordGroup?.(inbound);\n        let mentioned = detectMention")
    text="import {admitGroup} from './group-participation.js';\n"+text
    replace("const segments = Array.isArray(event.message) ? event.message : undefined;", "const segments = Array.isArray(event.message) ? event.message : kind === 'group' ? parseCqString(String(event.raw_message ?? event.message ?? '')) : undefined;")
    replace("parseMessage, segmentText,", "parseMessage, parseCqString, segmentText,")
    replaceBlock("        let verifiedQuote;", "        // M3-D5 identity whitelist:", `        let verifiedQuote, ambient, contextualTurn=false;
        if (kind === 'group') {
            const admission = await admitGroup(this.ctx, inbound, detectExplicitMention(inbound.segments,inbound.raw,this.ctx.selfId(),this.ctx.config.botQQ));
            if (!admission || !groupAllowed(groupId,this.ctx.policy) || (admission.context && !this.ctx.policy.groupAllowFrom.includes(groupId))) {
                currentTrace()?.emit('mention',{ok:false,reason:TRACE_REASONS.inboundUnmentioned});
                return;
            }
            mentioned=admission.direct;
            ambient=admission.context;
            contextualTurn=!admission.direct;
            verifiedQuote=admission.quote;
        }
`)
    replace("if (inbound.replyId !== undefined) {", "if (inbound.replyId !== undefined && (kind !== 'group' || verifiedQuote)) {")
    replace("...(ambient ? { ambient: true } : {})", "...(ambient?.length ? { groupContext: true } : {}), ...(contextualTurn ? { ambient: true } : {})")

  }
  if (file === 'lib/client.js' || file === 'lib/settings-remote.js') replace('requireMention: true,', 'requireMention: false,')
  if (file === 'lib/index.js') replace("export const inject = ['tools',", "export const inject = ['alcorSecretary', 'tools',")
  if (file === 'lib/index.js') {
    replace("requireMention: z.boolean().default(true)", "requireMention: z.boolean().default(false)")
    text = "import { installConversationInfo } from './alcor-conversation-info.js';\n" + text
    replace("    // Lifecycle: start the bridge", "    ctx.effect(() => installConversationInfo(ctx, connection, bridge, config, policy));\n    // Lifecycle: start the bridge")
  }
  if (file === 'lib/bridge.js') {
    replace("return { ...source, qqUserId: identity.qqUserId,", "return { ...source, ...(identity.groupContext ? {qqGroupContext:true}:{}), qqMessageId:identity.qqMessageId, qqUserId: identity.qqUserId,")
    replace("GroupParticipation, shouldJoinGroup", "GroupParticipation, chooseGroupReply, historyRows")
    replace("new GroupParticipation((lines, signal) => shouldJoinGroup(deps.ctx, deps.defaultModel(), lines, signal))", `new GroupParticipation(
            (lines, signal, candidates, groupId, mode) => chooseGroupReply(deps.ctx,deps.defaultModel?.(),lines,signal,candidates,groupId,mode),
            {
                load:async groupId => {
                    const self=String(deps.connection.selfId || '');
                    if(!deps.connection.connected || !self || (deps.config.botQQ && self!==String(deps.config.botQQ)) ||
                       !deps.policy.groupAllowFrom.includes(groupId) || !groupAllowed(groupId,deps.policy)) return [];
                    const data=await deps.connection.call('get_group_msg_history',{group_id:Number(groupId),count:80,reverseOrder:true});
                    return historyRows(data,groupId,self);
                },
                report:message=>deps.log('warn',message),
            })`)
    replace("'followup from ' + chatId + ': ' + final.slice(0, 120)", "'followup from ' + chatId")
    replace("const ids = await this.outbound.sendToChat(chatId, text, options);", `const ids = await this.outbound.sendToChat(chatId, text, options);
        if (ids.length) {
            const sessionId = [...this.registry.bySession].find(([,key])=>key===chatId)?.[0];
            try { await this.deps.ctx.get('alcorSecretary')?.memory.noteQQReply?.(sessionId,text); }
            catch { this.deps.log('warn','消息已送达，上次回复时间暂未保存。'); }
        }`)
    replace("if (queueNotice !== null && !this.queueNoticeChats.has(chatId))", "if (!identity?.ambient && queueNotice !== null && !this.queueNoticeChats.has(chatId))")
    replace("if (connected)\n                this.outbound.drainPendingSends();", "if (connected) { this.participation.reconnected(); this.outbound.drainPendingSends(); }")
    replace("            // Group chat is text-only; never expand quoted IDs, forwards, or local media.", "            // Resolve same-group quote text inside admission. Media and forwards remain text-only.")
    replace("media: [], replyId: undefined, forwardId: undefined", "media: [], forwardId: undefined")
    replace("import { createGroupMemory } from 'dsh-lan-memory/group-memory';\n", '')
    replace("const ref = splitChatId(chatId);\n    if (ref.kind", "const ref = splitChatId(chatId);\n    if (ref.kind === 'private') return { ...source, qqUserId: ref.target };\n    if (ref.kind")
    replace("memory = createGroupMemory({ groupId, dataRoot: join(this.deps.config.mediaDir, 'group-memory'),\n                    llm: this.deps.ctx.llm, getDefaultModel: this.deps.defaultModel, logger });", "memory = this.deps.ctx.get('alcorSecretary').memory.group(groupId);")
    replace("            considerGroup: (inbound, direct) =>", "            recordGroup: inbound => this.alcorRecordGroup?.(inbound),\n            personaName: () => this.deps.ctx.get('alcorSecretary')?.memory.personal.identity().name ?? 'Alcor',\n            considerGroup: (inbound, direct) =>")
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
  if (file === 'lib/group-authority.js') replace("['qq_group_remember', 'qq_group_recall']", "['qq_group_remember', 'qq_group_recall', 'qq_group_correct', 'qq_group_forget', 'qq_group_followup', 'qq_group_history']")
  if (file === 'lib/group-chat.js') {
    replace("export const inject = ['tools', 'systemPrompt'];", "export const inject = ['alcorSecretary', 'tools', 'systemPrompt'];")
    replace('text: () => memory.contextText()', 'text: ({ agent }) => memory.contextText(agent)')
    replace('execute: args => memory.remember(args)', 'execute: (args, execution) => memory.remember(args, execution.agent)')
    replace('execute: args => memory.recall(args.query)', 'execute: (args, execution) => memory.recall(args.query, execution.agent)')
    replace("    const output = {", `    ctx.tools.register({
        name:'qq_group_history',description:'读取当前 QQ 群较早的真实消息，保留发言人和时间。首次省略 before，之后传返回的 nextBefore 继续向前翻页。query 可筛选本页。',
        parameters:{before:{type:'number'},query:{type:'string'}},
        output:{schema:{type:'object'},render:(_args,value)=>[{type:'text',text:JSON.stringify(value)}]},
        execute:(args,execution)=>memory.history(args,execution.agent),
    });
    ctx.tools.register({
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
export const adaptedFiles = ['lib/client.js','lib/settings-remote.js','lib/group-participation.js', 'lib/inbound.js', 'lib/index.js', 'lib/bridge.js', 'lib/group-authority.js', 'lib/group-chat.js', 'lib/outbound.js', 'lib/prompt.js']
