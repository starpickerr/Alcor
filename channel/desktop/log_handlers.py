"""Authenticated log exploration. The file root is always host-owned."""
import json

from common.log_records import query_logs


class LogsEntriesHandler:
    def GET(self):
        from channel.desktop import desktop_channel as desktop
        desktop._require_auth()
        desktop.web.header('Content-Type', 'application/json; charset=utf-8')
        desktop.web.header('Cache-Control', 'no-store')
        params = desktop.web.input(q='', levels='', source='', since='', cursor='', limit='100')
        try:
            page = query_logs(desktop.get_data_root(), query=params.q, levels=params.levels, source=params.source,
                              since=params.since, cursor=params.cursor, limit=int(params.limit))
            return json.dumps({'status': 'success', **page}, ensure_ascii=False)
        except (ValueError, TypeError) as exc:
            return desktop._api_reject('Logs', exc)
        except OSError:
            return desktop._api_fail('Logs', 'Unable to read log files')
