"""Standalone, escaped reference reports built from one canonical evidence document."""
import csv
import html
import io
import json

MIMES = {'csv':'text/csv; charset=utf-8','pdf':'application/pdf','json':'application/json',
         'yaml':'application/yaml','html':'text/html; charset=utf-8'}
MAX_CONTENT = 64*1024*1024
# Rows arrive in configured NSX order (category, policy sequence, rule sequence).
COLUMNS = ('Rule category','Name','Path','Policy','Policy sequence','Rule sequence','Rule ID','Sources','Destinations','Services','Context profiles','Applied to','Action','State','Matched in','Reason / evidence')
ACTION = COLUMNS.index('Action')
NONE = {'context_profiles':'None','applied_to':'DFW'}


def text(value):
    if value is None: return ''
    if isinstance(value,(dict,list)): return json.dumps(value,ensure_ascii=False,sort_keys=True)
    return str(value)


def rule_field(item,name):
    field=(item.get('rule_fields') or {}).get(name)
    if not field or not field.get('recorded'): return ''
    if field.get('any'): return NONE.get(name,'Any')
    values=[entry.get('name') or entry.get('path') for entry in field.get('items',[])]
    if field.get('total',len(values))>len(values): values.append('+%d more'%(field['total']-len(values)))
    prefix=('From policy: ' if field.get('origin')=='policy' else '')+('NOT ' if field.get('excluded') else '')
    return prefix+'; '.join(text(value) for value in values)


def row(item):
    if not isinstance(item,dict): return (text(item),)+('',)*(len(COLUMNS)-1)
    label=lambda group: text(group.get('name',group.get('path'))) if isinstance(group,dict) else text(group)
    matches=item.get('field_matches',[])
    direct={match.get('group',{}).get('path') for match in matches}
    indirect=[group for group in item.get('matched_groups',item.get('via_groups',[])) if (group.get('path') if isinstance(group,dict) else group) not in direct]
    # Matched in: field/object pairs, plus matched objects whose field was not retained.
    groups='; '.join([match.get('field','')+': '+label(match.get('group',{})) for match in matches]+
                     [('Field not retained: ' if matches else '')+label(group) for group in indirect])
    evidence='; '.join(text(item[key]) for key in ('reference_reason','membership_basis','membership_source','network_checked_at','ip_checked_at','field_note') if item.get(key))
    if item.get('via'): evidence+='; Interface links: '+text(item['via'])
    if item.get('ip_matches'): evidence+='; IP matches: '+text(item['ip_matches'])
    order=item.get('nsx_order') if isinstance(item.get('nsx_order'),dict) else {}
    return tuple(text(value) for value in (order.get('category',item.get('category')),item.get('name',item.get('tag','')),item.get('path'),item.get('policy_name'),
        order.get('policy_sequence'),order.get('rule_sequence',item.get('sequence_number')),item.get('rule_id'),
        *(rule_field(item,name) for name in ('sources','destinations','services','context_profiles','applied_to')),item.get('action'),
        'Disabled' if item.get('disabled') is True else 'Enabled' if item.get('disabled') is False else '',
        groups,evidence))


def report_table(section):
    if section['key'] in ('firewall','confirmed_rules','firewall_references'):
        return COLUMNS,[7,10,9,8,5,5,4,8,8,7,6,7,4,4,8,10],[row(item) for item in section['items']]
    rows=[]
    for item in section['items']:
        if not isinstance(item,dict): rows.append((text(item),'',''));continue
        rows.append((text(item.get('name',item.get('tag',''))),text(item.get('path',item.get('external_id',item.get('identity')))),
                     text({key:value for key,value in item.items() if key not in ('name','path','external_id','identity')})))
    return ('Object','Path / identity','Evidence'),[24,34,42],rows


def human(value):
    return text(value).replace('_',' ').capitalize()


def safe_cell(value):
    value = text(value)
    # Spreadsheet programs can execute formula-like fields even in quoted CSV.
    return "'"+value if value.lstrip().startswith(('=','+','-','@')) or value.startswith(('\t','\r','\n')) else value


