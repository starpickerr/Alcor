import { execFile } from 'node:child_process'
import { promisify } from 'node:util'

const execute = promisify(execFile)
async function notify(title) {
  if (process.platform === 'darwin') {
    await execute('/usr/bin/osascript', ['-e', 'on run argv\ndisplay notification (item 1 of argv) with title "Stella 事务提醒"\nend run', title], { timeout: 10000 })
  } else if (process.platform === 'linux') {
    await execute('notify-send', ['--', 'Stella 事务提醒', title], { timeout: 10000 })
  } else throw new Error('当前系统尚未配置桌面提醒适配器。')
}

export class ReminderDelivery {
  constructor(worker, { send = notify, diagnostic = () => {} } = {}) {
    this.worker = worker
    this.send = send
    this.diagnostic = diagnostic
    this.closed = false
  }

  tick() {
    if (this.closed) return Promise.resolve()
    if (this.pending) return this.pending
    this.pending = this.deliver().catch(error => this.diagnostic('提醒投递失败：' + error.message)).finally(() => { this.pending = undefined })
    return this.pending
  }

  async deliver() {
    for (const reminder of await this.worker.call('due_reminders')) {
      if (this.closed) break
      let error
      try { await this.send(reminder.title) }
      catch (failure) { error = failure.message }
      await this.worker.call('invoke', { domain: 'tasks', method: 'acknowledge_desktop_reminder', arguments: {
        delivery_id: reminder.delivery_id, claim_id: reminder.claim_id, shown: !error, ...(error ? { error } : {}),
      } })
      if (error) this.diagnostic('提醒尚未送达：' + error)
    }
  }

  async close() { this.closed = true; await this.pending }
}
