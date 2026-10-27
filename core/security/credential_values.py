"""One verified reference transaction for declared credential file adapters."""
from hashlib import sha256
import hmac
import re
import secrets
import sys

from common.log import logger
from core.security.keychain import KeychainUnavailable

REFERENCE = 'stella-keychain:v1:'


def account(name, reference):
    version = reference.removeprefix(REFERENCE)
    if not re.fullmatch(r'[a-f0-9]{32}', version):
        raise KeychainUnavailable('Credential reference is invalid; restore it through its owning settings')
    return sha256(name.encode()).hexdigest() + ':' + version


def resolve(path, values, backend_for, *, strict=True):
    decoded, states = {}, {}
    backend = None
    for name, value in values.items():
        if not value.startswith(REFERENCE):
            decoded[name], states[name] = value, bool(value)
            continue
        try:
            backend = backend or backend_for(path)
            result = backend.get(account(name, value))
            states[name] = result is not None
            if result is None:
                raise KeychainUnavailable('Saved credential is unavailable; restore it through its owning settings')
            decoded[name] = result
        except (OSError, PermissionError):
            if strict:
                raise
            decoded[name] = ''
            states.setdefault(name, None)
    return decoded, states


def statuses(path, values, backend_for):
    result = {}
    for name, value in values.items():
        if not value.startswith(REFERENCE):
            result[name] = bool(value)
            continue
        try:
            result[name] = backend_for(path).contains(account(name, value))
        except (OSError, PermissionError):
            result[name] = None
    return result


def publish(path, values, before, backend_for, commit):
    """Caller holds its file lock. Keep both versions on uncertain publication."""
    secure = sys.platform == 'darwin' or any(value.startswith(REFERENCE) for value in before.values())
    backend = None
    saved, created = {}, []
    publication_started = False
    try:
        for name, value in values.items():
            if value.startswith(REFERENCE):
                if value != before.get(name):
                    raise KeychainUnavailable('Credential reference cannot be reassigned; use its owning settings')
                saved[name] = value
            elif not secure or not value:
                saved[name] = value
            else:
                backend = backend or backend_for(path)
                reference = REFERENCE + secrets.token_hex(16)
                identifier = account(name, reference)
                backend.put(identifier, value)
                created.append(identifier)
                verified = backend.get(identifier)
                if verified is None or not hmac.compare_digest(verified.encode(), value.encode()):
                    raise KeychainUnavailable('Credential verification failed; existing configuration retained')
                saved[name] = reference
        publication_started = True
        commit(saved)
    except BaseException:
        for identifier in ([] if publication_started else created):
            try:
                backend.delete(identifier)
            except (OSError, PermissionError):
                logger.warning('[Credentials] Unpublished encrypted entry cleanup pending')
        raise
    for name, reference in before.items():
        if reference.startswith(REFERENCE) and reference != saved.get(name):
            try:
                backend = backend or backend_for(path)
                backend.delete(account(name, reference))
            except (OSError, PermissionError):
                logger.warning('[Credentials] Retired encrypted entry cleanup pending')
    return saved
