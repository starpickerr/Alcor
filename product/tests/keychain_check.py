"""Exercise the native provider in a temporary Keychain, with synthetic values only."""
import ctypes
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]


def main():
    assert sys.platform == 'darwin', 'This check requires macOS Security.framework'
    assert (ROOT / 'packages/stella-privacy/native/keychain-bridge').exists(), 'Build the privacy package first'
    security = ctypes.CDLL('/System/Library/Frameworks/Security.framework/Security')
    foundation = ctypes.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
    signatures = {
        'SecKeychainCreate': [ctypes.c_char_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_bool, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)],
        'SecKeychainCopySearchList': [ctypes.POINTER(ctypes.c_void_p)],
        'SecKeychainSetSearchList': [ctypes.c_void_p],
        'SecKeychainDelete': [ctypes.c_void_p],
        'SecKeychainLock': [ctypes.c_void_p],
        'SecKeychainUnlock': [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_bool],
        'SecKeychainSetUserInteractionAllowed': [ctypes.c_bool],
        'SecKeychainGetUserInteractionAllowed': [ctypes.POINTER(ctypes.c_bool)],
    }
    for name, arguments in signatures.items():
        getattr(security, name).argtypes = arguments
        getattr(security, name).restype = ctypes.c_int32
    foundation.CFRelease.argtypes = [ctypes.c_void_p]
    interaction = ctypes.c_bool()
    assert security.SecKeychainGetUserInteractionAllowed(ctypes.byref(interaction)) == 0
    assert security.SecKeychainSetUserInteractionAllowed(False) == 0
    previous = ctypes.c_void_p()
    keychain = ctypes.c_void_p()
    password = b'isolated-stella-dsh-synthetic-keychain'
    temporary = tempfile.TemporaryDirectory(prefix='stella-keychain-check-')
    try:
        home = Path(temporary.name).resolve()
        keychain_path = home / 'synthetic.keychain-db'
        assert security.SecKeychainCopySearchList(ctypes.byref(previous)) == 0
        try:
            assert security.SecKeychainCreate(str(keychain_path).encode(), len(password), password, False, None, ctypes.byref(keychain)) == 0
        finally:
            assert security.SecKeychainSetSearchList(previous) == 0
        test_home = home / 'dsh-homes'
        test_home.mkdir()
        environment = {key: value for key, value in os.environ.items() if key in ['PATH', 'TMPDIR', 'LANG']}
        environment.update(HOME=str(home), STELLA_TEST_KEYCHAIN=str(keychain_path), STELLA_TEST_HOME=str(test_home))
        subprocess.run(['node', '--test', 'product/tests/keychain-check.mjs'], cwd=ROOT, env=environment, check=True, timeout=90)
        assert security.SecKeychainLock(keychain) == 0
        subprocess.run(['node', 'product/tests/keychain-worker.mjs', 'locked', str(test_home / 'lock-case'), str(keychain_path)],
            cwd=ROOT, env=environment, check=True, timeout=30)
        assert security.SecKeychainUnlock(keychain, len(password), password, True) == 0
        subprocess.run(['node', 'product/tests/keychain-worker.mjs', 'unlocked', str(test_home / 'lock-case'), str(keychain_path)],
            cwd=ROOT, env=environment, check=True, timeout=30)
        if '--host' in sys.argv:
            from keychain_host_check import check
            os.environ['STELLA_TEST_KEYCHAIN'] = str(keychain_path)
            try:
                check(home / 'stock-host', keychain, security)
            finally:
                del os.environ['STELLA_TEST_KEYCHAIN']
    finally:
        try:
            if keychain.value:
                security.SecKeychainUnlock(keychain, len(password), password, True)
                assert security.SecKeychainDelete(keychain) == 0
                foundation.CFRelease(keychain)
        finally:
            if previous.value:
                assert security.SecKeychainSetSearchList(previous) == 0
                foundation.CFRelease(previous)
            assert security.SecKeychainSetUserInteractionAllowed(interaction.value) == 0
            temporary.cleanup()
    print('PASS: native Keychain reference/record contract, rotation, cross-process refresh, real lock/unlock refusal and recovery; synthetic temporary Keychain only')


if __name__ == '__main__':
    main()