def csv_bytes(document):
    output = io.StringIO(newline='')
    writer = csv.writer(output)
    metadata = document['metadata']
    writer.writerow(('Environment','Object','Object type','Object path','Snapshot','Collected at','Requested at','Prepared at','Category','Evidence source','Coverage','Checked at','Notes','Row type',*COLUMNS,'Structured reference JSON'))
    for section in document['sections']:
        prefix = [metadata['environment'],metadata['object']['name'],metadata['object'].get('type',metadata['object'].get('view')),metadata['object'].get('path'),metadata['snapshot_id'],metadata['collected_at'],metadata['requested_at'],metadata['prepared_at'],section['label'],section['source'],section.get('status','unknown'),section.get('checked_at'),'; '.join([metadata['note'],*section['notes']])]
        for item in section['items']:
            writer.writerow([safe_cell(value) for value in [*prefix,'reference',*row(item),text(item)]])
        if not section['items']:
            writer.writerow([safe_cell(value) for value in [*prefix,'coverage',*('',)*len(COLUMNS),'']])
    return ('\ufeff'+output.getvalue()).encode('utf-8')


def html_bytes(document):
    esc = lambda value: html.escape(text(value),quote=True)
    metadata = document['metadata']
    parts = ['<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">',
        '<title>Reference report</title><style>body{font:14px system-ui,sans-serif;color:#233746;margin:32px}h1{font-size:26px}h2{margin-top:32px}p{line-height:1.5;overflow-wrap:anywhere}table{border-collapse:collapse;width:100%;table-layout:fixed}th,td{padding:10px;text-align:left;vertical-align:top;border-bottom:1px solid #cdd7df;overflow-wrap:anywhere}th{background:#edf2f5}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:12px monospace}small{color:#526572}input{padding:10px;width:min(32rem,90%)}.status{font-weight:600}.table-scroll{overflow-x:auto}.table-scroll table{min-width:1100px}.action-allow{color:#146343}.action-drop,.action-reject{color:#9c2d24}@media print{body{margin:0;font-size:9pt}input,label{display:none}.table-scroll{overflow:visible}.table-scroll table{min-width:0}thead{display:table-header-group}tr{break-inside:avoid}details pre{display:block}@page{size:A3 landscape;margin:12mm}}</style>',
        '<h1>'+esc(metadata['object']['name'])+' — References</h1>',
        '<p>'+esc(metadata['object'].get('type',metadata['object'].get('view')))+' · '+esc(metadata['object'].get('path'))+'</p>',
        '<p>'+esc(metadata['environment'])+' · Snapshot '+esc(metadata['snapshot_id'])+'<br>Collected '+esc(metadata['collected_at'])+' · Requested '+esc(metadata['requested_at'])+' · Prepared '+esc(metadata['prepared_at'])+'</p>',
        '<p>'+esc(metadata['note'])+'</p><label for="search">Filter references</label> <input id="search" type="search" placeholder="Search this report">']
    for section in document['sections']:
        columns,weights,rows=report_table(section)
        parts.extend(['<section><h2>'+esc(section['label'])+' ('+str(section['count'])+')</h2>',
            '<p class="status">'+esc(human(section.get('status','unknown')))+' · '+esc(human(section['source']))+' · '+esc(section.get('checked_at'))+'</p>',
            '<p>'+esc(' '.join(section['notes']))+'</p><div class="table-scroll"><table><thead><tr>'+''.join('<th scope="col">'+esc(column)+'</th>' for column in columns)+'</tr></thead><tbody>'])
        for item,cells in zip(section['items'],rows):
            parts.append('<tr data-reference>'+''.join('<td'+(' class="action-'+cells[ACTION].lower()+'"' if len(cells)==len(COLUMNS) and index==ACTION and cells[ACTION].lower() in ('allow','drop','reject') else '')+'>'+esc(value)+'</td>' for index,value in enumerate(cells))+'</tr>')
            parts.append('<tr data-reference><td colspan="'+str(len(columns))+'"><details><summary>Structured reference</summary><pre>'+esc(json.dumps(item,ensure_ascii=False,indent=2))+'</pre></details></td></tr>')
        if not section['items']: parts.append('<tr><td colspan="'+str(len(columns))+'">No references retained in this category. See coverage above.</td></tr>')
        parts.append('</tbody></table></div></section>')
    # Constant script only; inventory content is always escaped, never executable.
    script = "document.getElementById('search').addEventListener('input',function(){const q=this.value.toLocaleLowerCase();document.querySelectorAll('[data-reference]').forEach(row=>{row.hidden=!row.textContent.toLocaleLowerCase().includes(q);});});"
    import base64,hashlib
    digest = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
    parts.insert(1,'<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; script-src \'sha256-'+digest+'\'; base-uri \'none\'; form-action \'none\'">')
    parts.append('<script>'+script+'</script></html>')
    return ''.join(parts).encode('utf-8')


