# Vendored bpmn-js (read-only viewer)

| File | From |
| --- | --- |
| `bpmn-navigated-viewer.production.min.js` | `bpmn-js@18.31.0`, `dist/` |
| `bpmn-js.css`, `diagram-js.css` | `bpmn-js@18.31.0`, `dist/assets/` |
| `LICENSE-bpmn-js` | `bpmn-js@18.31.0`, `LICENSE` |

Taken from the npm tarball
`https://registry.npmjs.org/bpmn-js/-/bpmn-js-18.31.0.tgz` (released
2026-10-01), whose SHA-512 matched the registry's published integrity
`sha512-tGc6spNXC81858FXhk0NI9+CjfsPYDmKWSyiq0u2Rhk9J8l+DJtzZK1CYj4fqcek026PQDEwwUQwsr2Opfec/w==`.
The files load nothing from the network.

Vendored rather than loaded from a CDN so the app works offline (only the LLM
call may go online). The bpmn.io license requires the bpmn.io watermark in
rendered diagrams to stay visible and unchanged.

To upgrade: download the new tarball, check its integrity against
`https://registry.npmjs.org/bpmn-js/<version>`, replace these four files and
update this table.
