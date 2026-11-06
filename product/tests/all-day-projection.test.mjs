import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { BusinessWorker } from '../../packages/stella-organizer/src/business.js'
import { recordSchema } from '../../packages/stella-organizer/src/records.js'

test('all-day source intervals keep an exclusive end date when Apple ends at the last second', async () => {
  const root=await mkdtemp(join(tmpdir(),'stella-all-day-'))
  const worker=new BusinessWorker(root,{worker:new URL('../../packages/stella-secretary/business/worker.py',import.meta.url)})
  try {
    for (const [source_key,end_at] of [['inclusive','2026-01-01T15:59:59Z'],['exclusive','2026-01-01T16:00:00Z']]) {
      await worker.call('invoke',{domain:'calendar',method:'create_event',arguments:{title:'Synthetic',start_at:'2025-12-31T16:00:00Z',end_at,all_day:true,repeat_timezone:'Asia/Shanghai'}})
    }
    const records=await worker.call('records')
    assert.equal(records.length,2)
    for(const record of records){recordSchema.parse(record);assert.equal(record.start,'2026-01-01');assert.equal(record.end,'2026-01-02')}
  } finally {await worker.close();await rm(root,{recursive:true,force:true})}
})
