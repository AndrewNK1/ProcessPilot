# Evaluation records

Copies of files that must be provably fixed before any evaluation run. The
originals live in `outputs/eval/<campaign>/` (gitignored); these copies are
byte-identical (`.gitattributes` turns off line-ending conversion).

## eval-2026-11-v1 (draft, 2026-11-18)

Frozen mining run `20261118T011500Z` (preset `bpi2019`, mined at commit `24303fd`).

| File | SHA-256 |
|---|---|
| `answer_key.json` | `3733312a1dd5fd19d866bd094efe9644daed8873c3cd3427c16c1fca1f7ddb28` |
| `answer_key.csv` | `12473a950bfd909ad524d65cc03dcb1ff53df31425df15be1f459e108105ae0c` |
| `manifest.json` (draft) | `c74d16b6233b79e12cee514400941823064db23c5391a4a01051978f2c0680ca` |

The manifest's `answer_key.sha256` is the hash of `answer_key.json` above.
Sealing (after Phases 5-6) writes a new manifest; the draft stays here as committed.
