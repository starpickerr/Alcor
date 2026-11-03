"""Report application protection separately from usable child-process sandboxes.

No environment variable, package trust flag or successful ES preflight can
attest that Core and the desktop process tree are actually protected.
"""
import sys


def application_isolation():
    return {
        'scope': 'application', 'status': 'trusted-host', 'protected': False,
        'platform': sys.platform,
        'reason': 'Application hosts Core and Electron are trusted; OS isolation applies to managed child processes.',
        'code': 'trusted_host_boundary',
        'unprotected': ['core', 'desktop-main', 'desktop-helpers'],
    }


def require_application_isolation():
    report = application_isolation()
    if not report['protected']:
        raise PermissionError('Application-wide native isolation is not provided by Stella.')
