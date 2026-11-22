# pnpm 12 CAS publication and in-place repair race

A deterministic, two-process filesystem model of an empty package-manifest read during concurrent shared-store installation.

**This is a syscall-interleaving model, not an execution of pnpm or a syscall trace of the original failure.** The original observed pnpm 12.17.0 failure was `ERR_PNPM_CMD_SHIM_PARSE_MANIFEST`, `EOF while parsing a value at line 1 column 0`, during two cold installs into separate root-local virtual stores sharing one content-addressed store. One install completed; the other reported copying packages and failed reading a dependency's `package.json`.

## Run

```sh
python3 repro.py
```

No dependencies, downloads, package-manager execution, compiler, Nix, credentials, services, or elevated privileges. Two short-lived child processes use only synthetic files under an owned temporary directory in `tmp/`; those files are removed on exit. All waits are bounded.

## Model

The relevant pinned upstream source is pnpm **12.17.0**, commit [`896dc10265faf931e64d8fa94006f4ea1501ce99`](https://github.com/pnpm/pnpm/tree/896dc10265faf931e64d8fa94006f4ea1501ce99):

- [`ensure_file.rs`](https://github.com/pnpm/pnpm/blob/896dc10265faf931e64d8fa94006f4ea1501ce99/pnpm/crates/fs/src/ensure_file.rs): `ensure()` creates the final CAS path before `write_all`; its mutex is explicitly process-local. `AlreadyExists` and mismatching size/content can enter `Repair::InPlace`.
- [`overwrite.rs`](https://github.com/pnpm/pnpm/blob/896dc10265faf931e64d8fa94006f4ea1501ce99/pnpm/crates/fs/src/ensure_file/overwrite.rs): identity is checked, then `set_len(0)`, rewind, and copy rewrite that same inode.
- [`cas_file.rs`](https://github.com/pnpm/pnpm/blob/896dc10265faf931e64d8fa94006f4ea1501ce99/pnpm/crates/store-dir/src/cas_file.rs): the buffered CAS writer uses `ensure_cas_file()`.

The deterministic schedule is:

1. The creator publishes the final empty CAS inode before writing its bytes.
2. A second process observes the size mismatch and selects in-place repair.
3. The creator finishes writing valid bytes and can begin importing.
4. The repair process truncates the same inode after that completion.
5. The importer copies the now-empty file and cannot parse the resulting manifest.
6. Repair finishes; the CAS file becomes valid again, but the imported copy stays empty.

The second arm writes a complete private file before atomically publishing its name. The competing process never sees an incomplete final inode, so this cold-creation schedule does not select repair. The model uses an atomic hard-link publication of the complete private inode; an upstream fix must choose the appropriate atomic publication primitive and preserve existing-entry behavior.

## Exercised result

The original model ran on macOS arm64, Darwin 27, Python **3.14.10**, in about one second:

| Arm | Imported bytes | Manifest parses | CAS valid after settlement |
| --- | ---: | --- | --- |
| Final-path-first creation plus in-place repair | 0 | No | Yes |
| Complete private file before atomic publication | 44 | Yes | Yes |

The public copy changes only the temporary-directory location to this repository's ignored `tmp/`.

## Boundaries

- This establishes that the modeled filesystem interleaving is possible; it does **not** establish that the original pnpm process followed this exact schedule.
- No pnpm failure-rate measurement or passing patched pnpm build is claimed.
- The model does not cover streamed extraction, every importer, or a genuine pre-existing corrupt CAS blob.
- In-place repair intentionally preserves hardlink identity for [pnpm#3445](https://github.com/pnpm/pnpm/issues/3445). A fix must retain that contract rather than silently replacing every healthy existing inode or serializing the test.
- Upstream main `f923105d8547bf8259897963f12cb95512557ea1` still has the final-path-first `ensure()` branch. A fixed released version has not been identified.
