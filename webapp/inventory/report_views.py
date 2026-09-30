"""Read small, immutable slices of a published snapshot; never load its JSONB report."""
import csv
import io
import json
import logging
import re
from functools import reduce
from operator import and_, or_

from django.contrib.auth.decorators import login_required
from django.db import connection, transaction, DatabaseError
from django.db.models import F, Q, Count, Case, When, Value, JSONField
from django.http import JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET
from .models import Snapshot, SnapshotPanel, SnapshotPresentation, SnapshotRecord
from .snapshot_index import tag_context

LOG = logging.getLogger('inventory.web')

SORTS = {'name','path','scope','category','policy_name','rule_count','hit_count','rule_id',
         'policy_rule_id','vm_count','group_count','other_count','tag_count','usage',
         'membership','hit_status','status','kind','method','references'}


def search_condition(term, field, regex=False):
    if regex:
        return Q(**{field + '__iregex': term})
    groups, parts, quoted, i = [[]], '', False, 0
    while i < len(term):
        if term[i] == '"':
            quoted = not quoted
            i += 1
            continue
        operator = re.match(r'(AND|OR)(?=\s|$)', term[i:], re.I) if not quoted and (i == 0 or term[i-1].isspace()) else None
        if operator:
            if not parts.strip(): raise ValueError('Enter a condition on both sides of AND / OR.')
            groups[-1].append(Q(**{field+'__icontains': parts.strip()}))
            parts = ''
            if operator[1].upper() == 'OR': groups.append([])
            i += len(operator[0])
        else:
            parts += term[i]
            i += 1
    if quoted: raise ValueError('Close the quotation marks in the search.')
    if not parts.strip(): raise ValueError('Enter a search condition.')
    groups[-1].append(Q(**{field+'__icontains': parts.strip()}))
    return reduce(or_, (reduce(and_, group) for group in groups))


def filtered(request, pk):
    panel = get_object_or_404(SnapshotPanel.objects.only('pk'), snapshot_id=pk, slug=request.GET.get('panel', 'overview'))
    rows = SnapshotRecord.objects.filter(snapshot_id=pk, panels__panel=panel)
    term = request.GET.get('q', '').strip()
    if len(term) > 1000: raise ValueError('Search is limited to 1,000 characters.')
    if term:
        condition = search_condition(term, 'search_evidence' if request.GET.get('evidence') == '1' else 'search_basic', request.GET.get('syntax') == 'regex')
        rows = rows.filter(~condition if request.GET.get('mode') == 'excludes' else condition)
    filters = json.loads(request.GET.get('filters', '[]'))
    if not isinstance(filters, list) or len(filters) > 6: raise ValueError('Invalid column filters.')
    for item in filters:
        index, value = item
        if type(index) is not int or not 0 <= index <= 5 or not isinstance(value, dict): raise ValueError('Invalid column filter.')
        text = value.get('text', '')
        if not isinstance(text, str) or len(text) > 1000: raise ValueError('Invalid column filter text.')
        if text:
            condition = Q(**{'columns__'+str(index)+'__icontains':text})
            rows = rows.filter(~condition if value.get('mode') == 'excludes' else condition)
    key = request.GET.get('sort', 'name')
    if key not in SORTS: raise ValueError('Invalid sort column.')
    expression = F('sort_name') if key == 'name' else Case(When(**{'sort_values__'+key:None}, then=Value(None)), default=F('sort_values__'+key), output_field=JSONField())
    order = expression.desc(nulls_last=True) if request.GET.get('order') == 'desc' else expression.asc(nulls_last=True)
    return rows.order_by(order, 'ordinal')


def limit_query():
    if connection.vendor == 'postgresql':
        with connection.cursor() as cursor: cursor.execute("SET LOCAL statement_timeout = '15s'")