def pdf_bytes(document):
    from pathlib import Path
    import reportlab
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A3, landscape
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, LongTable, TableStyle, KeepTogether
    for name,filename in (('ReferenceFont','Vera.ttf'),('ReferenceBold','VeraBd.ttf')):
        if name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(name,str(Path(reportlab.__file__).parent/'fonts'/filename)))
    # Unsupported glyphs are written as explicit Unicode code points, never boxes.
    cmap = pdfmetrics.getFont('ReferenceFont').face.charToGlyph
    def printable(value):
        return ''.join(char if ord(char) in cmap or char in '\n\t' else '[U+%04X]'%ord(char) for char in text(value))
    base = ParagraphStyle('Reference',fontName='ReferenceFont',fontSize=7,leading=10,wordWrap='CJK',alignment=TA_LEFT,spaceAfter=3)
    heading = ParagraphStyle('ReferenceHeading',parent=base,fontName='ReferenceBold',fontSize=14,leading=18)
    small = ParagraphStyle('ReferenceSmall',parent=base,fontSize=7,leading=10)
    def para(value,style=base): return Paragraph(html.escape(printable(value)).replace('\n','<br/>'),style)
    output=io.BytesIO(); size=landscape(A3)
    pdf=SimpleDocTemplate(output,pagesize=size,rightMargin=28,leftMargin=28,topMargin=30,bottomMargin=30,title='Reference report',author='NSX Security Analyzer')
    metadata=document['metadata']
    story=[para(metadata['object']['name']+' — References',heading),para(text(metadata['object'].get('type',metadata['object'].get('view')))+' · '+text(metadata['object'].get('path'))),para(metadata['environment']+' · Snapshot '+metadata['snapshot_id']),
        para('Collected '+metadata['collected_at']+' · Requested '+metadata['requested_at']+' · Prepared '+metadata['prepared_at']),para(metadata['note']),Spacer(1,10)]
    for section in document['sections']:
        columns,weights,rows=report_table(section)
        widths=[pdf.width*weight/sum(weights) for weight in weights]
        story.append(KeepTogether([para(section['label']+' ('+str(section['count'])+')',heading),
            para(human(section.get('status','unknown'))+' · '+human(section['source'])+' · '+text(section.get('checked_at'))),para(' '.join(section['notes'])),Spacer(1,6)]))
        # Headers are plain words: wrap between words, unlike paths in body cells.
        values=[[para(column,ParagraphStyle('Column',parent=small,fontName='ReferenceBold',wordWrap=None)) for column in columns]]
        for cells in rows:
            values.append([para(value,ParagraphStyle('Action',parent=base,textColor=colors.HexColor('#146343' if value=='ALLOW' else '#9c2d24'))) if len(cells)==len(COLUMNS) and index==ACTION and value in ('ALLOW','DROP','REJECT') else para(value) for index,value in enumerate(cells)])
        if not section['items']: values.append([para('No references retained. See coverage above.')]+[para('') for _ in columns[1:]])
        table=LongTable(values,colWidths=widths,repeatRows=1,splitInRow=1,hAlign='LEFT')
        table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#edf2f5')),('VALIGN',(0,0),(-1,-1),'TOP'),('LINEBELOW',(0,0),(-1,0),.5,colors.HexColor('#a5b7c4')),('LINEBELOW',(0,1),(-1,-1),.25,colors.HexColor('#d5dfe5')),('LEFTPADDING',(0,0),(-1,-1),5),('RIGHTPADDING',(0,0),(-1,-1),5),('TOPPADDING',(0,0),(-1,-1),6),('BOTTOMPADDING',(0,0),(-1,-1),6)]))
        story.extend([table,Spacer(1,16)])
    story.append(para('Characters unavailable in the embedded font are represented as [U+code point]. JSON, YAML and HTML retain the original Unicode text.',small))
    def footer(canvas,doc):
        canvas.setFont('ReferenceFont',7);canvas.setFillColor(colors.HexColor('#526572'))
        canvas.drawString(28,16,'Reference report · '+metadata['environment']);canvas.drawRightString(size[0]-28,16,'Page '+str(doc.page))
    pdf.build(story,onFirstPage=footer,onLaterPages=footer)
    return output.getvalue()


def render(document,format):
    if format=='json': result=json.dumps(document,ensure_ascii=False,indent=2).encode('utf-8')
    elif format=='yaml':
        import yaml
        result=yaml.safe_dump(document,allow_unicode=True,sort_keys=False).encode('utf-8')
    elif format=='csv': result=csv_bytes(document)
    elif format=='html': result=html_bytes(document)
    elif format=='pdf': result=pdf_bytes(document)
    else: raise ValueError('Unsupported export format.')
    if len(result)>MAX_CONTENT: raise ValueError('Export exceeds 64 MiB. Export one category at a time.')
    return result
