import {readFileSync} from 'node:fs'

import {qqConversationStyle} from '../../packages/alcor-secretary/src/qq-style.js'

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
    replace('- 长回复会自动分段发送，无需自行截断。', '- ' + qqConversationStyle)
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

    text="import {MessageBursts,admitGroup} from './group-participation.js';\n"+text
    replace("const segments = Array.isArray(event.message) ? event.message : undefined;", "const segments = Array.isArray(event.message) ? event.message : kind === 'group' ? parseCqString(String(event.raw_message ?? event.message ?? '')) : undefined;")
    replace("parseMessage, segmentText,", "parseMessage, parseCqString, segmentText,")
    replace('    seenMessages = new Map();','    seenMessages = new Map();\n    bursts = new MessageBursts();')
    replace('        let mentioned = detectMention', `        const slash = /^[/]/.test((inbound.segments || []).filter(s=>s.type==='text').map(s=>s.data?.text || '').join('').trimStart() || inbound.text.trimStart());
        const silent = () => this.ctx.config.alcorAutoReply === false || this.ctx.isSilenced?.(chatId);
        if (!slash && silent()) { this.bursts.finish(chatId,true); return; }
        if (!slash) await this.ctx.recordGroup?.(inbound);
        if (!slash && !this.ctx.bypassBurst?.(chatId) && await this.ctx.claimMessage?.(Object.freeze({kind,chatId,userId,groupId,text:inbound.text,messageId:inbound.messageId}))) { this.bursts.finish(chatId); return; }
        const plainBurst = !slash && inbound.text.trim() && !inbound.replyId && !inbound.forwardId && !inbound.media?.length &&
            !inbound.nonTextEvent && !this.ctx.bypassBurst?.(chatId) &&
            !(inbound.segments || []).some(s=>s.type !== 'text' && !(s.type === 'at' && String(s.data?.qq) === String(this.ctx.selfId())));
        if (plainBurst) {
            inbound = await this.bursts.take(inbound,()=>this.ctx.getChat(chatId)?.agent?.status==='running');
            if (!inbound || silent() || this.ctx.unavailable?.()) return;
        } else this.bursts.finish(chatId,slash && /(?:^|\\s)[/]silent(?:\\s|$)/i.test(inbound.text));
        if (kind === 'private' ? !dmAllowed(userId,this.ctx.policy) : !groupAllowed(groupId,this.ctx.policy)) return;
        let mentioned = detectMention`)
    replaceBlock("        let verifiedQuote;", "        // M3-D5 identity whitelist:", `        let verifiedQuote, ambient, contextualTurn=false;
        if (kind === 'group') {
            const admission = await admitGroup(this.ctx, inbound, detectExplicitMention(inbound.segments,inbound.raw,this.ctx.selfId(),this.ctx.config.botQQ));
            if (!admission || this.ctx.unavailable?.() || (!slash && silent()) || !groupAllowed(groupId,this.ctx.policy) || (admission.context && !this.ctx.policy.groupAllowFrom.includes(groupId))) {
                currentTrace()?.emit('mention',{ok:false,reason:TRACE_REASONS.inboundUnmentioned});
                return;
            }
            mentioned=admission.direct;
            ambient=admission.context;
            contextualTurn=!admission.direct;
            verifiedQuote=admission.quote;
        }
`)
    replace("if (this.ctx.isSilenced?.(chatId) === true)", "if (silent())")
    replace("        const identity = kind === 'group'", "        if (silent() || this.ctx.unavailable?.() || (kind === 'private' ? !dmAllowed(userId,this.ctx.policy) : !groupAllowed(groupId,this.ctx.policy))) return;\n        const identity = kind === 'group'")
    replace("if (inbound.replyId !== undefined) {", "if (inbound.replyId !== undefined && (kind !== 'group' || verifiedQuote)) {")
    replace("...(ambient ? { ambient: true } : {})", "...(ambient?.length ? { groupContext: true } : {}), ...(contextualTurn ? { ambient: true } : {})")

  }
  if (file === 'lib/client.js' || file === 'lib/settings-remote.js') replace('requireMention: true,', 'requireMention: false,')
  if(file === 'lib/client.js') {
    replace('{ key: "allowAllUsers", label: "允许所有用户", type: "boolean", hint: "关闭时仅管理员可触发。" }','{ key: "alcorAutoReply", label: "自动回复", type: "boolean", hint: "关闭后仍可使用管理命令。" }')
    replace('label: "私聊策略", type: "enum", options: ["open", "allowlist", "disabled"], optionLabels: { open: "开放"','label: "私聊策略", type: "enum", options: ["open", "allowlist", "disabled"], optionLabels: { open: "仅管理员"')
    replace('label: "群聊策略", type: "enum", options: ["open", "allowlist", "disabled"], optionLabels: { open: "开放"','label: "群聊策略", type: "enum", options: ["allowlist", "disabled", "open"], optionLabels: { open: "白名单（兼容）"')
    replace('每行一个群号，仅白名单策略生效。','允许自动响应的群号，每行一个。')
    replace('allowAllUsers: false,','alcorAutoReply: true,\n            allowAllUsers: false,')
    replace('allowAllUsers: vBoolean(),','alcorAutoReply: vBoolean(),\n            allowAllUsers: vBoolean(),')
    replace('allowAllUsers: vOptional(vBoolean()),','alcorAutoReply: vOptional(vBoolean()),\n            allowAllUsers: vOptional(vBoolean()),')
  }
  if(file === 'lib/settings-remote.js') {
    replace("permissions: ['requireMention',","permissions: ['alcorAutoReply','requireMention',")
    replace('allowAllUsers: false,','alcorAutoReply: true,\n    allowAllUsers: false,')
    replace("'requireMention', 'allowAllUsers', 'interimMessages'", "'alcorAutoReply', 'requireMention', 'allowAllUsers', 'interimMessages'")
  }
  if(file === 'lib/chat.js') {
    replace("    if (policy.dmPolicy === 'allowlist')\n        return policy.allowFrom.includes(userId);\n    return policy.allowAllUsers || policy.adminUsers.includes(userId);", "    return policy.adminUsers.includes(userId) || (policy.dmPolicy === 'allowlist' && policy.allowFrom.includes(userId));")
    replace("    if (policy.groupPolicy === 'allowlist')\n        return policy.groupAllowFrom.includes(groupId);\n    return true;", "    return policy.groupAllowFrom.includes(groupId);")
  }
  if(file === 'lib/commands.js') replace("    { name: 'silent',", "    { name: 'active', adminOnly: true, group: '操作', help: '解除当前会话的静音', handler: async(ctx,id)=>{ctx.setSilentUntil(id,undefined); await ctx.sendToChat(id,'已恢复当前会话。');} },\n    { name: 'silent',")
  if (file === 'lib/index.js') replace("export const inject = ['tools',", "export const inject = ['alcorSecretary', 'tools',")
  if (file === 'lib/index.js') {
    replace('open=仅管理员；allowlist=仅 allowFrom','open=仅管理员；allowlist=管理员及私聊白名单')
    replace('open=所有群；allowlist=仅 groupAllowFrom','open 与 allowlist 均限已登记的群')
    replace('groupPolicy=allowlist 时允许的群号','允许自动响应的群号')
    replace('放行所有用户（仅开发用；也可用 ONEBOT_ALLOW_ALL_USERS=true）','旧版兼容项；访问仍受管理员和白名单限制')
    replace('    ignoreSelf: z.boolean()', "    alcorAutoReply: z.boolean().default(true).description('自动处理普通 QQ 消息'),\n    ignoreSelf: z.boolean()")
    replace('            ignoreSelf: config.ignoreSelf,','            alcorAutoReply: config.alcorAutoReply,\n            ignoreSelf: config.ignoreSelf,')
    replace("requireMention: z.boolean().default(true)", "requireMention: z.boolean().default(false)")
    text = "import { installConversationInfo } from './alcor-conversation-info.js';\n" + text
    replace("    // Lifecycle: start the bridge", "    ctx.effect(() => installConversationInfo(ctx, connection, bridge, config, policy));\n    // Lifecycle: start the bridge")
  }
  if (file === 'lib/bridge.js') {
    replace("return { ...source, qqUserId: identity.qqUserId,", "return { ...source, ...(identity.groupContext ? {qqGroupContext:true}:{}), qqMessageId:identity.qqMessageId, qqUserId: identity.qqUserId,")
    replace("GroupParticipation, shouldJoinGroup", "GroupParticipation, chooseGroupReply, historyRows, topicOverlap")
    replace("new GroupParticipation((lines, signal) => shouldJoinGroup(deps.ctx, deps.defaultModel(), lines, signal))", `new GroupParticipation(
            (lines, signal, candidates, groupId, mode) => chooseGroupReply(deps.ctx,deps.defaultModel?.(),lines,signal,candidates,groupId,mode),
            {
                relevant:(groupId,query)=>{
                    if (!deps.policy.groupAllowFrom.includes(groupId) || !groupAllowed(groupId,deps.policy)) return 0;
                    const memory=deps.ctx.get('alcorSecretary')?.memory;
                    if (!memory) return 0;
                    const personality=memory.publicPersona();
                    const known=memory.storeForAudience('group:'+groupId).records().filter(row=>row.kind!=='recent').slice(-80);
                    return (topicOverlap(query,personality)?0.35:0) + (known.some(row=>topicOverlap(query,row.content))?0.5:0);
                },
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
    replace("if (connected)\n                this.outbound.drainPendingSends();", "if (connected) { this.participation.reconnected(); this.outbound.drainPendingSends(); } else this.inbound.bursts.clear();")
    replace("            // Group chat is text-only; never expand quoted IDs, forwards, or local media.", "            // Resolve same-group quote text inside admission. Media and forwards remain text-only.")
    replace("media: [], replyId: undefined, forwardId: undefined", "nonTextEvent:inbound.segments.some(s=>s.type!=='text' && s.type!=='at'), media: [], forwardId: undefined")
    replace("import { createGroupMemory } from 'dsh-lan-memory/group-memory';\n", '')
    replace("const ref = splitChatId(chatId);\n    if (ref.kind", "const ref = splitChatId(chatId);\n    if (ref.kind === 'private') return { ...source, qqUserId: ref.target };\n    if (ref.kind")
    replace("memory = createGroupMemory({ groupId, dataRoot: join(this.deps.config.mediaDir, 'group-memory'),\n                    llm: this.deps.ctx.llm, getDefaultModel: this.deps.defaultModel, logger });", "memory = this.deps.ctx.get('alcorSecretary').memory.group(groupId);")
    replace('            this.interim.onTurnEnd(chatId, chat);','            this.interim.onTurnEnd(chatId, chat);\n            this.inbound.bursts.release(chatId);')
    replace('        this.participation.stop();', '        this.participation.stop();\n        this.inbound.bursts.clear();')
    replace("this.interim = new InterimTracker({\n            sendToChat: (chatId, text, options) => this.sendToChat(chatId, text, options),","this.interim = new InterimTracker({\n            sendToChat: (chatId, text, options) => chatId.startsWith('group:') && text.trim() === '[沉默]' ? Promise.resolve([]) : this.sendToChat(chatId,text,options),")
    replace("            considerGroup: (inbound, direct) =>", "            bypassBurst: chatId=>!!currentInjectRound() || !!this.activeQuestionFor(chatId),\n            unavailable: ()=>this.stopping || !deps.connection.connected,\n            claimMessage: msg=>currentInjectRound() ? false : deps.ctx.serial('alcor/qq-message',msg).then(result=>result===true),\n            recordGroup: inbound => this.alcorRecordGroup?.(inbound),\n            personaName: () => this.deps.ctx.get('alcorSecretary')?.memory.personal.identity().name ?? 'Alcor',\n            considerGroup: (inbound, direct) =>")
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
    replace("text: '你是 Alcor，一个友善、自然、有分寸的 QQ 群聊伙伴。", "text: () => ctx.alcorSecretary.memory.publicPersona() + '\\n\\n" + qqConversationStyle + " 群聊里没有合适的话、或对方希望你安静时，可以整轮只输出 [沉默]。\\n当前在 QQ 群聊中。")
  }
  return text
}
export const adaptedFiles = ['lib/chat.js','lib/commands.js','lib/client.js','lib/settings-remote.js','lib/group-participation.js', 'lib/inbound.js', 'lib/index.js', 'lib/bridge.js', 'lib/group-authority.js', 'lib/group-chat.js', 'lib/outbound.js', 'lib/prompt.js']
