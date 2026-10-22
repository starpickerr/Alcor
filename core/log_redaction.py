"""Side-effect-free redaction shared by the host and isolated plugin runner."""
import re
import json

_FIELDS = re.compile(r'''(?ix)(["']?(?:password|passwd|token|api[_-]?key|cookie|authorization|client[_-]?secret|access[_-]?token|refresh[_-]?token)["']?\s*[:=]\s*)("[^"\n]*"|'[^'\n]*'|[^\s,;}]+)''')
_BEARER = re.compile(r'(?i)\bBearer\s+[^\s,;"\']+')
_SK = re.compile(r'(?i)\bsk-[A-Za-z0-9_\-]{16,}')
_QUERY = re.compile(r'(?i)([?&](?:token|access_token|api_key|key|password|secret|code)=)[^\s&#"\']+')


def redact(message):
    message = str(message)
    # Decode structured messages before processing escaped quotes. Applying a
    # plain-text pattern to JSON first can redact only the first secret word.
    if message.lstrip().startswith('{'):
        try:
            row = json.loads(message)
            if isinstance(row, dict):
                def clean(value, key=''):
                    if re.fullmatch(r'(?i)password|passwd|token|api[_-]?key|cookie|authorization|client[_-]?secret|access[_-]?token|refresh[_-]?token', key):
                        return '[REDACTED]'
                    if isinstance(value, str):
                        return redact(value)
                    if isinstance(value, dict):
                        return {name: clean(item, name) for name, item in value.items()}
                    if isinstance(value, list):
                        return [clean(item) for item in value]
                    return value
                return json.dumps(clean(row), ensure_ascii=False)
        except (ValueError, RecursionError):
            pass
    message = _QUERY.sub(r'\1[REDACTED]', message)
    message = _BEARER.sub('Bearer [REDACTED]', message)
    message = _SK.sub('sk-[REDACTED]', message)
    return _FIELDS.sub(lambda match: match[1] + ('"[REDACTED]"' if match[2].startswith('"') else
                                               "'[REDACTED]'" if match[2].startswith("'") else '[REDACTED]'), message)


def redact_lines(text):
    """Preserve record boundaries when exporting a mix of JSON and text logs."""
    return ''.join(redact(line.rstrip('\r\n')) + ('\n' if line.endswith('\n') else '')
                   for line in text.splitlines(keepends=True))
