"""Synthetic regression tests for AgentHub identity compatibility; no real profile."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from business.agent_hub.domain.service import AgentHubService
from business.agent_hub.domain.store import AgentHubStore
from business.agent_hub.entry import CONTRACT, SERVICE_DESCRIPTORS
from business.agent_hub.domain.providers import ADAPTERS


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='hub-identity-synthetic-')
        self.addCleanup(self.temp.cleanup)
        self.store = AgentHubStore(Path(self.temp.name) / 'hub.sqlite3')
        self.service = AgentHubService(self.store)

    def test_new_identity_and_legacy_input_alias(self):
        task = self.service.create_task({'title': 'Synthetic', 'goal': 'stella is literal text',
            'participants': [{'nativeAgentId': 'alcor'}, {'nativeAgentId': 'stella', 'displayName': 'Stella'}, {'nativeAgentId': 'dsh'}]}, 'req-new')
        self.assertEqual([(p['nativeAgentId'], p['agentId'], p['displayName']) for p in task['participants']],
            [('alcor', 'alcor', 'Alcor'), ('alcor', 'alcor', 'Stella'), ('dsh', 'dsh', '开发助手')])
        with self.store._connect() as db:
            stored = json.loads(db.execute('SELECT document_json FROM hub_tasks').fetchone()[0])
        self.assertEqual(stored['participants'], task['participants'])
        self.assertEqual(stored['goal'], 'stella is literal text')
        task = self.service.post_message(task['taskId'], 'Perform work', [task['participants'][0]['participantId']], 'task', task['revision'], 'new-work')
        claim = self.service.claim_step(task['taskId'], task['steps'][0]['stepId'])
        self.assertEqual(claim['snapshot']['nativeAgentId'], 'alcor')
        self.assertEqual(CONTRACT, 'alcor.agent-hub@1')
        self.assertEqual(SERVICE_DESCRIPTORS[0]['contract'], CONTRACT)
        for name, cls in ADAPTERS.items():
            self.assertEqual(cls().describe()['providerId'], f'alcor.agent-hub/{name}')

    def test_old_task_receipt_and_history_remain_usable(self):
        payload = {'title': 'Legacy Stella', 'goal': 'Preserve stella verbatim', 'participants': [{'nativeAgentId': 'alcor'}]}
        task = self.service.create_task(payload, 'legacy-request')
        person = task['participants'][0]
        person['nativeAgentId'] = person['agentId'] = 'stella'
        person['displayName'] = 'Stella'
        task['messages'].append({'messageId': 'historic', 'kind': 'user', 'body': 'stella in old text', 'recipientParticipantIds': [], 'stepId': None})
        document = json.dumps(task, ensure_ascii=False)
        with self.store._connect() as db:
            db.execute('UPDATE hub_tasks SET document_json=? WHERE task_id=?', (document, task['taskId']))
            db.execute('UPDATE hub_requests SET result_json=? WHERE request_id=?', (document, 'legacy-request'))
        self.assertEqual(self.service.get_task(task['taskId']), task)
        self.assertEqual(self.service.create_task(payload, 'legacy-request'), task)
        with self.store._connect() as db:
            self.assertEqual(db.execute('SELECT document_json FROM hub_tasks WHERE task_id=?', (task['taskId'],)).fetchone()[0], document)
        updated = self.service.post_message(task['taskId'], 'keep Stella prose', [person['participantId']], 'task', task['revision'], 'new-message')
        self.assertEqual(updated['participants'][0], person)
        self.assertEqual(updated['messages'][0]['body'], 'stella in old text')
        claim = self.service.claim_step(task['taskId'], updated['steps'][0]['stepId'])
        self.assertEqual((claim['snapshot']['nativeAgentId'], claim['snapshot']['agentId']), ('alcor', 'alcor'))
        self.assertIn('"agentId": "alcor"', claim['goal'])
        self.assertIn('Preserve stella verbatim', claim['goal'])
        self.assertEqual(self.service.get_task(task['taskId'])['participants'][0], person)


if __name__ == '__main__':
    unittest.main()