def csv_stream(rows, headers):
    # The transaction belongs to the iterator because StreamingHttpResponse runs later.
    with transaction.atomic():
        limit_query()
        yield '\ufeff'
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(headers)
        yield buffer.getvalue()
        buffer.seek(0)
        buffer.truncate(0)
        for values in rows.values_list('columns', flat=True).iterator(chunk_size=250):
            writer.writerow(["'"+str(v) if re.match(r'^\s*[=+@-]', str(v)) else v for v in values])
            yield buffer.getvalue()
            buffer.seek(0)
            buffer.truncate(0)


@login_required
@require_GET
def snapshot_data(request, pk):
    get_object_or_404(Snapshot.objects.only('pk'), pk=pk)
    try:
        with transaction.atomic():
            limit_query()
            op = request.GET.get('op', 'rows')
            if op == 'section':
                panel = get_object_or_404(SnapshotPanel.objects.only('html'), snapshot_id=pk, slug=request.GET.get('panel'))
                result = {'html':panel.html}
            elif op in ('detail', 'tag-coverage'):
                if op == 'detail':
                    row = get_object_or_404(SnapshotRecord.objects.only('data','view','ordinal'), snapshot_id=pk, ordinal=int(request.GET.get('id', '-1')))
                    result = {'id':row.ordinal,'data':row.data,'view':row.view}
                    if row.view == 'tags':
                        meta = get_object_or_404(SnapshotPresentation.objects.only('tag_evidence'), snapshot_id=pk).tag_evidence
                        result['tag_evidence'] = tag_context(row.data, meta)
                else:
                    meta = get_object_or_404(SnapshotPresentation.objects.only('tag_evidence'), snapshot_id=pk).tag_evidence
                    result = {'tag_evidence':meta}
            elif op in ('rows', 'export', 'values'):
                rows = filtered(request, pk)
                if op == 'values':
                    index = int(request.GET.get('column', '-1'))
                    if not 0 <= index <= 5: raise ValueError('Invalid column.')
                    field = 'columns__'+str(index)
                    text = request.GET.get('value', '')[:1000]
                    values = rows.filter(**{field+'__icontains':text}).order_by().values(value=F(field)).annotate(count=Count('pk')).order_by('value')[:100]
                    response = JsonResponse({'values':list(values)})
                    response['Cache-Control'] = 'private, no-store'
                    return response
                if op == 'export':
                    # Validate the search before starting a streamed download.
                    rows.exists()
                    view = rows.values_list('view', flat=True).first()
                    headers = ['Object / path','Type','Usage','Membership','Evidence'] if view == 'inventory' else ['Tag / scope','Usage','VMs','Groups','Other resources','Evidence'] if view == 'tags' else ['Scope','Tags','VMs','Groups','Other resources','Evidence'] if view == 'scopes' else ['Object / path','Policy / category','Status','Count','Evidence']
                    response = StreamingHttpResponse(csv_stream(rows, headers), content_type='text/csv; charset=utf-8')
                    response['Content-Disposition'] = 'attachment; filename="snapshot-'+str(pk)+'.csv"'
                    response['Cache-Control'] = 'private, no-store'
                    return response
                size = int(request.GET.get('size', '25'))
                if size not in (25,50,100): raise ValueError('Invalid page size.')
                count = rows.count()
                page = min(max(0,int(request.GET.get('page', '0'))),max(0,(count-1)//size))
                result = {'count':count,'page':page,'rows':[
                    {'id':r.ordinal,'view':r.view,'data':r.compact}
                    for r in rows.only('ordinal','view','compact')[page*size:(page+1)*size]]}
            else: raise ValueError('Unknown report request.')
    except (ValueError, TypeError, KeyError):
        response = JsonResponse({'error':'Invalid search or filter. Check the expression and try again.'}, status=400)
    except DatabaseError:
        LOG.exception('Snapshot query failed snapshot=%s operation=%s', pk, request.GET.get('op', 'rows'))
        response = JsonResponse({'error':'The search could not finish. Simplify the expression and try again.'}, status=400)
    else:
        response = JsonResponse(result)
    response['Cache-Control'] = 'private, no-store'
    return response
