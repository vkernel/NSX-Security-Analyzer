"""Assembled application maps, bounded by snapshot and exact display filters."""
from time import perf_counter
from django.db import transaction
from . import analysis_index
from .analysis_index import LOG
from .models import SnapshotAnalysisIndex, SnapshotApplicationMap
from .naming import digest
from .segmentation import build

VERSION = 1
MAX_VARIANTS = 12


def context(query='',scope='',service='',include_disabled=False,hidden_paths=()):
    return {'query':query[:255], 'scope':scope[:255], 'service':service[:255],
            'include_disabled':bool(include_disabled), 'hidden_paths':sorted(set(p for p in hidden_paths if p))}


def compact(data):
    """Store topology once, without repeated node definitions or rule payloads."""
    graph=data['applications']
    nodes=[]
    for node in graph['nodes']:
        item={key:value for key,value in node.items() if key!='groups'}
        item['group_count']=len(node['groups'])
        item['groups']=[{key:g[key] for key in ('path','name','type')} for g in node['groups'][:50]]
        nodes.append(item)
    edges=[]
    for edge in graph['edges']:
        item={key:value for key,value in edge.items() if key not in ('source','destination','rules')}
        item.update(source=edge['source']['id'],destination=edge['destination']['id'],rule_paths=[r['path'] for r in edge['rules']])
        edges.append(item)
    graph={key:value for key,value in graph.items() if key not in ('nodes','edges')}
    graph.update(nodes=nodes,edges=edges)
    return dict(applications=graph, group_total=data['group_total'],
                hidden_groups=[{'path':g['path']} for g in data['hidden_groups']],
                workloads=data['workloads'],workloads_truncated=data['workloads_truncated'],
                warnings=data['warnings'],truncated=data['truncated'],rule_limit=data['rule_limit'])


def expand(payload,filters,stamp):
    graph=payload['applications']
    nodes={n['id']:n for n in graph['nodes']}
    for edge in graph['edges']:
        edge['source']=nodes[edge['source']]
        edge['destination']=nodes[edge['destination']]
        edge['rules']=[]  # Connection evidence is fetched only when opened.
    payload.update(map_context=filters,map_source_stamp=stamp.isoformat(),
                   group_catalog=[],groups=[],matrix=[],edges=[],graph_nodes=[],graph_edges=[],rules=[],priorities=[])
    return payload


def cached_map(snapshot,query='',scope='',service='',include_disabled=False,hidden_paths=()):
    filters=context(query,scope,service,include_disabled,hidden_paths)
    key=digest([VERSION,analysis_index.VERSION,filters])
    if snapshot.browsing_status!='ready':
        raise ValueError('A ready snapshot is required for an application map')
    started=perf_counter()
    projection=SnapshotAnalysisIndex.objects.filter(snapshot=snapshot).only('version','updated_at').first()
    if projection is None or projection.version!=analysis_index.VERSION:
        analysis_index.load(snapshot,'groups')
        projection=SnapshotAnalysisIndex.objects.only('version','updated_at').get(snapshot=snapshot)
    def existing():
        return SnapshotApplicationMap.objects.filter(projection=projection,cache_key=key,version=VERSION,source_stamp=projection.updated_at).values_list('payload',flat=True).first()
    payload=existing()
    hit=payload is not None
    if not hit:
        with transaction.atomic():
            projection=SnapshotAnalysisIndex.objects.select_for_update().only('version','updated_at').get(snapshot=snapshot)
            payload=existing()
            if payload is None:
                data=build(snapshot,**filters,application_mode=True)
                payload=compact(data)
                # Preparing missing projection columns may advance its timestamp.
                projection.refresh_from_db(fields=['updated_at'])
                SnapshotApplicationMap.objects.update_or_create(projection=projection,cache_key=key,
                    defaults={'version':VERSION,'source_stamp':projection.updated_at,'payload':payload})
                old=list(SnapshotApplicationMap.objects.filter(projection=projection).order_by('-created_at','-pk').values_list('pk',flat=True)[MAX_VARIANTS:])
                SnapshotApplicationMap.objects.filter(pk__in=old).delete()
    LOG.info('Assembled application map',extra={'details':{'snapshot':str(snapshot.pk),'cache_hit':hit,'duration_seconds':round(perf_counter()-started,4)}})
    return expand(payload,filters,projection.updated_at)
