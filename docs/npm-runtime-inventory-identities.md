# Path-qualified npm runtime inventory

The host-side Linux runtime observer records each discovered global npm package
instance with an install-path-qualified identity instead of collapsing the
inventory by package name.

The dependency identity format is:

`npm:<normalized-package-name>#<image-owned-install-path>`

Examples:

- `npm:alpha#usr/lib/node_modules/alpha`
- `npm:shared#usr/lib/node_modules/alpha/node_modules/shared`
- `npm:shared#usr/lib/node_modules/beta/node_modules/shared`
- `npm:@scope/pkg#usr/lib/node_modules/@scope/pkg`

This preserves multiple valid versions of the same package when they occur at
different nested install locations. The SBOM verifier retains these identities
as distinct keys and exact live-inventory comparison therefore checks both
version and install location.

The observer also verifies that the package directory suffix agrees with the
package's declared `name` in `package.json`. It continues to reject malformed
manifests, symlinked or inaccessible package paths, duplicate install paths,
excessive nesting, excessive entry counts, and identities longer than the
bounded inventory key limit.

## Scope and limitations

The observer inventories global npm trees under `/usr/lib/node_modules`
and `/usr/local/lib/node_modules`. It can additionally inventory one
operator-configured application root after mount-namespace verification, using
the package-lock v2/v3 parser and exact reconciliation with the actual
`node_modules` tree. Application scanning is disabled unless the trusted host
service sets `YUKI_HOST_INVENTORY_APPLICATION_ROOT`. Link/workspace entries,
pnpm stores and Yarn Plug'n'Play remain unsupported. Missing or divergent
lockfile/live-tree evidence fails closed.

The identity is a filesystem install location, not a claim that the package
contents are cryptographically authenticated by this observer. Image digest
binding and the independently publisher-authenticated SBOM remain required.


## Container mount provenance groundwork

`core/linux_container_mount_provenance.py` parses bounded Linux
`/proc/<host-pid>/mountinfo` records, including kernel octal path escapes,
and validates a proposed application root against the process mount namespace.
An application root is rejected if it is itself a mountpoint, contains a
nested mount, overlaps a mount ancestor, or intersects the explicit
task-controlled `/workspace` mount. Exactly one root mount and one workspace
mount are required.

The live Linux observer now reads mountinfo from the same pinned procfs PID
directory used to open the container root. When the trusted host configuration
sets `YUKI_HOST_INVENTORY_APPLICATION_ROOT` to a container-absolute path, the
observer validates that root against the pinned mount table before opening it
with descriptor-relative, no-follow traversal. It then reads the bounded
package-lock v2/v3 file, inventories the root's actual `node_modules` tree,
and requires exact identity-and-version equality before adding application
packages to live evidence. The workspace mount and any nested mount overlap
cause collection to fail closed. Leave the setting unset when the image has no
known immutable application root; application dependency inventory is then
omitted rather than inferred.


## Enabling application dependency inventory

Set `YUKI_HOST_INVENTORY_APPLICATION_ROOT` only in the trusted host inventory
service environment, not in task-controlled container variables. The value is
a container-absolute application root (for example, `/opt/yuki`) baked into
the pinned image. The observer requires a package-lock v2/v3 file and an
installed `node_modules` directory beneath that root; their complete
path-qualified dependency inventories must match exactly. Optional,
platform-specific or omitted production dependencies that make the lockfile
differ from the installed tree cause collection to fail closed. This strict
policy is intentional until a more precise npm install-state model is
implemented.

The host configuration must be tied to the immutable image profile and must
not be populated from a task request, workspace file, or mutable container
environment. Do not set this variable for an application root that is mounted
from a volume.


## npm dependency-role classification

The bounded package-lock parser now exposes a separate classification view for
`production`, `development`, `optional`, `development_optional`, and
bundled variants using npm's boolean `dev`, `optional`, `devOptional`, and
`inBundle` flags. Malformed flag types and contradictory `devOptional`
metadata are rejected. The existing `parse_npm_package_lock` return contract
remains identity-to-version so current live-inventory and signed-SBOM schemas
are unchanged.

