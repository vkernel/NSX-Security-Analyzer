"""Offline checks for the web application collection engine."""

import json
from pathlib import Path
import unittest
import tempfile
import io
import shutil
import subprocess
from html.parser import HTMLParser
from unittest.mock import Mock, patch

from webapp.inventory import collector as nsx


def rendered_document(report):
    """Inspect the internal generated document in offline renderer tests."""
    return nsx._render_report(report, fragments=False)


class InventoryTests(unittest.TestCase):
    def test_web_renderer_returns_fragments_without_cli_entrypoint(self):
        self.assertFalse(hasattr(nsx, "main"))
        self.assertFalse(hasattr(nsx, "load_manager_targets"))
        self.assertFalse(hasattr(nsx, "atomic_write"))







    def test_readable_timestamp_keeps_timezone_explicit(self):
        self.assertEqual(nsx.display_timestamp('2026-09-21T16:44:26.840467+02:00'),
                         '21 Sep 2026 · 14:44 UTC')
        self.assertEqual(nsx.display_timestamp('Not recorded'), 'Not recorded')

    @unittest.skipUnless(shutil.which("node"), "Node.js required for CSV checks")
    def test_csv_export_escapes_values_and_exports_current_matching_order(self):
        report = dict(objects=[], generated_at='test', groups_scanned=0,
                      custom_services_scanned=0, system_groups_excluded=0,
                      indexed_objects_scanned=0, scope='test', usage_definition='test', limitations='test')
        html = rendered_document(report)
        script = html.split('</script><script>', 1)[1].split('</script>', 1)[0]
        csv = script[script.index('  function csvContent'):script.index('  function downloadCsv')]
        handler = script[script.index("    exportButton.addEventListener('click'"):script.index('    tools.append(exportButton)')]
        checks = r'''
const assert = require('node:assert/strict');
assert.equal(csvContent(['Name','Evidence'],[['café, "test"','line1\nline2']]),
 '\ufeff"Name","Evidence"\r\n"café, ""test""","line1\nline2"\r\n');
assert.equal(csvContent(['Name'],[['=1+1']]),'\ufeff"Name"\r\n"\'=1+1"\r\n');
let click, matching = [99], invalid = false, refreshed = 0, downloads = [];
const exportButton = {addEventListener:(_,callback)=>click=callback};
const timer = undefined, panelId = 'dfw-rules', exportHeaders = ['Name'];
const search = {hasAttribute:()=>invalid};
function refresh() { refreshed++; matching = [7,3,1]; }
function rowColumnText(id) { return ['rule '+id]; }
function downloadCsv(...args) { downloads.push(args); }
'''
        checks_after = '''
click();
assert.equal(refreshed,1);
assert.deepEqual(downloads,[['dfw-rules',['Name'],[['rule 7'],['rule 3'],['rule 1']]]]);
invalid=true; click(); assert.equal(downloads.length,1);
'''
        subprocess.run(['node'], input=csv + checks + handler + checks_after,
                       text=True, check=True, capture_output=True)

    def test_applied_to_dfw_page_uses_scope_and_includes_disabled_rules(self):
        rules = [dict(name=str(index), path='/rules/' + str(index), scope=scope,
                      disabled=index == 1, system_owned=index == 1,
                      source_groups=['ANY'], destination_groups=['ANY'],
                      hit_status='traffic_recorded')
                 for index, scope in enumerate([['ANY'], ['ANY'], ['/groups/scoped'], [], None])]
        report = dict(objects=[], dfw=dict(rules=rules, policies=[], errors=[]),
                      generated_at='test', groups_scanned=0, custom_services_scanned=0,
                      system_groups_excluded=0, indexed_objects_scanned=0,
                      scope='test', usage_definition='test', limitations='test')
        html = rendered_document(report)
        self.assertIn('href="#dfw-scope-rules">Applied to DFW <span>2</span>', html)
        panel = html.split('id="dfw-scope-rules"', 1)[1].split('</section>', 1)[0]
        indices = panel.split('data-rows="', 1)[1].split('"', 1)[0].split(',')
        payload = json.loads(html.split('id="report-rows">', 1)[1].split('</script>', 1)[0])
        self.assertEqual({payload['rows'][int(index)]['data']['path'] for index in indices},
                         {'/rules/0', '/rules/1'})
        self.assertIn('class="search-evidence"', panel)
        report['dfw']['rules'] = rules[2:]
        empty_html = rendered_document(report)
        self.assertIn('href="#dfw-scope-rules">Applied to DFW <span>0</span>', empty_html)

    def test_rules_with_empty_groups_include_only_confirmed_direct_references(self):
        groups = [dict(path='/groups/empty', name='Empty <group>', membership='empty'),
                  dict(path='/groups/unknown', membership='unknown'),
                  dict(path='/groups/full', membership='nonempty')]
        rules = [dict(name='Affected', path='/rules/a', source_groups=['/groups/empty'] * 2,
                      destination_groups=['/groups/empty'], scope=['/groups/empty'],
                      disabled=True, hit_status='unknown'),
                 dict(name='Other', path='/rules/b', source_groups=['/groups/unknown'],
                      destination_groups=['/groups/full'], hit_status='zero_hits', disabled=False)]
        annotated = nsx.rules_with_empty_group_evidence(rules, groups)
        self.assertEqual([e['field'] for e in annotated[0]['empty_group_references']],
                         ['Source', 'Destination', 'Applied to'])
        self.assertEqual(annotated[1]['empty_group_references'], [])
        self.assertNotIn('empty_group_references', rules[0])
        report = dict(objects=[dict(g, kind='group', name=g.get('name', g['path']),
                                   usage='referenced') for g in groups],
                      dfw=dict(rules=rules, policies=[], errors=[]), generated_at='test',
                      groups_scanned=3, custom_services_scanned=0, system_groups_excluded=0,
                      indexed_objects_scanned=3, scope='test', usage_definition='test', limitations='test')
        html = rendered_document(report)
        self.assertIn('href="#empty-group-rules">Empty groups <span>1</span>', html)
        panel = html.split('id="empty-group-rules"', 1)[1].split('</section>', 1)[0]
        self.assertIn('class="search-evidence"', panel)
        self.assertIn('DFW rules with empty groups', panel)
        self.assertIn('nested groups are not expanded', panel)

    def test_dfw_overview_segmentation_denominator_and_partial_inventory(self):
        def rule(source, destination, action="ALLOW", disabled=False):
            return dict(source_groups=source, destination_groups=destination, action=action,
                        disabled=disabled, hit_status="zero_hits", services=["ANY"], scope=["ANY"],
                        category="<Application>")
        dfw = dict(policies=[dict(category="<Application>", status="has_rules")], errors=[], rules=[
            rule(["/groups/app"], ["/groups/db"]),
            rule(["ANY"], ["/groups/db"]),
            rule([], ["/groups/db"]),
            rule(["/groups/app"], ["/groups/db"], disabled=True),
            rule(["ANY"], ["ANY"], action="DROP")])
        html = nsx.dfw_overview(dfw)
        self.assertIn("33.3%", html)
        self.assertIn("1 of 3 enabled ALLOW rules", html)
        self.assertIn("ANY source or destination: <strong>1</strong>", html)
        self.assertIn("&lt;Application&gt;", html)
        self.assertNotIn("<Application>", html)
        self.assertIn("Not assessed", nsx.dfw_overview(dfw, testing=True))
        self.assertIn("Not assessed", nsx.dfw_overview(dict(dfw, errors=["Unavailable"])))
        self.assertIn("N/A", nsx.dfw_overview(dict(policies=[], rules=[], errors=[])))
        report = dict(objects=[], dfw=dict(policies=[], rules=[], errors=[]), generated_at="test",
                      groups_scanned=0, custom_services_scanned=0, system_groups_excluded=0,
                      indexed_objects_scanned=0, scope="test", usage_definition="test", limitations="test")
        rendered = rendered_document(report)
        self.assertIn('href="#dfw-overview">Overview</a>', rendered)
        self.assertEqual(rendered.count('id="dfw-overview"'), 1)



    def test_overview_charts_count_disjoint_categories_and_empty_samples(self):
        groups = [{"usage": value} for value in
                  ("referenced", "unused_candidate", "unknown", "not_assessed")]
        rules = [{"disabled": True, "hit_status": "zero_hits"},
                 {"disabled": False, "hit_status": "zero_hits"},
                 {"disabled": False, "hit_status": "traffic_recorded"},
                 {"disabled": False, "hit_status": "unknown"}]
        html = nsx.overview_charts(groups, [], rules, testing=True)
        self.assertIn("Referenced: 1; Unused candidates: 1; Unknown: 1; Not assessed: 1", html)
        self.assertIn("Traffic recorded: 1; Zero recorded hits: 1; Disabled: 1; Unknown: 1", html)
        self.assertEqual(html.count('role="img"'), 2)
        self.assertEqual(html.count('25.0%'), 16)  # Segment titles and visible legends.
        self.assertIn("No objects inventoried", html)
        self.assertIn("Testing sample only", html)
        self.assertIn('href="#disabled-rules"', html)
        self.assertNotIn("nan", html.lower())
        self.assertNotIn('<svg', nsx.overview_charts([], [], []))





    @unittest.skipUnless(shutil.which("node"), "Node.js required for sorting/search check")
    def test_visible_status_search_and_numeric_sort_with_missing_values(self):
        source = Path(nsx.__file__).read_text()
        search = source[source.index("  function searchable(id,"):source.index("  const controllers =")]
        sorting = source[source.index("    function sortValue(id, key)"):source.index("    const previous = tools.querySelector")]
        checks = """
const assert = require('node:assert/strict');
const rowPool = [
 {view:'dfw',data:{name:'SG_A',path:'/a',kind:'group',status:'noncompliant',hit_count:10}},
 {view:'dfw',data:{name:'SG_B',path:'/b',kind:'group',status:'compliant',tagTemplate:'SG_{app}',hit_count:2}},
 {view:'tags',data:{name:'prod',path:'/c',status:'unknown',hit_count:null}}
];
const textCache = new Map(), types = {group:'Groups'}, labels = {};
const tagRowConditions = () => [];
assert.ok(searchable(2).includes('needs review'));
const view='dfw', sortKey={value:'hit_count'}, sortOrder={value:'asc'};
const collator = new Intl.Collator('en',{numeric:true,sensitivity:'base'});
assert.deepEqual([0,1,2].sort(compare),[1,0,2]);
sortOrder.value='desc';
assert.deepEqual([0,1,2].sort(compare),[0,1,2]);
"""
        subprocess.run(["node"], input=search + sorting + checks, text=True, check=True, capture_output=True)



    @unittest.skipUnless(shutil.which("node"), "Node.js required for column filter checks")
    def test_column_filters_combine_inclusion_exclusion_and_empty_cells(self):
        source = Path(nsx.__file__).read_text()
        helper = source[source.index("  function matchesColumnFilters"):source.index("  function columnFilters")]
        script = """
const assert = require('node:assert/strict');
const rows = [
 ['SG_PROD_WEB','Group','Referenced','Has members','Used by rule 123'],
 ['SG_PROD_DB','Group','Unused candidate','Empty','No references'],
 ['SG_TEST_WEB','Group','Referenced','Has members','Used by rule 456']
];
const filters=new Map([[0,{mode:'contains',text:'prod'}],[3,{mode:'excludes',text:'empty'}]]);
assert.deepEqual(rows.filter(row=>matchesColumnFilters(row,filters)),[rows[0]]);
filters.set(4,{mode:'contains',text:'RULE 123'});
assert.deepEqual(rows.filter(row=>matchesColumnFilters(row,filters)),[rows[0]]);
filters.set(4,{mode:'excludes',text:'rule 123'});
assert.equal(rows.filter(row=>matchesColumnFilters(row,filters)).length,0);
filters.clear();
assert.equal(rows.filter(row=>matchesColumnFilters(row,filters)).length,3);
assert.ok(matchesColumnFilters([''],new Map([[0,{mode:'excludes',text:'prod'}]])));
assert.ok(!matchesColumnFilters([''],new Map([[0,{mode:'contains',text:'prod'}]])));
assert.ok(matchesColumnFilters(['1,234 hits'],new Map([[0,{mode:'contains',text:'1,234'}]])));
"""
        subprocess.run(["node"], input=helper + script, text=True, check=True, capture_output=True)

    @unittest.skipUnless(shutil.which("node"), "Node.js required for regex search checks")
    def test_table_search_regex_and_exclusion(self):
        source = Path(nsx.__file__).read_text()
        helper = source[source.index("  function tableSearchMatcher"):source.index("  function matchesColumnFilters")]
        script = r"""
const assert = require('node:assert/strict');
const rows=['PROD rule 123','Stage rule 45','DEV rule 9'];
const select=(text,syntax,mode)=>rows.filter(tableSearchMatcher(text,syntax,mode));
assert.deepEqual(select('prod','text','contains'),[rows[0]]);
assert.deepEqual(select('prod','text','excludes'),rows.slice(1));
assert.deepEqual(select('prod|stage','regex','contains'),rows.slice(0,2));
assert.deepEqual(select('prod|stage','regex','excludes'),[rows[2]]);
assert.deepEqual(select(String.raw`\d{3}$`,'regex','contains'),[rows[0]]);
assert.deepEqual(select(String.raw`\D+$`,'regex','contains'),[]);
assert.deepEqual(select('prod|stage','text','contains'),[]);
assert.deepEqual(select('','regex','excludes'),rows);
assert.throws(()=>tableSearchMatcher('[','regex','contains'),SyntaxError);
assert.deepEqual(select('DEV','text','contains'),[rows[2]]);
"""
        subprocess.run(["node"], input=helper + script, text=True, check=True, capture_output=True)

    @unittest.skipUnless(shutil.which("node"), "Node.js required for boolean search checks")
    def test_table_search_and_or_conditions(self):
        source = Path(nsx.__file__).read_text()
        helper = source[source.index("  function tableSearchMatcher"):source.index("  function matchesColumnFilters")]
        script = r'''
const assert = require('node:assert/strict');
const rows=['prod web server','prod database','stage web server','Sales and Marketing',
            'Condition 2 precedes Condition 1','android origin'];
const select=(query,mode='contains')=>rows.filter(tableSearchMatcher(query,'text',mode));
assert.deepEqual(select('prod AND web'),[rows[0]]);
assert.deepEqual(select('PROD and WEB'),[rows[0]]);
assert.deepEqual(select('prod OR stage'),rows.slice(0,3));
assert.deepEqual(select('prod OR stage AND database'),rows.slice(0,2));
assert.deepEqual(select('prod AND database OR stage AND web'),[rows[1],rows[2]]);
assert.deepEqual(select('prod AND web','excludes'),rows.slice(1));
assert.deepEqual(select('prod OR stage','excludes'),rows.slice(3));
assert.deepEqual(select('Condition 1 AND Condition 2'),[rows[4]]);
assert.deepEqual(select('web server'),[rows[0],rows[2]]);
assert.deepEqual(select('"Sales and Marketing"'),[rows[3]]);
assert.deepEqual(select('"Sales and Marketing" OR database'),[rows[1],rows[3]]);
assert.deepEqual(select('android AND origin'),[rows[5]]);
assert.deepEqual(select('prod\tAND\nweb'),[rows[0]]);
assert.deepEqual(select('   ','excludes'),rows);
for(const query of ['AND prod','prod OR','prod AND OR web','"prod','""']) {
  assert.throws(()=>tableSearchMatcher(query,'text','contains'),SyntaxError,query);
}
assert.equal(tableSearchMatcher('prod AND web','regex','contains')('prod web'),false);
assert.equal(tableSearchMatcher('prod AND web','regex','contains')('prod AND web'),true);
'''
        subprocess.run(["node"], input=helper + script, text=True, check=True, capture_output=True)

    @unittest.skipUnless(shutil.which("node"), "Node.js required for column value checks")
    def test_column_value_suggestions_use_all_rows_and_literal_case_insensitive_search(self):
        source = Path(nsx.__file__).read_text()
        helper = source[source.index("  function columnValueOptions"):source.index("  function columnFilters")]
        script = """
const assert = require('node:assert/strict');
const rows=Array.from({length:60},(_,i)=>['object '+i,i<40?'Referenced':'Unused candidate']);
assert.deepEqual(columnValueOptions(rows,1,''),[{value:'Referenced',count:40},{value:'Unused candidate',count:20}]);
assert.deepEqual(columnValueOptions(rows,1,' UNUSED '),[{value:'Unused candidate',count:20}]);
assert.deepEqual(columnValueOptions(rows,1,'unknown'),[]);
assert.deepEqual(columnValueOptions([['10'],['2'],[''],[null]],0,''),[{value:'2',count:1},{value:'10',count:1}]);
assert.deepEqual(columnValueOptions([['a.b'],['axb']],0,'.'),[{value:'a.b',count:1}]);
"""
        subprocess.run(["node"], input=helper + script, text=True, check=True, capture_output=True)

    @unittest.skipUnless(shutil.which("node"), "Node.js required for available-value check")
    def test_available_values_refresh_search_and_count_filtered_rows_before_pagination(self):
        source = Path(nsx.__file__).read_text()
        helpers = source[source.index("  function tableSearchMatcher"):source.index("  function columnFilters(")]
        callback = source[source.index("    function availableColumnValues()"):source.index("    const columnState =")]
        checks = """
const assert = require('node:assert/strict');
const rows = Array.from({length:70},(_,i)=>[i<40?'prod-'+i:'dev-'+i,i%2?'Referenced':'Unused']);
const columnText = new Map(), timer = undefined;
const rowColumnText = id => rows[id];
let matching = [], text = '', syntax = 'text', mode = 'contains';
const filters = new Map();
function refresh() {
  try {
    const accepts = tableSearchMatcher(text,syntax,mode);
    matching = rows.flatMap((row,id)=>accepts(row.join(' ')) && matchesColumnFilters(row,filters)?[id]:[]);
  } catch (_) { matching = []; }
}
function counts() { return Object.fromEntries(columnValueOptions(availableColumnValues(),1,'').map(o=>[o.value,o.count])); }
assert.deepEqual(counts(),{Referenced:35,Unused:35});
text='prod'; // Opening the header must refresh immediately, before a debounce callback.
assert.deepEqual(counts(),{Referenced:20,Unused:20});
mode='excludes';
assert.deepEqual(counts(),{Referenced:15,Unused:15});
mode='contains'; syntax='regex'; text='^prod-';
filters.set(1,{text:'Referenced',mode:'contains'});
assert.deepEqual(counts(),{Referenced:20});
text='[';
assert.deepEqual(counts(),{});
filters.clear(); text='';
assert.deepEqual(counts(),{Referenced:35,Unused:35});
"""
        subprocess.run(["node"], input=helpers + callback + checks,
                       text=True, check=True, capture_output=True)

    @unittest.skipUnless(shutil.which("node"), "Node.js required for search scope checks")
    def test_search_can_exclude_membership_and_reference_evidence(self):
        source = Path(nsx.__file__).read_text()
        search = source[source.index("  function searchable(id,"):source.index("  const controllers =")]
        matcher = source[source.index("  function tableSearchMatcher"):source.index("  function matchesColumnFilters")]
        checks = """
const assert = require('node:assert/strict');
const textCache = new Map(), labels = {};
const rowPool = [
 {view:'inventory',data:{name:'target-group',path:'/groups/target-group'}},
 {view:'inventory',data:{name:'parent',path:'/groups/parent',membership_definition:{criteria:['/groups/target-group']}}},
 {view:'inventory',data:{name:'consumer',path:'/groups/consumer',referenced_by:['/groups/target-group']}}
];
const select=(include,text='target-group',syntax='text',mode='contains')=>{
 const accepts=tableSearchMatcher(text,syntax,mode);
 return rowPool.flatMap((_,id)=>accepts(tableSearchText(id,include))?[id]:[]);
};
assert.deepEqual(select(true),[0,1,2]);
assert.deepEqual(select(false),[0]);
assert.deepEqual(select(false,'TARGET-GROUP','regex'),[0]);
assert.deepEqual(select(false,'target-group','text','excludes'),[1,2]);
assert.deepEqual(select(true,'target-group','text','excludes'),[]);
assert.deepEqual(select(false,'/groups/parent'),[1]);
assert.deepEqual(select(false,''),[0,1,2]);
assert.deepEqual(select(true),[0,1,2]); // Re-enabling restores evidence matches.
"""
        subprocess.run(["node"], input=search + matcher + checks,
                       text=True, check=True, capture_output=True)

    @unittest.skipUnless(shutil.which("node"), "Node.js required for search scope checks")
    def test_search_scope_is_consistent_across_all_table_views(self):
        source = Path(nsx.__file__).read_text()
        search = source[source.index("  function searchable(id,"):source.index("  const controllers =")]
        matcher = source[source.index("  function tableSearchMatcher"):source.index("  function matchesColumnFilters")]
        checks = """
const assert = require('node:assert/strict');
const textCache = new Map(), labels = {};
const tagRowConditions = (row,key) => row[key] || [];
const examples = [
 ['inventory',{kind:'group',membership_definition:{criteria:['target-object']}}],
 ['inventory',{kind:'custom_service',referenced_by:['target-object']}],
 ['dfw',{rule_count:1,notes:['target-object']}],
 ['dfw',{source_groups:['target-object'],disabled:false}],
 ['tags',{condition_evidence:[{group:'target-object'}]}],
 ['scopes',{tags:[{name:'target-object'}]}]
];
const rowPool = examples.flatMap(([view,evidence],index) => [
 {view,data:{name:'target-object',path:'/objects/'+index}},
 {view,data:{name:'related-object',path:'/related/'+index,...evidence}}
]);
examples.forEach((_,index) => {
 const own=index*2, related=own+1;
 for (const syntax of ['text','regex']) {
  for (const include of [true,false]) {
   const contains=tableSearchMatcher('TARGET-OBJECT',syntax,'contains');
   const excludes=tableSearchMatcher('TARGET-OBJECT',syntax,'excludes');
   assert.equal(contains(tableSearchText(own,include)),true);
   assert.equal(contains(tableSearchText(related,include)),include);
   assert.equal(excludes(tableSearchText(own,include)),false);
   assert.equal(excludes(tableSearchText(related,include)),!include);
  }
 }
 assert.equal(tableSearchMatcher('/related/'+index,'text','contains')(tableSearchText(related,false)),true);
 assert.equal(tableSearchMatcher('','text','excludes')(tableSearchText(related,false)),true);
 // Switching back to full search must restore cached related-object matches.
 assert.equal(tableSearchMatcher('target-object','text','contains')(tableSearchText(related,true)),true);
});
"""
        subprocess.run(["node"], input=search + matcher + checks,
                       text=True, check=True, capture_output=True)

    def test_html_has_no_naming_pages_links_or_browser_dependencies(self):
        report = dict(objects=[], generated_at="test", groups_scanned=0,
                      custom_services_scanned=0, system_groups_excluded=0,
                      indexed_objects_scanned=0, scope="test", usage_definition="test", limitations="test",
                      naming={"obsolete": "saved naming data must not affect HTML rendering"})
        html = rendered_document(report)
        for text in ('href="#naming', 'id="naming', 'id="group-naming', 'Naming conventions',
                     'refreshGroupNaming', 'family-search', 'naming_rules'):
            self.assertNotIn(text, html)
        self.assertIn('id="dfw-rules"', html)
        self.assertIn('id="tags-all"', html)
        self.assertIn('id="feature-guide"', html)
        self.assertIn('0 unique object(s) to review', html)
        if shutil.which("node"):
            script = html.split('</script><script>', 1)[1].split('</script>', 1)[0]
            subprocess.run(["node", "--check"], input=script, text=True, check=True, capture_output=True)

    def test_hit_history_survives_reset_and_unknown_statistics(self):
        def report(hits, date, status="traffic_recorded", manager="nsx.example.com"):
            return {"manager": manager, "generated_at": date, "dfw": {"rules": [
                {"path": "/rules/a", "rule_id": 12, "policy_rule_id": "a",
                 "hit_status": status, "hit_count": hits, "statistics_checked_at": date}]}}
        first = report(42, "2026-09-01T12:00:00+00:00")
        nsx.retain_hit_history(first)
        reset = report(0, "2026-09-02T12:00:00+00:00", "zero_hits")
        nsx.retain_hit_history(reset, first)
        history = reset["dfw"]["rules"][0]["last_positive_observation"]
        self.assertEqual(history, {"hit_count": 42, "observed_at": first["generated_at"]})
        unknown = report(None, "2026-09-03T12:00:00+00:00", "unknown")
        nsx.retain_hit_history(unknown, reset)
        self.assertEqual(unknown["dfw"]["rules"][0]["last_positive_observation"], history)
        active = report(3, "2026-09-04T12:00:00+00:00")
        nsx.retain_hit_history(active, unknown)
        self.assertEqual(active["dfw"]["rules"][0]["last_positive_observation"]["hit_count"], 3)
        for change in ("manager", "identity", "future", "testing"):
            current = report(0, "2026-09-02T12:00:00+00:00", "zero_hits")
            old = report(42, "2026-09-01T12:00:00+00:00")
            if change == "manager": old["manager"] = "other"
            if change == "identity": old["dfw"]["rules"][0]["rule_id"] = 99
            if change == "future": old["dfw"]["rules"][0]["statistics_checked_at"] = "2027-01-01T00:00:00+00:00"
            if change == "testing": old["testing"] = True
            nsx.retain_hit_history(current, old)
            self.assertIsNone(current["dfw"]["rules"][0]["last_positive_observation"], change)

    def test_tag_scopes_deduplicates_usage_and_preserves_empty_scope(self):
        def tag(scope, name, vms, conditions, assignments):
            return dict(scope=scope, name=name, status="both", vm_count=len(vms),
                        group_count=len(set(conditions) | set(assignments)),
                        vms=dict.fromkeys(vms, "VM"),
                        group_conditions=dict.fromkeys(conditions, "Group"),
                        group_assignments=dict.fromkeys(assignments, "Group"))
        rows = nsx.tag_scopes({"objects": [
            tag("app", "web", ["vm1"], ["g1"], ["g1"]),
            tag("app", "db", ["vm1", "vm2"], ["g1"], ["g2"]),
            tag("", "unscoped", [], [], []),
            tag("App", "other", [], [], [])]})
        scope = next(row for row in rows if row["scope"] == "app")
        self.assertEqual((scope["tag_count"], scope["vm_count"], scope["group_count"]), (2, 2, 2))
        self.assertEqual([tag["name"] for tag in scope["tags"]], ["db", "web"])
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["name"], "(empty scope)")
        self.assertEqual(nsx.tag_scopes({}), [])

    def test_logging_modes(self):
        original_handlers = nsx.LOG.handlers[:]
        original_level, original_propagate = nsx.LOG.level, nsx.LOG.propagate
        try:
            for debug in (False, True):
                with self.subTest(debug=debug), patch("sys.stderr", new_callable=io.StringIO) as output:
                    nsx.configure_logging(debug=debug)
                    nsx.LOG.info("Reading inventory...")
                    nsx.LOG.debug("Request details")
                    nsx.LOG.warning("Retrying")
                    text = output.getvalue()
                    self.assertIn("Reading inventory...", text)
                    self.assertIn("Retrying", text)
                    self.assertEqual("Request details" in text, debug)
                    self.assertEqual("[MainThread]" in text, debug)
        finally:
            for handler in nsx.LOG.handlers:
                handler.close()
            nsx.LOG.handlers = original_handlers
            nsx.LOG.setLevel(original_level)
            nsx.LOG.propagate = original_propagate



    def test_debug_requests_do_not_log_headers_params_or_response_bodies(self):
        client = nsx.NSXClient("example.com", "admin", "secret-password", retries=1)
        client._get = Mock(side_effect=[nsx.AuditError("private response", status_code=503),
                                       {"private_payload": "do-not-log"}])
        with self.assertLogs(nsx.LOG, level="DEBUG") as capture, patch.object(nsx.time, "sleep"):
            self.assertEqual(client.get("/infra/tags", {"cursor": "private-cursor"}),
                             {"private_payload": "do-not-log"})
        output = "\n".join(capture.output)
        self.assertIn("retry 1/1", output)
        self.assertIn("completed in", output)
        self.assertEqual(client.metrics, {"requests": 2, "retries": 1})
        for secret in ("secret-password", "Authorization", "private response", "private-cursor", "do-not-log"):
            self.assertNotIn(secret, output)

    def test_log_formatter_redacts_credentials_in_tracebacks(self):
        formatter = nsx.DiagnosticFormatter(("secret-password", "encoded-token"))
        try:
            raise ValueError("secret-password encoded-token")
        except ValueError:
            record = nsx.logging.LogRecord("test", nsx.logging.ERROR, __file__, 1,
                                          "Failed: %s", ("secret-password",), nsx.sys.exc_info())
        text = formatter.format(record)
        self.assertIn("Traceback", text)
        self.assertIn("[REDACTED]", text)
        self.assertNotIn("secret-password", text)
        self.assertNotIn("encoded-token", text)

    def test_group_definition_preserves_conditions_and_nested_logic(self):
        expression = [{"resource_type": "NestedExpression", "expressions": [
            {"resource_type": "Condition", "member_type": "VirtualMachine", "key": "Tag",
             "operator": "EQUALS", "value": "app|web"},
            {"resource_type": "ConjunctionOperator", "conjunction_operator": "OR"},
            {"resource_type": "PathExpression", "paths": ["/infra/segments/web"]}]}]
        definition = nsx.group_definition({"expression": expression})
        self.assertEqual(definition["methods"], ["Segments / ports", "Tag conditions"])
        self.assertIn("VirtualMachine · Tag EQUALS app|web", definition["criteria"])
        self.assertEqual(definition["definition"]["expression"], expression)

    def test_container_membership_requires_evidence_for_empty_result(self):
        for member in ("Pod", "Namespace", "Cluster", "Service", "KubernetesNamespace", "AntreaEgress"):
            with self.subTest(member=member):
                group = {"path": "/infra/domains/default/groups/c",
                         "expression": [{"resource_type": "Condition", "member_type": member}]}
                client = Mock()
                client.get.return_value = {"results": []}
                self.assertEqual(nsx.membership(client, group)[0], "unknown")
                client.get.return_value = {"results": ["192.0.2.1"]}
                self.assertEqual(nsx.membership(client, group)[0], "nonempty")
        client = Mock()
        client.get.return_value = {"results": []}
        self.assertEqual(nsx.membership(client, {"path": "/groups/a", "group_type": "ANTREA"})[0], "unknown")

    def test_antrea_and_container_groups_are_audited(self):
        included = {"path": "/infra/domains/default/groups/g", "expression": []}
        antrea = {"path": "/infra/domains/default/groups/k", "group_type": ["ANTREA"],
                    "expression": [{"resource_type": "PathExpression", "paths": [included["path"]]}]}
        container = {"path": "/infra/domains/default/groups/pods",
                     "expression": [{"resource_type": "Condition", "member_type": "Pod",
                                     "key": "Tag", "operator": "EQUALS", "value": "app|web"}]}
        inventory = [included, antrea, container]
        def items(path, params=None):
            return {"/infra/tags": [], "/infra/realized-state/virtual-machines": [], "/infra/domains": [{"path": "/infra/domains/default"}],
                    "/infra/domains/default/groups": inventory, "/infra/services": [],
                    "/search/query": inventory, "/infra/domains/default/security-policies": []}[path]
        client = Mock()
        client.items.side_effect = items
        client.get.return_value = {"results": []}
        report = nsx.audit(client)
        self.assertEqual(report["groups_scanned"], 3)
        self.assertEqual(report["system_groups_excluded"], 0)
        self.assertNotIn("container_groups_excluded", report)
        self.assertEqual(len(report["inventory"]["groups"]), 3)
        for row in report["inventory"]["groups"][1:]:
            self.assertEqual(row["usage"], "unused_candidate")
            self.assertEqual(row["membership"], "unknown")
            self.assertEqual(row["audit_exclusions"], [])
        self.assertEqual(report["objects"][0]["referenced_by"], [antrea["path"]])
        self.assertEqual(client.get.call_count, 12)
        html = rendered_document(report)
        self.assertNotIn('Antrea / container groups excluded', html)
        self.assertIn('class="sort-key"', html)
        self.assertIn('Reference count', html)

    def test_testing_mode_bounds_calls_and_withholds_partial_conclusions(self):
        domain = "/infra/domains/d"
        policy = domain + "/security-policies/p"
        group = domain + "/groups/g"
        rule = policy + "/rules/r"
        data = {"/infra/tags": [], "/infra/realized-state/virtual-machines": [], "/infra/domains": [{"path": domain}],
                domain + "/groups": [{"path": domain + "/groups/default", "is_default": True},
                                     {"path": group, "display_name": "sample"}],
                "/infra/services": [{"path": "/infra/services/default", "is_default": True},
                                    {"path": "/infra/services/custom"}],
                "/search/query": [], domain + "/security-policies": [{"path": policy}],
                policy + "/rules": [{"path": rule}],
                rule + "/statistics": [{"hit_count": 0}, {"hit_count": 15}]}
        def get(path, params=None):
            self.assertNotIn("cursor", params or {})
            if "/members/" in path:
                return {"results": []}
            return {"results": data[path], "cursor": "more", "result_count": 999}
        client = self.client_with_pages([])
        client.get.side_effect = get
        report = nsx.audit(client, workers=16, testing=True)
        self.assertTrue(report["testing"])
        self.assertEqual(report["performance"]["workers"], 1)
        self.assertEqual(client.retries, 0)
        self.assertEqual(client.get.call_count, 13)
        self.assertEqual(report["groups_scanned"], 1)
        self.assertEqual(report["custom_services_scanned"], 1)
        self.assertTrue(all(r["usage"] == "unknown" for r in report["objects"]))
        self.assertEqual(report["dfw"]["policies"][0]["status"], "unknown")
        self.assertEqual(report["dfw"]["rules"][0]["hit_status"], "unknown")
        self.assertIn("Testing sample — not a full audit", rendered_document(report))

    def test_repeated_reads_always_fetch_fresh_data(self):
        client = self.client_with_pages([{"results": [1]}, {"results": [2]}])
        self.assertEqual(list(client.items("/infra/services")), [1])
        self.assertEqual(list(client.items("/infra/services")), [2])
        self.assertEqual(client.get.call_count, 2)

    def test_transient_retries_are_bounded_and_counted(self):
        client = nsx.NSXClient("nsx.example.com", "admin", "dummy")
        client._get = Mock(side_effect=[nsx.AuditError("busy", 429), {"results": []}])
        with patch.object(nsx.time, "sleep"):
            client.get("/infra/services")
        self.assertEqual(client.metrics, {"requests": 2, "retries": 1})
        client._get = Mock(side_effect=nsx.AuditError("busy", 503))
        with patch.object(nsx.time, "sleep"), self.assertRaises(nsx.AuditError):
            client.get("/infra/services")
        self.assertEqual(client._get.call_count, 3)
        client._get = Mock(side_effect=nsx.AuditError("denied", 403))
        with self.assertRaises(nsx.AuditError):
            client.get("/infra/services")
        self.assertEqual(client._get.call_count, 1)

    def test_rule_fallbacks_in_one_policy_share_bounded_workers(self):
        rules = [{"path": "/p/rules/" + str(i), "id": str(i)} for i in range(4)]
        barrier = nsx.threading.Barrier(2)
        lock = nsx.threading.Lock()
        active = peak = 0
        def inventory(client, path):
            return [{"path": "/p"}] if path.endswith("security-policies") else rules
        def items(path, **kwargs):
            nonlocal active, peak
            if path == "/p/statistics":
                raise nsx.AuditError("bulk unavailable", 404)
            with lock:
                active += 1
                peak = max(active, peak)
            barrier.wait(timeout=3)
            with lock:
                active -= 1
            return [{"hit_count": int(path.split("/")[-2])}]
        client = Mock()
        client.items.side_effect = items
        with patch.object(nsx, "objects", side_effect=inventory):
            result, _ = nsx.audit_dfw(client, [{"path": "/d"}], workers=2)
        self.assertEqual(peak, 2)
        self.assertEqual([r["hit_count"] for r in result["rules"]], [0, 1, 2, 3])
        self.assertTrue(all(r["statistics_fallback_reason"] == "bulk unavailable" for r in result["rules"]))
        self.assertFalse(any("_pending_statistics" in r for r in result["rules"]))
        # Sequential mode must return the same classifications and evidence.
        client.items.side_effect = lambda path, **kw: ([] if path == "/p/statistics" else
                                                     [{"hit_count": int(path.split("/")[-2])}])
        with patch.object(nsx, "objects", side_effect=inventory):
            sequential, _ = nsx.audit_dfw(client, [{"path": "/d"}], workers=1)
        for a, b in zip(result["rules"], sequential["rules"]):
            for key in ("hit_status", "hit_count", "statistics", "configuration_fingerprint", "notes"):
                self.assertEqual(a[key], b[key])

    def test_incomplete_nested_bulk_and_unmapped_internal_ids_fall_back(self):
        rule = {"path": "/p/rules/a", "id": "a", "rule_id": 100}
        for counters in ({"results": [{"rule": "a", "hit_count": 0}], "result_count": 2},
                         {"results": [{"rule": "a", "hit_count": 0}], "cursor": "more"},
                         {"results": [{"internal_rule_id": "100", "hit_count": 0}]}):
            client = Mock()
            client.items.side_effect = [[{"enforcement_point": "ep", "statistics": counters}],
                                       [{"hit_count": 5}]]
            result = nsx.policy_statistics(client, {"path": "/p"}, [rule])[rule["path"]]
            self.assertEqual(result["hit_count"], 5)
            self.assertEqual(result["statistics_source"], "rule")
            self.assertTrue(result["statistics_fallback_reason"])

    def test_bulk_failure_cooldown_fetches_fresh_rules_without_extending_deadline(self):
        now = nsx.datetime.now(nsx.timezone.utc)
        retry = (now + nsx.timedelta(minutes=20)).isoformat()
        rule = {"path": "/p/rules/a", "id": "a", "policy_path": "/p",
                "statistics_fallback_reason": "GET /p/statistics: HTTP 500 failure",
                "statistics_bulk_retry_at": retry, "hit_count": 0}
        previous = {"manager": "nsx.example", "generated_at": now.isoformat(), "dfw": {"rules": [rule]}}
        client = Mock()
        client.statistics_backoff = nsx.statistics_backoff(previous, "nsx.example")
        client.items.return_value = [{"hit_count": 9}]
        result = nsx.policy_statistics(client, {"path": "/p"}, [rule])[rule["path"]]
        client.items.assert_called_once_with("/p/rules/a/statistics", page_size=None)
        self.assertEqual(result["hit_count"], 9)
        self.assertTrue(result["statistics_bulk_skipped"])
        self.assertEqual(result["statistics_bulk_retry_at"], retry)
        self.assertEqual(nsx.statistics_backoff(previous, "different.example"), {})
        self.assertEqual(nsx.statistics_backoff(dict(previous, testing=True), "nsx.example"), {})
        rule["statistics_bulk_retry_at"] = (now - nsx.timedelta(seconds=1)).isoformat()
        self.assertEqual(nsx.statistics_backoff(previous, "nsx.example"), {})
        client.statistics_backoff["/p"]["retry_at"] = rule["statistics_bulk_retry_at"]
        client.items.reset_mock()
        client.items.return_value = [{"rule": "a", "hit_count": 12}]
        recovered = nsx.policy_statistics(client, {"path": "/p"}, [rule])[rule["path"]]
        client.items.assert_called_once_with("/p/statistics", page_size=None)
        self.assertEqual(recovered["statistics_source"], "policy")
        self.assertEqual(recovered["hit_count"], 12)

    def test_policy_statistics_batches_rules(self):
        rules = [{"path": "/p/rules/a", "id": "a"}, {"path": "/p/rules/b", "id": "b"}]
        for response in ([{"rule": "a", "hit_count": 0}, {"rule": "b", "hit_count": 5}],
                         [{"enforcement_point": "ep", "statistics": [
                             {"rule": "a", "hit_count": 0}, {"rule": "b", "hit_count": 5}]}]):
            client = Mock()
            client.items.return_value = response
            results = nsx.policy_statistics(client, {"path": "/p"}, rules)
            self.assertEqual(results[rules[0]["path"]]["hit_status"], "zero_hits")
            self.assertEqual(results[rules[1]["path"]]["hit_count"], 5)
            client.items.assert_called_once_with("/p/statistics", page_size=None)

    def test_incomplete_policy_statistics_falls_back_only_for_missing_rule(self):
        rules = [{"path": "/p/rules/a", "id": "a"}, {"path": "/p/rules/b", "id": "b"}]
        client = Mock()
        client.items.side_effect = [[{"rule": "a", "hit_count": 0}], nsx.AuditError("missing", 404)]
        results = nsx.policy_statistics(client, {"path": "/p"}, rules)
        self.assertEqual(results[rules[0]["path"]]["hit_status"], "zero_hits")
        self.assertEqual(results[rules[1]["path"]]["hit_status"], "unknown")
        self.assertEqual(client.items.call_count, 2)

    def test_missing_enforcement_point_and_duplicate_counters_require_fallback(self):
        rule = {"path": "/p/rules/a", "id": "a"}
        for response in ([{"enforcement_point": "ep1", "statistics": [{"rule": "a", "hit_count": 0}]},
                          {"enforcement_point": "ep2", "statistics": []}],
                         [{"rule": "a", "hit_count": 0}, {"rule": "a", "hit_count": 0}]):
            client = Mock()
            client.items.side_effect = [response, nsx.AuditError("unavailable", 503)]
            self.assertEqual(nsx.policy_statistics(client, {"path": "/p"}, [rule])[rule["path"]]["hit_status"], "unknown")
            self.assertEqual(client.items.call_count, 2)

    def test_policy_checks_run_with_bounded_concurrency(self):
        barrier = nsx.threading.Barrier(2)
        lock = nsx.threading.Lock()
        active = peak = 0
        policies = [{"path": "/p/" + str(i)} for i in range(4)]
        def inventory(client, path):
            return policies if path.endswith("security-policies") else []
        def statistics(client, policy, rules, **kwargs):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            barrier.wait(timeout=3)
            with lock:
                active -= 1
            return {}
        with patch.object(nsx, "objects", side_effect=inventory), \
                patch.object(nsx, "policy_statistics", side_effect=statistics):
            report, _ = nsx.audit_dfw(Mock(), [{"path": "/d"}], workers=2)
        self.assertEqual(peak, 2)
        self.assertEqual([p["path"] for p in report["policies"]], [p["path"] for p in policies])




    def test_tags_usage_categories_scope_and_evidence(self):
        client = Mock()
        catalog = [{"scope": "app", "tag": name} for name in ("web", "vm-only", "unused")]
        catalog += [{"scope": "other", "tag": "web"}]
        vm = {"external_id": "vm-1", "display_name": "VM <one>", "tags": catalog[:2]}
        condition = {"resource_type": "Condition", "key": "Tag", "operator": "EQUALS", "value": "APP|WEB"}
        groups = [{"path": "/g", "_system_owned": True, "expression": [{"resource_type": "NestedExpression", "expressions": [condition]}],
                   "tags": [{"scope": "owner", "tag": "group-only"}]}]
        client.items.side_effect = [catalog, [vm, vm]]
        report = nsx.audit_tags(client, groups)
        rows = {(r["scope"], r["name"]): r for r in report["objects"]}
        self.assertEqual(rows[("app", "web")]["status"], "both")
        self.assertEqual(rows[("app", "web")]["vm_count"], 1)
        self.assertEqual(report["conditions"][report["condition_sets"][rows[("app", "web")]["condition_evidence_set"]][0]]["condition"], condition)
        self.assertEqual(rows[("other", "web")]["status"], "unknown")
        self.assertEqual(rows[("app", "vm-only")]["status"], "vm_only")
        self.assertEqual(rows[("app", "unused")]["status"], "unknown")
        self.assertEqual(rows[("owner", "group-only")]["status"], "group_only")
        self.assertEqual(len(rows), 5)
        self.assertEqual(client.items.call_count, 2)

    def test_profile_and_other_policy_tag_assignments(self):
        client = Mock()
        client.items.side_effect = [[], []]
        tag = {"scope": "owner", "tag": "team"}
        profile = {"path": "/infra/ipfix-dfw-profiles/a", "resource_type": "IPFIXDFWProfile",
                   "display_name": "Firewall export", "tags": [tag, tag]}
        other = {"path": "/infra/profiles/b", "resource_type": "AnotherProfile", "tags": [tag]}
        deleted = dict(other, path="/deleted", marked_for_delete=True)
        group = {"path": "/g", "resource_type": "Group", "tags": [tag]}
        report = nsx.audit_tags(client, [], resources=[profile, profile, other, deleted, group])
        row = report["objects"][0]
        self.assertEqual(row["status"], "other_only")
        self.assertEqual(row["other_count"], 2)
        self.assertEqual(row["other_assignments"][profile["path"]]["resource_type"], "IPFIXDFWProfile")
        self.assertEqual(nsx.tag_scopes(report)[0]["other_count"], 2)
        self.assertNotIn("unused", nsx.TAG_STATUSES)
        self.assertEqual(client.items.call_count, 2)
        # A directly retrieved replacement without tags overrides stale search tags.
        client.items.side_effect = [[], []]
        report = nsx.audit_tags(client, [], resources=[profile, dict(profile, tags=[])])
        self.assertEqual(report["objects"], [])

    def test_tag_firewall_references_follow_nested_groups_and_cycles(self):
        groups = [{"path": "/g"}, {"path": "/parent", "expression": [{"paths": ["/g", "/cycle"]}]},
                  {"path": "/cycle", "expression": [{"paths": ["/parent"]}]}]
        rule = {"path": "/p/rules/r", "resource_type": "Rule", "id": "r", "rule_id": 12,
                "display_name": "Allow", "disabled": True, "source_groups": ["/parent"]}
        deleted = dict(rule, path="/p/rules/deleted", marked_for_delete=True)
        tags = {"objects": [{"group_conditions": {"/g": "Group"}, "group_assignments": {}},
                            {"group_conditions": {}, "group_assignments": {"/parent": "Parent"}}]}
        nsx.add_tag_firewall_references(tags, groups, groups + [rule, deleted])
        self.assertEqual(len(tags["firewall_rules"]), 1)
        self.assertEqual(tags["firewall_rules"][0]["rule_id"], 12)
        self.assertTrue(tags["firewall_rules"][0]["disabled"])
        self.assertEqual(tags["objects"][0]["firewall_references"],
                         [{"rule": 0, "via_group": "/g", "tag_use": "condition"}])
        self.assertEqual(tags["objects"][1]["firewall_references"][0]["tag_use"], "assignment")

    def test_tag_catalog_assignment_counts(self):
        for fields, expected in (
            ({"tagged_objects_count": 10}, 10),
            ({"tagged_objects": 7}, 7),
            ({"tagged_objects_count": 0, "tagged_objects": 7}, 0),
            ({"tagged_objects_count": 10, "tagged_objects": 7}, 10),
            ({}, None),
            ({"tagged_objects_count": None}, None),
            ({"tagged_objects_count": -1}, None),
            ({"tagged_objects_count": True}, None),
            ({"tagged_objects_count": "10"}, None),
        ):
            with self.subTest(fields=fields):
                tag = {"scope": "s", "tag": "t", **fields}
                client = Mock()
                client.items.side_effect = [[tag], [{"id": "v", "tags": [tag]}]]
                group = {"path": "/g", "expression": [{"resource_type": "Condition",
                         "key": "Tag", "operator": "EQUALS", "value": "s|t"}]}
                row = nsx.audit_tags(client, [group])["objects"][0]
                self.assertEqual(row["tagged_objects"], expected)
                self.assertEqual(row["vm_count"], 1)
                self.assertEqual(row["group_count"], 1)
                self.assertEqual(client.items.call_count, 2)

    def test_tags_failed_inventory_and_unsupported_conditions_are_unknown(self):
        tag = {"scope": "s", "tag": "t"}
        group = {"path": "/g", "expression": [{"resource_type": "Condition", "key": "Tag", "operator": "MATCHES", "value": "s|.*"}]}
        for responses, groups, testing in (([[tag], nsx.AuditError("denied", 403)], [], False),
                                            ([[tag], []], [group], False), ([[tag], []], [], True),
                                            ([[tag], [{"id": "v", "tags": None}]], [], False)):
            with self.subTest(responses=responses):
                client = Mock(); client.items.side_effect = responses
                report = nsx.audit_tags(client, groups, testing=testing)
                self.assertEqual(report["objects"][0]["status"], "unknown")

    def test_tag_scope_free_prefix_condition_and_pagination(self):
        client = self.client_with_pages([
            {"results": [{"scope": "s", "tag": "web-prod"}], "result_count": 2, "cursor": "next"},
            {"results": [{"scope": "other", "tag": "unrelated"}]},
            {"results": [{"external_id": "v", "tags": [{"scope": "s", "tag": "web-prod"}]}]},
        ])
        group = {"path": "/g", "expression": [{"resource_type": "Condition", "key": "Tag", "operator": "STARTSWITH", "value": "|WEB"}]}
        result = nsx.audit_tags(client, [group])
        rows = {r["name"]: r for r in result["objects"]}
        self.assertEqual(rows["web-prod"]["status"], "both")
        self.assertEqual(rows["unrelated"]["status"], "unknown")
        self.assertEqual(client.get.call_count, 3)
        self.assertFalse(result["unsupported_conditions"])

    def test_unsupported_tag_condition_does_not_block_unrelated_categories(self):
        tags = [{"scope": scope, "tag": name} for scope, name in
                (("safe", "vm"), ("safe", "group"), ("safe", "unused"), ("affected", "vm"), ("affected", "group"))]
        vm = {"id": "vm", "tags": [tags[0], tags[3]]}
        group = {"path": "/g", "tags": [tags[1], tags[4]], "expression": [
            {"resource_type": "Condition", "key": "Tag", "operator": "MATCHES", "value": "affected|.*"}]}
        client = Mock(); client.items.side_effect = [tags, [vm]]
        report = nsx.audit_tags(client, [group])
        rows = {(r["scope"], r["name"]): r for r in report["objects"]}
        self.assertEqual(rows[("safe", "vm")]["status"], "vm_only")
        self.assertEqual(rows[("safe", "group")]["status"], "group_only")
        self.assertEqual(rows[("safe", "unused")]["status"], "unknown")
        self.assertEqual(rows[("affected", "vm")]["status"], "unknown")
        self.assertEqual(rows[("affected", "vm")]["vm_usage"], "used")
        self.assertEqual(rows[("affected", "vm")]["group_usage"], "unknown")
        self.assertTrue(rows[("affected", "vm")]["notes"])
        self.assertTrue(report["condition_sets"][rows[("affected", "vm")]["review_condition_set"]])
        self.assertEqual(rows[("affected", "group")]["status"], "group_only")

    def test_tag_api_errors_only_block_missing_usage_from_failed_source(self):
        tag = {"scope": "s", "tag": "t"}
        client = Mock(); client.items.side_effect = [nsx.AuditError("catalog denied", 403), [{"id": "v", "tags": [tag]}]]
        result = nsx.audit_tags(client, [])
        self.assertEqual(result["objects"][0]["status"], "vm_only")
        self.assertEqual(result["errors"], ["catalog denied"])
        client.items.side_effect = [[tag], nsx.AuditError("VM inventory denied", 403)]
        result = nsx.audit_tags(client, [{"path": "/g", "tags": [tag]}])
        self.assertEqual(result["objects"][0]["status"], "unknown")
        self.assertEqual(result["objects"][0]["vm_usage"], "unknown")
        self.assertEqual(result["objects"][0]["group_usage"], "used")

    def test_malformed_tag_condition_still_requires_review_without_known_scope(self):
        client = Mock(); client.items.side_effect = [[{"scope": "s", "tag": "t"}], []]
        group = {"path": "/g", "expression": [{"resource_type": "Condition", "key": "Tag", "value": "unparseable"}]}
        result = nsx.audit_tags(client, [group])
        self.assertEqual(result["objects"][0]["status"], "unknown")

    def test_tag_evidence_is_shared_without_losing_conditions(self):
        condition = {"resource_type": "Condition", "key": "Tag", "operator": "MATCHES", "value": "s|.*", "description": "x" * 2000}
        client = Mock(); client.items.side_effect = [[{"scope": "s", "tag": str(i)} for i in range(100)], []]
        result = nsx.audit_tags(client, [{"path": "/g", "expression": [condition]}])
        self.assertEqual(result["schema_version"], 2)
        self.assertEqual(len(result["conditions"]), 1)
        self.assertEqual(len(result["condition_sets"]), 2)
        self.assertEqual(result["conditions"][0]["condition"], condition)
        self.assertEqual(result["unsupported_conditions"], [0])
        self.assertEqual(result["unmatched_conditions"], [0])
        self.assertEqual(len({r["review_condition_set"] for r in result["objects"]}), 1)
        self.assertNotIn("review_conditions", result["objects"][0])
        encoded = nsx.json.dumps(result)
        self.assertEqual(encoded.count("x" * 2000), 1)

    def test_unscoped_tag_conditions_do_not_block_vm_only_tags(self):
        for value in ("WEB", "|WEB"):
            for operator in ("EQUALS", "CONTAINS", "STARTSWITH", "ENDSWITH"):
                with self.subTest(value=value, operator=operator):
                    tags = [{"scope": "app", "tag": "web"}, {"scope": "app", "tag": "vm-only"}]
                    client = Mock(); client.items.side_effect = [tags, [{"id": "v", "tags": tags}]]
                    condition = {"resource_type": "Condition", "key": "Tag", "member_type": "VirtualMachine", "operator": operator, "value": value}
                    result = nsx.audit_tags(client, [{"path": "/g", "expression": [condition]}])
                    rows = {r["name"]: r for r in result["objects"]}
                    self.assertEqual(rows["web"]["status"], "both")
                    self.assertEqual(rows["vm-only"]["status"], "vm_only")
                    self.assertEqual(rows["vm-only"]["group_usage"], "not_found")
                    self.assertEqual(rows["vm-only"]["group_review_reason"], "")
                    self.assertFalse(result["unsupported_conditions"])
                    self.assertEqual(client.items.call_count, 2)

    def test_unknown_group_condition_has_specific_reason(self):
        client = Mock(); client.items.side_effect = [[{"scope": "s", "tag": "t"}], [{"id": "v", "tags": [{"scope": "s", "tag": "t"}]}]]
        result = nsx.audit_tags(client, [{"path": "/g", "expression": [{"resource_type": "Condition", "key": "Tag", "operator": "MATCHES", "value": "s|.*"}]}])
        self.assertEqual(result["objects"][0]["group_review_reason"], "Unsupported group condition")
        self.assertEqual(result["objects"][0]["status"], "unknown")

    def test_tags_group_condition_without_vm_assignment(self):
        client = Mock(); client.items.side_effect = [[], []]
        group = {"path": "/g", "expression": [{"resource_type": "Condition", "key": "Tag", "operator": "EQUALS", "value": "s|missing"}]}
        report = nsx.audit_tags(client, [group])
        self.assertEqual(report["objects"][0]["status"], "group_only")
        self.assertFalse(report["objects"][0]["catalog"])

    def setUp(self):
        self.group = {"path": "/infra/domains/default/groups/g", "expression": []}

    def test_search_compatibility_retries_400_with_explicit_types(self):
        client = Mock()
        client.items.side_effect = [nsx.AuditError("unsupported resource type wildcard", 400),
                                    nsx.AuditError("resource type required", 400), iter([self.group])]
        resources, coverage = nsx.search_configuration(client)
        self.assertEqual(resources, [self.group])
        self.assertEqual(coverage["mode"], "explicit_types")
        self.assertEqual(len(coverage["rejected_queries"]), 2)
        self.assertIn("resource_type:Rule", coverage["query"])
        self.assertNotIn("*", coverage["query"])

    def test_search_compatibility_preserves_broad_scope_when_possible(self):
        client = Mock()
        client.items.side_effect = [nsx.AuditError("unsupported wildcard field", 400), iter([self.group])]
        resources, coverage = nsx.search_configuration(client)
        self.assertEqual(coverage["mode"], "all_types")
        self.assertEqual(coverage["query"], "*")
        self.assertEqual(resources, [self.group])

    def test_search_does_not_retry_authentication_or_pagination_failures(self):
        for error in (nsx.AuditError("HTTP 401", 401), nsx.AuditError("HTTP 403", 403),
                      nsx.AuditError("incomplete inventory")):
            client = Mock()
            client.items.side_effect = error
            with self.assertRaises(nsx.AuditError):
                nsx.search_configuration(client)
            self.assertEqual(client.items.call_count, 1)

    @patch.object(nsx.time, "sleep")
    def test_changing_search_restarts_and_discards_partial_pages(self, sleep):
        client = self.client_with_pages([
            {"results": [{"path": "/old"}], "result_count": 2, "cursor": "old-cursor"},
            {"results": [{"path": "/extra"}, {"path": "/third"}]},
            {"results": [{"path": "/fresh"}], "result_count": 1},
        ])
        calls = []
        get = client.get
        client.get = lambda path, params: (calls.append(dict(params)), get(path, params))[1]
        resources, coverage = nsx.search_configuration(client)
        self.assertEqual(resources, [{"path": "/fresh"}])
        self.assertNotIn("cursor", calls[2])
        self.assertEqual(coverage["inventory_retries"], 1)
        sleep.assert_called_once_with(2)

    @patch.object(nsx.time, "sleep")
    def test_changing_search_has_bounded_retries_and_never_returns_partial_data(self, sleep):
        client = self.client_with_pages([{"results": [{"path": "/partial"}], "result_count": 2}] * 3)
        with self.assertRaisesRegex(nsx.InventoryChanged, "after 3 attempt"):
            nsx.search_configuration(client)
        self.assertEqual(client.get.call_count, 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [2, 4])

    @patch.object(nsx.time, "sleep")
    def test_testing_search_does_not_retry_inventory_errors(self, sleep):
        client = Mock(testing=True)
        client.items.side_effect = nsx.InventoryChanged("incomplete/changing")
        with self.assertRaises(nsx.InventoryChanged):
            nsx.search_configuration(client)
        self.assertEqual(client.items.call_count, 1)
        sleep.assert_not_called()

    def test_search_failure_never_becomes_empty_inventory(self):
        client = Mock()
        client.items.side_effect = nsx.AuditError("query rejected", 400)
        with self.assertRaisesRegex(nsx.AuditError, "all search query variants"):
            nsx.search_configuration(client)
        self.assertEqual(client.items.call_count, 3)

    def test_http_error_includes_nsx_details_and_status(self):
        client = object.__new__(nsx.NSXClient)
        client.base_url = "https://nsx.example.com/policy/api/v1"
        client.timeout = 30
        client.headers = {}
        client.opener = Mock()
        body = io.BytesIO(b'{"error_code":60508,"module_name":"search","error_message":"Invalid resource type"}')
        client.opener.open.side_effect = nsx.HTTPError(client.base_url, 400, "Bad Request", {}, body)
        with self.assertRaises(nsx.AuditError) as caught:
            client.get("/search/query", {"query": "resource_type:*"})
        self.assertEqual(caught.exception.status_code, 400)
        self.assertIn("Invalid resource type", str(caught.exception))
        self.assertIn("60508", str(caught.exception))

    def test_html_report_escapes_inventory_and_counts_unique_findings(self):
        report = {"manager": "nsx.example.com", "generated_at": "2026-09-08T12:00:00+00:00",
                  "groups_scanned": 1, "custom_services_scanned": 0, "system_groups_excluded": 6,
                  "indexed_objects_scanned": 10, "scope": "Local Manager /infra",
                  "usage_definition": "No references found", "limitations": "Index visibility applies",
                  "objects": [{"kind": "group", "name": '<script>alert("x")</script>',
                               "path": "/infra/groups/a&b", "usage": "unused_candidate",
                               "membership": "empty", "referenced_by": [], "notes": ["<error>"]}]}
        html = rendered_document(report)
        self.assertNotIn('<script>alert("x")</script>', html)
        self.assertIn(r"\u003cscript>", html)
        self.assertIn(r"\u003cerror>", html)
        self.assertIn("a&b", html)
        self.assertIn("1 unique object(s) to review", html)
        self.assertIn("No objects in this category.", html)
        HTMLParser().feed(html)
        report["objects"][0].update(usage="referenced", membership="unknown",
                                     referenced_by=["/infra/rules/<rule>"])
        html = rendered_document(report)
        self.assertIn("Some checks need review", html)
        self.assertIn(r"/infra/rules/\u003crule>", html)
        stored_rows = nsx.json.loads(html.split('id="report-rows">', 1)[1].split('</script>', 1)[0])
        self.assertEqual(stored_rows["rows"][0]["data"]["referenced_by"], ["/infra/rules/<rule>"])
        # The unknown-membership and inventory tables share one stored row.
        self.assertEqual(sum(row["view"] == "inventory" for row in stored_rows["rows"]), 1)
        self.assertNotIn("<tbody><tr>", html)
        report["objects"] = []
        self.assertIn("0 unique object(s) to review", rendered_document(report))
        self.assertIn('aria-label="Report sections"', html)
        self.assertIn('data-panel id="zero-hit-rules"', html)
        self.assertIn('class="previous"', html)
        self.assertNotIn('id="naming-findings"', html)

    def test_rule_statistics_handles_wrapped_flat_and_multiple_enforcement_points(self):
        for entries, expected, count in (
            ([{"statistics": {"hit_count": 0}, "enforcement_point": "/ep/1"}], "zero_hits", 0),
            ([{"hit_count": 0}, {"statistics": {"hit_count": 7}}], "traffic_recorded", 7),
            ([{"hit_count": 0, "packet_count": 2}], "traffic_recorded", 0),
        ):
            with self.subTest(entries=entries):
                client = Mock()
                client.items.return_value = iter(entries)
                result = nsx.rule_statistics(client, {"path": "/rule"})
                self.assertEqual(result["hit_status"], expected)
                self.assertEqual(result["hit_count"], count)
                client.items.assert_called_once_with("/rule/statistics", page_size=None)

    def test_rule_statistics_missing_invalid_and_failed_are_unknown(self):
        for entries in ([], [{}], [{"hit_count": None}], [{"hit_count": -1}],
                        [{"hit_count": True}], [{"hit_count": "0"}],
                        [{"statistics": []}], [{"hit_count": 0}, {}],
                        [{"hit_count": 0, "error_message": "partial failure"}]):
            with self.subTest(entries=entries):
                client = Mock()
                client.items.return_value = iter(entries)
                result = nsx.rule_statistics(client, {"path": "/rule"})
                self.assertEqual(result["hit_status"], "unknown")
                self.assertIsNone(result["hit_count"])
                self.assertTrue(result["notes"])
        client.items.side_effect = nsx.AuditError("HTTP 403")
        self.assertEqual(nsx.rule_statistics(client, {"path": "/rule"})["hit_status"], "unknown")

    def test_dfw_empty_policy_disabled_rule_and_failed_inventory(self):
        domain = {"path": "/infra/domains/default"}
        policies = [{"path": domain["path"] + "/security-policies/" + name, "id": name}
                    for name in ("empty", "disabled-only", "unavailable")]
        rule = {"path": policies[1]["path"] + "/rules/r", "id": "r", "rule_id": 1042, "disabled": True}
        def items(path, **kwargs):
            if path == domain["path"] + "/security-policies":
                return iter(policies)
            if path == policies[0]["path"] + "/rules":
                return iter([])
            if path == policies[1]["path"] + "/rules":
                return iter([rule])
            if path == rule["path"] + "/statistics":
                return iter([{"statistics": {"hit_count": 0}}])
            raise nsx.AuditError("HTTP 403: " + path)
        client = Mock()
        client.items.side_effect = items
        dfw, config = nsx.audit_dfw(client, [domain])
        self.assertEqual([p["status"] for p in dfw["policies"]], ["empty", "has_rules", "unknown"])
        self.assertEqual([p["rule_count"] for p in dfw["policies"]], [0, 1, None])
        self.assertTrue(dfw["rules"][0]["disabled"])
        self.assertEqual(dfw["rules"][0]["rule_id"], 1042)
        self.assertEqual(dfw["rules"][0]["policy_rule_id"], "r")
        self.assertEqual(dfw["rules"][0]["hit_status"], "zero_hits")
        self.assertIn(rule, config)
        self.assertTrue(nsx.needs_review({"objects": [], "dfw": dfw}))
        self.assertEqual(len(dfw["errors"]), 1)
        report = {"objects": [], "dfw": dfw, "generated_at": "2026-09-08T12:00:00+00:00",
                  "groups_scanned": 0, "custom_services_scanned": 0, "system_groups_excluded": 0,
                  "indexed_objects_scanned": 0, "scope": "/infra", "usage_definition": "No references",
                  "limitations": "Snapshot"}
        html = rendered_document(report)
        self.assertIn("DFW inventory incomplete", html)
        self.assertIn("Counter &amp; rule details", html)
        self.assertIn('"disabled":true', html)
        zero_section = html.split('id="zero-hit-rules"', 1)[1].split('</section>', 1)[0]
        self.assertIn("No objects in this category.", zero_section)
        self.assertNotIn(rule["path"], zero_section)

    def test_missing_rule_id_is_not_replaced_by_policy_id(self):
        identity = nsx.firewall_rule_identity({"path": "/p/rules/policy-uuid"})
        self.assertEqual(identity, {"rule_id": None, "policy_rule_id": "policy-uuid"})
        self.assertIn("Rule ID: Not returned", nsx.rule_id_text(identity))

    def test_statistics_pagination_without_page_size(self):
        client = self.client_with_pages([
            {"results": [{"hit_count": 0}], "result_count": 2, "cursor": "next"},
            {"results": [{"hit_count": 3}], "cursor": "last"},
        ])
        result = nsx.rule_statistics(client, {"path": "/rule"})
        self.assertEqual(result["hit_count"], 3)
        self.assertNotIn("page_size", client.get.call_args.args[1])

    def test_references_include_disabled_rules_nested_groups_and_service_entries(self):
        service = {"path": "/infra/services/custom"}
        resources = [
            {"path": "/rule", "disabled": True, "source_groups": [self.group["path"]]},
            {"path": "/nested", "expression": [{"paths": [self.group["path"]]}]},
            {"path": "/service", "service_entries": [
                {"nested_service_path": service["path"] + "/service-entries/entry"}]},
            {"path": "/deleted", "marked_for_delete": True,
             "destination_groups": [self.group["path"]]},
            {"path": "/metadata", "description": self.group["path"],
             "parent_path": self.group["path"], "tags": [{"tag": service["path"]}]},
        ]
        refs = nsx.collect_references(resources + [self.group, service], [self.group, service])
        self.assertEqual(refs[self.group["path"]], {"/rule", "/nested"})
        self.assertEqual(refs[service["path"]], {"/service"})

    def test_service_self_and_child_references_do_not_count_as_usage(self):
        service = {"path": "/infra/services/test", "reference": "/infra/services/test",
                   "service_entries": [{"path": "/infra/services/test/service-entries/ip",
                                        "reference": "/infra/services/test"}]}
        child = {"path": "/infra/services/test/service-entries/ip",
                 "reference": "/infra/services/test"}
        refs = nsx.collect_references([service, child], [service])
        self.assertEqual(refs[service["path"]], set())
        other = {"path": "/infra/services/test-other", "service_entries": [
            {"nested_service_path": service["path"]}]}
        refs = nsx.collect_references([service, child, other], [service])
        self.assertEqual(refs[service["path"]], {other["path"]})

    def test_group_self_reference_does_not_count_as_usage(self):
        self.group["reference"] = self.group["path"]
        refs = nsx.collect_references([self.group], [self.group])
        self.assertEqual(refs[self.group["path"]], set())

    def test_realization_records_do_not_count_as_configuration_usage(self):
        service = {"path": "/infra/services/test"}
        rule = {"path": "/infra/domains/default/security-policies/Test_Policy/rules/drop",
                "source_groups": [self.group["path"]]}
        realized = [
            {"resource_type": "GenericPolicyRealizedResource", "intent_paths": [obj["path"]]}
            for obj in (self.group, service)
        ]
        resources = [self.group, service, rule] + realized
        refs = nsx.collect_references(resources, [self.group, service])
        self.assertEqual(refs[service["path"]], set())
        self.assertEqual(refs[self.group["path"]], {rule["path"]})
        # A real rule reference must still prevent an unused-service finding.
        rule["services"] = [service["path"]]
        refs = nsx.collect_references(resources, [self.group, service])
        self.assertEqual(refs[service["path"]], {rule["path"]})

    def test_empty_requires_successful_membership_checks(self):
        client = Mock()
        client.get.return_value = {"results": [], "result_count": 0}
        self.assertEqual(nsx.membership(client, self.group), ("empty", []))
        self.assertEqual(client.get.call_count, 4)

    def test_failed_membership_is_unknown(self):
        client = Mock()
        client.get.side_effect = nsx.AuditError("HTTP 403")
        self.assertEqual(nsx.membership(client, self.group)[0], "unknown")

    def test_vm_without_ip_is_nonempty(self):
        client = Mock()
        client.get.side_effect = [{"results": []}, {"results": [{"id": "vm-without-ip"}]}]
        self.assertEqual(nsx.membership(client, self.group)[0], "nonempty")

    def test_explicit_ip_range_is_nonempty(self):
        self.group["expression"] = [{"resource_type": "IPAddressExpression",
                                     "ip_addresses": ["192.0.2.0/24"]}]
        self.assertEqual(nsx.membership(Mock(), self.group)[0], "nonempty")

    def test_unsupported_members_are_unknown(self):
        self.group["expression"] = [{"resource_type": "Condition", "member_type": "IdentityGroup"}]
        client = Mock()
        client.get.return_value = {"results": []}
        self.assertEqual(nsx.membership(client, self.group)[0], "unknown")

    def client_with_pages(self, pages):
        client = object.__new__(nsx.NSXClient)
        client.get = Mock(side_effect=pages)
        return client

    def test_pagination_preserves_query_and_opaque_cursor(self):
        client = self.client_with_pages([
            {"results": [1], "result_count": 2, "cursor": "opaque+/="},
            {"results": [2]},
        ])
        self.assertEqual(list(client.items("/search/query", {"query": "resource_type:*"})), [1, 2])
        self.assertEqual(client.get.call_args.args[1]["cursor"], "opaque+/=")
        self.assertEqual(client.get.call_args.args[1]["query"], "resource_type:*")

    def test_truncated_inventory_fails(self):
        client = self.client_with_pages([{"results": [1], "result_count": 2}])
        with self.assertRaises(nsx.AuditError):
            list(client.items("/search/query"))

    def test_repeated_cursor_fails(self):
        client = self.client_with_pages([
            {"results": [1], "cursor": "a"}, {"results": [2], "cursor": "a"}])
        with self.assertRaises(nsx.AuditError):
            list(client.items("/search/query"))



if __name__ == "__main__":
    unittest.main()
