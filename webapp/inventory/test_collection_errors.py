from types import SimpleNamespace
from django.template.loader import render_to_string
from django.test import SimpleTestCase
from .templatetags.collection_errors import collection_error


class CollectionErrorTests(SimpleTestCase):
    def test_known_failures_have_specific_guidance(self):
        cases = [('GET /search/query: incomplete/changing inventory (19511/19509)', 'inventory changed'), ('[Errno -2] Name or service not known', 'address could not be resolved'),
                 ('HTTP 401 Unauthorized', 'sign-in failed'),
                 ('HTTP 403 Forbidden', 'access was denied'),
                 ('certificate verify failed', 'certificate could not be verified'),
                 ('No route to host', 'Could not connect'),
                 ('The read operation timed out', 'timed out'),
                 ('HTTP 429 Too Many Requests', 'limiting requests'),
                 ('HTTP 500 Internal Server Error', 'server error')]
        for raw, expected in cases:
            with self.subTest(raw=raw):
                self.assertIn(expected, collection_error(raw)['title'])
        self.assertIn('VPN', collection_error('[Errno -2] Name or service not known')['action'])

    def test_technical_details_are_escaped_and_collapsed(self):
        raw = 'GET /infra/domains: <urlopen error [Errno -2] Name or service not known><script>alert(1)</script>'
        html = render_to_string('inventory/collection_error.html', {'job': SimpleNamespace(error=raw)})
        self.assertIn('NSX Manager address could not be resolved', html)
        self.assertIn('&lt;script&gt;', html)
        self.assertNotIn('<script>', html)
        self.assertNotIn('<details open', html)
        self.assertIn('<summary>', html)

    def test_missing_and_unrecognized_errors_remain_available(self):
        for error in ('', 'An unexpected error occurred'):
            html = render_to_string('inventory/collection_error.html', {'job': SimpleNamespace(error=error)})
            self.assertIn('Collection could not be completed', html)
            self.assertIn(error or 'No additional error detail was recorded.', html)
