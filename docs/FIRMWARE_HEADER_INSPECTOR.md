# Read-only firmware header inspection

Yuki's optional C11 firmware-header inspector recognizes three container
headers from caller-provided immutable bytes:

- U-Boot legacy image header (64-byte header; big-endian payload length).
- Flattened Image Tree (FIT/FDT) header (40-byte fixed header; big-endian total size).
- Broadcom TRX header (28-byte header; little-endian total size).

The Python API is `defense.firmware.inspect_firmware_header(data)`. It accepts
bytes-like input up to 512 MiB and returns a `FirmwareHeaderInfo`, or `None`
for an unknown format. Truncated headers and lengths that exceed the supplied
buffer raise `ValueError`. It never opens paths, extracts payloads, accesses a
router, writes firmware, flashes devices or executes content.

The Python implementation is authoritative. The C ABI accepts only a pointer,
length and output struct; it has no filesystem, network or device APIs. The
native shared library is not auto-discovered. An operator may explicitly load
a deployment-built library with `load_native_library(absolute_path)`. Loading
a shared library executes native code: only load artifacts built by a trusted
CI pipeline and verified by deployment controls, never user-uploaded binaries.

## C build and tests

```sh
make -C native/firmware_header test
make -C native/firmware_header sanitize
```

The libFuzzer entrypoint is `native/firmware_header/fuzz_firmware_header.c`.
Build it with a Clang toolchain supporting libFuzzer and ASan/UBSan.

The C implementation remains optional until sanitizer, fuzz, Python contract,
cross-platform and parity checks pass. Header recognition is not a firmware
integrity or authenticity verdict; CRC/signature validation and full container
parsing are deliberately outside this component.
