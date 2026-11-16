export const briefingRunning = progress => ['starting', 'collecting', 'model'].includes(progress?.phase)

export function briefingProgressVisible(progress, date, revision) {
  return (briefingRunning(progress) || ['failed', 'cancelled', 'interrupted'].includes(progress?.phase))
    && !revision && (!date || date === progress.date)
}
