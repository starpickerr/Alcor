import { automationDeleteUnavailable } from '../../alcor-organizer/src/automation.js'
export { automationDeleteUnavailable }

export const isAutomationDeletion = execution => execution?.name === 'alcor_business'
  && execution.arguments?.domain === 'automation'
  && (execution.arguments.method === 'deleteInactive' || (execution.arguments.method === 'edit' && execution.arguments.arguments?.action === 'delete'))

// No public Schedule API proves pending or uncertain delivery is absent.
export function createAutomationDeleteGate() {
  return {
    preExecute: async (execution, next) => isAutomationDeletion(execution)
      ? { kind: 'deny', reason: automationDeleteUnavailable } : next(),
    execute: async () => { throw new Error(automationDeleteUnavailable) },
    release() {},
  }
}
