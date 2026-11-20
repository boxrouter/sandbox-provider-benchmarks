# Sandbox Provider Benchmarks

This repository defines how sandbox providers are measured. **Inspect the method behind every result.**

It holds the public scientific record for sandbox benchmarks: the YAML benchmark definitions, sandbox configurations, commands, workload and trace identities, replay rules, sampling schedules, statistical aggregations, cost calculation methods, and exclusion criteria. It is written for developers, researchers, and sandbox providers who want to audit how results are produced, and for tools that validate benchmark definitions or render methodology documentation.

The benchmark runner reads these definitions, and no measurement rule is restated in runner code. The same files generate the public methodology page.

This repository works alongside two companion specifications: the [Sandbox Provider Spec](https://github.com/boxrouter/sandbox-provider-spec) (SPS) defines the standard sandbox API and capability guarantees, and [Sandbox Provider Pricing](https://github.com/boxrouter/sandbox-provider-pricing) defines provider price sheets and per-sandbox cost calculations.

## Layout

SPS (`sandbox-provider-spec`, Apache-2.0) defines sandbox capabilities. Sandbox Provider Pricing owns
price sheets and the `price()` calculation. This package defines measurement methodology.

```text
methodology/                YAML benchmark definitions, in benchmark-page order
parameters/<id>/            What each benchmark runs: run.json (its procedure), workloads, pinned assets
schemas/                    JSON Schemas for methodology, names, reader and presentation records
names/                      The names table every label on the page comes from
reader/                     The reader text of the methodology page
conventions/                Text conventions the methodology and reader text must follow
contracts/                  Contracts for the generated text and for timing
src/sandbox_benchmark_verifier/   Validation and the text bundle generator
```

Each methodology document has a stable ID and one current version, which changes in place.
`sandbox-benchmark-verify validate` checks schemas, references, parameters and names;
`sandbox-benchmark-verify text` generates the text bundle the website reads.

## Standard image

Every benchmark starts its sandboxes from one container image, unless a benchmark states another. It
is pinned by digest in `sbs.common` (`environment.image.reference`), so a result always names the exact
bytes it ran on. The image is public: pull that reference, signed out, with
`docker pull <reference>`. The [registry page](https://github.com/orgs/boxrouter/packages/container/package/standard-image)
lists its tags.

## Licensing

| Paths | Licence |
| --- | --- |
| `LICENSE`, `src/`, `schemas/`, verifier code | Apache-2.0 |
| `methodology/` | CC BY 4.0, subject to upstream terms for included third-party material |
| `parameters/` | CC BY 4.0, subject to upstream terms for included third-party material |

See [LICENSE](LICENSE), [LICENSE-CC-BY-4.0](LICENSE-CC-BY-4.0) and [NOTICE](NOTICE).

## Python package

The `sandbox-benchmark-verifier` wheel ships the schemas and every methodology YAML.

```python
from sandbox_benchmark_verifier import METHODOLOGY_COMMIT, load_methodology, load_schema

for entry in load_methodology():
    print(entry.path, entry.document["id"], entry.source_commit)
schema = load_schema("methodology")
```

`MethodologyDocument.path` is repository relative; `document` contains the full parsed YAML,
including multi-document files. `source_commit` (also `METHODOLOGY_COMMIT`) is the immutable
commit containing that exact methodology snapshot. Build source links using that commit and
path. When methodology changes, update the snapshot commit before releasing a new wheel.

## Releases

A change to the package version on `main` releases the wheel, the text bundle and their checksums
for outside consumers (the website's methodology text, the public mirror's tags). The workflow checks
the released-methodology lock, REUSE and the public-text boundary before tagging.
