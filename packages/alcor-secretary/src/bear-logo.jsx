import React from 'react'
import avatar from './assets/alcor-avatar.svg'

export function BearLogo({ size = 24, className = '' }) {
  return <img src={avatar} width={size} height={size} className={`alcor-bear-logo ${className}`} alt="" aria-hidden="true" draggable={false} />
}
