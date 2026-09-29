# Phase 2: DFW IPFIX proof of concept

Status: started on `development`. A bounded IPFIX v10 message/template inspector
and synthetic tests are implemented, together with an optional Docker UDP receiver
and GUI exporter mapping/status. Stateful flow decoding, PostgreSQL flow storage
and traffic exploration are **not yet available**. Existing snapshot
collection is unaffected. This is a validation project, not a production release.

## Live pilot requirements

| Requirement | Action |
| --- | --- |
| NSX environment | Select one environment with DFW IPFIX available; record NSX/ESXi versions and verify feature entitlement. Start with a small workload scope. |
| Collector address | Use the existing Docker host with a stable IP reachable from exporting ESXi management VMkernel interfaces. The collector runs in its own optional Compose service. Docker Desktop needs validated inbound UDP forwarding; localhost cannot be the exporter destination. |
| Network | Allow approved ESXi source addresses to the chosen collector UDP port. 2055 is a common NSX deployment choice; receiver and profile must agree. Keep it off public networks. |
| Export configuration | Use the optional approved setup wizard with an account permitted to configure IPFIX, or configure NSX manually. Inventory collection uses GET requests only. |
| Environment mapping | Explicit listener/exporter-to-environment mapping. Overlapping addresses or NAT require separate listener/session context; an observation-domain ID alone is insufficient. |
| Test traffic | Known IPv4 endpoints, ports and rule matches; IPv6 where used. Include long-lived connections and allowed/blocked traffic where exported. Synchronize clocks. |
| Protocol evidence | Template and data messages from the same session; record template refresh, sampling, timeout and restart behavior. Receive long enough to see refreshed templates. |
| Storage | PostgreSQL pilot storage with bounded batch writes and independent retention in a later increment. Never commit captures or customer flow data to Git or images. |

Suggested starting experiment: reserve capacity on the Docker host for a collector
container with up to 2 CPUs and 4 GiB RAM. No separate Linux VM is required.
This is an unbenchmarked starting allocation, not a guaranteed capacity requirement.
Disk must be sized from measured records/second, stored bytes/record, retention,
indexes and WAL. Start with a short 15–30 minute capture and a small scope; use
24-hour flow retention initially once storage is implemented.

The earlier investigation observed NSX 4.2.1; actual DFW exports still need validation.
Do not substitute Switch IPFIX for DFW IPFIX when testing rule/action correlation.
Their fields and profile settings differ.

## Initial implementation

`webapp/inventory/ipfix/inspect.py` validates one UDP payload:

- IPFIX version, message/set lengths and bounded template field counts.
- Template and Options Template fields, enterprise IDs and scope fields.
- Field order, duplicates and variable-length declarations preserved.
- Data sets identified by ID/size, explicitly not decoded without template state.
- Invalid messages and UDP template withdrawals rejected.

It opens no sockets, stores no raw data and modifies no database records. Tests use
synthetic bytes only; they do not establish compatibility with actual NSX exports.

## Selected deployment: Docker Compose

The collector will run on the existing Docker host as an optional `ipfix` service,
using the application image with a dedicated internal receiver command. It will
share the private Compose network for PostgreSQL access and have independent
restart behavior and health reporting. The web process will not receive UDP.

Publish a configurable host UDP port (initial proposal: `2055:2055/udp`). NSX must
target the Docker host's reachable IP and published port, not a container address.
Validate which source addresses reach the receiver through Docker networking before
relying on exporter allowlists or environment mapping. Containers do not remove the
need for routing and host-firewall access from the ESXi exporters.

Collector settings and exporter mapping will be managed through the GUI. Database
storage and retention will remain independent of the container lifecycle. The
optional service will be disabled by default until configured. The optional receiver service now exists; see the development setup below.

## Next increments

1. **Optional Docker Compose receiver service:** separate from Django web workers, with bounded
   receive queues, source allowlists, explicit environment mapping and health counters.
   Configuration/status belong in the GUI; worker commands are internal deployment details.
2. **Stateful decoder:** scope template caches to environment, listener, exporter
   transport session (IP/port), observation domain and template ID. Handle expiration,
   refresh, Options Data, missing templates, variable/reduced-size fields and restarts.
