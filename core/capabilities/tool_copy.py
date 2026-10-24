"""Conservative, bounded display copy; never rewrites a callable definition."""
import re

from core.capabilities.descriptions import text_for

_TECHNICAL = re.compile(
    r'[`{}\[\]=]|\b\w+_\w+\b|\w+\s*\([^)]*\)|https?://|'
    r'\b(?:json|schema|api|parameters?|arguments?|kwargs|returns?|enum|inputSchema|outputSchema)\b|'
    r'\b\w+\s*:\s*(?:string|integer|number|boolean|object|array)\b|'
    r'参数|调用|返回值|返回结构|字段|接口|示例代码', re.IGNORECASE)


def _sentences(value, lang):
    fenced = False
    for line in text_for(value, lang)[:32768].splitlines():
        if line.strip().startswith(('```', '~~~')):
            fenced = not fenced
            continue
        if fenced:
            continue
        line = re.sub(r'^\s*(?:[-*+#>]\s+|\d+[.)、]\s*)', '', line).strip()
        for sentence in re.split(r'(?<=[。！？!?])\s*|(?<=\.)\s+', line):
            sentence = sentence.strip()
            if 4 <= len(sentence) <= 180 and not sentence.endswith((':', '：')) and not _TECHNICAL.search(sentence):
                yield sentence


def user_tool_copy(summary, *details):
    """Reuse prose in provider order; no inferred operations or invented examples."""
    result = {'userSummary': {}, 'userDetails': {}}
    for lang in ('zh', 'en'):
        sentences = list(dict.fromkeys(_sentences(summary, lang)))
        for value in details:
            extra = [sentence for sentence in _sentences(value, lang) if sentence not in sentences]
            if extra:
                sentences.extend(dict.fromkeys(extra))
                break
        if sentences:
            result['userSummary'][lang] = sentences[0]
            if len(sentences) > 1:
                result['userDetails'][lang] = '\n'.join(sentences[1:3])
    return result
