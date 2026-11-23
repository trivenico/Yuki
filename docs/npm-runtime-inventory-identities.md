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
