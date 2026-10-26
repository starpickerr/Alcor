"""Report application protection separately from usable child-process sandboxes.

No environment variable, package trust flag or successful ES preflight can
attest that Core and the desktop process tree are actually protected.
"""
import sys


def application_isolation():
    return {
        'scope': 'application', 'status': 'unavailable', 'protected': False,
        'platform': sys.platform,
        'reason': 'Application-wide native protection is not installed and verified.',
        'code': 'application_guard_not_verified',
        'unprotected': ['core', 'desktop-main', 'desktop-helpers'],
    }


def require_application_isolation():
    report = application_isolation()
    if not report['protected']:
        raise PermissionError(report['reason'])
