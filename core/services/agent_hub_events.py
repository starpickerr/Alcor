"""Only documented final-message events become conversation replies."""
import json


def classify_output(provider, line):
    try:
        event = json.loads(line)
    except (ValueError, TypeError):
        return 'process', {'text': line[:65536]}
    if not isinstance(event, dict):
        return 'process', {'text': line[:65536]}
    if provider == 'codex-cli' and event.get('type') == 'item.completed':
        item = event.get('item') or {}
        if isinstance(item, dict) and item.get('type') == 'agent_message' and isinstance(item.get('text'), str):
            return 'reply_candidate', {'text': item['text'][:65536]}
    if provider in {'claude-code', 'cursor-cli'} and event.get('type') == 'result':
        if event.get('is_error') is not True and isinstance(event.get('result'), str):
            return 'reply_candidate', {'text': event['result'][:65536]}
    return 'process', {'text': line[:65536]}
