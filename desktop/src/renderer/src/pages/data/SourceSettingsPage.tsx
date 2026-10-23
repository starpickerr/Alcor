import { Navigate, useLocation } from 'react-router-dom'
import { detailPath } from '../capabilities/presentation'

/** Legacy source bookmarks reuse the one capability configuration editor. */
export default function SourceSettingsPage({ owner }: { owner: 'email' | 'elearning' }) {
  const location = useLocation()
  return <Navigate to={detailPath('mcp', owner, 'configuration')} replace state={location.state || { from: '/data?section=sources', scroll: 0 }} />
}
