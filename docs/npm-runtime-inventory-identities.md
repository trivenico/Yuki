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

This is a read-only inventory of global npm trees under
`/usr/lib/node_modules` and `/usr/local/lib/node_modules`. A separate bounded
parser now understands npm package-lock v2/v3 package maps and emits the same
path-qualified identity shape under an `app/` prefix. It is not yet wired into
the live observer: doing so requires a separately verified application-root
identity and proof that the lockfile and installed dependency paths are
image-owned rather than task-controlled workspace mounts. Link/workspace
entries are rejected by the parser. pnpm stores and Yarn Plug'n'Play remain
unsupported. Until application-root provenance is enforced, the SBOM
publisher must describe exactly the supported global inventory scope; missing
or divergent entries fail closed.

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

This is a validation primitive, not yet an end-to-end application-root
discovery claim. The next integration step must read mountinfo from the same
PID directory already pinned while matching the exact container cgroup,
discover candidate package-lock roots through the container root descriptor,
and call this validator before reading a lockfile. The lockfile parser remains
unwired into live inventory until that complete chain is in place.