Important limitation: this classification helper is not yet included in the
authenticated runtime envelope or publisher SBOM schema. It is advisory lock
metadata only and must not be treated as proof that a package is present in the
installed tree. The live observer continues to establish installed package
presence through exact path-and-version reconciliation.


## Python application dependency provenance integration

The Python lock parser is now connected to the existing image-owned
application-root observer. The host reads at most one of `poetry.lock`,
`uv.lock`, or `requirements.txt` from the verified root. Requirements files
are accepted only when every non-comment line is an exact `name==version`
pin; includes, options, URLs, editable declarations, ranges, environment
markers, and duplicate normalized names are rejected. uv's exact editable
workspace-root marker (`source = { editable = "." }`) is excluded as the
project itself; other local, VCS, path, and editable dependency sources remain
unsupported.

A Python lock source requires exactly one image-owned application virtualenv
at `.venv/lib/pythonX.Y/site-packages` or
`venv/lib/pythonX.Y/site-packages`. Installed distributions are read only
from bounded `.dist-info/METADATA` files and must match the lock's normalized
name/version map exactly. Multiple lock sources, missing or ambiguous
virtualenvs, incomplete metadata, and any lock/installed mismatch fail closed.
Python-only application roots are supported; if application `node_modules`
exists without a `package-lock.json`, collection fails closed.

The runtime filesystem walk now skips every mountpoint reported by the same
pinned container mount namespace, including `/workspace`; it cannot treat
task-mounted packages as image-owned dependencies. The application root is
still operator-configured through host-only
`YUKI_HOST_INVENTORY_APPLICATION_ROOT` and is validated before any lockfile
or application dependency traversal.

The lock parser and observer tests are committed. They have not been executed
in this environment, and the authenticated envelope/SBOM still carries only
the existing package identity/version map. Role/group/marker resolution and
non-venv Python install layouts are intentionally unsupported.


Python registry trust is restricted to the canonical `https://pypi.org/simple` endpoint. uv registry metadata and Poetry's legacy `type = "legacy"` source representation are accepted only when their URL/reference identifies public PyPI. Custom indexes, ambiguous source fields, and non-registry sources are rejected rather than silently trusted.


PEP 621 `pyproject.toml` metadata is parsed separately from resolved lockfiles.
The parser requires a statically declared project name, validates an optional
static version, and extracts normalized dependency identities with
`runtime` and `optional:<extra>` roles from standardized project dependency
fields. Dependency declarations are constraints, not installed-version
evidence; direct URL/path dependencies are rejected, and role metadata is not
yet included in the authenticated runtime envelope or SBOM schema.


## Runtime evidence contract compatibility

The host-authenticated inventory envelope and publisher-signed SBOM schema v1
continue to carry the existing `runtime_versions` and
`installed_dependencies` maps. Runtime evidence validation now uses the
shared canonical dependency identity validator, so Python distribution names,
Debian package identities (`deb:<name>`), and path-qualified npm identities
(`npm:<name>#<install-path>`) survive evidence construction without being
misread as plain Python package names. The evidence model permits up to 4,096
installed dependency entries while retaining the separate 128-entry runtime
version bound.

Operator compatibility locks are normalized through the same identity
contract before exact version comparison. Existing unprefixed Python package
locks remain backward compatible. The change does not add application
metadata, package roles, or image metadata to the signed envelope; those
require a separately versioned and independently reviewed schema extension.


## Signed inventory and authenticated-envelope parity

The signed SBOM verifier and authenticated runtime-envelope verifier now both
delegate installed-dependency identity validation to the same canonical
provenance contract used by the live filesystem observer and readiness gate.
This preserves Debian prefixes and npm install-path case, and prevents the
older generic Python-name normalizer from collapsing distinct Linux paths.

Runtime-version maps retain their 128-entry ceiling. Installed dependency maps
use a separate 4,096-entry ceiling, subject to the existing overall signed
manifest / IPC message byte limits. Thus the item ceiling is not a promise that
an arbitrarily large inventory fits in a transport frame. Schema-v1 manifest
fields and authenticated envelope fields remain unchanged.


