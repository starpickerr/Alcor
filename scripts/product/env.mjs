/** Prefer the current developer setting while refusing contradictory legacy input. */
export function renamedEnv(current, legacy, environment = process.env) {
  const next = environment[current], old = environment[legacy]
  if (next && old && next !== old) throw new Error(`Conflicting ${current} and legacy setting`)
  return next || old
}
