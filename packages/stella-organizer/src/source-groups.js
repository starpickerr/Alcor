export const briefingSourceGroup = source => source.source.startsWith('plugin:') ? source.source.split(':').slice(0, 2).join(':') : source.source
export const briefingGroupEnabled = (group, preferences, flags) => flags[group] ?? (['email', 'elearning'].includes(group) || preferences.some(source => briefingSourceGroup(source) === group))
