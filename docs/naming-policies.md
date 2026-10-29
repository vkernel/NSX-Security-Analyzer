# VM naming policies and tagging proposals

Naming assessment is read-only. It never renames a VM, changes an NSX object, or writes a tag to NSX/vCenter. Naming issues are separate from finding reviews and deletion approvals.

## Configure a policy

Open **Administration → Naming & tagging → Naming policies**. Create a global policy or an environment-specific policy. A policy's environment is fixed after creation; create another policy to change its scope.

Add ordered components, using Move up / Move down or order numbers. Each component has a logical field, source, default scope, required/optional flag, output case, allowed character format and maximum length. Sources are VM tags, fixed text or a VM identity suffix. No automatic sequence numbers are invented. Separators may be hyphens, underscores, dots or empty. Missing optional values do not leave extra separators.

Example:

| Logical field | Scope | Observed value | Mapping / output |
|---|---|---|---|
| Environment | Environment | Production | Production=PRD |
| Location | Location | Rotterdam | Rotterdam=RTM |
| Application | Application | Citrix | Citrix=CTX |
| Role | Role | Desktop | Desktop=VDI |
| Instance | Instance | 0042 | 0042 |

With uppercase and a hyphen separator, the proposed name is `PRD-RTM-CTX-VDI-0042`.

Mappings are one `observed=output` per line. If mappings exist, unmapped values are invalid instead of silently passing through. Tags/scopes and mapping keys match exactly, including case. Case conversion affects output only. Duplicate identical tag assignments are deduplicated; distinct values for a single scope are ambiguous.

## Environments and categories

A logical field such as Application can use `Application` by default and `AppCode` in another environment. Save these changes under **Environment scope mappings**. Restore a default by mapping the field to its default scope again. Suggestions come from retained scope inventory; free-text entry supports environments not yet tagged.

To target a VM category, specify a logical tag field and an exact observed category value. Environment-specific matching policies take precedence over matching global policies. Within that scope, category-specific policies take precedence over a general default. Multiple remaining matches are reported as ambiguous; no arbitrary first policy is chosen. A nonmatching environment category falls back to global rules.

## Draft, preview, activate

Saving components or environment mappings creates a draft/version; the active configuration remains unchanged. **Preview affected VMs** compares the active configuration with the draft using each affected environment's latest retained full collection. It shows outcome counts, before/after transitions, and up to 40 examples with missing scopes, invalid values and collisions.

Acknowledge the impact and activate. If policy revisions, mappings, or evaluated inventory change after preview, activation requires another preview. Every change is versioned and audited. Previous versions can be opened as drafts, saved, previewed and activated; their environment scope mappings must be reviewed separately. Disabling assessment is explicit and may expose global defaults or leave VMs without an applicable policy.

## Compliance and onboarding

Open **Naming compliance** in navigation and choose an environment. The page shows VM identity, observed name, proposed name, policy, outcome and an explanation of every component. Filter by issue, policy, owner or search across names/identities/observed tags. Application-tag values are searchable. Export the filtered results to CSV.

Outcomes include compliant, name differs, missing required tags, ambiguous tags, invalid value, duplicate proposed name, no applicable policy, ambiguous policy, and incomplete evidence. Proposed names are compared case-insensitively against other current and proposed VM names in the same environment. This is deliberately conservative: it can flag a name still occupied by a VM that might itself be renamed later.

For untagged environments:

1. Collect inventory and define a default policy.
2. Export the assessment to obtain stable VM identities.
3. Select VMs and prepare scope/value proposals, or import a UTF-8 CSV with `vm_identity,scope,value` columns. Import is limited to 1 MB and 1,000 rows; unknown identities, duplicate scopes and invalid rows reject the entire import.
4. Assign responsibility in bulk. Review evidence and mark proposals reviewed, or reject/clear them.
5. Enable **Simulate proposed tags** to inspect proposed names. Simulation is explicitly labeled and never changes observed compliance.
6. Apply changes outside this application through your organization's approved process, then collect again to verify observed state.

Operators/administrators can manage proposals; viewers can inspect assessment; only administrators configure policies. Every proposal action is audited. A proposal stores only proposed fields, ownership, state and an observed-evidence fingerprint. If the VM name or observed tags change, the proposal becomes stale and is excluded from simulation. Reject it and prepare a fresh draft. Reviewed proposals also become drafts when their values change. No review here authorizes a deletion or an external write.

## Evidence, storage and limits

Assessment uses indexed VMs from the latest non-testing, non-imported full snapshot. It does not contact NSX. Archived browsing data must be restored before evaluation. Inventory warnings make results provisional and prevent claiming compliance. Snapshot observations are not real-time state.

Results are calculated on request from projected identity/name/tag fields; the feature does not duplicate per-VM results on every collection. Persisted data consists of policies/versions, scope mappings and at most one current proposal per environment/VM. Proposal changes are retained in the existing audit trail. UI lists have 50 rows per page; bulk selections are limited to 100 VMs. Large-environment assessments/activation previews scan all applicable VM identities so collision checks remain complete; this can take longer than opening an individual finding.

This release supports VM naming assessment. Groups, services, firewall rules and policies remain separate object types; names are not inferred from arbitrary member VMs.

Deploy migration **0055** before starting the updated web/worker components.
