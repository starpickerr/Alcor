import React from 'react'
import CredentialEditor from '../../components/CredentialEditor'

export const modelSecretName = (field?: string | null): string => field === 'open_ai_api_key' ? 'OPENAI_API_KEY' : (field || '').toUpperCase()
export const credentialRoute = (name: string): string => `/models${name ? `?credential=${encodeURIComponent(name)}` : ''}`

/** The model configuration owns its credential editor and existing storage API. */
const ModelCredentialLink: React.FC<{ name: string; configured?: boolean | null; needsRepair?: boolean }> = props => <CredentialEditor {...props} />
export default ModelCredentialLink