## Docker image-object identity binding

The supervisor now validates the immutable application image through
`core.image_provenance.verify_docker_image_identity` before accepting host
runtime evidence. It requires all three values to agree:

- the image reference pinned in the operator-owned sandbox profile;
- the image ID reported by the live container;
- the image object's own `Id` plus an exact matching `RepoDigests` entry.

Malformed, oversized, absent, or mismatched repository digest metadata fails
closed. The helper returns the verified pinned digest for the supervisor
snapshot. This is a Docker control-plane consistency check, not a publisher
signature or hardware attestation; signed SBOM and authenticated host-agent
provenance remain separate trust requirements. Existing runtime evidence and
signed SBOM schema-v1 wire formats are unchanged.


## Signed application release provenance (schema v1)

`core.application_release_provenance` adds a bounded, canonical-JSON HMAC
record for an application release. The record binds the operator-pinned
application version and build ID to the immutable image reference and digest,
then binds that release to the exact task, worker, and execution-spec identity.
It carries the measured runtime-version map and canonical installed
Python/Debian/npm dependency inventory.

Verification requires an operator-supplied key of at least 256 bits, an exact
schema-v1 field set, a valid constant-time HMAC comparison, exact release and
execution identity matches, and exact runtime/dependency inventory parity.
Unknown fields, unsupported schema versions, malformed identities, inventory
drift, or signature failures are rejected. The record is bounded to 32 KiB and
retains the existing runtime IPC and signed SBOM schema-v1 contracts unchanged.

This module is an explicit signing/verification primitive for trusted release
composition; it does not silently infer an application version or build ID
from task-controlled environment variables. Production host composition must
supply these values from its authenticated release configuration and invoke
verification against the live host evidence before treating the release record
as trusted. HMAC proves possession of the configured shared secret, not
public-key publisher identity or hardware-backed attestation. Key custody and
the release-signing pipeline remain in the trusted computing base.


## Host evidence release-provenance gate integration

`core.application_release_provenance.build_release_verified_runtime_inventory_provider`
provides the composition adapter for `LinuxHostRuntimeEvidenceBuilder`.
Production host composition wraps its existing trusted runtime inventory
provider with this adapter before constructing the evidence builder. The adapter
collects measured runtime/dependency inventory, resolves a signed release
record from a host/service-owned immutable record store, and verifies the HMAC,
application version, build ID, immutable image reference/digest, task, worker,
execution-spec identity, and exact measured inventory parity. It returns the
inventory only after all checks pass.

The release-record lookup receives only the immutable image reference and
host-observed execution identities (task, worker, spec, container). It must be
implemented by trusted operator code backed by a protected release-record
store; it must not read task-controlled environment variables, workspace
files, or mutable container configuration. The HMAC key, application version,
and build ID are fixed by root/service-managed deployment configuration.
Missing records, invalid signatures, identity replay, and runtime or dependency
drift fail closed before `RuntimeEnvironmentEvidence` is constructed.

This is an explicit production composition gate, not an implicit global
switch: deployments that have not provisioned signed per-execution release
records may continue using the existing schema-v1 SBOM/runtime path, but must
not describe those deployments as application-release-provenance verified.
Enable the gate only after the host-owned record lookup and signing pipeline
are deployed. Runtime IPC and SBOM schema-v1 contracts remain unchanged.
HMAC remains shared-secret provenance, not hardware attestation or a
public-key publisher signature.


## Host-agent release provenance deployment

The managed host-agent factory now supports an explicit release-provenance
gate. Configure all four variables in the root/service-managed environment to
enable it:

- `YUKI_HOST_INVENTORY_RELEASE_DIRECTORY`: absolute, service-owned directory
  containing immutable per-execution release records.
- `YUKI_HOST_INVENTORY_RELEASE_KEY_FILE`: absolute private HMAC key file,
  owned by the host inventory service UID and inaccessible to group/others.
