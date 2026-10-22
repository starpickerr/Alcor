"""Side-effect-free redaction shared by the host and isolated plugin runner."""
import re

_FIELDS = re.compile(r'''(?ix)(["']?(?:password|passwd|token|api[_-]?key|cookie|authorization|client[_-]?secret|access[_-]?token|refresh[_-]?token)["']?\s*[:=]\s*)("[^"\n]*"|'[^'\n]*'|[^\s,;}]+)''')
_BEARER = re.compile(r'(?i)\bBearer\s+[^\s,;"\']+')
_SK = re.compile(r'(?i)\bsk-[A-Za-z0-9_\-]{16,}')
_QUERY = re.compile(r'(?i)([?&](?:token|access_token|api_key|key|password|secret|code)=)[^\s&#"\']+')


def redact(message):
    message = _QUERY.sub(r'\1[REDACTED]', str(message))
    message = _BEARER.sub('Bearer [REDACTED]', message)
    message = _SK.sub('sk-[REDACTED]', message)
    return _FIELDS.sub(lambda match: match[1] + ('"[REDACTED]"' if match[2].startswith('"') else
                                               "'[REDACTED]'" if match[2].startswith("'") else '[REDACTED]'), message)
