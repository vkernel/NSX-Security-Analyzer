# Group naming policies and component proposals

Naming compliance assesses **NSX group display names**, not VM names. VM tags/scopes are evidence for building a group name. This workflow never renames objects or changes tags, membership expressions, rules or references in NSX/vCenter. Naming decisions are separate from deletion approvals.

## Group types and evidence

| Type | Definition | Naming source |
|---|---|---|
| Dynamic | Membership conditions | Positive VM Tag EQUALS conditions with scope and value; simple AND combinations can supply components directly, even for empty groups. |
| Static | Explicit IP/MAC addresses, object paths or external IDs; also an empty expression list | Reviewed component values. Explicit member VM tags appear as suggestions, never automatic naming inputs. |
| Mixed / nested | Conditions plus explicit members, or references to other groups | Reviewed component values. Nested definition evidence is shown with cycle/missing-definition warnings. |
| Unknown | Missing or unsupported definition | Explicit review; never assumed to be an empty static group. |

Classification uses the saved configuration, not current resolved membership. An empty dynamic group remains dynamic. OR, nested condition logic, extended expressions, unsupported conditions and mixed definitions require review before a name is generated. Conflicting values never select an arbitrary tag.

## Configure policies

Open **Administration → Naming policies**. Choose a global default or environment, then choose **Dynamic**, **Static**, **Mixed / nested** or **All group types**.

Add ordered components and move them up/down. Sources are definition tag/reviewed scope values, fixed text, group path suffix, and group type. Each component supports required/optional values, case, allowed characters, maximum length and exact value mappings. Separators may be hyphens, underscores, dots or empty. Missing optional values do not add spare separators.

For a type prefix, use the Group type source with mappings such as `dynamic=DGR`, `static=SGR`, `mixed=MGR`. These are examples; prefixes are entirely configurable. A template might produce `DGR-RTM-CTX-PRD` from type, location, application and environment. It does not need to reproduce every membership condition in the name.

Use **Choose existing scope** to search collected scopes. The selector shows how many group definitions and VMs use each scope, labels VM-only suggestions, and compares availability across environments for global policies. Select a scope before choosing its tag values in category filters, the mapping builder or static-group proposals. **Enter a new scope/value manually** keeps new environments supported.

Expand **Build a value mapping**, choose a collected value such as Production, enter PRD and select **Add / update mapping**. The editable mapping text remains available. Choosing suggestions never marks a proposal reviewed or changes NSX.

The **Live naming example** updates as components, mappings or order change. It evaluates an example from up to 100 saved groups in the selected environment (the first environment for a global draft), using saved draft scope bindings. It is illustrative; the full impact preview remains required for policy precedence and collisions. Complete required form fields first.

Suggestions come from ready, retained full snapshots and are cached for two minutes. Coverage warnings and missing collections are shown. Search returns up to 50 matches, from at most 10,000 scope/value pairs per environment; a limit warning is explicit. Manual values remain allowed.

Tag/scope names and mapping keys match case-sensitively. With mappings configured, unmapped values are invalid. Environment scope mappings let a logical field such as Application use `Application` in Rotterdam and `AppCode` in Amsterdam. Suggestions come from saved scopes; free-text entry supports new environments before tagging exists.

Matching environment policies precede global policies. Within that scope, a matching group-type policy precedes an all-type default; category rules then precede generic rules. A category is an exact scope value before mapping. Multiple remaining matches are ambiguous. A nonmatching local policy does not hide a matching global default.

## Preview and activate

Saving creates a draft/version. Active rules and mappings remain unchanged until **Preview affected groups**, impact acknowledgement and activation. The preview compares old/new names and outcomes, with up to 40 examples. Inventory, mapping or policy changes invalidate the preview. Configuration/version changes and proposal actions are audited.

Previous versions can be opened as drafts, saved, previewed and activated; review their environment mappings separately. Disabling a policy may expose a global default or leave groups without a policy.

## Group naming compliance

Use **Naming compliance** in the left menu. Filter by group type, outcome, policy, owner or search. Each row shows current name, group path/type, proposed name and the evidence behind every component. Expand evidence for definition tags, explicit addresses/members, nested definitions and member VM tag suggestions. Group metadata tags are not treated as membership conditions.

Duplicate proposals are checked case-insensitively against other groups' current and proposed names in the same environment. Names occupied by another group remain collisions even when that group might later be renamed.

## Reviewed values and exceptions

For static, mixed, untagged or otherwise ambiguous groups:

1. Select groups and enter scope/value proposals explicitly. Member suggestions never become accepted values automatically.
2. Optionally assign an operator and import drafts using UTF-8 CSV columns `group_path,scope,value`.
3. Inspect **Simulate draft component values**. Complex definitions continue to require review.
4. Mark selected proposals reviewed. Reviewed values now supply naming components in normal assessment; every component identifies whether its source is a definition tag, reviewed value or draft simulation.
5. Apply any eventual changes externally using your approved process. This application performs no rename or tagging operation.

CSV imports require known current group paths, are limited to 1 MB / 1,000 rows, and reject invalid imports atomically. Bulk page selection is limited to 100 groups. Value changes reset review. A changed group name, identity or definition makes proposals stale; reject/clear them before preparing fresh values. Changing a static member's VM tags does not automatically change its reviewed naming values.

Administrators can select **Accept naming exception with reason** for intentional names or groups outside the convention. The reason and reviewer appear in the row; an exception is distinct from compliance and never grants deletion approval. Group or naming-policy/mapping changes invalidate the exception. Reject/clear removes it from current assessment while preserving audit history.

## Deployment and storage

Apply migrations **0055 and 0056** before starting matching web/worker versions. Earlier VM proposals are preserved as legacy records and excluded from group assessment. Existing VM policies are not automatically repurposed: review group type/components, save a group draft, preview and activate.

Assessment uses the latest retained full, non-test, non-imported snapshot. Archived browsing data must be restored. Missing/warning coverage is explicit; saved evidence is not a live NSX check. Nested supporting evidence is bounded to 100 unique definitions and 30 levels, with an explicit limit message.

Results are calculated on request rather than duplicated per collection. Persisted data is policies/version history, scope mappings, and one proposal/exception per environment/group. List pages contain 50 rows; collision checks include all groups in the environment.