- `YUKI_APPLICATION_VERSION`: trusted deployed application version.
- `YUKI_BUILD_ID`: trusted immutable build identifier.

Partial configuration is rejected at startup. If none are configured, the
legacy SBOM/runtime collector remains available for staged rollout and must
not be described as application-release-provenance verified.

`ReadOnlyApplicationReleaseProvenanceStore` keys records by the full immutable
image reference, task, worker, execution spec and daemon container ID. It opens
records relative to a pinned directory descriptor with no-follow semantics,
checks service ownership, private file mode, regular-file type, single-link
identity and the 32 KiB ceiling, and rejects duplicate JSON keys. The
`publish(record, container_id=..., hmac_key=...)` method verifies the HMAC
and inventory before atomically creating a mode-0600 file with exclusive
creation; an existing execution key cannot be overwritten. The publisher must
run as the configured service UID and use the same protected key custody as
the verifier.

The trusted launcher/orchestrator must publish the record for the exact
execution after it has the daemon-assigned container ID and the trusted
measured inventory, but before requesting authenticated host inventory.
The trusted launcher can compose this sequence with
`build_application_release_record_publisher(...)`: it invokes the host-owned
inventory collector for the exact execution, canonicalizes the measured maps,
creates the signed record and publishes it through the store. This callback
must run after Docker assigns the container ID and before requesting the
host-agent inventory response. The agent then independently measures again
and rejects any drift. For specialized publishers, the lower-level
`create_application_release_provenance(...)` and store `publish(...)` APIs
remain available. The image/SBOM build pipeline supplies the expected application
version, build ID, immutable image reference and inventory; the trusted
orchestrator supplies task, worker, spec and daemon container identity.
Task/container environment and workspace data are never record sources.
A missing or mismatched record blocks host-agent inventory collection.

The agent-side factory composes this gate after the existing digest-bound SBOM
and live observer parity check. The IPC response and SBOM schema-v1 formats
are unchanged. HMAC remains shared-secret provenance, not hardware attestation.


## Docker started-event release-record handoff

`core.docker_release_provenance_lifecycle.build_docker_release_record_lifecycle_callback(...)`
provides the trusted lifecycle adapter for the launcher. Compose it around the
existing executor lifecycle callback, using the same operator-owned
`DockerEngineAPI` instance used by the supervisor and the host-owned
`build_application_release_record_publisher(...)` callback. The adapter
resolves the generated Yuki container name through the GET-only Docker inspect
API, verifies the daemon-assigned 64-hex container ID, running state, exact
immutable image reference, and task/worker/spec ownership labels, then
publishes the signed per-execution record. Only after publication succeeds
does it forward `task.sandbox.started` to the downstream lifecycle callback.
Publisher or identity failures propagate and prevent downstream started-event
handling.

This adapter is a composition primitive; deployments must explicitly install
it in the trusted launcher that calls `DockerSandboxExecutor.execute(...)`.
The repository's current change does not claim that every deployment entry
point has been configured to use it. The host-agent's independent inventory
measurement and release-record verification remain mandatory. The adapter
does not use container environment variables, task workspace files, or
task-controlled paths as provenance inputs.

Regression coverage verifies publication ordering, exact container/task/
worker/spec/image binding, invalid container-ID rejection, and fail-closed
behavior when publication fails. Tests are committed but have not been
executed in this environment.


### Validated supervisor integration (preferred production path)

The production handoff belongs on
`DockerEngineSupervisorCollector(release_record_publisher=...)`, not on the
executor's early `task.sandbox.started` callback. The executor emits that
event immediately after spawning the Docker CLI; daemon container creation may
still be in progress. The supervisor already performs bounded inspect retries,
validates the daemon-assigned container ID, live state, Yuki task/worker/spec
and owner labels, immutable image object/repository digest, and configured
resource limits. It then invokes the injected publisher with
`(task_id, worker_id, container_id, profile.image, spec.spec_id)` immediately
before invoking the host evidence builder. A publication exception propagates
and prevents host evidence measurement.

