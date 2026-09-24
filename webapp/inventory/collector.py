"""Read-only NSX collection and report rendering for the web application.

Invoked by the background worker; configuration and results belong to the database.
Requires read access across Local Manager /infra Policy inventory and search.
Missing references and zero counters are review evidence, not deletion approval.
"""

import base64
import hashlib
import json
import logging
import re
import ssl
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
import time
from collections import Counter
from html import escape
from datetime import datetime, timezone, timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener, HTTPSHandler


LOG = logging.getLogger("nsx_inventory")
LOG.addHandler(logging.NullHandler())


class DiagnosticFormatter(logging.Formatter):
    """Redact credentials even when an exception happens to contain them."""

    def __init__(self, secrets=(), debug=True):
        super().__init__("%(asctime)s %(levelname)s [%(threadName)s] %(message)s"
                         if debug else "%(levelname)s: %(message)s")
        self.secrets = tuple(value for value in secrets if value)

    def format(self, record):
        message = super().format(record)
        for value in sorted(self.secrets, key=len, reverse=True):
            message = message.replace(value, "[REDACTED]")
        return message


def configure_logging(debug=False):
    # Configure only this script's logger, including repeated main() calls.
    for handler in LOG.handlers[:]:
        LOG.removeHandler(handler)
        handler.close()
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(DiagnosticFormatter(debug=debug))
    LOG.addHandler(handler)
    LOG.setLevel(logging.DEBUG if debug else logging.INFO)
    LOG.propagate = False


class AuditError(Exception):
    """A request or inventory completeness check failed."""

    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward credentials to a redirected endpoint.
        return None


class NSXClient:
    def __init__(self, manager, username, password, timeout=30, ca_bundle=None,
                 insecure=False, retries=2, ca_data=None):
        manager = manager if "://" in manager else "https://" + manager
        parsed = urlsplit(manager)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username
                or parsed.password or parsed.path not in ("", "/")
                or parsed.query or parsed.fragment):
            raise AuditError("--manager must be an HTTPS hostname or origin URL")
        self.base_url = manager.rstrip("/") + "/policy/api/v1"
        self.timeout = timeout
        context = ssl.create_default_context(cafile=ca_bundle, cadata=ca_data)
        if insecure:
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        self.opener = build_opener(NoRedirect(), HTTPSHandler(context=context))
        token = base64.b64encode((username + ":" + password).encode()).decode()
        self.headers = {"Authorization": "Basic " + token, "Accept": "application/json"}
        self.retries = retries
        self.testing = False
        self.context = context
        self.local = threading.local()
        self.local.opener = self.opener
        self.metrics_lock = threading.Lock()
        self.metrics = {"requests": 0, "retries": 0}

    def get(self, path, params=None):
        for attempt in range(getattr(self, "retries", 0) + 1):
            if hasattr(self, "metrics_lock"):
                with self.metrics_lock:
                    self.metrics["requests"] += 1
                    self.metrics["retries"] += int(attempt > 0)
            started = time.perf_counter()
            LOG.debug("GET %s attempt=%d timeout=%ss", path, attempt + 1, getattr(self, "timeout", "default"))
            try:
                data = self._get(path, params)
                LOG.debug("GET %s completed in %.3fs", path, time.perf_counter() - started)
                return data
            except AuditError as exc:
                LOG.debug("GET %s failed status=%s elapsed=%.3fs", path, exc.status_code,
                          time.perf_counter() - started)
                if exc.status_code not in (429, 502, 503, 504) or attempt == self.retries:
                    raise
                delay = min(2 ** attempt, 8)
                LOG.warning("GET %s returned HTTP %s; retry %d/%d in %ss",
                            path, exc.status_code, attempt + 1, self.retries, delay)
                time.sleep(delay)

    def _get(self, path, params=None):
        url = self.base_url + quote(path, safe="/")
        if params:
            url += "?" + urlencode(params)
        try:
            request = Request(url, headers=self.headers, method="GET")
            opener = self.opener
            if hasattr(self, "local"):
                if not hasattr(self.local, "opener"):
                    self.local.opener = build_opener(NoRedirect(), HTTPSHandler(context=self.context))
                opener = self.local.opener
            with opener.open(request, timeout=self.timeout) as response:
                data = json.load(response)
        except HTTPError as exc:
            detail = ""
            try:
                payload = json.loads(exc.read(16384))
                if isinstance(payload, dict):
                    detail = "; ".join("{}={}".format(k, payload[k]) for k in
                                       ("error_code", "module_name", "error_message", "details") if k in payload)
                    detail = " ".join(detail.split())[:1500]
            except (ValueError, OSError):
                pass
            message = "GET {}: HTTP {} {}".format(path, exc.code, exc.reason)
            if detail:
                message += " — " + detail
            raise AuditError(message, status_code=exc.code) from exc
        except (URLError, OSError, ValueError) as exc:
            raise AuditError("GET {}: {}".format(path, exc)) from exc
        if not isinstance(data, dict):
            raise AuditError("GET {}: expected a JSON object".format(path))
        return data

    def items(self, path, params=None, page_size=1000):
        params = dict(params or {})
        if getattr(self, "testing", False) is True:
            # Statistics endpoints may not accept page_size. Preserve their API
            # parameters but bound the records processed and never follow cursors.
            limit = 100 if path.endswith("/groups") or path == "/infra/services" else 1
            if page_size is not None:
                params["page_size"] = limit
            page = self.get(path, params)
            if not isinstance(page.get("results"), list):
                raise AuditError("GET {}: missing results array".format(path))
            yield from page["results"][:limit]
            return
        if page_size is not None:
            params["page_size"] = page_size
        seen = set()
        total = 0
        expected = None
        while True:
            page = self.get(path, params)
            results = page.get("results")
            if not isinstance(results, list):
                raise AuditError("GET {}: missing results array".format(path))
            if expected is None and "result_count" in page:
                expected = page["result_count"]
                if not isinstance(expected, int) or expected < 0:
                    raise AuditError("GET {}: invalid result_count".format(path))
            total += len(results)
            LOG.debug("Inventory %s: page=%d total=%d expected=%s continuation=%s",
                      path, len(results), total, expected, bool(page.get("cursor")))
            yield from results
            if expected is not None and total == expected:
                return
            cursor = page.get("cursor")
            if not cursor:
                if expected is not None and total != expected:
                    raise AuditError("GET {}: incomplete/changing inventory ({}/{})".format(
                        path, total, expected))
                return
            if cursor in seen:
                raise AuditError("GET {}: repeated pagination cursor".format(path))
            seen.add(cursor)
            params["cursor"] = cursor


def objects(client, path):
    result = list(client.items(path))
    if any(not isinstance(obj, dict) or not obj.get("path") for obj in result):
        raise AuditError("{} returned objects without Policy paths".format(path))
    return [obj for obj in result if not obj.get("marked_for_delete")]


# Identity and presentation fields are not configuration references.
NON_REFERENCE_FIELDS = {
    "path", "parent_path", "relative_path", "unique_id", "realization_id",
    "id", "display_name", "description", "tags", "remote_path",
}


def collect_references(resources, targets):
    aliases = {}
    references = {obj["path"]: set() for obj in targets}
    for obj in targets:
        aliases[obj["path"]] = obj["path"]
        if obj.get("remote_path"):
            aliases[obj["remote_path"]] = obj["path"]

    def visit(value, owner):
        if isinstance(value, dict):
            # Search also returns realization records whose intent paths link
            # back to the configured object. These are not policy consumers.
            if (value.get("marked_for_delete")
                    or value.get("resource_type") == "GenericPolicyRealizedResource"):
                return
            owner = value.get("path", owner)
            for key, child in value.items():
                if not key.startswith("_") and key not in NON_REFERENCE_FIELDS:
                    visit(child, owner)
        elif isinstance(value, list):
            for child in value:
                visit(child, owner)
        elif isinstance(value, str):
            # NestedServiceServiceEntry may reference a service entry path.
            target = aliases.get(value) or aliases.get(value.split("/service-entries/")[0])
            # A service/group and its own child objects do not establish
            # external usage. Keep references from other services and rules.
            owner_target = aliases.get(owner, owner)
            if target and owner_target != target and not owner_target.startswith(target + "/"):
                references[target].add(owner)

    for resource in resources:
        visit(resource, resource.get("path", resource.get("resource_type", "unknown")))
    return references


def firewall_rule_identity(rule):
    """Keep the numeric enforcement ID distinct from the Policy object ID."""
    return {"rule_id": rule.get("rule_id"),
            "policy_rule_id": rule.get("id") or rule["path"].rstrip("/").rsplit("/", 1)[-1]}


def rule_id_text(row):
    if "policy_rule_id" not in row:
        return ""
    return "Rule ID: {} | Policy rule ID: {}".format(
        row.get("rule_id") if row.get("rule_id") is not None else "Not returned", row["policy_rule_id"])


def group_definition(group):
    """Describe configured criteria; keep the original tree to preserve AND/OR logic."""
    methods, criteria = set(), []

    def visit(value):
        if isinstance(value, list):
            for child in value:
                visit(child)
        elif isinstance(value, dict):
            kind = value.get("resource_type", "")
            member = str(value.get("member_type", ""))
            if kind == "Condition":
                method = "Tag conditions" if str(value.get("key", "")).casefold() == "tag" else "Dynamic conditions"
                methods.add(method)
                criteria.append("{} · {} {} {}".format(member, value.get("key", ""),
                    value.get("operator", ""), value.get("value", "")))
            elif kind == "PathExpression":
                for path in value.get("paths", []):
                    method = ("Segments / ports" if "/segments/" in path else
                              "Nested groups" if "/groups/" in path else "Object paths")
                    methods.add(method)
                    criteria.append(path)
            elif kind in {"IPAddressExpression", "MACAddressExpression", "ExternalIDExpression"}:
                method, key = {"IPAddressExpression": ("IP addresses", "ip_addresses"),
                               "MACAddressExpression": ("MAC addresses", "mac_addresses"),
                               "ExternalIDExpression": ("Explicit objects", "external_ids")}[kind]
                methods.add(method)
                criteria.extend(str(v) for v in value.get(key, []))
            elif kind and kind not in {"NestedExpression", "ConjunctionOperator"}:
                methods.add(kind)
            for child in value.values():
                if isinstance(child, (dict, list)):
                    visit(child)
    definition = {"expression": group.get("expression", []),
                  "extended_expression": group.get("extended_expression", [])}
    visit(definition)
    return {"methods": sorted(methods) or ["No criteria"], "criteria": criteria,
            "definition": definition}


def membership(client, group):
    """Positive evidence wins; negative results require supported, successful checks."""
    issues = []
    group_types = group.get("group_type", [])
    if isinstance(group_types, str):
        group_types = [group_types]
    if any(str(kind).casefold() == "antrea" for kind in group_types):
        issues.append("Antrea membership requires review; container members are not checked by these endpoints")
    supported_types = {"VirtualMachine", "VIF", "Segment", "SegmentPort",
                       "LogicalSwitch", "LogicalPort", "IPAddress"}
    supported_expressions = {"Condition", "ConjunctionOperator", "NestedExpression",
                             "IPAddressExpression", "MACAddressExpression",
                             "ExternalIDExpression", "PathExpression"}

    def inspect(value):
        if isinstance(value, list):
            return any([inspect(child) for child in value])
        if not isinstance(value, dict):
            return False
        kind = value.get("resource_type")
        if kind and kind not in supported_expressions:
            issues.append("Unsupported membership expression: " + kind)
        member_type = value.get("member_type")
        if member_type and member_type not in supported_types:
            issues.append("Unsupported member type: " + member_type)
        literal = bool(value.get("ip_addresses") or value.get("mac_addresses"))
        # Referenced/nested groups may resolve other member types.
        if kind == "PathExpression" and value.get("paths"):
            issues.append("Path expression requires nested/member-type review")
        return inspect(value.get("expressions", [])) or literal

    explicit = inspect(group.get("expression", []))
    if group.get("extended_expression"):
        issues.append("Extended membership expressions require review")
    if explicit:
        return "nonempty", ["Contains explicit IP/MAC members"]
    for endpoint in ("ip-addresses", "virtual-machines", "logical-ports", "logical-switches"):
        try:
            # Only existence is needed; do not download a large membership list.
            page = client.get(group["path"] + "/members/" + endpoint, {"page_size": 1})
            if not isinstance(page.get("results"), list):
                raise AuditError("{}: missing results array".format(endpoint))
            count = page.get("result_count", 0)
            if not isinstance(count, int) or count < 0:
                raise AuditError("{}: invalid result_count".format(endpoint))
            if page["results"] or count > 0:
                return "nonempty", ["Resolved members found: " + endpoint]
            if page.get("cursor"):
                issues.append(endpoint + ": empty first page with continuation cursor")
        except AuditError as exc:
            issues.append(str(exc))
    return ("unknown", sorted(set(issues))) if issues else ("empty", [])


DFW_COUNTER_NOTE = (
    "Current NSX counter snapshot across returned enforcement points; observation start and "
    "last reset time are unknown. Zero recorded hits is a review candidate, not proof of "
    "historical non-use. Counters may be cached or reset; no counters are reset by this audit."
)


def rule_statistics(client, rule, entries=None):
    """Accept explicit counters only; absent/error/incomplete results are unknown."""
    result = {"hit_status": "unknown", "hit_count": None, "statistics": [], "notes": [],
              "statistics_checked_at": datetime.now(timezone.utc).isoformat()}
    try:
        if entries is None:
            entries = list(client.items(rule["path"] + "/statistics", page_size=None))
        if not entries:
            raise AuditError("No statistics returned for this rule")
        for entry in entries:
            if not isinstance(entry, dict):
                raise AuditError("Unexpected statistics result")
            # NSX schemas wrap counters in statistics; older responses are flat.
            counters = entry.get("statistics", entry)
            if not isinstance(counters, dict):
                raise AuditError("Missing rule statistics object")
            if any(entry.get(k) or counters.get(k) for k in ("error", "error_code", "error_message")):
                raise AuditError("NSX returned an error for an enforcement point")
            hits = counters.get("hit_count")
            if type(hits) is not int or hits < 0:
                raise AuditError("Missing or invalid hit_count; cannot classify as zero hits")
            # Preserve counters and their scope for review in JSON/HTML.
            sample = {"enforcement_point": entry.get("enforcement_point", "Aggregated / unspecified"),
                      "hit_count": hits}
            for key in ("packet_count", "byte_count", "session_count"):
                count = counters.get(key)
                if count is not None:
                    if type(count) is not int or count < 0:
                        raise AuditError("Invalid " + key)
                    sample[key] = count
            result["statistics"].append(sample)
        result["hit_count"] = sum(s["hit_count"] for s in result["statistics"])
        activity = any(s.get(k, 0) > 0 for s in result["statistics"]
                       for k in ("hit_count", "packet_count", "byte_count", "session_count"))
        result["hit_status"] = "traffic_recorded" if activity else "zero_hits"
        if activity and result["hit_count"] == 0:
            result["notes"].append("Other traffic counters are positive despite zero hit_count.")
    except AuditError as exc:
        result["notes"].append(str(exc))
    return result


HIT_HISTORY_NOTE = (
    "Last observed positive count is a saved audit snapshot, not the time of the last packet "
    "or a count of hits on that date. It can survive counter resets between audits. "
    "Traffic and resets between snapshots may be missed; pre-audit history is unavailable."
)


def retain_hit_history(report, previous=None):
    """Carry positive snapshots across runs without claiming a last-hit timestamp."""
    dfw = report.setdefault("dfw", {})
    dfw["hit_history_note"] = HIT_HISTORY_NOTE
    old_rules = {}
    if previous:
        if (previous.get("manager") != report.get("manager") or not report.get("manager")
                or previous.get("testing")):
            LOG.warning("Previous hit history ignored: manager differs/is missing or report is a testing sample.")
        else:
            old_rules = {r["path"]: r for r in previous.get("dfw", {}).get("rules", [])}

    def valid_snapshot(value):
        if not isinstance(value, dict) or type(value.get("hit_count")) is not int or value["hit_count"] <= 0:
            return None
        try:
            stamp = datetime.fromisoformat(value["observed_at"].replace("Z", "+00:00"))
            current = datetime.fromisoformat(report["generated_at"].replace("Z", "+00:00"))
            if stamp.tzinfo is None or current.tzinfo is None or stamp > current:
                return None
        except (KeyError, TypeError, ValueError, AttributeError):
            return None
        return {"observed_at": stamp.astimezone(timezone.utc).isoformat(), "hit_count": value["hit_count"]}

    def snapshot(row):
        if row.get("hit_status") != "traffic_recorded":
            return None
        return valid_snapshot({"observed_at": row.get("statistics_checked_at"), "hit_count": row.get("hit_count")})

    for row in dfw.get("rules", []):
        old = old_rules.get(row["path"], {})
        # A recreated rule must not inherit the previous object's observations.
        if any(row.get(key) != old.get(key) for key in ("rule_id", "policy_rule_id", "unique_id", "created_at")):
            old = {}
        candidates = [valid_snapshot(old.get("last_positive_observation")), snapshot(old)]
        if not report.get("testing"):
            candidates.append(snapshot(row))
        candidates = [value for value in candidates if value]
        row["last_positive_observation"] = max(candidates, key=lambda v: v["observed_at"]) if candidates else None


def statistics_backoff(previous, manager):
    """Reuse only short-lived endpoint failures, never old counters or findings."""
    if not previous or previous.get("testing") or previous.get("manager") != manager:
        return {}
    backoff = {}
    for rule in previous.get("dfw", {}).get("rules", []):
        policy = rule.get("policy_path", "")
        reason = rule.get("statistics_fallback_reason", "")
        if not policy or not reason.startswith("GET " + policy + "/statistics:"):
            continue
        try:
            retry = rule.get("statistics_bulk_retry_at")
            if retry:
                retry = datetime.fromisoformat(retry.replace("Z", "+00:00"))
            else:
                retry = datetime.fromisoformat(previous["generated_at"].replace("Z", "+00:00")) + timedelta(minutes=30)
            now = datetime.now(timezone.utc)
            if retry.tzinfo is not None and now < retry <= now + timedelta(minutes=30):
                backoff[policy] = {"reason": reason, "retry_at": retry.isoformat()}
        except (KeyError, ValueError, TypeError, AttributeError):
            continue
    return backoff


def policy_statistics(client, policy, rules, defer_fallback=False):
    """Fetch counters once per policy; fall back when bulk evidence is incomplete."""
    if not rules:
        return {}
    aliases = {}
    for rule in rules:
        for key in (rule["path"], rule.get("id", rule["path"].rsplit("/", 1)[-1]), rule.get("rule_id")):
            if key is not None:
                aliases.setdefault(str(key), set()).add(rule["path"])
    samples = {r["path"]: [] for r in rules}
    scopes = set()
    fallback_reason = None
    retry_at = None
    backoff = getattr(client, "statistics_backoff", {})
    cached = backoff.get(policy["path"]) if isinstance(backoff, dict) else None
    if cached:
        try:
            if datetime.fromisoformat(cached["retry_at"]) <= datetime.now(timezone.utc):
                cached = None
        except (KeyError, TypeError, ValueError):
            cached = None
    try:
        if cached:
            fallback_reason, retry_at = cached["reason"], cached["retry_at"]
            entries = []
        else:
            try:
                entries = list(client.items(policy["path"] + "/statistics", page_size=None))
            except AuditError:
                retry_at = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
                raise
        for entry in entries:
            if not isinstance(entry, dict) or any(entry.get(k) for k in ("error", "error_code", "error_message")):
                raise AuditError("Invalid policy statistics")
            scope = entry.get("enforcement_point", "Aggregated / unspecified")
            scopes.add(scope)
            counters = entry.get("statistics", entry)
            if isinstance(counters, dict) and "results" in counters:
                if (not isinstance(counters["results"], list) or
                        any(counters.get(k) for k in ("error", "error_code", "error_message"))):
                    raise AuditError("Invalid nested policy statistics")
                if (counters.get("cursor") or
                        ("result_count" in counters and counters["result_count"] != len(counters["results"]))):
                    raise AuditError("Incomplete nested policy statistics")
                counters = counters["results"]
            counters = counters if isinstance(counters, list) else [counters]
            for counter in counters:
                if not isinstance(counter, dict):
                    raise AuditError("Invalid policy counter")
                identity = counter.get("rule", counter.get("rule_id"))
                matches = aliases.get(str(identity), set())
                if len(matches) != 1:
                    raise AuditError("Unmapped or ambiguous policy counter (identity fields: {})".format(
                        ", ".join(k for k in ("rule", "rule_id", "internal_rule_id") if k in counter) or "none"))
                samples[next(iter(matches))].append({"enforcement_point": scope, "statistics": counter})
    except AuditError as exc:
        fallback_reason = str(exc)
        samples = {r["path"]: [] for r in rules}
    results = {}
    for rule in rules:
        entries = samples[rule["path"]]
        complete = bool(entries) and len(entries) == len(scopes) and {e["enforcement_point"] for e in entries} == scopes
        result = rule_statistics(client, rule, entries) if complete else None
        if result is None or result["hit_status"] == "unknown":
            reason = fallback_reason or ("; ".join(result["notes"]) if result else
                                         "Missing, duplicate or incomplete enforcement-point counters")
            LOG.debug("Policy statistics fallback for %s: %s", rule["path"], reason)
            result = {"_pending_statistics": True} if defer_fallback else rule_statistics(client, rule)
            result["statistics_source"] = "rule"
            result["statistics_fallback_reason"] = reason
            if retry_at:
                result["statistics_bulk_retry_at"] = retry_at
                result["statistics_bulk_skipped"] = bool(cached)
        else:
            result["statistics_source"] = "policy"
        results[rule["path"]] = result
    return results


