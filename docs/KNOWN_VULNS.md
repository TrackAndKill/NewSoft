# Known vulnerabilities

Phase 6 `npm audit` status for `dashboard`:

- Next.js patched from `15.1.12` to `15.5.15` while keeping React on `18.3.1`.
- `postcss` is pinned via npm `overrides` to `8.5.10` to clear GHSA-qx2v-qp2m-jg93 from Next's transitive dependency tree.
- `npm audit --json` currently reports `0` vulnerabilities.

No React 19 migration was performed.
