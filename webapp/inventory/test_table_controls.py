from types import SimpleNamespace
from django.test import SimpleTestCase, RequestFactory
from django.core.paginator import Paginator
from django.template.loader import render_to_string
from .preferences import table_page_size
from .templatetags.table_controls import table_footer

class TableControlTests(SimpleTestCase):
    def request(self, query=''):
        request=RequestFactory().get('/findings/'+query)
        request.session={}
        request._workspace_preferences=SimpleNamespace(page_size=20)
        return request

    def test_size_is_bounded_and_remembered_without_database_writes(self):
        request=self.request('?page_size=50')
        self.assertEqual(table_page_size(request),50)
        self.assertEqual(request.session['table_page_size'],50)
        request.GET={};self.assertEqual(table_page_size(request),50)
        request.GET={'page_size':'999999'};self.assertEqual(table_page_size(request),50)
        request.session={};self.assertEqual(table_page_size(request),20)

    def test_footer_preserves_filters_and_resets_page_when_size_changes(self):
        request=self.request('?q=hello%20world&owner=me&review=ready&page=2&gap_page=3&page_size=25')
        page=Paginator(list(range(80)),25).page(2)
        data=table_footer({'request':request},page,total=140)
        self.assertIn('owner=me',data['next']);self.assertIn('review=ready',data['next']);self.assertIn('page=3',data['next'])
        for item in data['sizes']:
            self.assertNotIn('&page=',item['url']);self.assertNotIn('gap_page=',item['url'])
            self.assertIn('q=hello+world',item['url'])
        html=render_to_string('components/table_footer.html',data)
        self.assertIn('Showing 26–50 of 80 matching results',html)
        self.assertIn('140 total in this view',html)

    def test_empty_and_single_page_keep_counts_and_size_controls(self):
        for rows,text in [([], '0 matching results'),([1,2], 'Showing 1–2 of 2 matching results')]:
            data=table_footer({'request':self.request()},Paginator(rows,25).page(1))
            html=render_to_string('components/table_footer.html',data)
            self.assertIn(text,html);self.assertIn('Rows per page',html)
            self.assertNotIn('total in this view',html)

    def test_secondary_paginator_preserves_primary_page(self):
        request=self.request('?page=2&gap_page=2&days=30')
        data=table_footer({'request':request},Paginator(range(80),25).page(2),'gap_page')
        self.assertIn('page=2',data['next']);self.assertIn('gap_page=3',data['next'])