The operator-owned host composition must inject
`build_application_release_record_publisher(...)` into that constructor when
release provenance is enabled. The host agent independently verifies the
same execution-scoped record after measuring its inventory. Keep the
publisher's inventory collector, protected store, HMAC key, application
version, and build ID in trusted host configuration. Do not source any of
these from the task, container, or workspace.

`core.docker_release_provenance_lifecycle` remains a lower-level lifecycle
adapter for specialized integrations and unit tests; it is not the preferred
production integration because it performs an immediate inspect at the early
executor event and does not inherit the supervisor's bounded daemon-start
retry or full image-object/resource-policy validation. Production deployments
should inject the publisher into `DockerEngineSupervisorCollector`.


### Operator-owned supervisor composition factory

Use `core.docker_engine_supervisor_factory.build_docker_engine_supervisor_collector(...)`
for host-side composition. Release provenance is required by default: the
factory loads `build_release_record_publisher()` from the trusted
`core.host_runtime_inventory_sbom_factory` and injects it into
`DockerEngineSupervisorCollector`. This makes the default factory path
fail closed when the release store, signing key, SBOM store, live observer,
application version, or build ID is not configured correctly. Legacy mode
requires the explicit `require_release_provenance=False` argument and should
be reserved for an approved migration window.

The publisher factory reads only service-managed environment settings:
`YUKI_HOST_INVENTORY_SERVICE_UID`,
`YUKI_HOST_INVENTORY_SBOM_DIRECTORY`,
`YUKI_HOST_INVENTORY_SBOM_PUBLISHER_KEY_FILE`,
`YUKI_HOST_INVENTORY_RUNTIME_OBSERVER`,
`YUKI_HOST_INVENTORY_RELEASE_DIRECTORY`,
`YUKI_HOST_INVENTORY_RELEASE_KEY_FILE`,
`YUKI_APPLICATION_VERSION`, and `YUKI_BUILD_ID`.
The launcher must run with effective UID equal to the configured service UID;
the immutable store rejects publication by any other UID. Provision the same
service-owned SBOM/release stores and key files for the host agent and trusted
launcher, and restrict their systemd environment files to the operator.

Example composition (the API, owner resolver, and evidence builder remain
operator-provided trusted host dependencies):

```python
from core.docker_engine_supervisor_factory import (
    build_docker_engine_supervisor_collector,
)

supervisor_collector = build_docker_engine_supervisor_collector(
    api=docker_api,
    host_id_provider=host_id_provider,
    owner_hash_resolver=owner_hash_resolver,
    evidence_builder=host_evidence_builder,
)
```

Factory tests verify that release provenance is injected by default, missing
trusted configuration fails during publisher construction, and legacy mode
requires an explicit boolean opt-out. This factory is now available for
operator composition; deployment-specific service entry points must call it
rather than constructing `DockerEngineSupervisorCollector` directly.


The example environment file at
`deploy/systemd/host-runtime-inventory.env.example` now includes the four
release settings and documents the separate release HMAC key. Replace all
example values before deployment. The release directory must be provisioned
service-owned with mode 0700; the release key must be a service-owned regular
file with mode 0400 and at least 32 random bytes. The application version and
build ID must come from the trusted release pipeline, not from a task, worker,
container environment, or workspace file.

The Docker supervisor process must use the same effective service UID as the
release store and must independently satisfy the Docker socket ownership policy
in `DockerEngineAPI`. Do not solve a socket ownership mismatch by making the
Docker socket group/world writable or by weakening the client checks. If the
deployment cannot satisfy both boundaries, keep the provenance-required
supervisor disabled and resolve the host service identity/socket design first.

The repository currently exposes the composition factory but does not yet
identify a concrete production Docker supervisor service entry point that
constructs it. Operators must not infer that the factory is automatically
active merely because the host inventory agent is installed. Wiring the
factory into that trusted launcher entry point remains a deployment integration
task; direct construction of `DockerEngineSupervisorCollector` without the
publisher remains legacy-compatible but is not release-provenance enforced.
