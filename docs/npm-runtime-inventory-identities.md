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
`/usr/lib/node_modules` and `/usr/local/lib/node_modules`. It does not yet
claim coverage of application-local dependency trees, npm lockfile v2/v3
resolution, pnpm stores, Yarn Plug'n'Play, or workspace dependencies. Those
sources require a separately verified application-root identity and proof that
the inspected paths are image-owned rather than task-controlled workspace
mounts. Until then, the SBOM publisher must describe exactly the supported
global inventory scope; missing or divergent entries fail closed.

The identity is a filesystem install location, not a claim that the package
contents are cryptographically authenticated by this observer. Image digest
binding and the independently publisher-authenticated SBOM remain required.
