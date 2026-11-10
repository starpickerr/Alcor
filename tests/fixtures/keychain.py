"""Real Keychain integration, isolated from the login keychain and its accounts."""
import ctypes as c
import sys

import pytest


@pytest.fixture(autouse=True)
def isolated_keychain(tmp_path, monkeypatch):
    if sys.platform != 'darwin':
        yield None
        return
    keychain = c.c_void_p()
    security = None
    foundation = None

    def factory(path):
        nonlocal security, foundation
        from core.security.keychain import MacOSKeychain, noninteractive
        if not keychain.value:
            security = c.CDLL('/System/Library/Frameworks/Security.framework/Security')
            foundation = c.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
            security.SecKeychainCreate.argtypes = [c.c_char_p, c.c_uint32, c.c_void_p, c.c_bool, c.c_void_p, c.POINTER(c.c_void_p)]
            security.SecKeychainCreate.restype = c.c_int32
            security.SecKeychainCopySearchList.argtypes = [c.POINTER(c.c_void_p)]
            security.SecKeychainSetSearchList.argtypes = [c.c_void_p]
            security.SecKeychainDelete.argtypes = [c.c_void_p]
            security.SecKeychainUnlock.argtypes = [c.c_void_p, c.c_uint32, c.c_void_p, c.c_bool]
            foundation.CFRelease.argtypes = [c.c_void_p]
            with noninteractive(security):
                previous = c.c_void_p()
                assert security.SecKeychainCopySearchList(c.byref(previous)) == 0
                try:
                    password = b'isolated-synthetic-keychain'
                    assert security.SecKeychainCreate(str(tmp_path / 'test.keychain-db').encode(), len(password),
                        password, False, None, c.byref(keychain)) == 0
                finally:
                    assert security.SecKeychainSetSearchList(previous) == 0
                    foundation.CFRelease(previous)
        return MacOSKeychain(str(path), _keychain=keychain)

    monkeypatch.setattr('core.security.env_store.keychain_for', factory, raising=False)
    try:
        yield factory
    finally:
        if keychain.value:
            from core.security.keychain import noninteractive
            with noninteractive(security):
                password = b'isolated-synthetic-keychain'
                assert security.SecKeychainUnlock(keychain, len(password), password, True) == 0
                assert security.SecKeychainDelete(keychain) == 0
                foundation.CFRelease(keychain)
