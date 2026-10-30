from datetime import timedelta
from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from .models import Environment, Snapshot, SnapshotRecord, SegmentationReview
from .segmentation import build

@override_settings(STORAGES={'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class SegmentationTests(TestCase):
    def setUp(self):
        self.env=Environment.objects.create(name='Rotterdam',slug='seg-test',manager='https://nsx.example')
        self.snapshot=Snapshot.objects.create(environment=self.env,generated_at=timezone.now(),report={})
        self.user=get_user_model().objects.create_superuser('seg-admin',password='test-only')
        self.client.force_login(self.user)
        self.ordinal=0
        for name in ['Web','App','Data']:
            self.row({'name':name,'path':'/groups/'+name,'kind':'group','configuration':{'expression':[]}})
        self.rule=self.row({'name':'Allow SQL','path':'/rules/sql','configuration':{'rule':{'action':'ALLOW','source_groups':['/groups/App'],'destination_groups':['/groups/Data'],'services':['TCP1433']}}})
        self.url=reverse('segmentation',args=[self.env.pk])+'?snapshot='+str(self.snapshot.pk)
    def row(self,data,view='inventory'):
        self.ordinal+=1
        return SnapshotRecord.objects.create(snapshot=self.snapshot,ordinal=self.ordinal,view=view,name=data['name'],sort_name=data['name'],data=data,compact={},columns=[],sort_values={},search_basic=data['name'],search_evidence='')
    def test_direct_edges_any_negation_disabled_and_same_group(self):
        result=build(self.snapshot)
        self.assertEqual(len(result['edges']),1)
        self.assertEqual(result['edges'][0]['label'],'Allow rule found')
        self.row({'name':'Broad','path':'/rules/broad','configuration':{'rule':{'action':'ALLOW','source_groups':['ANY'],'destination_groups':['/groups/Data']}}})
        self.row({'name':'Negated','path':'/rules/negative','configuration':{'rule':{'action':'DROP','sources_excluded':True,'source_groups':['/groups/App'],'destination_groups':['/groups/Data']}}})
        self.row({'name':'Disabled','path':'/rules/off','configuration':{'rule':{'action':'DROP','disabled':True,'source_groups':['/groups/App'],'destination_groups':['/groups/Data']}}})
        result=build(self.snapshot)
        self.assertEqual(len(result['edges']),2)
        self.assertTrue(any(e['source']['path']=='ANY' for e in result['edges']))
        self.assertFalse(any(r['name']=='Disabled' for r in result['rules']))
        self.assertTrue(any('Negated endpoints' in str(r['reasons']) for r in result['priorities']))
    def test_snapshot_pinned_and_all_tabs_render(self):
        response=self.client.get(reverse('segmentation',args=[self.env.pk]))
        self.assertIn('snapshot=',response.url)
        for tab in ['matrix','map','priorities','changes','settings']:
            self.assertContains(self.client.get(self.url+'&tab='+tab),'Workload segmentation')
        self.assertContains(self.client.get(self.url+'&rule=/rules/sql'),'Open saved rule evidence')
    def test_permission_and_exception_validation(self):
        self.client.force_login(get_user_model().objects.create_user('reader'))
        self.assertEqual(self.client.post(self.url,{'action':'settings','scope':'Application'}).status_code,403)
        self.client.force_login(self.user)
        fingerprint=build(self.snapshot)['rules'][0]['fingerprint']
        url=self.url+'&rule=/rules/sql'
        self.client.post(url,{'action':'review','fingerprint':fingerprint,'state':'exception','note':'','expires_at':''})
        self.assertEqual(SegmentationReview.objects.count(),0)
        self.client.post(url,{'action':'review','fingerprint':fingerprint,'state':'exception','note':'Approved maintenance','expires_at':(timezone.now()+timedelta(days=7)).date().isoformat()})
        self.assertEqual(SegmentationReview.objects.count(),1)
        self.rule.data['configuration']['rule']['services']=['ANY'];self.rule.save()
        self.assertTrue(self.client.get(url).context['selected']['review_stale'])
    def test_wrong_environment_and_archived_snapshot(self):
        env=Environment.objects.create(name='Other',slug='other-seg',manager='https://other.example')
        self.assertEqual(self.client.get(reverse('segmentation',args=[env.pk])+'?snapshot='+str(self.snapshot.pk)).status_code,404)
        self.snapshot.browsing_status='archived';self.snapshot.save()
        self.assertContains(self.client.get(self.url),'Browsing evidence is archived')
    def test_boundary_and_csv(self):
        self.client.post(self.url,{'action':'boundary','name':'App isolation','source':'/groups/App','destination':'/groups/Data'})
        response=self.client.get(self.url+'&tab=priorities')
        self.assertEqual(len(response.context['deviations'][0]['rules']),1)
        self.assertContains(self.client.get(self.url+'&export=csv'),'Allow SQL')
    def test_limits_hidden_groups_and_service_definitions(self):
        self.row({'name':'SQL Server','kind':'service','path':'TCP1433','configuration':{'service_entries':[{'destination_ports':['1433'],'l4_protocol':'TCP'}]}})
        self.assertEqual(len(build(self.snapshot,service='1433')['rules']),1)
        self.assertEqual(len(build(self.snapshot,service='SQL Server')['rules']),1)
        hidden=build(self.snapshot,hidden_paths=['/groups/App'])
        self.assertEqual(len(hidden['hidden_groups']),1)
        self.assertFalse(hidden['edges'])
        for i in range(15): self.row({'name':str(i),'kind':'group','path':'/more/'+str(i),'configuration':{'expression':[]}})
        result=build(self.snapshot)
        self.assertTrue(result['truncated'])
        self.assertLessEqual(len(result['groups']),13)
    def test_new_snapshot_notification_and_invalid_snapshot(self):
        self.assertEqual(self.client.get(reverse('segmentation',args=[self.env.pk])+'?snapshot=invalid').status_code,404)
        newer=Snapshot.objects.create(environment=self.env,generated_at=timezone.now(),report={})
        status=self.client.get(reverse('segmentation',args=[self.env.pk])+'?check=1').json()
        self.assertEqual(status['latest'],str(newer.pk))
        self.assertEqual(self.client.get(self.url).context['snapshot'].pk,self.snapshot.pk)
    def test_personal_saved_views_and_visible_validation_errors(self):
        from .models import SegmentationSavedView
        self.client.post(self.url+'&q=App',{'action':'save_view','name':'App access'})
        saved=SegmentationSavedView.objects.get(user=self.user)
        self.assertEqual(saved.filters['q'],'App')
        other=get_user_model().objects.create_user('another-reader')
        self.client.force_login(other)
        self.assertEqual(self.client.get(self.url).context['saved_views'],[])
        self.client.post(self.url,{'action':'delete_view','view_id':saved.pk})
        self.assertTrue(SegmentationSavedView.objects.filter(pk=saved.pk).exists())
        self.client.force_login(self.user)
        response=self.client.post(self.url+'&rule=/rules/sql',{'action':'review','fingerprint':build(self.snapshot)['rules'][0]['fingerprint'],'state':'exception','note':''})
        self.assertContains(response,'An exception requires a reason and a future expiry date.')
        self.assertContains(response,'Save review')
