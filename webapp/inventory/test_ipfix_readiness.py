from unittest.mock import Mock
from django.test import SimpleTestCase
from .collector import AuditError
from .ipfix.readiness import inspect_readiness


class ReadinessTests(SimpleTestCase):
    def test_failures_are_unknown_not_ready(self):
        api=Mock()
        api.get.side_effect=AuditError('offline')
        api.items.side_effect=AuditError('offline')
        plan={'profiles':[], 'operations':[], 'checks':[], 'group':'/infra/domains/default/groups/test'}
        rows=inspect_readiness(api,plan)
        self.assertEqual(len(rows),3)
        self.assertIn('unknown',rows[0]['result'])
        api.http.request.assert_not_called()

    def test_realization_and_members_do_not_claim_delivery(self):
        api=Mock()
        api.get.side_effect=[{'results':[{'state':'REALIZED','alarms':[]}]}, {'results':[{}]}, {'results':[]}]
        api.items.return_value=[{'ipfix_dfw_profile_path':'/infra/ipfix-dfw-profiles/p'}]
        plan={'profiles':[{'path':'/infra/ipfix-dfw-profiles/p'}], 'profile_mode':'new',
              'operations':[], 'checks':[], 'group':'/infra/domains/default/groups/test'}
        rows=inspect_readiness(api,plan)
        self.assertIn('REALIZED',rows[0]['result'])
        self.assertIn('host validation',rows[1]['result'])
        self.assertIn('not established',rows[2]['result'])