3. **PostgreSQL storage:** batch inserts with independent retention; track freshness,
   queue drops, undecodable data and uncertain sequence gaps. IPFIX sequence numbers
   count Data Records, not packets. Missing templates, reordering and restarts prevent
   exact loss estimates.
4. **Pilot GUI:** exporter/template status and a limited time-filtered flow table.
   Sampling must be explicit or unknown. UDP sources are not authenticated; an
   allowlist does not establish tenant isolation or prove exporter identity.
5. **Correlation experiment:** verify enterprise rule-ID/action fields against known
   rules and historical inventory. Never infer vendor semantics from field position
   or length alone. Keep ambiguous correlations visible.
6. **Benchmark:** replay representative data at increasing rates and measure CPU,
   memory, accepted/decoded records, queue drops, storage writes and query latency.

## Acceptance gates

- Actual NSX templates and decoded records agree with independent inspection such
  as Wireshark; record exact NSX/ESXi versions and enterprise element IDs.
- Known traffic matches expected endpoints, ports and available counters.
- Missing templates, export gaps, sampling and ambiguous mappings remain visible.
- Restart, reordering, template changes and overlapping environment addresses do
  not contaminate other sessions or environments.
- Overload stays bounded and reports lost/undecodable input; retention limits disk use.
- Stopping the collector does not affect snapshot audits.

Dependency maps and policy recommendations remain later work. No observed flows
is not proof of non-use or approval to delete a rule.

## Inputs needed before live reception

- Reachable collector IP and desired UDP port.
- First Analyzer environment, approved exporter addresses and workload scope.
- Approximate exporter count/traffic volume.
- An operator to configure DFW IPFIX once the receiver is ready.

NSX destinations are changed only through the explicitly approved setup wizard. Do not send IPFIX to the web port.

## References

