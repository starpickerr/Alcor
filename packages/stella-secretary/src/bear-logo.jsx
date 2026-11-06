import React from 'react'

// A solid silhouette with negative-space features, matching the native fish mark.
export function BearLogo({ size = 24, className = '' }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor" className={`stella-bear-logo ${className}`} aria-hidden="true">
    <path fillRule="evenodd" d="M5.1 3.1C2.8 2.4 1 4 1.3 6.2c.1 1.2.8 2.2 1.9 2.8A10 10 0 0 0 2 13.8C2 19 6.2 22 12 22s10-3 10-8.2A10 10 0 0 0 20.8 9c1.1-.6 1.8-1.6 1.9-2.8.3-2.2-1.5-3.8-3.8-3.1-1.1.3-1.8 1-2.2 1.9A12 12 0 0 0 12 4a12 12 0 0 0-4.7 1c-.4-.9-1.1-1.6-2.2-1.9ZM7.6 10.7a1.1 1.1 0 1 0 0 2.2 1.1 1.1 0 0 0 0-2.2Zm8.8 0a1.1 1.1 0 1 0 0 2.2 1.1 1.1 0 0 0 0-2.2ZM12 13c-2.4 0-4.4 1.6-4.4 3.6 0 2 2 3.4 4.4 3.4s4.4-1.4 4.4-3.4c0-2-2-3.6-4.4-3.6Z" />
    <path d="M10 15.5c0-.7.9-1 2-1s2 .3 2 1c0 .6-.7 1.2-1.4 1.5v1a.6.6 0 0 1-1.2 0v-1c-.7-.3-1.4-.9-1.4-1.5Z" />
  </svg>
}
