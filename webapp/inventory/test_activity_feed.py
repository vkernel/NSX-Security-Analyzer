from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from .models import Environment, AuditEvent, DeletionBatch, DeletionItem


@override_settings(STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class ActivityFeedTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_superuser('activity-admin', first_name='Ada', last_name='Admin')
        self.env=Environment.objects.create(name='East',slug='activity-east',manager='https://east.example')
        self.client.force_login(self.user)
        self.url=reverse('activity')
        self.event=AuditEvent.objects.create(action='finding.approve',actor_id_text=str(self.user.pk),
            details={'environment_id':self.env.pk,'name':'Old group','path':'/infra/groups/old',
                     'from':'owner_review','to':'second_review','reason':'Approved for removal'})
        self.batch=DeletionBatch.objects.create(environment=self.env,manager=self.env.manager,requested_by=self.user,status='running',ticket='CHG-ACTIVITY')
        self.item=DeletionItem.objects.create(batch=self.batch,name='Deleted service',path='/infra/services/old',revision=1,status='deleted')
        AuditEvent.objects.create(action='nsx.deletion_deleted',target_type='DeletionItem',target_id=str(self.item.pk),
            details={'environment_id':self.env.pk,'batch':str(self.batch.pk),'name':self.item.name,'path':self.item.path,'ticket':self.batch.ticket})

    def test_timeline_includes_reviews_and_grouped_objects(self):
        response=self.client.get(self.url)
        self.assertContains(response,'First approval')
        self.assertContains(response,'Ada Admin')
        self.assertContains(response,'Deleted service')
        self.assertContains(response,'CHG-ACTIVITY')
        self.assertContains(response,'No configuration saved for this attempt')
        self.assertNotContains(response,reverse('deletion-configuration',args=[self.item.pk]))
        self.item.before={'resource_type':'Service','path':self.item.path}; self.item.save()
        self.assertContains(self.client.get(self.url),reverse('deletion-configuration',args=[self.item.pk]))
        self.assertContains(response,'View deletion request')
        self.assertNotContains(response,'>View details</a>')
        self.assertEqual(response.headers['Cache-Control'],'private, no-store')

    def test_audit_filters_and_pagination_preserve_retained_evidence(self):
        response=self.client.get(self.url,{'tab':'audit','actor':'Ada','q':'Old group','type':'approval','outcome':'second_review'})
        self.assertEqual(response.context['page'].paginator.count,1)
        self.assertContains(response,'Approved for removal')
        self.assertNotContains(response,'Deleted service')
        response=self.client.get(self.url,{'tab':'audit','q':'CHG-ACTIVITY','type':'deletion','outcome':'deleted'})
        self.assertEqual(response.context['page'].paginator.count,1)
        self.assertContains(response,reverse('deletion-detail',args=[self.batch.pk]))
        AuditEvent.objects.bulk_create([AuditEvent(action='finding.assign',details={'environment_id':self.env.pk}) for _ in range(51)])
        response=self.client.get(self.url,{'tab':'audit','type':'approval'})
        self.assertContains(response,'Page 1 of 2')
        self.assertIn('type=approval',response.context['query_string'])
        self.assertContains(self.client.get(self.url,{'tab':'audit','start':'invalid'}),'Enter a valid date')
        self.assertEqual(self.client.get(self.url,{'tab':'audit','environment':'9999'}).context['page'].paginator.count,0)

    def test_running_tasks_and_deletion_permissions(self):
        response=self.client.get(self.url,{'tab':'running'})
        self.assertContains(response,'data-poll="true"')
        self.assertContains(response,'Deleted service')
        self.assertNotContains(response,'First approval')
        self.batch.status='completed';self.batch.save()
        self.assertNotContains(self.client.get(self.url,{'tab':'running'}),'Deleted service')
        operator=get_user_model().objects.create_user('activity-operator',is_staff=True)
        self.client.force_login(operator)
        for tab in ('all','running','audit'):
            response=self.client.get(self.url,{'tab':tab})
            self.assertNotContains(response,'Deleted service')
            self.assertNotContains(response,'CHG-ACTIVITY')
        self.assertContains(self.client.get(self.url,{'tab':'audit'}),'First approval')
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code,302)