def audit_dfw(client, domains, workers=4, testing=False):
    policies, rules, errors, configuration = [], [], [], []
    jobs = []
    for domain in domains:
        try:
            domain_policies = objects(client, domain["path"] + "/security-policies")
        except AuditError as exc:
            errors.append(str(exc))
            continue
        # Retrieve independent rule lists concurrently; retain deterministic output order.
        def read_rules(policy):
            try:
                return objects(client, policy["path"] + "/rules"), None
            except AuditError as exc:
                return None, str(exc)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            rule_lists = list(pool.map(read_rules, domain_policies))
        for policy, (policy_rules, error) in zip(domain_policies, rule_lists):
            configuration.append(policy)
            record = {"name": policy.get("display_name", policy.get("id", "")),
                      "path": policy["path"], "domain": domain["path"],
                      "category": policy.get("category", "Unspecified"),
                      "system_owned": bool(policy.get("_system_owned")),
                      "rule_count": None, "status": "unknown", "notes": []}
            policies.append(record)
            if error is not None:
                record["notes"].append(error)
                errors.append(error)
                continue
            record["rule_count"] = len(policy_rules)
            record["status"] = "empty" if not policy_rules else "has_rules"
            if testing:
                record.update(rule_count=None, status="unknown")
                record["notes"].append("Testing mode: only one rule sampled; policy emptiness was not assessed.")
            configuration.extend(policy_rules)
            jobs.append((policy, policy_rules))
            for rule in policy_rules:
                row = {"name": rule.get("display_name", rule.get("id", "")), "path": rule["path"],
                       "policy_name": record["name"], "policy_path": policy["path"],
                       "category": record["category"], "action": rule.get("action", "Unspecified"),
                       "disabled": bool(rule.get("disabled")),
                       "unique_id": rule.get("unique_id"), "created_at": rule.get("_create_time"),
                       "configuration_fingerprint": hashlib.sha256(json.dumps(
                           {"rule": {k: v for k, v in rule.items() if not k.startswith("_")},
                            "policy": {k: policy.get(k) for k in ("scope", "category", "sequence_number", "stateful")}},
                           sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
                       "system_owned": bool(rule.get("_system_owned") or record["system_owned"]),
                       "source_groups": rule.get("source_groups", []),
                       "destination_groups": rule.get("destination_groups", []),
                       "services": rule.get("services", []), "scope": rule.get("scope", policy.get("scope", []))}
                row.update(firewall_rule_identity(rule))
                rules.append(row)
    def check(job):
        policy, policy_rules = job
        LOG.debug("Checking DFW policy statistics: %s", policy["path"])
        if testing:
            results = {}
            for rule in policy_rules:
                result = rule_statistics(client, rule)
                result["hit_status"] = "unknown"
                result["notes"].append("Testing mode: statistics sampled; activity classification withheld.")
                results[rule["path"]] = result
            return results
        return policy_statistics(client, policy, policy_rules, defer_fallback=True)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        statistics = {}
        for result in pool.map(check, jobs):
            statistics.update(result)
        # All rule fallbacks share this bounded executor, including rules in one large policy.
        pending = [rule for _, policy_rules in jobs for rule in policy_rules
                   if statistics[rule["path"]].get("_pending_statistics")]
        def fallback(rule):
            result = rule_statistics(client, rule)
            result["statistics_source"] = "rule"
            result.update({key: value for key, value in statistics[rule["path"]].items()
                           if key.startswith("statistics_")})
            return rule["path"], result
        for path, result in pool.map(fallback, pending):
            statistics[path] = result
    for row in rules:
        row.update(statistics[row["path"]])
    return {"policies": policies, "rules": rules, "errors": errors,
            "counter_note": DFW_COUNTER_NOTE}, configuration


TAG_STATUSES = {"both": "VMs and groups", "vm_only": "VM use",
                "group_only": "Group use", "other_only": "Other resource use",
                "unknown": "Needs review"}
TAG_NOTE = ("Tags are identified by scope and value. Evidence includes VM assignments, group "
            "conditions, tags attached to groups, and assignments to visible indexed Policy objects "
            "such as firewall IPFIX profiles and other profiles. Group condition references do not "
            "prove resolved membership. Categories describe observed use, not exclusive assignment "
            "types. No unused-tag conclusion is made. Search coverage, indexing delays and access "
            "restrictions apply; tags absent from every source cannot be discovered.")


def tag_condition_parts(value):
    """NSX accepts unscoped tag values as well as scope|tag conditions."""
    if not isinstance(value, str) or not value or value.count("|") > 1:
        return None
    scope, tag = value.split("|", 1) if "|" in value else ("", value)
    return (scope, tag) if scope or tag else None


def audit_tags(client, groups, testing=False, resources=(), workers=1):
    """Compare the tag catalog, VM assignments and all inventoried group definitions."""
    errors, catalog, vms = [], [], []
    vm_errors, group_errors = [], []
    paths = ("/infra/tags", "/infra/realized-state/virtual-machines")
    def read_tags(path):
        try:
            records = list(client.items(path))
            if any(not isinstance(r, dict) for r in records):
                raise AuditError(path + ": invalid inventory record")
            return records, None
        except AuditError as exc:
            return [], str(exc)
    with ThreadPoolExecutor(max_workers=1 if testing else workers) as pool:
        inventories = list(pool.map(read_tags, paths))
    for destination, (records, error) in zip((catalog, vms), inventories):
        destination.extend(records)
        if error:
            errors.append(error)
            if destination is vms:
                vm_errors.append(error)
    records, conditions = {}, []

    def tag_record(tag):
        if (not isinstance(tag, dict) or not isinstance(tag.get("tag"), str)
                or not isinstance(tag.get("scope", ""), str)):
            raise AuditError("Invalid tag scope/value")
        key = (tag.get("scope", ""), tag["tag"])
        if key not in records:
            records[key] = {"name": key[1], "scope": key[0], "path": json.dumps(key),
                            "vms": {}, "group_conditions": {}, "group_assignments": {},
                            "catalog": False, "condition_evidence": [], "other_assignments": {}}
        return records[key]

    for tag in catalog:
        try:
            row = tag_record(tag)
            row["catalog"] = True
            # TagInfo uses tagged_objects_count; some API examples/responses
            # use tagged_objects. Keep one normalized field in our reports.
            count = tag.get("tagged_objects_count", tag.get("tagged_objects"))
            row["tagged_objects"] = count if type(count) is int and count >= 0 else None
        except AuditError as exc:
            errors.append(str(exc))
    for collection, field in ((vms, "vms"), (groups, "group_assignments")):
        source_errors = vm_errors if field == "vms" else group_errors
        for obj in collection:
            if obj.get("marked_for_delete"):
                continue
            identity = obj.get("path") or obj.get("external_id") or obj.get("id")
            if not identity:
                source_errors.append("VM/group inventory record has no identity")
                continue
            tags = obj.get("tags", [])
            if not isinstance(tags, list):
                source_errors.append("Invalid tags for " + identity)
                continue
            for tag in tags:
                try:
                    tag_record(tag)[field][identity] = obj.get("display_name", identity)
                except AuditError as exc:
                    source_errors.append(identity + ": " + str(exc))

    # Search provides arbitrary Policy types, including profiles. Direct group and
    # VM inventories remain authoritative for those assignment categories.
    group_paths = {g["path"] for g in groups}
    configuration = {obj["path"]: obj for obj in resources if obj.get("path")}
    for path, obj in configuration.items():
        if (obj.get("marked_for_delete") or path in group_paths
                or obj.get("resource_type") in {"Group", "VirtualMachine", "GenericPolicyRealizedResource"}):
            continue
        assigned = obj.get("tags", [])
        if not isinstance(assigned, list):
            errors.append("Invalid tags for " + path)
            continue
        for tag in assigned:
            try:
                tag_record(tag)["other_assignments"][path] = {
                    "name": obj.get("display_name", obj.get("id", path)),
                    "resource_type": obj.get("resource_type", "Policy object")}
            except AuditError as exc:
                errors.append(path + ": " + str(exc))

    def visit(value, group):
        if isinstance(value, list):
            for child in value:
                visit(child, group)
        elif isinstance(value, dict):
            if value.get("marked_for_delete"):
                return
            if value.get("resource_type") == "Condition" and value.get("key") == "Tag":
                conditions.append((group, value))
            for child in value.values():
                if isinstance(child, (dict, list)):
                    visit(child, group)
    for group in groups:
        if not group.get("marked_for_delete"):
            visit(group.get("expression", []), group)
            visit(group.get("extended_expression", []), group)
    condition_pool, condition_ids, condition_cache = [], {}, {}
    def condition_id(group, condition):
        identity = (group["path"], id(condition))
        if identity in condition_cache:
            return condition_cache[identity]
        evidence = {"group": group["path"], "condition": condition}
        key = json.dumps(evidence, sort_keys=True, separators=(',', ':'))
        if key not in condition_ids:
            condition_ids[key] = len(condition_pool)
            condition_pool.append(evidence)
        condition_cache[identity] = condition_ids[key]
        return condition_ids[key]

    condition_sets, set_ids, matched_ids = [], {}, set()
    def condition_set(ids):
        key = tuple(sorted(set(ids)))
        if key not in set_ids:
            set_ids[key] = len(condition_sets)
            condition_sets.append(list(key))
        return set_ids[key]

    unsupported = []
    supported = []
    known_tags = {(a.casefold(), b.casefold()) for a, b in records}
    for group, condition in conditions:
        parts = tag_condition_parts(condition.get("value"))
        # Scope-free conditions are matched against every observed scope.
        # Complex operators are retained for review rather than guessed.
        if (condition.get("operator") not in {"EQUALS", "CONTAINS", "STARTSWITH", "ENDSWITH"}
                or condition.get("scope_operator", "EQUALS") != "EQUALS"
                or parts is None):
            unsupported.append({"group": group["path"], "condition": condition})
            continue
        scope, tag = parts
        if scope and tag and condition["operator"] == "EQUALS" and (scope.casefold(), tag.casefold()) not in known_tags:
            tag_record({"scope": scope, "tag": tag})
            known_tags.add((scope.casefold(), tag.casefold()))
        supported.append((group, scope.casefold(), tag.casefold(), condition))
    def may_reference(condition, scope, tag):
        """Use understood constraints to bound uncertainty from an unsupported condition."""
        parts = tag_condition_parts(condition.get("value"))
        if parts is None:
            return True
        expected_scope, expected_tag = (part.casefold() for part in parts)
        if (expected_scope and condition.get("scope_operator", "EQUALS") == "EQUALS"
                and scope.casefold() != expected_scope):
            return False
        # Unknown/negative tag operators cannot safely exclude a tag in this scope.
        comparisons = {"EQUALS": lambda: tag.casefold() == expected_tag,
                       "CONTAINS": lambda: expected_tag in tag.casefold(),
                       "STARTSWITH": lambda: tag.casefold().startswith(expected_tag),
                       "ENDSWITH": lambda: tag.casefold().endswith(expected_tag)}
        comparison = comparisons.get(condition.get("operator"))
        return not expected_tag or comparison is None or comparison()

    exact_conditions, other_conditions = {}, []
    for index, (_, scope, tag, condition) in enumerate(supported):
        if condition["operator"] == "EQUALS":
            exact_conditions.setdefault((scope, tag), []).append(index)
        else:
            other_conditions.append(index)
    for (scope, tag), row in records.items():
        keys = {(scope.casefold(), tag.casefold()), ("", tag.casefold()),
                (scope.casefold(), ""), ("", "")}
        candidates = sorted(other_conditions + [index for key in keys for index in exact_conditions.get(key, [])])
        for index in candidates:
            group, expected_scope, expected_tag, condition = supported[index]
            if ((not expected_scope or scope.casefold() == expected_scope)
                    and (not expected_tag or {"EQUALS": lambda: tag.casefold() == expected_tag,
                         "CONTAINS": lambda: expected_tag in tag.casefold(),
                         "STARTSWITH": lambda: tag.casefold().startswith(expected_tag),
                         "ENDSWITH": lambda: tag.casefold().endswith(expected_tag)}[condition["operator"]]())):
                row["group_conditions"][group["path"]] = group.get("display_name", group["path"])
                cid = condition_id(group, condition)
                row["condition_evidence"].append(cid)
                matched_ids.add(cid)
        vm_used = bool(row["vms"])
        group_used = bool(row["group_conditions"] or row["group_assignments"])
        row["review_conditions"] = [condition_id({"path": item["group"]}, item["condition"]) for item in unsupported
                                    if may_reference(item["condition"], scope, tag)]
        vm_unknown = not vm_used and (testing or bool(vm_errors))
        group_unknown = not group_used and (testing or bool(group_errors) or bool(row["review_conditions"]))
        row["vm_usage"] = "used" if vm_used else "unknown" if vm_unknown else "not_found"
        row["group_usage"] = "used" if group_used else "unknown" if group_unknown else "not_found"
        row["vm_review_reason"] = ("Testing sample" if testing else "VM inventory unavailable or invalid") if vm_unknown else ""
        row["group_review_reason"] = (("Testing sample" if testing else "Group metadata unavailable or invalid")
                                      if testing or group_errors else "Unsupported group condition") if group_unknown else ""
        row["notes"] = []
        if vm_unknown:
            row["notes"].append("VM usage is unknown: " + ("testing sample" if testing else "; ".join(vm_errors)))
        if group_unknown:
            row["notes"].append("Group usage is unknown: " + ("testing sample" if testing else
                "; ".join(group_errors) or "unsupported conditions may reference this tag; see evidence"))
        row["other_count"] = len(row["other_assignments"])
        row["status"] = ("both" if vm_used and group_used else "other_only" if row["other_count"] and not vm_used and not group_used
                         else "unknown" if vm_unknown or group_unknown else "vm_only" if vm_used
                         else "group_only" if group_used else "unknown")
        if not vm_used and not group_used and not row["other_count"]:
            row["notes"].append("No assignment or group reference found in the visible snapshot; catalog/index timing or incomplete coverage may explain this. Not classified as unused.")
        row["condition_evidence_set"] = condition_set(row.pop("condition_evidence"))
        row["review_condition_set"] = condition_set(row.pop("review_conditions"))
        row["vm_count"] = len(row["vms"])
        row["group_count"] = len(set(row["group_conditions"]) | set(row["group_assignments"]))
    LOG.debug("Tags: %d VMs, %d groups, %d tags; statuses=%s", len(vms), len(groups),
             len(records), dict(Counter(row["status"] for row in records.values())))
    LOG.debug("Tag coverage: unsupported_conditions=%d vm_errors=%d group_errors=%d catalog_errors=%d",
              len(unsupported), len(vm_errors), len(group_errors), len(errors))
    all_ids = {condition_id(group, condition) for group, condition in conditions}
    unmatched = sorted(all_ids - matched_ids)
    unsupported_ids = sorted({condition_id({"path": item["group"]}, item["condition"]) for item in unsupported})
    return {"schema_version": 2, "conditions": condition_pool, "condition_sets": condition_sets,
            "unmatched_conditions": unmatched, "objects": sorted(records.values(), key=lambda r: (r["scope"], r["name"])),
            "errors": sorted(set(errors + vm_errors + group_errors)), "unsupported_conditions": unsupported_ids,
            "vm_inventory_complete": not testing and not vm_errors,
            "testing": testing, "vms_scanned": len(vms), "groups_scanned": len(groups),
            "note": TAG_NOTE, "statuses": TAG_STATUSES}


def tag_scopes(tags):
    """Summarize observed scopes, counting each VM/group once per scope."""
    scopes = {}
    for tag in tags.get("objects", []):
        scope = tag.get("scope", "")
        row = scopes.setdefault(scope, {"name": scope or "(empty scope)",
            "scope": scope, "path": json.dumps(scope), "tags": [], "vms": set(), "groups": set(), "other_resources": set()})
        row["tags"].append({"name": tag["name"], "status": tag["status"],
                            "vm_count": tag["vm_count"], "group_count": tag["group_count"], "other_count": tag.get("other_count", 0)})
        row["other_resources"].update(tag.get("other_assignments", {}))
        row["vms"].update(tag.get("vms", {}))
        row["groups"].update(tag.get("group_conditions", {}))
        row["groups"].update(tag.get("group_assignments", {}))
    for row in scopes.values():
        row["other_count"] = len(row.pop("other_resources"))
        row["tag_count"] = len(row["tags"])
        row["vm_count"] = len(row.pop("vms"))
        row["group_count"] = len(row.pop("groups"))
        row["tags"].sort(key=lambda tag: tag["name"])
    return sorted(scopes.values(), key=lambda row: row["scope"])


def add_tag_firewall_references(tags, groups, resources):
    """Trace tag-using groups through nested groups to visible firewall rules."""
    group_paths = {g["path"] for g in groups}
    # Last record wins so directly retrieved DFW configuration supersedes search.
    configuration = {r["path"]: r for r in resources if r.get("path")}
    refs = collect_references(configuration.values(), groups)
    rules = {path: r for path, r in configuration.items()
             if "/rules/" in path and not r.get("marked_for_delete")
             and (r.get("resource_type") == "Rule" or "rule_id" in r)}
    pool, indices, cache = [], {}, {}

    def consumers(group_path):
        if group_path in cache:
            return cache[group_path]
        pending, seen, found = [group_path], set(), set()
        while pending:
            path = pending.pop()
            if path in seen:
                continue
            seen.add(path)
            for owner in refs.get(path, ()):
                if owner in rules:
                    found.add(owner)
                elif owner in group_paths:
                    pending.append(owner)
        cache[group_path] = found
        return found

    for row in tags["objects"]:
        evidence = []
        for group_path in sorted(set(row["group_conditions"]) | set(row["group_assignments"])):
            for path in sorted(consumers(group_path)):
                if path not in indices:
                    rule = rules[path]
                    indices[path] = len(pool)
                    pool.append(dict(path=path, name=rule.get("display_name", rule.get("id", path)),
                                     disabled=bool(rule.get("disabled")), **firewall_rule_identity(rule)))
                evidence.append({"rule": indices[path], "via_group": group_path,
                                 "tag_use": "condition" if group_path in row["group_conditions"] else "assignment"})
        row["firewall_references"] = evidence
    tags["firewall_rules"] = pool
    tags["firewall_reference_note"] = ("Visible firewall rules referencing tag-using groups, directly or through nested groups. "
        "Includes disabled rules. Tags attached to groups are metadata; these references do not prove tag-based traffic matching. "
        "Search coverage and inventory gaps apply; absence of references is not proof of non-use.")


def needs_review(report):
    dfw = report.get("dfw", {})
    return (any(r["membership"] == "unknown" for r in report["objects"])
            or bool(report.get("tags", {}).get("errors"))
            or bool(report.get("tags", {}).get("unsupported_conditions"))
            or any(r["status"] == "unknown" for r in report.get("tags", {}).get("objects", []))
            or bool(dfw.get("errors"))
            or any(r["hit_status"] == "unknown" for r in dfw.get("rules", [])))


# Explicit configuration types for managers that reject unrestricted searches.
# Keep the fallback scope visible: this is not a claim to cover every indexed type.
REFERENCE_SEARCH_TYPES = (
    "Group", "Service", "SecurityPolicy", "Rule", "GatewayPolicy", "PolicyNatRule",
    "LBPool", "LBVirtualServer", "PolicyExcludeList", "RedirectionPolicy", "RedirectionRule",
    "IdsSecurityPolicy", "IdsRule", "EndpointPolicy", "EndpointRule", "IPFIXDFWProfile",
    "FloodProtectionProfileBindingMap", "SessionTimerProfileBindingMap", "IPFIXDFWCollectorProfile",
)


def search_configuration(client):
    """Retry HTTP 400 with alternate syntax, without hiding access or paging failures."""
    rejected = []
    queries = ("resource_type:*", "*", " OR ".join(
        "resource_type:" + kind for kind in REFERENCE_SEARCH_TYPES))
    for index, query in enumerate(queries):
        try:
            # Start each alternative from page one, discarding any partial attempt.
            resources = list(client.items("/search/query", {"query": query}))
            if any(not isinstance(obj, dict) for obj in resources):
                raise AuditError("Search returned a non-object result")
            return resources, {"query": query, "mode": "explicit_types" if index == 2 else "all_types",
                               "resource_types": list(REFERENCE_SEARCH_TYPES) if index == 2 else [],
                               "rejected_queries": rejected}
        except AuditError as exc:
            if exc.status_code != 400:
                raise
            rejected.append({"query": query, "error": str(exc)})
            LOG.warning("Search query rejected (HTTP 400); trying compatible query syntax." if index < 2
                        else "All search query variants were rejected.")
    raise AuditError("NSX rejected all search query variants. " + rejected[-1]["error"], status_code=400)


def audit(client, workers=4, testing=False, progress=None):
    # Phase notifications for the background worker.
    progress = progress or (lambda completed, stage: None)
    if testing:
        client.testing = True
        client.retries = 0
        workers = 1
        LOG.warning("Testing sample only; results are not a full audit.")
    started = phase = time.perf_counter()
    phases = {}
    progress(0, "Reading domains, groups and services")
    LOG.info("Reading domains, groups and services...")
    groups = []
    domains = objects(client, "/infra/domains")
    paths = [domain["path"] + "/groups" for domain in domains] + ["/infra/services"]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        inventories = list(pool.map(lambda path: objects(client, path), paths))
    for inventory in inventories[:-1]:
        groups.extend(inventory)
    services = inventories[-1]
    all_groups = groups
    # The UI name can differ from the ID/path; flags can also be absent.
    groups = [g for g in all_groups
              if not g.get("is_default") and not g.get("_system_owned")
              and g.get("id") != "DefaultMaliciousIpGroup"
              and (g.get("display_name") or "").strip().casefold() != "defaultmaliciousipgroup"
              and g["path"].rstrip("/").rsplit("/", 1)[-1] != "DefaultMaliciousIpGroup"]
    system_groups_excluded = len(all_groups) - len(groups)
    definitions = {g["path"]: group_definition(g) for g in groups}
    if testing:
        groups = groups[:1]
    # Built-in services must never appear as unused custom services.
    custom = [s for s in services if not s.get("is_default") and not s.get("_system_owned")]
    if testing:
        custom = custom[:1]
    phases["inventory"] = round(time.perf_counter() - phase, 2)
    phase = time.perf_counter()
    LOG.info("Scanning indexed Policy configuration references...")
    progress(1, "Scanning configuration references")
    resources, search_coverage = search_configuration(client)
    # A stale/empty index must not turn the entire inventory into unused objects.
    indexed_paths = {obj.get("path") for obj in resources}
    missing = {obj["path"] for obj in all_groups + services} - indexed_paths
    if missing and not testing:
        raise AuditError("Search index is missing {} inventory objects; retry after indexing".format(
            len(missing)))
    LOG.info("Reading DFW policies, rules and hit statistics...")
    progress(2, "Checking firewall rules and counters")
    phases["search"] = round(time.perf_counter() - phase, 2)
    phase = time.perf_counter()
    dfw, dfw_configuration = audit_dfw(client, domains, workers, testing=testing)
    phases["dfw"] = round(time.perf_counter() - phase, 2)
    phase = time.perf_counter()
    LOG.info("Checking membership for %d groups...", len(groups))
    progress(3, "Checking group membership and references")
    with ThreadPoolExecutor(max_workers=workers) as pool:
        memberships = dict(zip((g["path"] for g in groups), pool.map(lambda g: membership(client, g), groups)))
    refs = collect_references(resources + all_groups + services + dfw_configuration, groups + custom)
    rule_references = {}
    # Include searched gateway/other firewall rules as well as directly read DFW rules.
    # Direct inventory takes precedence over potentially stale search records.
    for resource in resources + dfw_configuration:
        path = resource.get("path") or ""
        if "/rules/" in path and (resource.get("resource_type") == "Rule" or "rule_id" in resource):
            rule_references[path] = dict(path=path, name=resource.get("display_name", resource.get("id", "")),
                                         **firewall_rule_identity(resource))
    for rule in dfw["rules"]:
        rule_references[rule["path"]] = {key: rule[key] for key in ("path", "name", "rule_id", "policy_rule_id")}
    rows = []
    group_paths = {g["path"] for g in groups}
    for index, obj in enumerate(groups + custom, 1):
        is_group = obj["path"] in group_paths
        row = {"kind": "group" if is_group else "custom_service",
               "name": obj.get("display_name", obj.get("id", "")), "path": obj["path"],
               "usage": "referenced" if refs[obj["path"]] else "unknown" if testing else "unused_candidate",
               "referenced_by": sorted(refs[obj["path"]]),
               "reference_details": [rule_references[path] for path in sorted(refs[obj["path"]]) if path in rule_references],
               "membership": "not_applicable", "notes": []}
        if is_group:
            LOG.debug("Group membership %d/%d: %s = %s", index, len(groups),
                     obj["path"], memberships[obj["path"]][0])
            row["membership"], row["notes"] = memberships[obj["path"]]
            row["membership_definition"] = definitions[obj["path"]]
            row["tags"] = obj.get("tags", [])
        rows.append(row)
    # Full inventory is separate from findings: exclusions remain visible without
    # issuing membership checks or unused-object conclusions for those objects.
    audited = {row["path"]: row for row in rows}
    inventory = {"groups": [], "services": []}
    for collection, key in ((all_groups, "groups"), (services, "services")):
        for obj in collection:
            is_group = key == "groups"
            reasons = []
            if obj.get("_system_owned"):
                reasons.append("System-owned object")
            if obj.get("is_default"):
                reasons.append("Default / built-in object")
            if is_group:
                if (obj.get("id") == "DefaultMaliciousIpGroup"
                        or obj["path"].rstrip("/").rsplit("/", 1)[-1] == "DefaultMaliciousIpGroup"
                        or (obj.get("display_name") or "").strip().casefold() == "defaultmaliciousipgroup"):
                    reasons.append("Default malicious IP group")
            row = audited.get(obj["path"])
            if row is None:
                row = {"kind": "group" if is_group else "custom_service",
                       "name": obj.get("display_name") or obj.get("id") or obj["path"].rsplit("/", 1)[-1],
                       "path": obj["path"], "usage": "not_assessed",
                       "membership": "not_assessed" if is_group else "not_applicable",
                       "referenced_by": [], "notes": reasons or ["Not assessed in this testing sample."]}
                if is_group:
                    row["membership_definition"] = group_definition(obj)
            row["inventory_type"] = "Group" if is_group else "Built-in service" if reasons else "Custom service"
            row["audit_exclusions"] = reasons
            inventory[key].append(row)
    phases["membership_and_references"] = round(time.perf_counter() - phase, 2)
    limitations = "Search is eventually consistent; non-indexed and RBAC-hidden references may be absent."
    if testing:
        limitations += " TESTING ONLY: one record per list and no pagination. No unused-object, policy-emptiness or rule-activity conclusions. Empty or excluded samples can leave checks unexercised."
        for row in rows:
            row["notes"].append("Testing mode: sampled reference search; absence of a reference does not establish non-use.")
    if search_coverage["mode"] == "explicit_types":
        limitations += (" Compatibility search is limited to these configuration types: "
                        + ", ".join(search_coverage["resource_types"])
                        + ". References from other types are outside this scan.")
    LOG.info("Checking tag inventory, VM assignments and group use...")
    progress(4, "Checking tags and VM assignments")
    tag_started = time.perf_counter()
    tags = audit_tags(client, all_groups, testing=testing, workers=workers, resources=resources + all_groups + services + dfw_configuration)
    tags["search_coverage"] = search_coverage
    add_tag_firewall_references(tags, all_groups, resources + all_groups + dfw_configuration)
    phases["tags"] = round(time.perf_counter() - tag_started, 2)
    return {"inventory": inventory, "tags": tags, "testing": testing, "generated_at": datetime.now(timezone.utc).isoformat(),
            "performance": {"workers": workers, "elapsed_seconds": round(time.perf_counter() - started, 2),
                            "phases_seconds": phases,
                            "http": dict(client.metrics) if isinstance(getattr(client, "metrics", None), dict) else {}},
            "scope": ("TESTING SAMPLE: first record per list, first domain only" if testing else "Local Manager /infra; references in visible Policy search index"),
            "usage_definition": "No configuration reference found; not a traffic analysis or deletion approval",
            "limitations": limitations, "search_coverage": search_coverage,
            "groups_scanned": len(groups), "custom_services_scanned": len(custom),
            "system_groups_excluded": system_groups_excluded,
            "indexed_objects_scanned": len(resources), "objects": rows, "dfw": dfw}


def display_number(value):
    """Group quantities for display without changing stored values or identifiers."""
    return format(value, ",") if type(value) in (int, float) else str(value)


def display_timestamp(value):
    """Readable UTC timestamps for presentation; preserve original report data."""
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.tzinfo is not None:
            return stamp.astimezone(timezone.utc).strftime("%d %b %Y · %H:%M UTC")
    except (ValueError, AttributeError, TypeError):
        pass
    return str(value)


def performance_description(report):
    performance = report.get("performance", {})
    http = performance.get("http", {})
    concurrency = performance.get("concurrency", {})
    if concurrency.get("mode") == "automatic":
        return ("Saved audit retrieval: " if report.get("rendered_from_saved_report") else "Fresh retrieval: ") + (
            "{} HTTP requests, {} retries; {} seconds. Automatic concurrency limits: started at {}, peaked at {}, "
            "finished at {} concurrent requests (maximum {}).").format(
                display_number(http.get("requests", "—")), display_number(http.get("retries", 0)),
                display_number(performance.get("elapsed_seconds", "—")), concurrency["initial"],
                concurrency["peak"], concurrency["final"], concurrency["maximum"])
    return ("TESTING SAMPLE — " if report.get("testing") else "") + ("Previously imported snapshot: " if report.get("rendered_from_saved_report") else "Fresh retrieval: ") + "{} HTTP requests, {} retries; {} seconds, {} workers.".format(
        display_number(http.get("requests", "—")), display_number(http.get("retries", 0)),
        display_number(performance.get("elapsed_seconds", "—")), display_number(performance.get("workers", "—")))


def overview_charts(groups, services, rules, testing=False):
    """Render disjoint snapshot categories as accessible, self-contained SVG charts."""
    def usage_slices(items, all_anchor, unused_anchor):
        counts = Counter(row.get("usage") for row in items)
        return [("Referenced", counts["referenced"], "#087f8c", all_anchor),
                ("Unused candidates", counts["unused_candidate"], "#b76b13", unused_anchor),
                ("Unknown", counts["unknown"], "#7955b2", all_anchor),
                ("Not assessed", len(items) - sum(counts[k] for k in
                 ("referenced", "unused_candidate", "unknown")), "#8493a6", all_anchor)]

    activity = Counter("disabled" if row.get("disabled") else row.get("hit_status") for row in rules)
    charts = [
        ("Group usage", "Configuration references across inventoried groups.",
         usage_slices(groups, "all-groups", "unused-groups")),
        ("Service usage", "Configuration references across inventoried services; built-ins are not assessed.",
         usage_slices(services, "all-services", "unused-services")),
        ("Firewall rule activity", "Disabled rules are separate. Enabled rules use the current counter snapshot.",
         [("Traffic recorded", activity["traffic_recorded"], "#087f8c", "dfw-rules"),
          ("Zero recorded hits", activity["zero_hits"], "#b76b13", "zero-hit-rules"),
          ("Disabled", activity["disabled"], "#8493a6", "disabled-rules"),
          ("Unknown", len(rules) - sum(activity[k] for k in
           ("traffic_recorded", "zero_hits", "disabled")), "#7955b2", "unknown-statistics")]),
    ]
    output = '<div class="overview-charts" aria-label="Inventory snapshot charts">'
    for index, (title, description, slices) in enumerate(charts):
        total = sum(count for _, count, _, _ in slices)
        title_id = "snapshot-chart-" + str(index)
        output += '<article class="snapshot-chart" aria-labelledby="{}"><h3 id="{}">{}</h3><p>{}</p>'.format(
            title_id, title_id, escape(title), escape(description))
        if not total:
            output += '<p class="chart-empty">No objects inventoried in this snapshot.</p></article>'
            continue
        summary = "; ".join("{}: {:,}".format(label, count) for label, count, _, _ in slices)
        output += '<div class="chart-content"><svg class="donut-chart" viewBox="0 0 120 120" role="img" aria-label="{}">'.format(
            escape(title + ". " + summary, quote=True))
        output += '<circle cx="60" cy="60" r="46" fill="none" stroke="#e9eef4" stroke-width="13"/>'
        offset = 0
        for label, count, color, _ in slices:
            if count:
                percent = 100 * count / total
                output += ('<circle cx="60" cy="60" r="46" fill="none" stroke="{}" stroke-width="13" '
                           'pathLength="100" stroke-dasharray="{:.6f} {:.6f}" stroke-dashoffset="{:.6f}" '
                           'transform="rotate(-90 60 60)"><title>{}: {:,} ({:.1f}%)</title></circle>').format(
                               color, percent, 100 - percent, -offset, escape(label), count, percent)
                offset += percent
        output += '<text x="60" y="58" text-anchor="middle" class="chart-total">{:,}</text><text x="60" y="75" text-anchor="middle" class="chart-caption">{}</text></svg>'.format(
            total, "sampled" if testing else "inventoried")
        output += '<ul class="chart-legend">'
        for label, count, color, anchor in slices:
            output += ('<li><a href="#{}"><span class="chart-key" style="background:{}" aria-hidden="true"></span>'
                       '<span>{}</span><strong>{:,}</strong><small>{:.1f}%</small></a></li>').format(
                           anchor, color, escape(label), count, 100 * count / total)
        output += '</ul></div></article>'
    return output + '</div><p class="chart-note">' + (
        'Testing sample only; proportions do not represent the full inventory. ' if testing else '') + (
        'Each chart counts every inventoried object once. Empty-group findings may overlap usage categories. '
        'Zero counters do not prove historical non-use. Select a legend entry to open its report page.</p>')


def dfw_overview(dfw, testing=False):
    """Configuration indicators, not a workload coverage or effective-policy score."""
    rules, policies = dfw.get("rules", []), dfw.get("policies", [])
    enabled = [r for r in rules if not r.get("disabled")]
    allows = [r for r in enabled if r.get("action", "").upper() == "ALLOW"]

    def dimension(row, key):
        values = row.get(key)
        if not isinstance(values, list) or not values or any(not isinstance(v, str) or not v for v in values):
            return "unknown"
        return "any" if any(v.upper() == "ANY" for v in values) else "specific"

    bounded = sum(all(dimension(r, key) == "specific" for key in
                      ("source_groups", "destination_groups")) for r in allows)
    broad = sum(any(dimension(r, key) == "any" for key in
                    ("source_groups", "destination_groups")) for r in allows)
    unknown = sum(any(dimension(r, key) == "unknown" for key in
                      ("source_groups", "destination_groups")) for r in allows)
    incomplete = testing or bool(dfw.get("errors")) or any(p.get("status") == "unknown" for p in policies)
    percentage = "Not assessed" if incomplete else "N/A" if not allows else "{:.1f}%".format(100 * bounded / len(allows))
    html = '<section data-panel id="dfw-overview" tabindex="-1"><h2>Distributed firewall overview</h2>'
    html += '<p>Configuration and current counter indicators for the inventoried DFW rules, including system-owned rules.</p>'
    if incomplete:
        html += '<p class="overview">Partial inventory or testing sample: counts describe retrieved objects only; the segmentation percentage is withheld.</p>'
    html += '<div class="overview"><h3>Segmentation indicator: ALLOW rules with specific endpoints</h3>'
    html += '<h2>{}</h2><p>{:,} of {:,} enabled ALLOW rules specify both sources and destinations without ANY.</p>'.format(percentage, bounded, len(allows))
    html += '<p>This is a rule configuration proxy, not the percentage of workloads that are microsegmented. Specific groups can still be broad or empty. Effective isolation also depends on group membership, rule order, Applied to, default rules and enforcement. No workload-level coverage is calculated.</p>'
    html += '<p>{:,} enabled ALLOW rules have missing or invalid endpoint information; they remain in the denominator. With no enabled ALLOW rules, the percentage is N/A.</p></div>'.format(unknown)
    cards = [
        ("Policies", len(policies), "dfw-policies"),
        ("Rules", len(rules), "dfw-rules"),
        ("Enabled rules", len(enabled), "dfw-rules"),
        ("Disabled rules", len(rules) - len(enabled), "disabled-rules"),
        ("Enabled rules recording traffic", sum(r.get("hit_status") == "traffic_recorded" for r in enabled), "dfw-rules"),
        ("Enabled rules with zero recorded hits", sum(r.get("hit_status") == "zero_hits" for r in enabled), "zero-hit-rules"),
        ("Enabled rules with unknown activity", sum(r.get("hit_status") not in {"traffic_recorded", "zero_hits"} for r in enabled), "unknown-statistics"),
        ("Empty policies", sum(p.get("status") == "empty" for p in policies), "empty-policies"),
    ]
    html += '<div class="cards">' + ''.join('<a class="card" href="#{}"><span>{}</span><b>{:,}</b><small>Open related rules or policies →</small></a>'.format(anchor, label, count) for label, count, anchor in cards) + '</div>'
    html += '<h3>Enabled ALLOW rules to review</h3><p>These indicators can overlap and are review prompts, not confirmed policy errors.</p><ul>'
    for label, count in [
        ("ANY source or destination", broad),
        ("ANY source and destination", sum(all(dimension(r, k) == "any" for k in ("source_groups", "destination_groups")) for r in allows)),
        ("ANY in the services field", sum(dimension(r, "services") == "any" for r in allows)),
        ("ANY Applied to scope", sum(dimension(r, "scope") == "any" for r in allows)),
        ("Missing or invalid service information", sum(dimension(r, "services") == "unknown" for r in allows)),
        ("Missing or invalid Applied to scope", sum(dimension(r, "scope") == "unknown" for r in allows)),
    ]:
        html += '<li>{}: <strong>{:,}</strong></li>'.format(label, count)
    html += '</ul><p>Service indicators use the saved services field; inline service entries are not evaluated here.</p><p><a href="#dfw-rules">Review rule definitions and evidence</a></p>'
    html += '<h3>Enabled rule actions</h3><ul>'
    for action, count in sorted(Counter(r.get("action", "Unspecified") for r in enabled).items()):
        html += '<li>{}: <strong>{:,}</strong></li>'.format(escape(action), count)
    html += '</ul><h3>Policy categories</h3><div class="table-wrap"><table><thead><tr><th>Category</th><th>Policies</th><th>Enabled rules</th><th>Disabled rules</th></tr></thead><tbody>'
    for category in sorted({p.get("category", "Unspecified") for p in policies} | {r.get("category", "Unspecified") for r in rules}):
        category_rules = [r for r in rules if r.get("category", "Unspecified") == category]
        html += '<tr><td>{}</td><td>{:,}</td><td>{:,}</td><td>{:,}</td></tr>'.format(escape(category), sum(p.get("category", "Unspecified") == category for p in policies), sum(not r.get("disabled") for r in category_rules), sum(bool(r.get("disabled")) for r in category_rules))
    html += '</tbody></table></div><p class="muted">' + escape(DFW_COUNTER_NOTE) + '</p></section>'
    return html


def feature_guide():
    """Standalone help for report readers; no connection or external assets needed."""
    content = '''<section data-panel id="feature-guide" tabindex="-1">
<div class="section-eyebrow">Help &amp; coverage</div><h2>Report user guide</h2>
<p>Use this guide to understand what the report checks, how to explore its results and what each finding means. This report displays a saved database snapshot. Opening it or filtering it does not modify NSX.</p>

<h3>Navigation and report information</h3>
<p>Expand sidebar categories to reveal their pages. The highlighted link identifies your current page. Cards and chart legends open related report pages. Sidebar counts describe the whole category, while table counts reflect your current filters.</p>
<p><strong>Manager</strong> identifies the audited NSX Manager. <strong>Generated</strong> is the audit timestamp. Fresh retrieval describes the collection run; saved audit retrieval means the HTML was regenerated from existing JSON without new NSX requests. Request, retry, elapsed-time and worker figures describe the saved collection run. A dash means that information was not recorded.</p>
<p><strong>Audit completed</strong> means no checks triggered the report's incomplete-check flag; it is not a security certification. <strong>Some checks need review</strong> indicates unknown membership, statistics or tag evidence, or collection errors. Cleanup candidates may exist with either status. <strong>Testing sample</strong> is a limited diagnostic run, not a representative full audit.</p>

<h3>Inventory overview, charts and findings</h3>
<p>The <a href="#overview">inventory overview</a> provides shortcuts to groups, services, firewall policies and rules. Its charts show group usage, service usage and firewall rule activity. Each chart counts an inventoried object once; disabled rules are separate from enabled-rule activity. Legend percentages use that chart's inventory total. Testing chart proportions describe only the sample.</p>
<p>Finding cards count objects in each category. Categories can overlap: one group can be both empty and an unused candidate. The unique-object review total removes duplicate paths across the included cleanup, membership and DFW categories; it is not a count of every tag or coverage issue.</p>

<h3>Tables: search, sorting and pagination</h3>
<p><strong>Export CSV</strong> downloads all rows matching the current table search and applied column filters, across all pagination pages, in the selected sort order. Evidence is included in the exported columns even when the search evidence toggle is off. Invalid search expressions must be corrected before export. Overview tables export their filtered rows, and evidence-dialog tables export their displayed rows. Files open as CSV in spreadsheet applications; values that could be interpreted as formulas are prefixed with an apostrophe.</p>
<p><strong>Search this table</strong> searches available row data, including names, paths and evidence, without regard to letter case. Choose Plain text or Regex under Syntax, and Contains or Does not contain under Match. In Plain text mode, combine conditions with <code>AND</code> and <code>OR</code>, ignoring operator case: <code>Condition 1 AND Condition 2</code> requires both conditions anywhere in the searched row; <code>prod OR stage</code> requires either. AND runs before OR: <code>prod AND web OR stage</code> means both prod and web, or stage. Spaces within each condition form a literal phrase. Operators must be separate words; quote literal phrases containing them, such as <code>"Sales and Marketing"</code>. Does not contain excludes rows matching the whole expression. Regex is case-insensitive; for example, <code>prod|stage</code> matches either word. An invalid expression displays an error until corrected. An empty search applies no restriction, including in Does not contain mode. With <strong>Include membership and evidence</strong> enabled, search also matches related objects and information in popups. Turn it off to search only the object’s own name and path. This prevents a group from matching solely because the searched group appears in its membership or references. The toggle also applies to Regex and Does not contain. Column filters remain independent. Search stays within that report page.</p>
<p><strong>Rows</strong> controls how many results appear at once. Previous and Next move between pages. A count such as 26–50 of 143 means 143 objects pass the current filters. Sorting and filtering apply to the full table, not just the visible rows.</p>
<p>Choose <strong>Sort by</strong> and <strong>Order</strong>, or click the sort arrow beside a column heading. Clicking again reverses the order. Quantities sort numerically, and missing sort values appear last. Commas group large quantities for readability; identifiers retain their original values.</p>
<p><strong>0 results</strong> means nothing passed the filters on this page. Clear the searches, open the relevant All objects page to broaden the search. Filters are kept while navigating within the open report but are not saved when you reload it.</p>

<p><strong>Column filters:</strong> click any column heading to choose Contains or Does not contain and enter text. Available values lists the distinct values in that column after the current table search and column filters, with matching row counts across all pagination pages. Type to narrow these suggestions, or select a value to fill the Text field. Up to 100 suggestions are displayed at once. Apply filter searches that column across all rows on the current page of the report, before pagination. Combine several columns to narrow the results; every filter and the table search must pass. Evidence columns include expandable details. Matches ignore letter case and use displayed text, including formatted quantities. Filter chips show active conditions; click a chip to remove it, or Clear column filters to remove them all. Sorting uses the separate arrow beside the heading. Filters remain while navigating the open report and reset on reload.</p>
<h3>Groups, services and reference evidence</h3>
<p><a href="#all-groups">All groups</a> and <a href="#all-services">All services</a> show retrieved inventory. Recent reports also include excluded system and built-in objects, labeled <strong>Excluded from findings</strong>. Older saved reports may contain only the objects originally assessed.</p>
<ul>
<li><strong>Referenced:</strong> a visible configuration object refers to this group or service. Open View evidence to see the consumer paths and available firewall rule identities.</li>
<li><strong>Unused candidate:</strong> no configuration reference was found in the checked snapshot. This is not proof of zero traffic or approval to delete the object.</li>
<li><strong>Unknown:</strong> usage or membership could not be established, for example in a testing sample or after an unsupported/failed check.</li>
<li><strong>Not assessed:</strong> this object was excluded from the check or was outside the assessed testing sample.</li>
<li><strong>Not applicable:</strong> the check does not apply, such as group membership for a service.</li>
</ul>
<p>Disabled rules and references from other groups or services still count as usage. Self-references and realization records do not. Built-in services and default/system groups are excluded from cleanup findings. Antrea/container groups are included in reference checks. Unsupported membership checks are reported as Unknown.</p>
<p><strong>Empty</strong> means the supported membership checks returned no members. <strong>Has members</strong> means explicit IP/MAC entries or resolved members were found. An empty group may still be referenced by a rule. Membership methods identify tag conditions, other dynamic conditions, IP/MAC addresses, explicit objects, nested groups or segment/port paths. The membership-definition popup shows configured criteria; its JSON preserves AND/OR structure and is not a resolved member list.</p>

<h3>Distributed firewall overview and segmentation indicator</h3>
<p>The <a href="#dfw-overview">DFW overview</a> summarizes policies, enabled/disabled rules, activity, empty policies, enabled-rule actions and policy categories. System-owned DFW rules are included. Related-page links may open a broader table than the card's counted subset.</p>
<p><strong>Segmentation indicator</strong> = enabled ALLOW rules with both specific sources and specific destinations ÷ all enabled ALLOW rules × 100. For example, 60 qualifying rules out of 100 gives 60%. A list containing ANY is not specific; missing/invalid endpoint lists do not qualify and remain in the denominator. No enabled ALLOW rules gives N/A. Testing or incomplete DFW inventory gives Not assessed.</p>
<p>This percentage describes rule configuration, not workload microsegmentation coverage or effective isolation. A specific group can still be broad or empty. Rule ordering, membership, default rules, Applied to and enforcement affect the actual outcome.</p>
<p><strong>Enabled ALLOW rules to review</strong> counts ANY endpoints, ANY services-field entries, ANY Applied to scopes and missing service/scope information. These categories overlap and do not establish a policy error. Inline service entries are not evaluated by these indicators. The actions and category summaries describe configured rules; they do not calculate effective traffic decisions.</p>

<h3>Firewall policies, rule identities and counters</h3>
<p><strong>Rules → Applied to DFW</strong> lists rules whose saved Applied to scope contains ANY, including enabled, disabled and system-owned rules. Rules scoped to specific objects, or with missing scope information, are omitted. Open evidence to inspect Applied to. This is a configuration inventory view, not a finding that the rule is incorrect.</p>
<p><strong>Rules → Empty groups</strong> lists DFW rules that directly reference a group confirmed empty by the saved audit in Sources, Destinations or Applied to. Disabled rules are included. Open View evidence to see each empty group's name, path and reference field. Unknown or unassessed groups are omitted, and nested groups are not expanded. This finding does not determine effective rule behavior. The page supports the same search, evidence toggle, column filters and sorting as other rule pages.</p>
<p><a href="#dfw-policies">Policies</a> show their category and retrieved rule count. <strong>Empty</strong> means no non-deleted rules, including disabled rules, were present. <strong>Has rules</strong> means at least one rule exists. Unknown means the list was unavailable or emptiness was withheld for testing.</p>
<p><a href="#dfw-rules">Rules</a> show their policy, action, enabled/disabled state and activity. <strong>Rule ID</strong> is the numeric enforcement identifier; <strong>Policy rule path</strong> identifies the Policy object. The shortened path shows its end; hover to see the full path or click to copy it. Evidence includes sources, destinations, services, Applied to scopes, collection time, returned enforcement points and counters.</p>
<ul>
<li><strong>Traffic recorded:</strong> at least one returned hit, packet, byte or session counter is positive. Other positive counters can establish activity even when hit_count is zero.</li>
<li><strong>Zero recorded hits:</strong> returned counters contain no positive activity. This is a review candidate, not proof the rule has never been used.</li>
<li><strong>Unknown:</strong> statistics were missing, invalid, incomplete or unavailable, or classification was withheld for testing.</li>
<li><strong>Disabled:</strong> the rule is configured as disabled. It may retain historical counters and still reference groups/services.</li>
</ul>
<p>Displayed hits sum the returned samples. Counters may be cached or reset, and their observation start/reset times are unknown. The audit never resets them. <strong>Last observed positive count</strong> is a retained positive audit snapshot, not the last packet time or hits during that day. It requires saved history from the same manager and matching rule identity; unavailable means no usable positive snapshot was saved. Traffic between audits may be missed.</p>

<h3>Tags, scopes and firewall references</h3>
<p><a href="#tags-all">Tags</a> are identified by scope and value together. An empty scope means an unscoped tag. VM assignments, tag conditions in group definitions, tags attached to groups and other indexed Policy assignments are separate kinds of evidence.</p>
<ul>
<li><strong>VMs and groups:</strong> evidence exists in both categories.</li>
<li><strong>VM use / Group use:</strong> use was observed in that category; these labels do not rule out other-resource assignments.</li>
<li><strong>Other resource use:</strong> assignments were found on other Policy objects. Its sidebar page includes every tag with such assignments, even if also used by VMs/groups.</li>
<li><strong>Needs review:</strong> usage evidence is inconclusive or incomplete. No tag is declared unused.</li>
</ul>
<p>VM, group and other-resource counts identify unique objects for that tag. Group counts combine membership-condition references and attached metadata tags. <a href="#tags-scopes">Scopes</a> combine tags and count each VM/group/other resource once per scope.</p>
<p>View details separates assignments, matching conditions and conditions requiring review. A group condition referencing a tag does not prove resolved membership. Firewall references follow tag-using groups through nested groups to visible rules, including disabled rules; attached group tags are metadata and do not prove tag-based traffic matching.</p>
<p>The NSX catalog assignment count is the count reported by NSX, not a count of group-condition references. Unavailable does not mean zero. Tags absent from every source cannot be discovered. <a href="#tags-coverage">Tag coverage</a> explains collection gaps and unsupported conditions.</p>

<h3>Evidence popups, copying and coverage limits</h3>
<p>View evidence and View details open a dialog. Expand its sections for more information. JSON blocks have line numbers, syntax highlighting and Copy buttons. Close the dialog with Close, Escape or a click outside it. Copying copies text only; it does not execute anything.</p>
<p>Interactive tables display the audit data saved in the workspace database. Choose a different snapshot to review earlier results, or run a new collection to refresh the inventory.</p>
<p><a href="#coverage">Audit scope &amp; exclusions</a> describes Local Manager /infra coverage and collection errors. NSX-V, legacy Manager objects, Global Manager and project inventories are outside scope. The dedicated firewall audit covers DFW; gateway/other firewall rules may still appear as configuration-reference evidence.</p>
<p>Policy search is eventually consistent and may omit non-indexed or inaccessible objects. A compatibility search can be limited to listed resource types. Findings depend on the audited account's visibility. Empty tables or missing references do not by themselves prove absence across the environment. Review coverage and evidence before deciding on cleanup or policy changes.</p>
</section>'''


    # Keep the detailed explanations, with searchable topics and a short starting point.
    intro, *topics = re.split(r"<h3>(.*?)</h3>", content.replace("</section>", ""))
    output = intro + '''<div class="guide-start"><h3>Start here</h3><ol>
<li><strong>Check coverage.</strong> Confirm the audit scope and any incomplete checks.</li>
<li><strong>Explore findings.</strong> Open a category, then narrow results with search or column filters.</li>
<li><strong>Read the evidence.</strong> Review object references and counters before deciding what to change.</li></ol>
<div class="guide-shortcuts"><a href="#coverage">Check audit coverage →</a><a href="#overview">Explore findings →</a><a href="#dfw-overview">Review firewall →</a></div></div>
<div class="guide-tools"><label>Find a help topic<input id="guide-search" type="search" placeholder="Try filters, empty groups or counters"></label><button type="button" id="guide-expand">Expand all</button><button type="button" id="guide-collapse">Collapse all</button></div>
<p id="guide-status" class="muted" aria-live="polite"></p><div class="guide-topics">'''
    for title, body in zip(topics[::2], topics[1::2]):
        output += '<details data-guide-topic><summary>' + title + '</summary><div class="guide-topic-body">' + body + '</div></details>'
    return output + '</div></section>'


def rules_with_empty_group_evidence(rules, groups):
    """Annotate direct group references using confirmed empty audit results only."""
    empty_groups = {g["path"]: g for g in groups if g.get("membership") == "empty"}
    annotated = []
    for rule in rules:
        evidence = []
        for field, label in (("source_groups", "Source"), ("destination_groups", "Destination"),
                             ("scope", "Applied to")):
            for path in dict.fromkeys(rule.get(field) or []):
                if path in empty_groups:
                    group = empty_groups[path]
                    evidence.append({"path": path, "name": group.get("name", path), "field": label})
        annotated.append(dict(rule, empty_group_references=evidence))
    return annotated


DIALOG_STYLES = """
/* Shared dialog presentation for reports and the environment workspace. */
.copy-path,dialog .copy-path{display:inline-block;max-width:100%;min-height:0;padding:0 2px;
  border:0;border-radius:3px;background:transparent;color:var(--teal);font:inherit;
  vertical-align:bottom;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
  direction:rtl;text-align:left;text-decoration:underline;text-underline-offset:3px;cursor:copy}
#detail-dialog,.column-filter-dialog,#collection-results{
  --ink:#203249;--muted:#52647a;--line:#dfe6ee;--teal:#087e83;
  max-height:85vh;overflow:auto;border:1px solid var(--line);border-radius:12px;
  padding:24px 28px;color:var(--ink);background:#fff;box-shadow:0 24px 80px #142c4330;
  font:14px/1.7 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
}
#detail-dialog::backdrop,.column-filter-dialog::backdrop,#collection-results::backdrop{background:rgba(15,30,45,.55)}
dialog .dialog-heading{display:flex;align-items:center;justify-content:space-between;gap:20px;
  position:sticky;top:-24px;z-index:2;background:#fff;padding:12px 0;margin:0 0 16px;border-bottom:1px solid var(--line)}
dialog .dialog-heading h2{font-size:24px;line-height:1.35;letter-spacing:-.5px;margin:0;overflow-wrap:anywhere}
dialog .dialog-heading button{flex-shrink:0}
dialog button{font:12px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;min-height:36px;
  padding:8px 12px;border:1px solid #d5dfe9;border-radius:7px;background:#fff;color:var(--ink);cursor:pointer}
dialog button:hover:not(:disabled){background:#edf6f8;border-color:#9cbec7}
dialog :is(button,input,select):focus-visible{outline:2px solid var(--teal);outline-offset:3px}
.column-filter-dialog label{font-size:13px;font-weight:600;color:var(--ink);gap:7px;margin:16px 0}
.column-filter-dialog input,.column-filter-dialog select{font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
  padding:10px 12px;border:1px solid #d5dfe9;border-radius:7px;color:var(--ink);background:#fff}
.column-filter-dialog p,#collection-results p{font-size:13px;color:var(--muted);line-height:1.7}
.filter-actions{display:flex;flex-wrap:wrap;gap:8px;justify-content:flex-end;margin-top:20px;padding-top:16px;border-top:1px solid var(--line)}
.filter-actions button[value="apply"]{background:var(--teal);border-color:var(--teal);color:#fff}
.filter-actions button[value="apply"]:hover{background:#06666b}
#collection-results ul{list-style:none;padding:0;margin:16px 0 0;display:grid;gap:12px}
#collection-results li{padding:14px 16px;border:1px solid var(--line);border-radius:8px;overflow-wrap:anywhere}
#collection-results li a{font-weight:600;color:var(--teal)}
#collection-results .collection-error{margin:10px 0 0;padding:10px 12px;background:#f7f9fb;border:1px solid var(--line);border-radius:7px}
@media(max-width:600px){#detail-dialog,.column-filter-dialog,#collection-results{padding:18px}
  dialog .dialog-heading{top:-18px;gap:12px}dialog .dialog-heading h2{font-size:21px}}
"""


def _render_report(report, *, fragments):
    """Build a self-contained report; escape all inventory values."""
    def safe(value):
        return escape(display_number(value), quote=True)

    rows = report["objects"]
    unused_groups = [r for r in rows if r["kind"] == "group" and r["usage"] == "unused_candidate"]
    empty = [r for r in rows if r["membership"] == "empty"]
    unused_services = [r for r in rows if r["kind"] == "custom_service" and r["usage"] == "unused_candidate"]
    unknown = [r for r in rows if r["membership"] == "unknown"]
    dfw = dict(report.get("dfw", {"policies": [], "rules": [], "errors": []}))
    dfw["rules"] = rules_with_empty_group_evidence(dfw["rules"], rows)
    empty_group_rules = [r for r in dfw["rules"] if r["empty_group_references"]]
    dfw_scope_rules = [r for r in dfw["rules"] if isinstance(r.get("scope"), list)
                       and any(isinstance(value, str) and value.upper() == "ANY"
                               for value in r["scope"])]
    empty_policies = [p for p in dfw["policies"] if p["status"] == "empty"]
    zero_rules = [r for r in dfw["rules"] if r["hit_status"] == "zero_hits" and not r["disabled"]]
    disabled_rules = [r for r in dfw["rules"] if r["disabled"]]
    unknown_rules = [r for r in dfw["rules"] if r["hit_status"] == "unknown"]
    unknown_policies = [p for p in dfw["policies"] if p["status"] == "unknown"]
    review_count = len({r["path"] for r in unused_groups + empty + unused_services + unknown
                       + empty_policies + zero_rules + disabled_rules + unknown_rules + unknown_policies
                       + empty_group_rules})
    full_inventory = report.get("inventory", {})
    all_group_rows = full_inventory.get("groups", [r for r in rows if r["kind"] == "group"])
    all_service_rows = full_inventory.get("services", [r for r in rows if r["kind"] == "custom_service"])
    charts = overview_charts(all_group_rows, all_service_rows, dfw["rules"], report.get("testing", False))
    row_pool, row_ids = [], {}

    def table_shell(headers, items, view):
        indices = []
        for row in sorted(items, key=lambda r: (r["name"].casefold(), r["path"])):
            key = (view, row["path"])
            if key not in row_ids:
                record = dict(row)
                row_ids[key] = len(row_pool)
                row_pool.append({"view": view, "data": record})
            indices.append(row_ids[key])
        return ('<div class="table-widget view-' + view + '" data-rows="' + ",".join(map(str, indices))
                + '"><div class="table-tools" hidden>'
                '<label>Search this table <input type="search" placeholder="prod AND web OR staging" title="Plain text: combine conditions with AND / OR. AND runs first. Quote literal phrases containing AND or OR."></label>'
                '<label>Match <select class="search-mode"><option value="contains">Contains</option><option value="excludes">Does not contain</option></select></label>'
                '<label>Syntax <select class="search-syntax"><option value="text">Plain text</option><option value="regex">Regex</option></select></label>'
                '<label class="search-related"><input class="search-evidence" type="checkbox" checked> Include membership and evidence</label>'
                '<label>Rows <select class="page-size"><option>25</option><option>50</option><option>100</option></select></label>'
                '<label>Sort by <select class="sort-key"><option value="name">Name</option>'
                '<option value="scope">Scope</option><option value="status">Status</option><option value="vm_count">VM count</option><option value="group_count">Group count</option><option value="path">Path</option><option value="membership">Membership status</option>'
                '<option value="method">Membership method</option><option value="references">Reference count</option>'
                '<option value="usage">Usage</option><option value="hit_count">Hit count</option>'
                '<option value="rule_count">Rule count</option><option value="rule_id">Rule ID</option><option value="policy_rule_id">Policy rule ID</option></select></label>'
                '<label>Order <select class="sort-order"><option value="asc">Ascending</option>'
                '<option value="desc">Descending</option></select></label>'
                '<span class="search-error" role="alert"></span>'
                '<span class="page-status" aria-live="polite"></span>'
                '<button type="button" class="previous">Previous</button>'
                '<button type="button" class="next">Next</button></div>'
                '<div class="table-wrap"><table><thead><tr>'
                + "".join('<th scope="col">{}</th>'.format(safe(h)) for h in headers)
                + '</tr></thead><tbody></tbody></table></div>'
                '<p class="no-matches" hidden>No matching objects. Try another search.</p></div>')

    def dfw_table(items):
        if not items:
            return '<p class="empty-state">No objects in this category.</p>'
        return table_shell(["Object / path", "Policy / category", "Status", "Count", "Evidence & notes"], items, "dfw")

    def table(items):
        if not items:
            return '<p class="empty-state">No objects in this category.</p>'
        if "membership" not in items[0]:
            return dfw_table(items)
        return table_shell(["Object / path", "Type", "Usage", "Membership", "Evidence & notes"], items, "inventory")

    categories = [
        ("unused-groups", "Unused groups", unused_groups,
         "No configuration references found. Confirm the group is no longer needed before cleanup."),
        ("empty-groups", "Empty groups", empty,
         "No members found by the supported checks. Referenced empty groups may affect policy behavior."),
        ("unused-services", "Unused custom services", unused_services,
         "No configuration references found. Built-in and system-owned services are excluded."),
        ("unknown-membership", "Membership needs review", unknown,
         "Membership could not be determined. Review the evidence and resolve failed or unsupported checks."),
        ("empty-policies", "Empty DFW policies", empty_policies,
         "The complete rule list contains no rules. Policies with disabled rules are not empty."),
        ("zero-hit-rules", "DFW rules with zero hits", zero_rules, DFW_COUNTER_NOTE),
        ("disabled-rules", "Disabled DFW rules", disabled_rules,
         "Disabled rules are listed separately, regardless of historical counters. Their configuration references still count."),
        ("unknown-statistics", "DFW statistics need review", unknown_rules,
         "Missing, invalid or failed statistics are unknown, never assumed to be zero."),
        ("empty-group-rules", "DFW rules with empty groups", empty_group_rules,
         "Rules directly referencing a confirmed empty group in Sources, Destinations or Applied to. "
         "Includes disabled rules. Open evidence to see the groups and reference fields. "
         "Unknown or unassessed membership is not treated as empty; nested groups are not expanded. "
         "This is a snapshot review finding, not a determination of effective rule behavior."),
    ]
    cards = "".join('<a class="card" href="#{}"><span>{}</span><b>{}</b><small>View details →</small></a>'.format(
        anchor, title, display_number(len(items))) for anchor, title, items, _ in categories)
    sections = "".join('<section data-panel id="{}" tabindex="-1"><h2>{} <span class="count">{}</span></h2><p class="muted">{}</p>{}</section>'.format(
        anchor, title, display_number(len(items)), description, table(items)) for anchor, title, items, description in categories)
    sections += ('<section data-panel id="dfw-scope-rules" tabindex="-1"><h2>Rules applied to DFW '
                 '<span class="count">' + display_number(len(dfw_scope_rules)) + '</span></h2>'
                 '<p class="muted">Rules whose saved Applied to scope contains ANY (DFW), including '
                 'enabled, disabled and system-owned rules. Specific-object scopes and missing scope '
                 'information are omitted. Open evidence to inspect Applied to. '
                 'This inventory view does not imply the rule is incorrect.</p>'
                 + dfw_table(dfw_scope_rules) + '</section>')
    def menu_link(anchor, title, count):
        return '<a href="#{}">{} <span>{}</span></a>'.format(anchor, safe(title), display_number(count))

    def menu_category(title, children):
        return '<details class="menu-category"><summary>{}</summary><div class="submenu">{}</div></details>'.format(
            safe(title), children)

    menu = '<a href="#overview">Overview</a>'
    menu += menu_category("Groups",
        menu_link("all-groups", "All groups", len(all_group_rows))
        + menu_link("unused-groups", "Unused groups", len(unused_groups))
        + menu_link("empty-groups", "Empty groups", len(empty))
        + menu_link("unknown-membership", "Membership review", len(unknown)))
    menu += menu_category("Services",
        menu_link("all-services", "All services", len(all_service_rows))
        + menu_link("unused-services", "Unused custom services", len(unused_services)))
    sections += dfw_overview(dfw, report.get("testing", False))
    menu += menu_category("Distributed firewall",
        '<a href="#dfw-overview">Overview</a>' + menu_category("Policies",
            menu_link("dfw-policies", "All policies", len(dfw["policies"]))
            + menu_link("empty-policies", "Empty policies", len(empty_policies)))
        + menu_category("Rules",
            menu_link("dfw-rules", "All rules", len(dfw["rules"]))
            + menu_link("zero-hit-rules", "Zero recorded hits", len(zero_rules))
            + menu_link("disabled-rules", "Disabled rules", len(disabled_rules))
            + menu_link("unknown-statistics", "Statistics review", len(unknown_rules))
            + menu_link("empty-group-rules", "Empty groups", len(empty_group_rules))
            + menu_link("dfw-scope-rules", "Applied to DFW", len(dfw_scope_rules))))
    tags = report.get("tags", {"objects": [], "errors": [], "unsupported_conditions": []})
    tag_menu, tag_panels = '', ''
    for status, title in [("all", "All tags")] + list(TAG_STATUSES.items()):
        items = [r for r in tags["objects"] if status == "all" or (r.get("other_count", 0) > 0 if status == "other_only" else r["status"] == status)]
        anchor = "tags-" + status
        tag_menu += menu_link(anchor, title, len(items))
        content = table_shell(["Tag / scope", "Usage", "VMs", "Groups", "Other resources", "Evidence"], items, "tags") if items else '<p class="empty-state">No tags in this category.</p>'
        tag_panels += '<section data-panel id="{}" tabindex="-1"><h2>{}</h2><p>{}</p>{}</section>'.format(anchor, safe(title), safe(TAG_NOTE), content)
    scopes = tag_scopes(tags)
    tag_menu += menu_link("tags-scopes", "Scopes", len(scopes))
    scope_content = table_shell(["Scope", "Tags", "VMs", "Groups", "Other resources", "Evidence"], scopes, "scopes") if scopes else '<p class="empty-state">No scopes found in the visible tag inventory.</p>'
    tag_panels += '<section data-panel id="tags-scopes" tabindex="-1"><h2>Scopes</h2><p>Scopes observed in the tag inventory. VM, group and other resource counts are unique within each scope; group use includes membership conditions and attached tags. Empty scope means an unscoped tag. Counts reflect visible evidence and may be incomplete; see Tag coverage.</p>' + scope_content + '</section>'
    tag_issues = (["Testing sample: absence of VM or group use cannot be established."] if report.get("testing") else []) + tags["errors"] + (["Some group tag conditions could not be fully evaluated; only potentially affected tags have unknown group usage."] if tags["unsupported_conditions"] else [])
    tag_issues.append("Other resource assignments follow Policy search coverage: " + report.get("search_coverage", {}).get("mode", "not recorded") + ". Non-indexed or inaccessible profiles/objects may be absent.")
    tag_panels += '<section data-panel id="tags-coverage" tabindex="-1"><h2>Tag coverage</h2><p>{}</p><p>{:,} VMs and {:,} groups checked.</p><ul>{}</ul><button type="button" class="detail-button" aria-haspopup="dialog" data-tag-coverage="true" data-title="Conditions requiring review">View conditions requiring review</button></section>'.format(
        safe(TAG_NOTE), tags.get("vms_scanned", 0), tags.get("groups_scanned", 0),
        ''.join('<li>' + safe(e) + '</li>' for e in tag_issues))
    cards += '<a class="card" href="#tags-all"><span>Tags</span><b>{}</b><small>View tag assignments and group use</small></a>'.format(display_number(len(tags["objects"])))
    menu += menu_category("Tags", tag_menu + '<a href="#tags-coverage">Coverage &amp; review</a>')
    menu += menu_category("Help & coverage",
        '<a href="#feature-guide">Report user guide</a>'
        '<a href="#coverage">Audit scope &amp; exclusions</a>'
        '<a href="#tags-coverage">Tag coverage</a>')
    sections += feature_guide()
    for anchor, title, items in (("all-groups", "All groups", all_group_rows),
                                  ("all-services", "All services", all_service_rows)):
        description = ("All retrieved objects, including system and built-in objects. Excluded objects are labeled and are not assessed for cleanup."
                       if "inventory" in report else "Objects available in this report. Regenerate the audit to include excluded inventory.")
        if report.get("testing"):
            description = "Testing sample only. " + description
        sections += '<section data-panel id="{}" tabindex="-1"><h2>{} <span class="count">{}</span></h2><p class="muted">{}</p>{}</section>'.format(anchor, title, display_number(len(items)), description, table(items))
    coverage_errors = "".join('<li>{}</li>'.format(safe(error)) for error in dfw["errors"])
    coverage_notice = ('<p><strong>DFW inventory incomplete.</strong> Some policy/rule lists could not be read. '
                       '<a href="#coverage">Review errors</a>.</p>' if dfw["errors"] else '')
    status = "Testing sample — not a full audit" if report.get("testing") else "Some checks need review" if needs_review(report) else "Audit completed"
    document = '''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>NSX Security Analyzer report</title><style>
:root{color-scheme:light;--ink:#182b42;--muted:#526479;--line:#dce4ed;--teal:#007f80}
*{box-sizing:border-box}body{margin:0;background:#f3f6fa;color:var(--ink);font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
main{max-width:1600px;margin-left:250px;padding:28px 32px}header{background:#142c43;color:white;border-top:5px solid #19b5ac;padding:24px 32px;border-radius:14px}
.sidebar{position:fixed;inset:0 auto 0 0;width:250px;padding:24px 14px;background:#fff;border-right:1px solid var(--line);overflow-y:auto}.sidebar strong{display:block;padding:0 12px 18px;font-size:19px}.sidebar a{display:flex;justify-content:space-between;gap:8px;padding:9px 12px;margin:3px 0;border-radius:7px;text-decoration:none;font-size:13px;color:var(--muted)}.sidebar a[aria-current="page"]{background:#e6f3f3;color:#006c6c;font-weight:700}.sidebar a:hover{background:#edf2f7}.sidebar span{font-variant-numeric:tabular-nums}.table-tools{display:flex;gap:10px;align-items:end;flex-wrap:wrap;margin-top:16px}.table-tools label{font-size:12px;display:grid;gap:4px}.table-tools input,.table-tools select,button{font:inherit;padding:8px 10px;border:1px solid #c5d1de;background:white;border-radius:6px;color:var(--ink)}button{cursor:pointer}button:disabled{opacity:.45;cursor:default}.page-status{font-size:12px;color:var(--muted);margin-left:auto}.table-tools[hidden],[hidden]{display:none!important}.no-matches{color:var(--muted)}
.eyebrow{font-size:12px;letter-spacing:2px;text-transform:uppercase;color:#75dfd6;font-weight:700}h1{font-size:36px;line-height:1.2;margin:12px 0}
.sidebar .menu-category{margin:5px 0}.sidebar .menu-category>summary{display:flex;align-items:center;gap:8px;list-style:none;padding:10px 12px;border-radius:7px;color:var(--ink);font-size:13px;font-weight:600;cursor:pointer}.sidebar .menu-category>summary::-webkit-details-marker{display:none}.sidebar .menu-category>summary::before{content:"›";font-size:18px;line-height:1;transition:transform .15s}.sidebar .menu-category[open]>summary::before{transform:rotate(90deg)}.sidebar .menu-category>summary:hover{background:#edf2f7}.sidebar .menu-category>summary:focus-visible{outline:2px solid var(--teal);outline-offset:2px}.sidebar .submenu{margin-left:16px;padding-left:5px;border-left:1px solid var(--line)}.sidebar .submenu a{font-size:12px;padding:8px}.sidebar .submenu .menu-category>summary{padding:8px;font-size:12px}
header p{color:#d4e1ee;margin:8px 0}.meta{display:flex;gap:24px;flex-wrap:wrap;margin-top:24px}.meta strong{color:#fff}
.status{display:inline-block;margin-top:16px;padding:5px 12px;border:1px solid #7693aa;border-radius:20px;font-size:13px}
.cards{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin:24px 0}.card{display:flex;flex-direction:column;background:white;padding:22px;border:1px solid var(--line);border-radius:12px;text-decoration:none;color:var(--ink)}
.card b{font-size:40px;line-height:1.5}.card small{color:var(--teal)}.card:hover,.card:focus{border-color:var(--teal)}
.overview{padding:20px 24px;background:#e6f3f3;border-left:4px solid var(--teal);border-radius:8px;margin-bottom:28px}.overview p{margin:4px 0}
section{background:#fff;padding:26px;border:1px solid var(--line);border-radius:12px;margin:22px 0;scroll-margin-top:20px}h2{font-size:22px;margin:0 0 6px}.count{font-size:14px;background:#edf2f7;border-radius:20px;padding:3px 10px;vertical-align:middle}
.muted{color:var(--muted)}.table-wrap{overflow-x:auto;margin-top:18px}table{border-collapse:collapse;width:100%;text-align:left;table-layout:fixed;min-width:820px}th{background:#f2f5f9;color:#46596e;text-transform:uppercase;letter-spacing:.5px;font-size:11px;padding:12px}
.header-sort{display:block;width:100%;border:0;background:transparent;padding:0;text-align:left;font:inherit;text-transform:inherit;letter-spacing:inherit;color:inherit}.header-sort:hover{color:var(--teal)}
th:first-child{width:30%}th:nth-child(2){width:12%}th:nth-child(3),th:nth-child(4){width:16%}th:last-child{width:26%}td{padding:16px 12px;border-bottom:1px solid var(--line);vertical-align:top;overflow-wrap:anywhere}tbody tr:last-child td{border-bottom:0}tbody tr:nth-child(even){background:#fafbfd}
code{font:12px/1.6 ui-monospace,SFMono-Regular,Consolas,monospace;overflow-wrap:anywhere}.path{display:block;color:var(--muted);margin-top:6px}
.badge{display:inline-block;padding:3px 9px;border-radius:6px;font-size:12px;font-weight:600}.green{background:#e4f3ed;color:#216447}.amber{background:#fff1d6;color:#815300}.purple{background:#eee9fa;color:#65438b}.gray{background:#edf1f5;color:#536174}
details summary{cursor:pointer;color:var(--teal);font-weight:600}ul{padding-left:18px}.notes{color:var(--muted);font-size:13px}.empty-state{border:1px dashed var(--line);border-radius:8px;padding:20px;color:var(--muted)}
.detail-button{color:var(--teal);font-size:13px}.detail-dialog-context{color:var(--muted)}
#detail-dialog{width:min(1100px,calc(100vw - 32px));max-height:85vh;overflow:auto;border:1px solid var(--line);border-radius:12px;padding:24px;color:var(--ink);background:white}
#detail-dialog::backdrop{background:rgba(15,30,45,.55)}.dialog-heading{display:flex;gap:20px;align-items:center;justify-content:space-between;position:sticky;top:-24px;background:white;padding:12px 0;z-index:1}
#detail-body{overflow-wrap:anywhere}#detail-body code{font-size:13px}#detail-body details{margin:14px 0}button:focus-visible{outline:2px solid var(--teal);outline-offset:3px}
#detail-body h3{font-size:14px;margin:16px 0 8px}#detail-body p{margin:8px 0}
.detail-summary{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0}.detail-summary span{background:#edf5f7;padding:6px 12px;border-radius:7px;font-size:13px}
#detail-body .detail-table{min-width:0;table-layout:auto;font-size:13px;margin:0}#detail-body .detail-table th{width:auto;position:sticky;top:0}#detail-body .detail-table td{padding:8px 10px}#detail-body .detail-table td:first-child{width:30%}
.detail-scroll{max-height:300px;overflow:auto;border:1px solid var(--line);border-radius:7px;margin:8px 0}
#detail-body details{border:1px solid var(--line);border-radius:8px;padding:10px 12px}#detail-body summary{font-size:13px}
.code-block{border:1px solid #cad5e2;border-radius:8px;overflow:hidden;margin:10px 0}.code-toolbar{display:flex;align-items:center;justify-content:space-between;gap:12px;background:#edf2f7;padding:6px 10px;font-size:12px}.code-toolbar button{padding:4px 10px;font-size:12px}
#detail-body .code-lines{margin:0;padding:10px 12px 10px 54px;max-height:360px;overflow:auto;background:#122236;color:#e1e9f4;font:12px/1.65 ui-monospace,SFMono-Regular,Consolas,monospace;tab-size:2;list-style:decimal}
.code-lines li{padding-left:10px;white-space:pre-wrap;overflow-wrap:anywhere}.code-lines li::marker{color:#8294ac}.syntax-key{color:#83d5ff}.syntax-string{color:#a5dfb2}.syntax-literal{color:#edc282}
pre{white-space:pre-wrap;overflow-wrap:anywhere}footer{padding:12px 2px 28px;color:var(--muted);font-size:13px}a{color:var(--teal)}
@media(max-width:1100px){.cards{grid-template-columns:repeat(2,1fr)}}
@media(max-width:800px){.sidebar{position:static;width:auto;max-height:300px;border-bottom:1px solid var(--line)}.sidebar a{font-size:12px}.sidebar strong{padding-bottom:4px}main{padding:16px;margin-left:0}.cards{grid-template-columns:repeat(2,1fr)}header{padding:24px}h1{font-size:28px}section{padding:18px}}

/* Report layout and controls. No external fonts or assets are required. */
:root{--ink:#213047;--muted:#607086;--line:#e3e9f0;--teal:#087f8c}
body{background:#f5f7fb;font-size:14px;line-height:1.65}
main{max-width:1900px;margin-left:264px;padding:32px 40px}
.sidebar{width:264px;padding:26px 16px;background:#fff;scrollbar-width:thin}
.sidebar strong{font-size:18px;letter-spacing:-.4px;padding:0 10px 26px}
.sidebar strong small{display:block;font-size:10px;letter-spacing:2px;color:var(--muted);margin:6px 0 0 40px;font-weight:600}
.brand-mark{display:inline-grid;place-items:center;width:30px;height:30px;border-radius:9px;background:#087f8c;color:white;margin-right:6px}
.sidebar a{transition:background .15s,color .15s;padding:10px 12px}
.sidebar a span{font-size:11px;min-width:24px;text-align:center;background:#f0f4f8;border-radius:6px;padding:0 5px}
.sidebar a[aria-current="page"]{background:#e8f6f5;box-shadow:inset 3px 0 #087f8c}
.sidebar .menu-category>summary{font-size:12px;padding-top:12px;padding-bottom:12px}
header{position:relative;border:1px solid #254760;border-radius:18px;padding:30px 34px;background:linear-gradient(115deg,#142c43,#204d60);box-shadow:0 8px 28px #16344a0c}
h1{font-size:34px;letter-spacing:-1px;margin:8px 0}.eyebrow{letter-spacing:1.6px;font-size:10px}
header .meta{margin-top:20px;gap:12px 26px;font-size:12px}.status{font-size:11px;background:#ffffff0c;border-color:#ffffff40}
.inventory-shortcuts{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:26px 0 30px}
.inventory-shortcuts a{display:flex;justify-content:space-between;align-items:center;gap:10px;padding:16px 18px;background:#fff;border:1px solid var(--line);border-radius:12px;text-decoration:none;color:var(--ink);font-weight:600}
.inventory-shortcuts b{background:#e8f6f5;color:#066c75;border-radius:7px;padding:2px 9px;font-variant-numeric:tabular-nums}
.inventory-shortcuts a:hover{border-color:var(--teal);box-shadow:0 4px 14px #16344a09}
.section-label{font-size:15px;letter-spacing:-.1px;margin-bottom:0}
.cards{gap:14px;margin-top:14px}.card{padding:19px 21px;border-radius:13px;box-shadow:0 3px 10px #16344a04;transition:border-color .15s,box-shadow .15s}
.card span{font-size:12px;font-weight:600;color:var(--muted)}.card b{font-size:32px;font-weight:650;letter-spacing:-1px}.card small{font-size:11px}
section{padding:28px;border-radius:15px;box-shadow:0 4px 16px #16344a04}section>p{max-width:100ch}
h2{font-size:21px;letter-spacing:-.4px}.count{font-size:12px;background:#eef4f8;margin-left:6px}
.overview{background:#edf6f8;border-left:3px solid #62aeba;border-radius:10px;font-size:13px}
.table-tools{padding:14px;background:#f7f9fc;border:1px solid var(--line);border-radius:10px;gap:10px 14px}
.table-tools label{font-size:11px;font-weight:600;color:var(--muted)}.table-tools label:first-child{flex:1;min-width:180px}
.table-tools input,.table-tools select,button{min-height:36px;font-size:12px;border-radius:7px;border-color:#d5dfe9}
.table-tools input:focus,.table-tools select:focus{outline:2px solid #87c9d0;outline-offset:1px}
button:hover:not(:disabled){background:#edf6f8;border-color:#9cbec7}.page-status{white-space:nowrap;font-variant-numeric:tabular-nums}
.table-wrap{border:1px solid var(--line);border-radius:10px;scrollbar-width:thin}
th{font-size:10px;background:#f4f7fa;padding:13px 14px}td{padding:16px 14px;font-size:13px}
tbody tr:hover{background:#f1f8fa}td strong{font-weight:650}.path{font-size:11px;line-height:1.5;color:#718198}
.view-tags th:first-child,.view-scopes th:first-child{width:26%}
.view-tags th:nth-child(2),.view-scopes th:nth-child(2){width:18%}
.view-tags th:nth-child(3),.view-tags th:nth-child(4),.view-tags th:nth-child(5),.view-scopes th:nth-child(3),.view-scopes th:nth-child(4),.view-scopes th:nth-child(5){width:12%}
.view-tags th:last-child,.view-scopes th:last-child{width:20%}
.badge{font-size:10px;border-radius:5px;padding:3px 7px}.detail-button{font-size:11px;background:#f7fbfc;border-color:#d5e5e9;white-space:normal}
.empty-state{background:#f9fbfd;padding:30px;text-align:center;border-radius:10px}
a:focus-visible,summary:focus-visible,input:focus-visible,select:focus-visible{outline:2px solid var(--teal);outline-offset:3px}
@media(min-width:1500px){.cards{grid-template-columns:repeat(4,minmax(0,1fr))}}
@media(max-width:1200px){main{padding:24px}.inventory-shortcuts{grid-template-columns:repeat(2,minmax(0,1fr))}.cards{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:800px){.sidebar{width:auto;padding:14px 16px;max-height:240px}.sidebar strong{padding-bottom:10px}.sidebar strong small{display:none}main{margin-left:0;padding:18px}header{padding:24px}h1{font-size:28px}section{padding:18px}.table-tools .page-status{margin-left:0}}
@media(max-width:480px){.cards,.inventory-shortcuts{grid-template-columns:1fr}.inventory-shortcuts{gap:8px}.card{padding:15px 18px}.card b{font-size:28px}.meta{display:grid}.table-tools label:first-child{min-width:100%}}
@media(prefers-reduced-motion:reduce){*{transition:none!important;scroll-behavior:auto!important}}

.overview-charts{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px;margin:14px 0 10px}
.snapshot-chart{min-width:0;background:white;border:1px solid var(--line);border-radius:14px;padding:20px;box-shadow:0 3px 12px #16344a04}
.snapshot-chart h3{font-size:15px;margin:0 0 5px;letter-spacing:-.2px}.snapshot-chart>p{font-size:12px;color:var(--muted);margin:0 0 16px;min-height:40px}
.chart-content{display:flex;align-items:center;gap:16px;flex-wrap:wrap}.donut-chart{width:132px;max-width:100%;flex:0 0 132px;height:132px}
.chart-total{font:600 22px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;fill:var(--ink)}.chart-caption{font:9px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;fill:var(--muted)}
.chart-legend{list-style:none;padding:0;margin:0;flex:1;min-width:180px}.chart-legend a{display:grid;grid-template-columns:8px 1fr auto 46px;align-items:center;gap:8px;padding:7px 4px;border-radius:5px;text-decoration:none;color:var(--ink);font-size:12px}.chart-legend a:hover{background:#edf6f8}.chart-key{width:8px;height:8px;border-radius:50%}.chart-legend strong,.chart-legend small{font-variant-numeric:tabular-nums;text-align:right}.chart-legend small{color:var(--muted);font-size:11px}.chart-note{font-size:12px;color:var(--muted);margin:10px 0 28px}.snapshot-chart .chart-empty{min-height:132px;display:grid;place-items:center;background:#f7f9fc;border-radius:9px;text-align:center;padding:16px;margin:0}
@media(max-width:1200px){.overview-charts{grid-template-columns:1fr}.chart-content{flex-wrap:nowrap}.snapshot-chart>p{min-height:0}.chart-legend{max-width:440px}}
@media(max-width:480px){.chart-content{flex-wrap:wrap;justify-content:center}.chart-legend{min-width:0;flex-basis:100%}}

/* Align table controls by row. */
.table-widget>.table-tools{display:grid;grid-template-columns:minmax(180px,2fr) repeat(2,minmax(130px,1fr));align-items:start}
.table-widget>.table-tools>label{min-width:0!important}
.table-tools input,.table-tools select{width:100%;min-width:0;max-width:100%}
.table-tools input,.table-tools select:not([size]){height:38px}
.table-widget>.table-tools>.page-status{grid-column:1;margin:0;align-self:center}
.table-widget>.table-tools>button{align-self:center}
.search-error{grid-column:1 / -1;color:#9b3030;font-size:12px}.search-error:empty{display:none}
@media(max-width:1100px){.table-widget>.table-tools{grid-template-columns:repeat(2,minmax(0,1fr))}.table-widget>.table-tools>.page-status{grid-column:1 / -1}}
@media(max-width:600px){.table-widget>.table-tools{grid-template-columns:minmax(0,1fr)}}

.header-filter{display:inline;text-align:left;font:inherit;color:inherit;letter-spacing:inherit;text-transform:inherit;background:transparent;border:0;padding:4px 0;min-height:30px;max-width:100%;white-space:normal}
.header-sort{display:inline-block;width:auto;padding:4px 8px;margin-left:4px;min-height:30px}
.filter-active{color:var(--teal);font-weight:700}.column-filter-summary{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}.column-filter-summary:empty{display:none}.column-filter-summary button{font-size:12px;overflow-wrap:anywhere;text-align:left}
.column-filter-dialog{width:min(600px,calc(100vw - 32px));max-height:90vh;overflow:auto;border:1px solid var(--line);border-radius:12px;padding:24px;color:var(--ink)}.column-filter-dialog::backdrop{background:#142c4380}.column-filter-dialog label{display:grid;gap:6px;margin:14px 0}.column-filter-dialog input,.column-filter-dialog select{font:inherit;padding:10px;border:1px solid var(--line);border-radius:6px;width:100%;min-width:0}.column-filter-dialog p{font-size:12px;color:var(--muted)}.filter-actions{display:flex;gap:8px;justify-content:flex-end}

.table-widget>.table-tools>.search-related{grid-column:1 / -1;display:flex;align-items:center;gap:8px}
.table-tools .search-evidence{width:16px;height:16px;min-height:0;accent-color:var(--teal)}

/* Shared visual language: quiet surfaces, clear hierarchy and readable controls. */
:root{--ink:#203249;--muted:#52647a;--line:#dfe6ee;--teal:#087e83}
body{background:#f5f7fa;font-size:15px;line-height:1.7}
main{padding:32px 36px;max-width:1840px}
.sidebar{background:#fbfcfe}.sidebar strong{padding-bottom:20px}.sidebar a{font-size:13px;border-radius:8px}.sidebar .submenu a{font-size:12px;padding:9px 10px}.sidebar .menu-category>summary{font-size:13px}.sidebar .submenu{margin-left:12px}
header{border:0;background:linear-gradient(110deg,#18344a,#245366);padding:28px 32px;box-shadow:none;border-radius:16px}
h1{font-size:32px;letter-spacing:-.8px}h2{font-size:24px;line-height:1.35;letter-spacing:-.5px}h3{font-size:17px;line-height:1.5;margin-top:26px}p{margin:10px 0 18px}
section{padding:28px 30px;border-radius:14px;box-shadow:0 2px 8px #16344a03}section>p{max-width:90ch}
.section-eyebrow{font-size:11px;letter-spacing:1.3px;text-transform:uppercase;font-weight:700;color:var(--teal);margin-bottom:8px}
.inventory-shortcuts a{padding:16px 18px;font-size:14px}.card{box-shadow:none;min-width:0;padding:20px}.card span{font-size:13px}.card small{font-size:12px}.card b{font-size:34px;margin:4px 0 8px}.card:hover{box-shadow:0 4px 14px #16344a0a}
.table-tools{background:#f7f9fb;border-radius:10px;padding:16px;gap:14px 16px}.table-tools label{font-size:12px;line-height:1.5;gap:7px;color:var(--ink)}.table-tools input,.table-tools select{font-size:13px}.table-tools input:disabled,.table-tools select:disabled{background:#edf1f5}
.table-wrap{margin-top:16px;border-radius:10px}th{font-size:11px;letter-spacing:.25px;background:#f3f6f9;vertical-align:top}td{font-size:14px;line-height:1.6;padding:18px 14px}.path{font-size:12px;color:#52647a}.badge{font-size:11px;padding:4px 8px}.detail-button{font-size:12px;min-height:36px}
.header-filter{line-height:1.5}.header-sort{color:var(--teal);border-radius:5px}.column-filter-summary button{background:#eaf5f5;border-color:#c7e1e2;color:#155e66}.page-status{font-size:13px}
.overview{padding:20px 24px;margin:22px 0;color:#28495c}.overview p{max-width:90ch}.overview h3{margin-top:0}.snapshot-chart>p{font-size:13px}.chart-legend a{font-size:13px}
.guide-start{padding:22px 26px;border:1px solid #cee3e7;border-radius:12px;background:#f0f8f8;margin:24px 0}.guide-start h3{margin:0 0 12px}.guide-start ol{padding-left:22px}.guide-start li{margin:8px 0}.guide-shortcuts{display:flex;gap:12px 24px;flex-wrap:wrap;margin-top:18px}.guide-shortcuts a{font-size:13px;font-weight:600;text-decoration:none}
.guide-tools{display:flex;gap:12px;align-items:end;flex-wrap:wrap}.guide-tools label{display:grid;gap:6px;flex:1;min-width:180px;font-size:13px;font-weight:600}.guide-tools input{font:inherit;background:white;border:1px solid #c8d4df;border-radius:8px;padding:10px 12px;width:100%;min-width:0}.guide-tools button{min-height:42px}
#guide-status{font-size:13px;margin:12px 0}.guide-topics{display:grid;gap:12px}.guide-topics>details{border:1px solid var(--line);border-radius:10px;overflow:hidden}.guide-topics>details>summary{padding:17px 20px;color:var(--ink);font-size:15px;background:#fafbfd}.guide-topics>details[open]>summary{border-bottom:1px solid var(--line);background:#edf6f7;color:#175f68}.guide-topic-body{padding:8px 24px 16px;max-width:100ch}.guide-topic-body li{margin:8px 0}.guide-topic-body p{line-height:1.8}.guide-topic-body strong{font-weight:650}
@media(max-width:1100px){main{padding:24px}section{padding:24px}}
@media(max-width:800px){main{padding:18px;margin-left:0}.sidebar{width:auto}header{padding:24px}h1{font-size:28px}}
@media(max-width:480px){main{padding:12px}section{padding:18px}h2{font-size:21px}.guide-start{padding:18px}.guide-topic-body{padding:8px 18px 14px}.guide-tools label{flex-basis:100%}.table-tools{padding:12px}}

/* Reading density, responsive controls and embedded workspace integration. */
.embedded-report .portal-return{display:none}.portal-return{margin:0 0 18px;font-size:13px}
.sidebar strong{font-size:16px;line-height:1.5;padding:0 8px 22px}.sidebar strong small{margin-left:38px;font-size:9px}
header .meta strong{font-variant-numeric:tabular-nums;overflow-wrap:anywhere}header .meta{gap:10px 24px}
.table-widget>.table-tools{grid-template-columns:repeat(6,minmax(0,1fr));align-items:end}
.table-widget>.table-tools>label:first-child{grid-column:span 3}
.table-widget>.table-tools>label:nth-child(3){grid-column:span 2}
.table-widget>.table-tools>.search-related{grid-column:span 3;align-self:center;font-weight:400}
.table-widget>.table-tools>.page-status{grid-column:span 3}
.table-tools>button{white-space:normal}.table-tools>button:last-child{background:#087e83;color:white;border-color:#087e83;font-weight:600}
.table-tools>button:last-child:hover{background:#06666b}.table-tools .search-error{grid-column:1 / -1}
table{min-width:760px}th{line-height:1.5}td{padding:15px 14px}.path{line-height:1.6}.header-sort{min-width:32px}
.view-tags th:first-child,.view-scopes th:first-child{width:24%}.view-tags th:nth-child(2),.view-scopes th:nth-child(2){width:16%}.view-tags th:last-child,.view-scopes th:last-child{width:24%}
.snapshot-chart{padding:20px}.chart-content{gap:12px}.chart-legend{min-width:155px}.chart-legend a{grid-template-columns:8px minmax(0,1fr) auto 44px;gap:6px}.chart-legend a span{overflow-wrap:anywhere}
#detail-dialog{padding:24px 28px}#detail-body .detail-table th{background:#f3f6f9;z-index:1}#detail-body .detail-scroll{max-height:45vh}.code-toolbar button{background:white}
@media(min-width:1201px) and (max-width:1550px){.overview-charts{grid-template-columns:1fr}.chart-content{flex-wrap:nowrap}.chart-legend{max-width:480px}.snapshot-chart>p{min-height:0}}
@media(max-width:1100px){.table-widget>.table-tools{grid-template-columns:repeat(2,minmax(0,1fr))}.table-widget>.table-tools>label:first-child,.table-widget>.table-tools>label:nth-child(3),.table-widget>.table-tools>.page-status{grid-column:auto}.table-widget>.table-tools>.search-related{grid-column:1 / -1}}
@media(max-width:600px){.table-widget>.table-tools{grid-template-columns:minmax(0,1fr)}.table-widget>.table-tools>label,.table-widget>.table-tools>.page-status{grid-column:1!important}.sidebar{max-height:220px}#detail-dialog{padding:18px}.chart-content{flex-wrap:wrap}.meta{flex-direction:column}}
''' + DIALOG_STYLES + '''</style></head><body><aside class="sidebar"><strong><span class="brand-mark">N</span> NSX Security Analyzer<small>POLICY AUDIT</small></strong><nav aria-label="Report sections">''' + menu + '''</nav></aside><main>
<header><div class="eyebrow">Infrastructure / NSX Policy</div><h1>NSX security report</h1>
<p>Explore your inventory, review findings and inspect the evidence.</p>
<div class="meta"><span>Manager <strong>''' + safe(report.get("manager", "Not recorded")) + '''</strong></span>
<span>Generated <strong><time datetime="''' + safe(report["generated_at"]) + '''" title="''' + safe(report["generated_at"]) + '''">''' + safe(display_timestamp(report["generated_at"])) + '''</time></strong></span></div>
<span class="status">''' + status + ''' · Read-only audit</span></header>
<p class="muted">''' + safe(performance_description(report)) + '''</p>
<div data-panel id="overview" tabindex="-1"><div class="inventory-shortcuts"><a href="#all-groups">All groups <b>''' + display_number(len(all_group_rows)) + '''</b></a><a href="#all-services">All services <b>''' + display_number(len(all_service_rows)) + '''</b></a><a href="#dfw-policies">Firewall policies <b>''' + display_number(len(dfw['policies'])) + '''</b></a><a href="#dfw-rules">Firewall rules <b>''' + display_number(len(dfw['rules'])) + '''</b></a></div><h2 class="section-label">Inventory at a glance</h2>''' + charts + '''<h2 class="section-label">Findings &amp; review</h2><nav class="cards" aria-label="Finding summary">''' + cards + '''</nav>
<div class="overview"><p><strong>''' + display_number(review_count) + ''' unique object(s) to review</strong></p>
<p>''' + safe(report["groups_scanned"]) + ''' groups and ''' + safe(report["custom_services_scanned"]) + ''' custom services scanned · ''' + safe(report["system_groups_excluded"]) + ''' default/system groups excluded.</p>
<p>''' + display_number(len(dfw["policies"])) + ''' DFW policies and ''' + display_number(len(dfw["rules"])) + ''' DFW rules inventoried.</p>
<p>Categories can overlap: an unused group can also be empty. Unknown membership is counted separately from empty.</p>''' + coverage_notice + '''</div></div>
''' + sections + tag_panels + '''
<section data-panel id="inventory" tabindex="-1"><h2>Groups &amp; services <span class="count">''' + display_number(len(rows)) + '''</span></h2>
<p class="muted">All included groups and custom services. Open reference evidence to see the consuming objects and firewall rule IDs.</p>''' + table(rows) + '''</section>
<section data-panel id="dfw-policies" tabindex="-1"><h2>All DFW policies</h2>''' + dfw_table(dfw["policies"]) + '''</section>
<section data-panel id="dfw-rules" tabindex="-1"><h2>All DFW rules</h2><p class="muted">''' + safe(DFW_COUNTER_NOTE) + '''</p>''' + dfw_table(dfw["rules"]) + '''</section>
<section data-panel id="coverage" tabindex="-1"><h2>Coverage &amp; definitions</h2>''' + (
    '<h3>DFW inventory errors</h3><ul>' + coverage_errors + '</ul>' if coverage_errors else '') + '''<p><strong>Unused candidate:</strong> ''' + safe(report["usage_definition"]) + '''.</p>
<p><strong>DFW counters:</strong> ''' + safe(DFW_COUNTER_NOTE) + ''' Rules with positive packet, byte or session counters are not zero-activity candidates, even if hit_count is zero. Disabled rules have their own section.</p>
<p><strong>Empty DFW policy:</strong> No non-deleted rules in its complete rule list. Rule-list failures are unknown. DFW policies and rules include system-owned objects, which are labeled for review. Gateway firewall policies are outside this check.</p>
<p><strong>Membership:</strong> Empty means all supported checks returned no members; unknown means the check needs review. An empty group may still be referenced.</p>
<p><strong>Scope:</strong> ''' + safe(report["scope"]) + '''. ''' + safe(report["indexed_objects_scanned"]) + ''' indexed objects scanned.</p>
<p><strong>Coverage:</strong> ''' + safe(report["limitations"]) + ''' Disabled rules and nested configuration references count as usage; realization records and self-references do not.</p>
<p><strong>Exclusions:</strong> Default/system-owned groups, DefaultMaliciousIpGroup, and built-in/system-owned services are omitted from findings.</p></section>
<footer>NSX Security Analyzer · Saved database snapshot.</footer>
</main><dialog id="detail-dialog" aria-labelledby="detail-title">
<div class="dialog-heading"><h2 id="detail-title">Details</h2><button type="button" id="close-details" autofocus>Close</button></div>
<div id="detail-body"></div></dialog><noscript><p>This report requires JavaScript to display tables. Enable JavaScript to explore this snapshot.</p></noscript>
<script type="application/json" id="report-rows">''' + json.dumps({"rows": row_pool, "tag_evidence": {key: value for key, value in tags.items() if key != "objects"}}, ensure_ascii=False, separators=(',', ':')).replace('<', '\\u003c') + '''</script><script>
(() => {
  if (window.self !== window.top) document.body.classList.add('embedded-report');
  const dataElement = document.getElementById('report-rows');
  const payload = JSON.parse(dataElement.textContent);
  const rowPool = payload.rows;
  dataElement.remove();
  const textCache = new Map();
  const numberFormat = new Intl.NumberFormat('en-US', {maximumFractionDigits:20});
  const number = value => typeof value === 'number' ? numberFormat.format(value) : String(value ?? '');
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const types = {group:'Groups',custom_service:'Custom services',dfw_policy:'DFW policies',dfw_rule:'DFW rules'};
  const labels = {referenced:['green','Referenced'],unused_candidate:['amber','Unused candidate'],empty:['amber','Empty'],
    nonempty:['green','Has members'],unknown:['purple','Unknown'],not_applicable:['gray','Not applicable'],not_assessed:['gray','Not assessed'],
    zero_hits:['amber','Zero recorded hits'],traffic_recorded:['green','Traffic recorded'],has_rules:['green','Has rules']};
  const badge = status => {
    const [color,label] = labels[status] || ['gray',status];
    return '<span class="badge '+color+'">'+esc(label)+'</span>';
  };
  const code = value => '<code>'+esc(value)+'</code>';
  const notes = values => values?.length ? '<ul class="notes">'+values.map(v=>'<li>'+esc(v)+'</li>').join('')+'</ul>' : '';
  function popup(title, content, label='View details') {
    return '<div class="detail-popup"><button type="button" class="detail-button" aria-haspopup="dialog" data-title="'+esc(title)+'">'+esc(label)+'</button>'
      + '<div class="detail-content" hidden>'+content+'</div></div>';
  }
  const dialog = document.getElementById('detail-dialog');
  const detailTitle = document.getElementById('detail-title');
  const detailBody = document.getElementById('detail-body');
  let detailTrigger;
  document.querySelector('main').addEventListener('click', event => {
    const button = event.target.closest('.detail-button');
    if (!button) return;
    detailTrigger = button;
    detailTitle.textContent = button.dataset.title;
    if (button.dataset.tagRow !== undefined) detailBody.innerHTML = tagEvidence(rowPool[Number(button.dataset.tagRow)].data);
    else if (button.dataset.tagCoverage) detailBody.innerHTML = '<pre>'+esc(JSON.stringify({unsupported:tagConditions(payload.tag_evidence.unsupported_conditions),unmatched:tagConditions(payload.tag_evidence.unmatched_conditions)},null,2))+'</pre>';
    else if (button.dataset.evidenceRow !== undefined) {
      const scratch = document.createElement('tbody');
      scratch.innerHTML = renderRow(Number(button.dataset.evidenceRow), true);
      detailBody.innerHTML = scratch.querySelector('.detail-content').innerHTML;
    } else detailBody.innerHTML = button.parentElement.querySelector('.detail-content').innerHTML;
    enhanceDetails();
    window.workspaceEvidence?.(detailBody, detailTitle, rowPool[Number(button.dataset.evidenceRow ?? button.dataset.tagRow)]?.data);
    dialog.showModal();
    dialog.scrollTop = 0;
  });
  document.getElementById('close-details').addEventListener('click', () => dialog.close());
  dialog.addEventListener('click', event => {
    const rect = dialog.getBoundingClientRect();
    if (event.target === dialog && (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom)) dialog.close();
  });
  dialog.addEventListener('close', () => {
    detailBody.replaceChildren();
    if (detailTrigger?.isConnected) detailTrigger.focus({preventScroll:true});
  });
  // Sidebar categories remain expandable; report details open in the dialog.
  document.querySelectorAll('main details').forEach(details => {
    if (details.hasAttribute('data-guide-topic') || details.parentElement.closest('details')) return;
    const summary = details.querySelector(':scope > summary');
    const title = summary?.textContent || 'Details';
    const content = details.cloneNode(true);
    content.querySelector(':scope > summary')?.remove();
    details.outerHTML = popup(title,content.innerHTML);
  });
  function ruleIdentity(r) {
    return r.policy_rule_id === undefined ? '' : '<small class="path">Rule ID: '+esc(r.rule_id ?? 'Not returned')
      + '<br>Policy rule path: <button type="button" class="copy-path" data-copy-path="'+esc(r.path)
      +'" title="'+esc(r.path)+'" aria-label="Copy full policy rule path: '+esc(r.path)+'">'
      +esc('…/rules/'+String(r.path).split('/').pop())+'</button></small>';
  }
  function tagConditions(ids) {
    return (ids || []).map(id => typeof id === 'number' ? payload.tag_evidence.conditions[id] : id);
  }
  function tagRowConditions(r, key) {
    const set = r[key === 'condition_evidence' ? 'condition_evidence_set' : 'review_condition_set'];
    return tagConditions(set === undefined ? r[key] : payload.tag_evidence.condition_sets[set]);
  }
  function tagEvidence(r) {
      const conditionRows = tagRowConditions(r, 'condition_evidence');
      const reviewRows = tagRowConditions(r, 'review_conditions');
      let evidence = '<div class="detail-summary"><span><b>'+esc(number(r.vm_count))+'</b> VMs</span><span><b>'+esc(number(r.group_count))+'</b> groups</span><span>Scope: '+esc(r.scope || '(empty)')+'</span></div>'+notes(r.notes);
      for (const [key,label] of [['vms','VM assignments'],['group_conditions','Group conditions'],['group_assignments','Tags attached to groups']]) {
        const entries = Object.entries(r[key]);
        evidence += '<details'+(entries.length && entries.length <= 10 ? ' open' : '')+'><summary>'+label+' ('+number(entries.length)+')</summary>';
        evidence += entries.length ? '<div class="detail-scroll"><table class="detail-table"><thead><tr><th>Name</th><th>Path / ID</th></tr></thead><tbody>'+entries.map(([path,name])=>'<tr><td>'+esc(name)+'</td><td>'+code(path)+'</td></tr>').join('')+'</tbody></table></div>' : '<p class="muted">No assignments or references found in this check.</p>';
        evidence += '</details>';
      }
      const other = Object.entries(r.other_assignments || {});
      evidence += '<details'+(other.length && other.length <= 10 ? ' open' : '')+'><summary>Other resource assignments ('+number(other.length)+')</summary>';
      evidence += other.length ? '<div class="detail-scroll"><table class="detail-table"><thead><tr><th>Name</th><th>Type</th><th>Path</th></tr></thead><tbody>'+other.map(([path,obj])=>'<tr><td>'+esc(obj.name)+'</td><td>'+esc(obj.resource_type)+'</td><td>'+code(path)+'</td></tr>').join('')+'</tbody></table></div>' : '<p>No other assignments found in the visible Policy search snapshot.</p>';
      evidence += '</details>';
      for (const [label,items] of [['Matching condition definitions',conditionRows],['Conditions requiring review',reviewRows]]) {
        if (items.length) evidence += '<details><summary>'+label+' ('+number(items.length)+')</summary><pre>'+esc(JSON.stringify(items,null,2))+'</pre></details>';
      }
      const firewallRefs = r.firewall_references || [];
      evidence += '<details><summary>Firewall rule references ('+number(new Set(firewallRefs.map(ref=>ref.rule)).size)+')</summary><p class="muted">'+esc(payload.tag_evidence.firewall_reference_note || 'Visible rules referencing groups that use this tag.')+'</p>';
      evidence += firewallRefs.length ? '<div class="detail-scroll"><table class="detail-table"><thead><tr><th>Firewall rule</th><th>Via group / tag use</th></tr></thead><tbody>'+firewallRefs.map(ref=>{
        const rule = payload.tag_evidence.firewall_rules[ref.rule];
        return '<tr><td>'+esc(rule.name)+ruleIdentity(rule)+'<br>'+code(rule.path)+(rule.disabled?' <b>Disabled</b>':'')+'</td><td>'+code(ref.via_group)+'<br>'+esc(ref.tag_use === 'condition' ? 'Membership condition' : 'Tag attached to group')+'</td></tr>';
      }).join('')+'</tbody></table></div>' : '<p>No firewall references found in the visible configuration.</p>';
      evidence += '</details>';
      evidence += '<p class="muted">NSX catalog assignment count: '+esc(number(r.tagged_objects ?? 'Unavailable'))+'</p>';
      evidence += '<p class="muted">Objects with this tag assigned, as reported by NSX. Group condition references are not included. An unavailable count does not mean zero assignments.</p>';
      return evidence;
  }
  function highlightLine(line) {
    // Tokenize raw text first; escape every token before adding markup.
    return line.split(/("(?:\\\\.|[^"\\\\])*"\\s*:|"(?:\\\\.|[^"\\\\])*"|\\b(?:true|false|null|-?\\d+(?:\\.\\d+)?)\\b)/g).map(token => {
      const style = token.startsWith('"') ? (token.trimEnd().endsWith(':') ? 'key' : 'string') : /^(true|false|null|-?\\d+(?:\\.\\d+)?)$/.test(token) ? 'literal' : '';
      return style ? '<span class="syntax-'+style+'">'+esc(token)+'</span>' : esc(token);
    }).join('');
  }
  async function copyText(value, button) {
    const original = button.dataset.copyLabel || button.textContent;
    button.dataset.copyLabel = original;
    try {
      try {
        await navigator.clipboard.writeText(value);
      } catch (_) {
        // Support browsers where the Clipboard API is unavailable.
        const input = document.createElement('textarea');
        input.value = value;
        input.style.cssText = 'position:fixed;opacity:0;top:0;left:0';
        (dialog.open ? dialog : document.body).append(input);
        try {
          input.select();
          if (!document.execCommand('copy')) throw new Error('Copy unavailable');
        } finally { input.remove(); button.focus(); }
      }
      button.textContent = 'Copied';
    } catch (_) { button.textContent = 'Select text to copy'; }
    setTimeout(() => { button.textContent = original; }, 2500);
  }
  document.addEventListener('click', event => {
    const button = event.target.closest('[data-copy-path]');
    if (button) copyText(button.dataset.copyPath, button);
  });
  function enhanceDetails() {
    detailBody.querySelectorAll('table').forEach(table => addStaticExport(table));
    detailBody.querySelectorAll('pre').forEach(pre => {
      const raw = pre.textContent;
      const block = document.createElement('div');
      block.className = 'code-block';
      let language = 'Text';
      try { JSON.parse(raw); language = 'JSON'; } catch (_) {}
      const lines = raw.split('\\n');
      block.innerHTML = '<div class="code-toolbar"><span>'+language+' · '+number(lines.length)+' lines</span><button type="button" aria-label="Copy code block" aria-live="polite">Copy</button></div><ol class="code-lines" tabindex="0" aria-label="'+language+' source with line numbers">'+lines.map(line=>'<li>'+(language === 'JSON' ? highlightLine(line) : esc(line) || ' ')+'</li>').join('')+'</ol>';
      block.querySelector('button').addEventListener('click', event => copyText(raw, event.currentTarget));
      pre.replaceWith(block);
    });
  }
  function renderRow(id, includeEvidence=false) {
    const rowPopup = (title, content, label='View details') => includeEvidence ? popup(title,content,label)
      : '<button type="button" class="detail-button" aria-haspopup="dialog" data-evidence-row="'+id+'" data-title="'+esc(title)+'">'+esc(label)+'</button>';
    const {view,data:r} = rowPool[id];
    const object = '<strong>'+esc(r.name)+'</strong>'+((r.audit_exclusions || []).length ? '<br><span class="badge gray">Excluded from findings</span>' : '')+ruleIdentity(r)+(r.policy_rule_id === undefined ? '<code class="path">'+esc(r.path)+'</code>' : '');
    let cells;
    if (view === 'scopes') {
      const detail = !includeEvidence ? '' : '<p>Scope: '+esc(r.scope || '(empty scope)')+'</p><div class="detail-scroll"><table class="detail-table"><thead><tr><th>Tag</th><th>Usage</th><th>VMs</th><th>Groups</th><th>Other resources</th></tr></thead><tbody>'+r.tags.map(tag=>'<tr><td>'+esc(tag.name)+'</td><td>'+esc(({both:'VMs and groups',vm_only:'VM use',group_only:'Group use',other_only:'Other resource use',unknown:'Needs review'})[tag.status] || tag.status)+'</td><td>'+esc(number(tag.vm_count))+'</td><td>'+esc(number(tag.group_count))+'</td><td>'+esc(number(tag.other_count ?? 0))+'</td></tr>').join('')+'</tbody></table></div>';
      cells = [esc(r.name),esc(number(r.tag_count)),esc(number(r.vm_count)),esc(number(r.group_count)),esc(number(r.other_count ?? 0)),rowPopup(r.name+' — Tags in scope',detail,'View tags')];
    } else if (view === 'tags') {
      const labels = {both:'VMs and groups',vm_only:'VM use',group_only:'Group use',other_only:'Other resource use',unknown:'Needs review'};
      cells = ['<strong>'+esc(r.name)+'</strong><code class="path">Scope: '+esc(r.scope || '(empty)')+'</code>',esc(labels[r.status]),esc(number(r.vm_count))+(r.vm_usage==='unknown'?'<br><small>'+esc(r.vm_review_reason || 'VM usage needs review')+'</small>':''),esc(number(r.group_count))+(r.group_usage==='unknown'?'<br><small>'+esc(r.group_review_reason || 'Group usage needs review')+'</small>':''),esc(number(r.other_count ?? 0)),'<button type="button" class="detail-button" aria-haspopup="dialog" data-tag-row="'+id+'" data-title="'+esc(r.name+' — Tag usage')+'">View details</button>'];
    } else if (view === 'inventory') {
      const references = new Map((r.reference_details || []).map(ref => [ref.path,ref]));
      let evidence = !includeEvidence ? '' : r.usage === 'not_assessed' ? '<p>Usage was not assessed for this object.</p>' : r.referenced_by.length
        ? '<details><summary>'+number(r.referenced_by.length)+' reference(s)</summary><ul>'+r.referenced_by.map(p=>'<li>'+esc(references.get(p)?.name || '')+ruleIdentity(references.get(p) || {})+code(p)+'</li>').join('')+'</ul></details>'
        : '<span class="muted">No configuration references found.</span>';
      const definition = r.membership_definition;
      let membership = badge(r.membership);
      if (definition) {
        membership += '<p>'+esc(definition.methods.join(', '))+'</p>';
        if (includeEvidence) evidence += '<details><summary>Membership definition</summary>'
          + '<p>Configured criteria; these are not resolved members. The full definition preserves AND/OR logic.</p>'
          + notes(definition.criteria) + '<pre><code>'+esc(JSON.stringify(definition.definition,null,2))+'</code></pre></details>';
      }
      cells = [object,esc(r.inventory_type || (r.kind==='group'?'Group':'Custom service')),badge(r.usage),membership,rowPopup(r.name+' — Evidence & notes',evidence+notes(r.notes),'View evidence'+(r.notes?.length?' & notes':''))];
    } else {
      let context,status,count,evidence;
      if ('rule_count' in r) {
        context=esc(r.category)+'<code class="path">'+esc(r.domain)+'</code>';
        status=badge(r.status);
        count=r.rule_count===null?'Unknown':number(r.rule_count)+' rule(s)';
        evidence=(r.rule_count===null?'Rule inventory unavailable.':'Complete rule list checked; disabled rules also count.')+notes(r.notes);
      } else {
        context=esc(r.policy_name)+'<code class="path">'+esc(r.policy_path)+'</code>';
        status=badge(r.hit_status)+'<br><small>'+(r.disabled?'Disabled':'Enabled')+' · '+esc(r.action)+'</small>';
        count=r.hit_count===null?'Unknown':number(r.hit_count)+' hit(s)';
        if (includeEvidence) {
        evidence='<details><summary>Counter &amp; rule details</summary><p>Checked: '+(globalThis.workspaceTime ? globalThis.workspaceTime(r.statistics_checked_at) : esc(r.statistics_checked_at))+'</p>';
        for (const sample of r.statistics) {
          evidence+='<p>'+code(sample.enforcement_point)+'<br>'+Object.entries(sample).filter(([k])=>k!=='enforcement_point').map(([k,v])=>esc(k.replaceAll('_',' '))+': '+esc(number(v))).join(' · ')+'</p>';
        }
        for (const [label,key] of [['Sources','source_groups'],['Destinations','destination_groups'],['Services','services'],['Applied to','scope']]) {
          evidence+='<p><strong>'+label+'</strong><br>'+code((r[key] || []).join(', ') || 'Not specified')+'</p>';
        }
        evidence+='</details>'+notes(r.notes);
        if (r.empty_group_references?.length) {
          evidence+='<h3>Confirmed empty group references</h3><ul>'+r.empty_group_references.map(group=>
            '<li><strong>'+esc(group.field)+'</strong>: '+esc(group.name)+'<br>'+code(group.path)+'</li>').join('')+'</ul>';
        }
        }
      }
      if (r.system_owned) status+='<br><span class="badge gray">System-owned</span>';
      let countDetails = esc(count);
      if (!('rule_count' in r)) {
        const history = r.last_positive_observation;
        countDetails += '<p class="notes"><strong>Last observed positive count</strong><br>'+(history ? (globalThis.workspaceTime ? globalThis.workspaceTime(history.observed_at) : esc(history.observed_at))+'<br>'+esc(number(history.hit_count))+' hit(s)' : 'Unavailable — no saved positive snapshot')+'</p>';
        evidence += '<p>Last observed positive count: '+(history ? esc(number(history.hit_count))+' hit(s) at '+(globalThis.workspaceTime ? globalThis.workspaceTime(history.observed_at) : esc(history.observed_at)) : 'Unavailable')+'</p><p>Saved audit observation, not the last packet time. Traffic and resets between audits may be missed.</p>';
      }
      cells=[object,context,status,countDetails,rowPopup(r.name+' — Evidence & notes',evidence)];
    }
    return '<tr>'+cells.map(cell=>'<td>'+cell+'</td>').join('')+'</tr>';
  }
  function searchable(id, preserveCase=false) {
    if (!textCache.has(id)) {
      const {view,data:r} = rowPool[id];
      const parts = [];
      function collect(value) {
        if (Array.isArray(value)) value.forEach(collect);
        else if (value && typeof value==='object') Object.values(value).forEach(collect);
        else if (value!==null && value!==undefined) parts.push(String(value));
      }
      collect(r);
      if (view==='tags') { collect(tagRowConditions(r, 'condition_evidence')); collect(tagRowConditions(r, 'review_conditions')); }
      if (view==='tags') parts.push(({both:'VMs and groups',vm_only:'VM use',group_only:'Group use',other_only:'Other resource use',unknown:'Needs review'})[r.status]);
      for (const key of ['usage','membership','hit_status','status']) if (labels[r[key]]) parts.push(labels[r[key]][1]);
      if (view==='dfw' && 'disabled' in r) parts.push(r.disabled?'Disabled':'Enabled');
      textCache.set(id,parts.join(' '));
    }
    return preserveCase ? textCache.get(id) : textCache.get(id).toLocaleLowerCase();
  }
  function tableSearchText(id, includeEvidence) {
    if (includeEvidence) return searchable(id,true);
    const row = rowPool[id].data;
    return [row.name, row.path].filter(value => value != null).join(' ');
  }
  const controllers = new Map();
  const panels = Array.from(document.querySelectorAll('[data-panel]'));
  const links = Array.from(document.querySelectorAll('.sidebar nav a'));
  function navigate(focus) {
    if (dialog.open) dialog.close();
    const id = location.hash.slice(1) || 'overview';
    const selected = panels.find(p => p.id === id) || panels[0];
    panels.forEach(p => {
      p.hidden = p !== selected;
      p.querySelectorAll('.table-widget').forEach(widget => {
        if (p === selected) tableController(widget).refresh();
        else if (controllers.has(widget)) controllers.get(widget).unmount();
      });
    });
    links.forEach(a => {
      if (a.hash === '#' + selected.id) {
        a.setAttribute('aria-current', 'page');
        // Reveal every parent category when following a card or a deep link.
        let category = a.closest('.menu-category');
        while (category) {
          category.open = true;
          category = category.parentElement.closest('.menu-category');
        }
      } else a.removeAttribute('aria-current');
    });
    if (focus) { selected.focus({preventScroll:true}); window.scrollTo(0, 0); }
  }
  window.addEventListener('hashchange', () => navigate(true));
  function tableSearchMatcher(text, syntax, mode) {
    const term = text.trim();
    if (!term) return () => true;
    // Compile once per refresh, preserving escapes and character classes.
    const regex = syntax === 'regex' ? new RegExp(term, 'i') : null;
    const groups = [[]];
    if (!regex) {
      // Keep spaces within conditions; only standalone, unquoted AND/OR are operators.
      let condition = '', quoted = false;
      const whitespace = char => char !== undefined && char.trim() === '';
      function appendCondition() {
        const literal = condition.trim().toLocaleLowerCase();
        if (!literal) throw new SyntaxError('Enter a condition on both sides of AND / OR.');
        groups[groups.length - 1].push(literal);
        condition = '';
      }
      for (let i = 0; i < term.length;) {
        if (term[i] === '"') { quoted = !quoted; i++; continue; }
        const word = !quoted && (i === 0 || whitespace(term[i - 1]))
          ? /^(AND|OR)/i.exec(term.slice(i)) : null;
        if (word && (i + word[0].length === term.length || whitespace(term[i + word[0].length]))) {
          appendCondition();
          if (word[0].toUpperCase() === 'OR') groups.push([]);
          i += word[0].length;
        } else { condition += term[i]; i++; }
      }
      if (quoted) throw new SyntaxError('Close the double quote around the literal phrase.');
      appendCondition();
    }
    return value => {
      const haystack = value.toLocaleLowerCase();
      const matched = regex ? regex.test(value)
        : groups.some(conditions => conditions.every(literal => haystack.includes(literal)));
      return mode === 'excludes' ? !matched : matched;
    };
  }
  function matchesColumnFilters(values, filters) {
    return Array.from(filters).every(([index,filter]) => {
      const found = String(values[index] ?? '').toLocaleLowerCase().includes(filter.text.toLocaleLowerCase());
      return filter.mode === 'excludes' ? !found : found;
    });
  }
  function columnValueOptions(rows, index, query) {
    const counts = new Map();
    const term = query.trim().toLocaleLowerCase();
    rows.forEach(row => {
      const value = String(row[index] ?? '').trim();
      if (value && value.toLocaleLowerCase().includes(term)) counts.set(value,(counts.get(value) || 0)+1);
    });
    return Array.from(counts,([value,count]) => ({value,count})).sort((a,b) => a.value.localeCompare(b.value,undefined,{numeric:true,sensitivity:'base'}));
  }
  function columnFilters(table, changed, getValues) {
    const filters = new Map();
    const headers = Array.from(table.tHead.rows[0].cells);
    const captions = headers.map(header => header.textContent);
    const buttons = [];
    const summary = document.createElement('div');
    summary.className = 'column-filter-summary';
    summary.setAttribute('aria-live','polite');
    table.closest('.table-wrap').before(summary);
    function update() {
      buttons.forEach((button,index) => {
        button.textContent = captions[index] + (filters.has(index) ? ' ●' : ' ▾');
        button.classList.toggle('filter-active',filters.has(index));
      });
      summary.replaceChildren();
      filters.forEach((filter,index) => {
        const chip = document.createElement('button');
        chip.type = 'button';
        chip.textContent = captions[index]+': '+(filter.mode === 'excludes' ? 'does not contain ' : 'contains ')+filter.text+' ×';
        chip.title = 'Remove this filter';
        chip.addEventListener('click',() => {filters.delete(index);update();});
        summary.append(chip);
      });
      if (filters.size) {
        const clear = document.createElement('button');
        clear.type='button'; clear.textContent='Clear column filters';
        clear.addEventListener('click',() => {filters.clear();update();});
        summary.append(clear);
      }
      changed();
    }
    headers.forEach((header,index) => {
      const button = document.createElement('button');
      button.type='button'; button.className='header-filter';
      button.textContent=captions[index]+' ▾';
      button.title='Filter '+captions[index];
      button.setAttribute('aria-haspopup','dialog');
      header.replaceChildren(button); buttons.push(button);
      button.addEventListener('click',() => {
        const editor=document.createElement('dialog');
        editor.className='column-filter-dialog';
        editor.setAttribute('aria-label','Filter '+captions[index]);
        editor.innerHTML='<form method="dialog"><div class="dialog-heading"><h2></h2><button value="cancel" type="submit">Close</button></div><label>Match <select><option value="contains">Contains</option><option value="excludes">Does not contain</option></select></label><label>Text <input type="text" placeholder="Enter text to match" autofocus></label><label>Available values <select class="column-values" size="5" aria-label="Available column values"></select></label><p class="column-values-status" aria-live="polite"></p><p>Choose a value to fill the Text field, or enter your own text. Values and counts reflect the current table search and applied column filters, across all pagination pages. Contains / Does not contain applies when you select Apply filter.</p><div class="filter-actions"><button value="cancel">Cancel</button><button value="clear">Clear</button><button value="apply">Apply filter</button></div></form>';
        editor.querySelector('h2').textContent='Filter '+captions[index];
        const input=editor.querySelector('input'), mode=editor.querySelector('select');
        input.value=filters.get(index)?.text || ''; mode.value=filters.get(index)?.mode || 'contains';
        const values=getValues();
        const choices=editor.querySelector('.column-values');
        const valueStatus=editor.querySelector('.column-values-status');
        function populateValues() {
          const options=columnValueOptions(values,index,input.value);
          choices.replaceChildren();
          options.slice(0,100).forEach(({value,count}) => {
            const option=new Option(value+' ('+number(count)+')',value);
            option.title=value;
            choices.add(option);
          });
          choices.selectedIndex=-1;
          choices.disabled=!options.length;
          valueStatus.textContent=options.length ? 'Showing '+number(Math.min(100,options.length))+' of '+number(options.length)+' distinct values. Counts show rows. Type to narrow the list.' : 'No matching values. You can still apply the text you entered.';
        }
        input.addEventListener('input',populateValues);
        choices.addEventListener('change',() => {if(choices.selectedIndex>=0) input.value=choices.value;});
        populateValues();
        // Enter applies the filter, rather than activating the first (Cancel) button.
        input.addEventListener('keydown',event => {if(event.key==='Enter'){event.preventDefault();editor.close('apply');}});
        editor.addEventListener('close',() => {
          if(editor.returnValue==='clear') filters.delete(index);
          if(editor.returnValue==='apply') {
            if(input.value.trim()) filters.set(index,{text:input.value.trim(),mode:mode.value});
            else filters.delete(index);
          }
          const applied=['apply','clear'].includes(editor.returnValue);
          editor.remove(); if(applied) update(); button.focus();
        });
        document.body.append(editor); editor.showModal(); input.focus();
      });
    });
    filters.notify = update;
    return filters;
  }
  function csvContent(headers, rows) {
    const cell = value => {
      let text = String(value ?? '');
      // Keep inventory strings as text when opened in spreadsheet applications.
      if (/^[\\s]*[=+@-]/.test(text) || /^[\\t\\r\\n]/.test(text)) text = "'" + text;
      return '"' + text.replaceAll('"','""') + '"';
    };
    return '\\ufeff' + [headers,...rows].map(row => row.map(cell).join(',')).join('\\r\\n') + '\\r\\n';
  }
  function downloadCsv(name, headers, rows) {
    const url = URL.createObjectURL(new Blob([csvContent(headers,rows)], {type:'text/csv;charset=utf-8'}));
    const link = document.createElement('a');
    link.href = url;
    link.download = 'nsx-' + name.replace(/[^a-z0-9_-]+/gi,'-') + '.csv';
    document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url),1000);
  }
  function exportCellText(cell) {
    const copy = cell.cloneNode(true);
    copy.querySelectorAll('[data-copy-path]').forEach(button => {
      button.replaceWith(document.createTextNode(button.dataset.copyPath));
    });
    return copy.textContent;
  }
  function addStaticExport(table) {
    if (!table.tHead || table.dataset.exportReady) return;
    table.dataset.exportReady = 'true';
    const headers = Array.from(table.tHead.rows[0].cells,cell => cell.textContent);
    const button = document.createElement('button');
    button.type = 'button'; button.textContent = 'Export CSV';
    button.addEventListener('click', () => downloadCsv(table.closest('[data-panel]')?.id || 'details',headers,
      Array.from(table.tBodies[0].rows).filter(row => !row.hidden).map(row => Array.from(row.cells,exportCellText))));
    table.before(button);
  }
  function rowColumnText(id) {
    const scratch=document.createElement('tbody');
    scratch.innerHTML=renderRow(id, true);
    // Include expandable evidence, not just the button caption.
    scratch.querySelectorAll('[data-tag-row]').forEach(button => {
      const details=document.createElement('div');
      details.innerHTML=tagEvidence(rowPool[id].data); button.replaceWith(details);
    });
    return Array.from(scratch.rows[0].cells,exportCellText);
  }
  function tableController(widget) {
    if (controllers.has(widget)) return controllers.get(widget);
    const tools = widget.querySelector('.table-tools');
    let ids = widget.dataset.rows.split(',').filter(Boolean).map(Number);
    const tbody = widget.querySelector('tbody');
    const exportHeaders = Array.from(widget.querySelectorAll('thead th'),cell => cell.textContent);
    const search = tools.querySelector('input');
    const searchMode = tools.querySelector('.search-mode');
    const searchSyntax = tools.querySelector('.search-syntax');
    const searchEvidence = tools.querySelector('.search-evidence');
    const searchError = tools.querySelector('.search-error');
    const size = tools.querySelector('.page-size');
    if (['25','50','100'].includes(document.body.dataset.reportPageSize)) size.value = document.body.dataset.reportPageSize;
    const sortKey = tools.querySelector('.sort-key');
    const sortOrder = tools.querySelector('.sort-order');
    const panelId = widget.closest('[data-panel]').id;
    const view = rowPool[ids[0]]?.view || 'inventory';
    const policyTable = view === 'dfw' && 'rule_count' in (rowPool[ids[0]]?.data || {});
    const headerKeys = view === 'scopes' ? ['name','tag_count','vm_count','group_count','other_count',null] : view === 'tags' ? ['name','status','vm_count','group_count','other_count',null]
      : view === 'inventory' ? ['name','kind','usage','membership',null]
      : ['name',policyTable ? 'category' : 'policy_name',policyTable ? 'status' : 'hit_status',policyTable ? 'rule_count' : 'hit_count',null];
    const permitted = view === 'scopes' ? ['name','tag_count','vm_count','group_count','other_count'] : view === 'tags' ? ['name','scope','status','vm_count','group_count','other_count'] : view === 'inventory' ? ['name','path','kind','membership','method','references','usage']
      : (policyTable ? ['name','path','category','status','rule_count']
        : ['name','path','category','policy_name','hit_status','hit_count','rule_id','policy_rule_id']);
    const extraLabels = {other_count:'Other resource count',tag_count:'Tag count',kind:'Type',category:'Category',policy_name:'Policy',status:'Status',hit_status:'Activity status'};
    for (const key of permitted) {
      if (!Array.from(sortKey.options).some(option => option.value === key)) sortKey.add(new Option(extraLabels[key] || key, key));
    }
    const columnText = new Map();
    function availableColumnValues() {
      // Read current controls even if the search debounce has not fired yet.
      clearTimeout(timer);
      refresh();
      return matching.map(id => {
        if (!columnText.has(id)) columnText.set(id,rowColumnText(id));
        return columnText.get(id);
      });
    }
    const columnState = columnFilters(widget.querySelector('table'), () => {page=0;lastTerm=null;refresh();}, availableColumnValues);
    const sortHeaders = [];
    widget.querySelectorAll('thead th').forEach((header,index) => {
      const key = headerKeys[index];
      if (!key) return;
      const label = header.querySelector('.header-filter').textContent.replace(/ ▾$/, '');
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'header-sort';
      button.textContent = label;
      button.title = 'Sort by ' + (key === 'references' ? 'reference count' : extraLabels[key] || key.replaceAll('_',' '));
      button.setAttribute('aria-label','Sort by '+label);
      header.append(button);
      sortHeaders.push({header,button,key,label});
      button.addEventListener('click', () => {
        sortOrder.value = sortKey.value === key && sortOrder.value === 'asc' ? 'desc' : 'asc';
        sortKey.value = key;
        page = 0; lastTerm = null; refresh();
      });
    });
    Array.from(sortKey.options).forEach(option => { if (!permitted.includes(option.value)) option.remove(); });
    const collator = new Intl.Collator(undefined, {numeric:true,sensitivity:'base'});
    function sortValue(id, key) {
      const r = rowPool[id].data;
      if (key === 'method') return r.membership_definition?.methods.join(', ') ?? '';
      if (key === 'references') return r.referenced_by?.length ?? 0;
      if (key === 'kind') return r.inventory_type || types[r.kind] || r.kind;
      if (view === 'tags' && key === 'status') return ({both:'VMs and groups',vm_only:'VM use',group_only:'Group use',other_only:'Other resource use',unknown:'Needs review'})[r.status];
      if (['usage','membership','hit_status','status'].includes(key)) return labels[r[key]]?.[1] || r[key];
      return r[key];
    }
    function compare(a,b) {
      const x = sortValue(a,sortKey.value), y = sortValue(b,sortKey.value);
      if (x == null || y == null) return x == null ? (y == null ? a-b : 1) : -1;
      const order = typeof x === 'number' && typeof y === 'number' ? x-y : collator.compare(String(x),String(y));
      return (sortOrder.value === 'desc' ? -order : order) || collator.compare(rowPool[a].data.path,rowPool[b].data.path);
    }
    const previous = tools.querySelector('.previous');
    const next = tools.querySelector('.next');
    let page = 0;
    let timer;
    let lastTerm = null;
    let matching = ids;
    function refresh() {
      if (widget.closest('[data-panel]').hidden) return;
      sortHeaders.forEach(({header,button,key,label}) => {
        const active = sortKey.value === key;
        if (active) header.setAttribute('aria-sort',sortOrder.value === 'asc' ? 'ascending' : 'descending');
        else header.removeAttribute('aria-sort');
        button.textContent = active ? (sortOrder.value === 'asc' ? '▲' : '▼') : '↕';
      });
      const term = JSON.stringify([search.value,searchMode.value,searchSyntax.value,searchEvidence.checked]);
      let acceptsSearch;
      try {
        acceptsSearch = tableSearchMatcher(search.value,searchSyntax.value,searchMode.value);
        searchError.textContent = '';
        search.removeAttribute('aria-invalid');
      } catch (exc) {
        matching = [];
        searchError.textContent = (searchSyntax.value === 'regex' ? 'Invalid regular expression: ' : 'Invalid search expression: ')+exc.message;
        search.setAttribute('aria-invalid','true');
        tbody.replaceChildren();
        widget.querySelector('.page-status').textContent = 'Invalid search';
        widget.querySelector('.no-matches').hidden = true;
        previous.disabled = next.disabled = true;
        lastTerm = null;
        return;
      }
      if (term !== lastTerm) {
        matching = ids.filter(id=>{
          if (search.value.trim() && !acceptsSearch(tableSearchText(id,searchEvidence.checked))) return false;
          if (!columnState.size) return true;
          if (!columnText.has(id)) columnText.set(id,rowColumnText(id));
          return matchesColumnFilters(columnText.get(id),columnState);
        }).sort(compare);
        lastTerm = term;
      }
      const limit = Number(size.value);
      page = Math.max(0, Math.min(page, Math.ceil(matching.length / limit) - 1));
      tbody.innerHTML = matching.slice(page * limit, (page + 1) * limit).map(id => renderRow(id)).join('');
      widget.querySelector('.page-status').textContent = matching.length
        ? number(page * limit + 1) + '–' + number(Math.min((page + 1) * limit, matching.length)) + ' of ' + number(matching.length)
        : '0 results';
      previous.disabled = page === 0;
      next.disabled = (page + 1) * limit >= matching.length;
      widget.querySelector('.no-matches').hidden = matching.length !== 0;
      widget.dispatchEvent(new Event('table-render'));
    }
    search.addEventListener('input', () => {
      clearTimeout(timer);
      timer = setTimeout(() => { page = 0; refresh(); }, 150);
    });
    [searchMode,searchSyntax,searchEvidence].forEach(control => control.addEventListener('change', () => {
      clearTimeout(timer); page=0; lastTerm=null; refresh();
    }));
    size.addEventListener('change', () => { page = 0; refresh(); });
    [sortKey, sortOrder].forEach(control => control.addEventListener('change', () => { page=0; lastTerm=null; refresh(); }));
    previous.addEventListener('click', () => { page--; refresh(); });
    next.addEventListener('click', () => { page++; refresh(); });
    const exportButton = document.createElement('button');
    exportButton.type = 'button'; exportButton.textContent = 'Export CSV';
    exportButton.title = 'Export all rows matching the search and column filters, in the current sort order';
    exportButton.addEventListener('click', () => {
      clearTimeout(timer);
      refresh();
      if (search.hasAttribute('aria-invalid')) return;
      downloadCsv(panelId,exportHeaders,matching.map(rowColumnText));
    });
    tools.append(exportButton);
    tools.hidden = false;
    const controller = {refresh,
      invalidate() { columnText.clear(); page=0; lastTerm=null; refresh(); },
      unmount() { clearTimeout(timer); tbody.replaceChildren(); }
    };
    controllers.set(widget, controller);
    window.workspaceTables?.(widget, {filters:columnState});
    return controller;
  }
  document.querySelectorAll('main table').forEach(table => {
    if(table.closest('.table-widget') || !table.tHead) return;
    addStaticExport(table);
    const rows=Array.from(table.tBodies[0].rows);
    const values=rows.map(row => Array.from(row.cells,cell => cell.textContent));
    const filters=columnFilters(table,() => rows.forEach((row,index) => {row.hidden=!matchesColumnFilters(values[index],filters);}), () => values.filter((_,index) => !rows[index].hidden));
  });
  const helpTopics=Array.from(document.querySelectorAll('[data-guide-topic]'));
  const helpSearch=document.getElementById('guide-search');
  function filterHelp() {
    const term=helpSearch.value.trim().toLocaleLowerCase();
    helpTopics.forEach(topic => {
      topic.hidden=!!term && !topic.textContent.toLocaleLowerCase().includes(term);
      if(term && !topic.hidden) topic.open=true;
    });
    const count=helpTopics.filter(topic=>!topic.hidden).length;
    document.getElementById('guide-status').textContent=count ? count+' topics · Select a topic to read more.' : 'No topics found. Try another word or clear the search.';
  }
  helpSearch.addEventListener('input',filterHelp);
  document.getElementById('guide-expand').addEventListener('click',()=>helpTopics.filter(topic=>!topic.hidden).forEach(topic=>topic.open=true));
  document.getElementById('guide-collapse').addEventListener('click',()=>helpTopics.forEach(topic=>topic.open=false));
  filterHelp();
  navigate(false);
})();
</script></body></html>'''

    if fragments:
        # Split only our generated shell, never stored or user-provided HTML.
        styles = document.split("<style>", 1)[1].split("</style>", 1)[0]
        body = document.split("</nav></aside><main>", 1)[1]
        content, scripts = body.split("</main>", 1)
        # The workspace supplies the environment heading, timestamp and status.
        content = content.split("</header>", 1)[1]
        content = content.rsplit("<footer>", 1)[0]
        content = content.replace("This report displays a saved database snapshot.",
                                  "This report displays a saved database snapshot.")
        scripts = scripts.removesuffix("</body></html>")
        scripts = scripts.replace(".sidebar nav a", ".report-navigation a")
        content = content.replace("Filters are kept while navigating within the open report but are not saved when you reload it.",
            "Enable Remember tables in Settings to save searches, filters, sorting and column layouts across visits and snapshots for this environment. Table columns lets you hide and reorder columns; Reset table preferences restores the defaults. CSV exports still include every column.")
        content = content.replace("Filters remain while navigating the open report and reset on reload.",
            "Filters are restored across visits when Remember tables is enabled in Settings.")
        content = content.replace("Expand sidebar categories to reveal their pages. The highlighted link identifies your current page. Cards and chart legends open related report pages. Sidebar counts describe the whole category, while table counts reflect your current filters.",
            "Use the main sidebar to choose Inventory or Firewall, then use page tabs and the View filter to select a category or finding. The environment selector keeps your report section when switching managers. Cards and chart legends open related views; table counts reflect current filters.")
        content = content.replace("Choose <strong>Sort by</strong> and <strong>Order</strong>, or click the sort arrow beside a column heading.",
            "Click the sort arrow beside a column heading.")
        content = content.replace("Choose Plain text or Regex under Syntax, and Contains or Does not contain under Match.",
            "Open Search options to choose Plain text or Regex under Syntax, and Contains or Does not contain under Match.")
        content = content.replace("View evidence and View details open a dialog. Expand its sections for more information. JSON blocks have line numbers, syntax highlighting and Copy buttons. Close the dialog with Close, Escape or a click outside it.",
            "Select an object name, View evidence or View details to open the evidence side panel. Use its Summary, References, Definition and Statistics tabs where applicable; raw JSON is under Advanced. JSON blocks have line numbers, syntax highlighting and Copy buttons. Close the panel with Close, Escape or a click outside it.")
        return {"styles": styles, "navigation": menu, "content": content, "scripts": scripts}
    return document



def render_html_report(report):
    """Return fragments for the integrated, database-backed report viewer."""
    return _render_report(report, fragments=True)
