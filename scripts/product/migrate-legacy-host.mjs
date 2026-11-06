import { readFile } from 'node:fs/promises'
import { pathToFileURL } from 'node:url'
import { createHash } from 'node:crypto'
import { Context } from '@deepseek-ai/cordis'
import Storage from '@deepseek-ai/dsh-storage'
import * as JsonStorage from '@deepseek-ai/dsh-storage-json'
import * as Domain from '@deepseek-ai/dsh-storage-domain'
import * as Jsonl from '@deepseek-ai/dsh-session-persistence-jsonl'
import Workspace from '@deepseek-ai/dsh-workspace'
import { scheduleDomain, createCronScheduleRecord } from '@deepseek-ai/dsh-schedule'
import { importHistory, legacySession } from './migrate-legacy-history.mjs'
import { recordSchema } from '../../packages/stella-organizer/src/records.js'
import { jobSchema } from '../../packages/stella-organizer/src/remote.js'
import { z } from 'zod'

export async function importHost(history, schedules, root, storageRoot, cwd) {
  const report = await importHistory(history, root, cwd)
  const ctx = new Context()
  try {
    await ctx.plugin(Storage)
    await ctx.plugin(JsonStorage, {root:storageRoot})
    await ctx.plugin(Domain, {backend:'json'})
    await ctx.plugin(Jsonl, Jsonl.Config({root}))
    await ctx.plugin(Workspace)
    const workspace = await ctx.workspaceRegistry.create(cwd)
    let pinned = 0
    for (const record of history) {
      const id = legacySession(record,cwd).header.id
      await workspace.attachSession(id)
      if (record.pinned) { await ctx.workspaceRegistry.pinSession(id); pinned++ }
    }
    const domain = await ctx.storageDomain.open(scheduleDomain)
    // Preserve the organizer's existing tables; only attach the imported schedule ids.
    const organizer = await ctx.storageDomain.open(Domain.defineDomain({name:'stella_organizer',version:1,tables:{records:Domain.domainTable(recordSchema),jobs:Domain.domainTable(jobSchema),schedules:Domain.domainTable(z.object({id:z.string()}).strict())}}))
    try {
      for (const old of schedules) {
        if (old.schedule.type !== 'cron' || old.action.type !== 'agent_task') throw new Error('Unsupported legacy schedule; keep offline copy')
        const id = 'legacy-stella-schedule-' + createHash('sha256').update(old.id).digest('hex').slice(0,24)
        if (!domain.table('tasks').get(id)) {
        const now = Date.now()
        await domain.table('tasks').put(id, {
          record:createCronScheduleRecord(id,old.action.task_description,{expression:old.schedule.expression,time_zone:'Asia/Shanghai'},now,old.name),
          target:{kind:'new_once',create:{cwd,agentPreset:'stella'}},
          status:'paused',involvedSessionIds:[],
          createdAt:new Date(old.created_at).toISOString(),updatedAt:new Date(now).toISOString(),
          deliveryHistory:{records:[],earlierRecordsUnavailable:true,earlierRecordsPruned:false},
        })
        }
        if (!organizer.table('schedules').get(id)) await organizer.table('schedules').put(id,{id})
      }
    } finally { await organizer.close(); await domain.close() }
    return {...report,pinned,schedules:schedules.length,imported_schedules_paused:true}
  } finally { await ctx.fiber.dispose() }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const [history,schedules,root,storageRoot,cwd] = process.argv.slice(2)
  if (!cwd) throw new Error('Usage: migrate-legacy-host.mjs <history.json> <schedules.json> <offline-session-root> <offline-storage-root> <workspace>')
  console.log(JSON.stringify(await importHost(JSON.parse(await readFile(history,'utf8')), JSON.parse(await readFile(schedules,'utf8')),root,storageRoot,cwd)))
}