- [Broadcom DFW export validation](https://knowledge.broadcom.com/external/article?articleNumber=422758)
- [DFW profile API](https://developer.broadcom.com/xapis/nsx-t-data-center-rest-api/latest/method_CreateOrReplaceIPFIXDFWProfile.html)
- [DFW collector API](https://developer.broadcom.com/xapis/nsx-t-data-center-rest-api/latest/method_CreateOrReplaceIPFIXDFWCollectorProfile.html)
- [RFC 7011](https://www.rfc-editor.org/rfc/rfc7011.html)

## Development receiver: Docker setup

The optional metadata receiver and **Administration → IPFIX collector** page are
now implemented. This is a template/delivery proof of concept; flow decoding,
correlation and traffic explorer remain pending.

From the source checkout's `webapp` directory, build the development image and start
its optional service:

```sh
docker compose --profile ipfix up -d --build web worker scheduler ipfix
```

This development feature is not yet published in the stable Docker Hub image.
For remote PostgreSQL, use the existing `compose.remote.yaml` override as well.
The receiver inherits the application database configuration.

1. Open **Administration → IPFIX collector**.
2. Add each approved exporter source IP and its target environment. This pilot has
   one listener and one environment per source address. Overlapping exporter IPs
   behind NAT require distinct externally visible addresses or separate deployments.
3. Enable reception. Configuration changes are applied within approximately five seconds.
4. Configure NSX DFW export separately to the Docker host IP on UDP 2055.
5. Confirm an online heartbeat, last-received timestamps and template previews.

`IPFIX_BIND_ADDRESS` and `IPFIX_PORT` in the Compose deployment can change the host
bind/port. The container listens on 2055/UDP; the default host binding is all IPv4
interfaces. Restrict host firewall access to intended exporters. The service is
optional and reception defaults to disabled. Stopping it does not stop snapshot jobs.

Only one receiver may run against a database, enforced by a PostgreSQL advisory
lock. It flushes metadata roughly every five seconds. A process/database failure
can lose that pending interval; metadata counters are diagnostic, not accounting.
No raw datagrams or decoded flows are stored. Source IP filtering is not authentication.

Resource bounds: 512 MiB container limit, two CPUs, a requested 1 MiB socket receive
buffer (OS may adjust it), at most 1,000 enabled mappings loaded, 16 recent template
previews per exporter and 128 fields per preview. Templates beyond that preview
length are marked truncated. UDP intake runs in a separate thread with a bounded 1,024-datagram queue (at most approximately 64 MiB of payloads). Parsing and periodic database writes consume that queue independently. Queue drops and Linux socket receive drops are counted; unsupported socket-drop monitoring is labeled unavailable. Network drops and losses before the container are not measured. A saturated receiver can drop datagrams; this is not loss-free ingestion.
Remove a mapping to delete its metadata. Disabled mappings retain their counters.

## Discover exporters through vCenter

In **Administration → IPFIX → Discover from vCenter**, choose the NSX environment,
retrieve and review its CA certificates, then enter the vCenter hostname and read-only credentials. The application host needs HTTPS access to vCenter on TCP
443. TLS certificate and hostname verification remain enabled. The account needs
visibility of the intended hosts and permission to read their network configuration
(`System.Read` for `QueryNetConfig`).

Review the host, cluster and management VMkernel address list, then select the
addresses to add. Discovery reads selected management IPv4 interfaces; it does not
guess addresses from DNS. Previews expire after ten minutes and are limited to 200
hosts and 1,000 addresses. Any incomplete host reads are shown in the preview.
Credentials are used only for the discovery request and are not saved. Existing mappings are preserved; a conflicting environment assignment
rejects the entire selection.

vCenter is used for discovery, not as a flow exporter. ESXi hosts still send IPFIX
directly to the Docker receiver. Verify that discovered management addresses match
the actual packet source addresses, especially when NAT is present. Adding mappings
does not enable reception or change NSX export configuration.

### Retrieve CA certificates during setup

Expand **Retrieve CA certificates from vCenter**, enter its hostname, and retrieve
its CA bundle. This uses vCenter's `/certs/download.zip` endpoint without credentials.
Because this bootstrap download is not yet authenticated, compare the displayed
SHA-256 fingerprints with a trusted administrator or independently verified source.
Select the confirmation checkbox, enter your discovery credentials, and connect.
The normal SDK connection still verifies the certificate chain and hostname.

Retrieved public CA certificates are held temporarily in your session, expire from
use after ten minutes, and are removed after successful discovery. They are not
installed into the system trust store. The former PEM upload field has been removed.
The download is bounded, redirects are rejected, and leaf certificates, CRLs and
expired certificates are not offered as CA trust anchors.

Reference: [Broadcom's vCenter certificate download instructions](https://knowledge.broadcom.com/external/article?legacyId=2108294).


## Guided NSX setup

Open **Administration → IPFIX → Configure IPFIX**. Confirm the reachable receiver
IPv4 address and published UDP port. Exporter source mappings are managed separately
under **Exporter sources**, using manual entry or vCenter discovery. Missing mappings
do not block NSX setup; delivery checks use the environment's existing enabled mappings. The receiver must be online; setup
cannot create Docker port mappings, routes or firewall exceptions.

Inspect and preview is read-only. When using an existing DFW profile, the wizard proposes adding the receiver to
its referenced collector, retaining existing destinations and export intervals.
To create a new profile, supply an existing group Policy path: the wizard creates a dedicated
collector, a one-minute DFW export profile and a GroupMonitoringProfileBindingMap.
Only the selected group's workloads are bound. Empty groups or groups without
eligible workloads may not export traffic. Existing profile scope is not expanded.

Review the exact PUT bodies and before values, then explicitly approve. The saved
Manager account needs IPFIX write permissions; TLS verification must be enabled.
Plans are bound to their creator and expire after ten minutes. Changes to environment
settings or inspected NSX resources invalidate the preview. Existing collector
updates include the retrieved revision. Protected/federated collectors are rejected. Writable system-owned collectors are explicitly labeled in the preview; their owner may later reconcile configuration.
The pilot supports at most four existing DFW profiles per setup attempt.

After successful writes, the receiver is enabled. Source mappings are never added or changed by this wizard.
Reception is global for all enabled mappings, which is stated in the preview.
The status page checks every ten seconds for three minutes for new valid datagrams
from mapped sources; it does not claim traffic delivery based on API success alone.
Template inspection remains the current POC scope; decoded flow analytics are pending.

Previews and outcomes are recorded in PostgreSQL. Approval cannot be replayed.
NSX writes are not atomic across resources: a failed or interrupted attempt may
leave earlier writes in place, and a timed-out write may have succeeded. Inspect the
listed paths and acknowledged outcomes before creating a fresh preview. No automatic
rollback deletes resources. An interrupted attempt may remain marked applying;
inspect NSX before trying again.

API references: [DFW profile](https://developer.broadcom.com/xapis/nsx-t-data-center-rest-api/latest/method_CreateOrReplaceIPFIXDFWProfile.html),
[collector profile](https://developer.broadcom.com/xapis/nsx-t-data-center-rest-api/latest/method_CreateOrReplaceIPFIXDFWCollectorProfile.html),
[group binding](https://developer.broadcom.com/xapis/nsx-t-data-center-rest-api/latest/method_UpdateGroupMonitoringBinding.html).


### Choose an existing or new profile

Choose **Use existing profile**, select the environment, and click **Load environment
profiles** to populate the profile selector. Select the desired profile. Only its
referenced collector is extended; other consumers sharing that collector also receive
the extra destination. Leave the group blank to retain scope, or supply a group path
to create an additional activation binding.

Choose **Create new profile** to create a dedicated collector and DFW export profile
even when profiles already exist. Enter a name, priority and group path. Applying the
approved plan activates the profile by creating its group binding; there is no
separate DFW-profile enabled flag in this workflow. Existing profiles remain intact.
The review shows competing priorities. Lower numbers take precedence on overlapping
scope; a pre-existing priority-zero global profile can prevent the new profile from
being effective. The wizard does not silently disable competing profiles. Check NSX
realization and actual datagrams after applying; API success is not proof of delivery.


## Receiver diagnostics and decoder evaluation (development)

The receiver retains up to 100 source IP/port/version/outcome combinations, expiring
one hour after last observation. Counts cover the retained entry's lifetime, not a
rolling one-hour packet total. Only header metadata is recorded, never packet payloads.
Unknown sources remain blocked. Version on a rejected message is only the first two
bytes, not a validation result. The staff-only collector page exposes paused,
unmapped and malformed outcomes separately. Do not automatically map a Docker/NAT
address shared by several environments.

The queue decouples socket intake from database flushes. It is intentionally bounded
and in-memory; it is not durable. Shutdown drains pending packets; crashes may lose
the queue and unflushed counters. Linux socket-drop counters can lag until another
packet arrives. Metrics do not establish loss-free collection.

Setup previews now include read-only realization, selected-group logical-port/switch
membership evidence, current group bindings and existing priorities. A refresh button
rechecks these independently of applying a plan. Unsupported/failed API reads remain
unknown; a realized profile or nonempty group does not prove effective host export.
Global scope and overlap are not automatically inferred.

GoFlow2 v2.2.6 is evaluated by `tools/evaluate_ipfix_decoder.py`, pinned to image digest
`sha256:b930e69d1bc7bd765da5b45d12f04edd10970c310b33ba44fd12a07a870da8e6`.
The developer-only harness uses isolated containers without published ports, synthetic
RFC documentation addresses, and a synthetic enterprise field. It removes its
containers and temporary mapping on completion. It makes no NSX requests and is not
a product configuration CLI. Build the application image as
`nsx-security-analyzer:ipfix-review` first; then run the harness with Python 3.
`DOCKER` optionally selects the Docker executable. No credentials are required.

Verified: three IPv4 records retain endpoints, destination port and the synthetic
enterprise value. This does **not** identify the VMware element as a firewall rule ID.
Real NSX templates, options/sampling, IPv6, missing-template recovery, restart behavior,
domain/session isolation and throughput remain acceptance gates before replacement.
See [GoFlow2's versioned mapping example](https://github.com/netsampler/goflow2/blob/v2.2.6/cmd/goflow2/mapping.yaml).

### Storage decision after decoder validation

Keep configuration and inventory in PostgreSQL. For an initial flow pilot, use a
separate normalized flow table with daily partitions, bounded batch inserts and
24-hour raw-record retention; retain coarse summaries longer. Preserve listener,
environment, exporter session, observation domain, template provenance, export and
receive timestamps and sampling-known/unknown state. Only populate rule/action fields
after validating actual vendor elements. Unknown correlation stays explicitly unknown.
Measure records/second, WAL growth and query latency before adopting this as the
production storage design. Flow storage is still gated on actual export validation;
no synthetic flow records are inserted into the application database.
