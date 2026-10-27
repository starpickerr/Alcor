"""Exact-reference macOS credential access through Security.framework.

No command line secrets, subprocesses, credential enumeration or UI prompts.
Only trusted Core connectors import this module; it is not an Agent service.
"""
from contextlib import contextmanager
import ctypes as c
from hashlib import sha256
from pathlib import Path
import sys
from threading import RLock


_interaction_lock = RLock()


@contextmanager
def noninteractive(security):
    """SecItem's UI flag alone is ignored by macOS file-based keychains.

    Scope the legacy interaction flag to our serialized calls and restore it.
    A locked or inaccessible item returns an error; never ask for a password.
    """
    with _interaction_lock:
        security.SecKeychainGetUserInteractionAllowed.argtypes = [c.POINTER(c.c_bool)]
        security.SecKeychainGetUserInteractionAllowed.restype = c.c_int32
        security.SecKeychainSetUserInteractionAllowed.argtypes = [c.c_bool]
        security.SecKeychainSetUserInteractionAllowed.restype = c.c_int32
        previous = c.c_bool()
        MacOSKeychain._check(security.SecKeychainGetUserInteractionAllowed(c.byref(previous)))
        MacOSKeychain._check(security.SecKeychainSetUserInteractionAllowed(False))
        try:
            yield
        finally:
            MacOSKeychain._check(security.SecKeychainSetUserInteractionAllowed(previous.value))


class KeychainUnavailable(PermissionError):
    pass


class MacOSKeychain:
    def __init__(self, namespace, *, _keychain=None):
        if sys.platform != 'darwin':
            raise KeychainUnavailable('This credential reference requires macOS Keychain')
        self.service = 'com.stella.core.credentials.' + sha256(str(Path(namespace).resolve()).encode()).hexdigest()
        self.keychain = _keychain
        try:
            self.security = c.CDLL('/System/Library/Frameworks/Security.framework/Security')
            self.cf = c.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
        except OSError:
            raise KeychainUnavailable('macOS credential storage is unavailable') from None
        for name in ('SecItemAdd', 'SecItemCopyMatching'):
            fn = getattr(self.security, name)
            fn.argtypes, fn.restype = [c.c_void_p, c.POINTER(c.c_void_p)], c.c_int32
        self.security.SecItemDelete.argtypes, self.security.SecItemDelete.restype = [c.c_void_p], c.c_int32
        signatures = {
            'CFStringCreateWithCString': ([c.c_void_p, c.c_char_p, c.c_uint32], c.c_void_p),
            'CFDataCreate': ([c.c_void_p, c.c_void_p, c.c_long], c.c_void_p),
            'CFDictionaryCreate': ([c.c_void_p, c.c_void_p, c.c_void_p, c.c_long, c.c_void_p, c.c_void_p], c.c_void_p),
            'CFArrayCreate': ([c.c_void_p, c.c_void_p, c.c_long, c.c_void_p], c.c_void_p),
            'CFDataGetLength': ([c.c_void_p], c.c_long),
            'CFDataGetBytePtr': ([c.c_void_p], c.c_void_p),
            'CFRelease': ([c.c_void_p], None),
        }
        for name, (args, result) in signatures.items():
            fn = getattr(self.cf, name)
            fn.argtypes, fn.restype = args, result

    def _symbol(self, name):
        return c.c_void_p.in_dll(self.cf if name.startswith('kCF') else self.security, name)

    @contextmanager
    def _dictionary(self, fields):
        owned = []
        def convert(value):
            if isinstance(value, c.c_void_p):
                return value
            if isinstance(value, bool):
                return self._symbol('kCFBooleanTrue' if value else 'kCFBooleanFalse')
            if isinstance(value, str):
                pointer = self.cf.CFStringCreateWithCString(None, value.encode(), 0x08000100)
            elif isinstance(value, bytes):
                data = (c.c_ubyte * len(value)).from_buffer_copy(value)
                pointer = self.cf.CFDataCreate(None, data, len(value))
            elif isinstance(value, list):
                array = (c.c_void_p * len(value))(*value)
                pointer = self.cf.CFArrayCreate(None, array, len(value), None)
            else:
                raise TypeError('Unsupported Keychain attribute')
            if not pointer:
                raise KeychainUnavailable('Credential storage allocation failed')
            owned.append(pointer)
            return pointer
        try:
            keys = (c.c_void_p * len(fields))(*(self._symbol(key) for key in fields))
            values = (c.c_void_p * len(fields))(*(convert(value) for value in fields.values()))
            dictionary = self.cf.CFDictionaryCreate(None, keys, values, len(fields), None, None)
            if not dictionary:
                raise KeychainUnavailable('Credential storage allocation failed')
            owned.append(dictionary)
            with noninteractive(self.security):
                yield dictionary
        finally:
            for pointer in reversed(owned):
                self.cf.CFRelease(pointer)

    def _fields(self, account, *, adding=False):
        fields = {'kSecClass': self._symbol('kSecClassGenericPassword'),
                  'kSecAttrService': self.service, 'kSecAttrAccount': account,
                  'kSecUseAuthenticationUI': self._symbol('kSecUseAuthenticationUIFail')}
        if self.keychain is not None:
            fields['kSecUseKeychain' if adding else 'kSecMatchSearchList'] = self.keychain if adding else [self.keychain]
        return fields

    @staticmethod
    def _check(status):
        if status != 0:
            raise KeychainUnavailable(f'macOS credential storage unavailable (status {status}); existing configuration retained')

    def put(self, account, value):
        fields = {**self._fields(account, adding=True), 'kSecValueData': value.encode()}
        with self._dictionary(fields) as query:
            self._check(self.security.SecItemAdd(query, None))

    def get(self, account):
        fields = {**self._fields(account), 'kSecReturnData': True}
        result = c.c_void_p()
        try:
            with self._dictionary(fields) as query:
                status = self.security.SecItemCopyMatching(query, c.byref(result))
            if status == -25300:
                return None
            self._check(status)
            size = self.cf.CFDataGetLength(result)
            if not 0 <= size <= 1_048_576:
                raise KeychainUnavailable('Credential value is invalid')
            return c.string_at(self.cf.CFDataGetBytePtr(result), size).decode()
        finally:
            if result.value:
                self.cf.CFRelease(result)

    def contains(self, account):
        result = c.c_void_p()
        try:
            with self._dictionary({**self._fields(account), 'kSecReturnAttributes': True}) as query:
                status = self.security.SecItemCopyMatching(query, c.byref(result))
            if status == -25300:
                return False
            self._check(status)
            return True
        finally:
            if result.value:
                self.cf.CFRelease(result)

    def delete(self, account):
        with self._dictionary(self._fields(account)) as query:
            status = self.security.SecItemDelete(query)
        if status != -25300:
            self._check(status)
