# Sandbox Provider Benchmarks

This repository describes how sandboxes are measured and records observations. **Inspect the
method and observations behind every result.**

## Three layers

1. **Public scientific record:** benchmark definitions, configurations, commands, workload and
   trace identities, replay semantics, sampling, statistics, cost method, exclusions, provider
   settings and versions, capability declarations, raw observations, aggregates and their
   verifier. This lives across Sandbox Provider Spec (SPS), this repository, and Sandbox Provider
   Pricing.
2. **Reference tests:** generic CPU, memory, disk, filesystem and network scripts without
   BoxRouter integration, whose per-trial output [compares](#reproduce-a-result) with published results.
3. **Measurement and interpretation:** declared configurations, collected observations and
   reproducible aggregates.

## Repository layout

SPS (`sandbox-provider-spec`, Apache-2.0) defines sandbox capabilities. This repository defines
measurement methodology and records results. Sandbox Provider Pricing owns price sheets and the
`price()` calculation.

```text
schemas/                    JSON Schemas for methodology, run, measurement and aggregate records
methodology/                Versioned YAML benchmark definitions, in benchmark-page order
reference-tests/            Generic machine tests
runs/                       Run manifests (none until a release)
data/<run_id>/raw/           Raw observations (none until a release)
data/<run_id>/aggregates/    Derived results (none until a release)
traces/                     Normalized trace listings (none until a release)
src/sandbox_benchmark_verifier/             methodology validation and aggregate reproduction CLI
```

Each methodology document has a stable ID and version. Runs and observations cite the versions
they used so published results remain interpretable. Methodology releases are immutable: revise a
definition by adding a version, never by editing a released version. Run `sandbox-benchmark-verify validate` to
check schemas and references and `sandbox-benchmark-verify reproduce` to recompute supported aggregates.

Standalone machine reference tests are linked from their methodologies: [CPU](reference-tests/cpu/),
[memory](reference-tests/memory/), [disk](reference-tests/disk/),
[filesystem](reference-tests/filesystem/) and [package registry installs](reference-tests/network/).

## Standard image

Every benchmark starts its sandboxes from one container image, unless a benchmark states another. It
is pinned by digest in `sbs.common` (`environment.image.reference`), so a result always names the exact
bytes it ran on. Results measured under `sbs.common` 1.0.10 and earlier ran on the public version 0.2.3:

```text
ghcr.io/boxrouter/standard-image@sha256:1f77271b6d589cc77ebbc2ce00a9d33a866980b05a0b37eb234f66a1bd09bc61
```

Pull it, signed out, with:

```sh
docker pull ghcr.io/boxrouter/standard-image@sha256:1f77271b6d589cc77ebbc2ce00a9d33a866980b05a0b37eb234f66a1bd09bc61
```

The [registry page](https://github.com/orgs/boxrouter/packages/container/package/standard-image) lists
its tags. `sbs.common` 1.0.13 pins version 0.3.5, also public (1.0.12 pinned 0.3.3, which
was never activated):

```text
ghcr.io/boxrouter/standard-image@sha256:1cb7f85ec988a9dcf66c47f953f8e7984d88c987fec964aa327afafb63cc9a0b
```

A benchmark that modifies the image per trial says how in its `environment.image.modification`.
The digest changes only in a new `sbs.common` version, because a new image means every provider
prepares its image again.

## Reproduce a result

Rerun a machine benchmark in a sandbox of your choice and see whether your numbers agree with a
published result. You need Python 3.11 or newer, the sandbox's shell, and about ten minutes for CPU.

1. **Install the tool for the benchmark** in a fresh sandbox, as its README says: the
   [CPU](reference-tests/cpu/), [memory](reference-tests/memory/), [disk](reference-tests/disk/),
   [filesystem](reference-tests/filesystem/) or [package install](reference-tests/network/) test.
   Use a 2 vCPU / 4 GiB sandbox like the publication's.
2. **Run the script once per fresh sandbox.** One run is one trial. Each prints measurement records
   in the published unit, with the `test_id` and `variant` the publication uses. Append the output of
   every run to one file; take as many trials as the methodology asks (10 for CPU and memory, 40 for
   disk and filesystem).

   ```sh
   ./run.sh >> results.jsonl
   ```

3. **Compare.** Copy `results.jsonl` to a machine with this package installed, and name the publication,
   the provider as the publication lists it, and the test:

   ```sh
   sandbox-benchmark-verify compare results.jsonl --publication <id> --provider <id> --test cpu-7zip-1t
   ```

   It prints your median and its 95% interval, the published ones, and whether the intervals overlap.
   The exit code is 0 when they agree, 1 when they differ and 2 when the inputs cannot be compared.
   `--summary` reads the publication summary from a file or an https address instead of the public one.

The test ids are `cpu-7zip-1t`, `stream-triad`, `disk-seq-read-1m`, `disk-seq-write-1m`,
`disk-rand-read-4k`, `disk-rand-write-4k`, `hardlink` and `install` (with the variant `git-clone`,
`npm-ci` or `pip-install`). The statistics are the methodology's own: median by nearest rank, interval
by seeded bootstrap. For create, exec and file timings, which need a provider's SDK, see
[Time sandbox operations with your own SDK](docs/operation-timing.md); its records go to the same
`compare`.

## Licensing

| Paths | Licence |
| --- | --- |
| `LICENSE`, `src/`, `schemas/`, `reference-tests/`, verifier code | Apache-2.0 |
| `methodology/`, `runs/`, `data/`, `traces/` | CC BY 4.0, subject to upstream terms for included third-party material |
| `parameters/` | CC BY 4.0, subject to upstream terms for included third-party material |

See [LICENSE](LICENSE), [LICENSE-CC-BY-4.0](LICENSE-CC-BY-4.0) and [NOTICE](NOTICE).

## Python package

The `sandbox-benchmark-verifier` wheel ships the four schemas and every methodology YAML.

```python
from sandbox_benchmark_verifier import METHODOLOGY_COMMIT, load_methodology, load_schema

for entry in load_methodology():
    print(entry.path, entry.document["id"], entry.source_commit)
schema = load_schema("aggregate")
```

`MethodologyDocument.path` is repository relative; `document` contains the full parsed YAML,
including multi-document files. `source_commit` (also `METHODOLOGY_COMMIT`) is the immutable
commit containing that exact methodology snapshot. Build source links using that commit and
path. When methodology changes, update the snapshot commit before releasing a new wheel.

Every aggregate declares `precision: {decimal_places: 3}` for rounded values, or
`precision: {absolute_tolerance: 0.000001}`. Reproduction compares both the value and interval
bounds with that absolute precision in the declared unit, with zero relative tolerance.
Missing precision is rejected; there is no implicit tolerance.

Run manifests carry `publication.accounts: dedicated`, `publication.visibility: public`,
the source summary's SHA-256 and the methodology snapshot commit. `validate` also runs
value-free redaction scans over run/data records. Only schema-approved fields may be
exported; provider resource/account identifiers and restricted command/output text are rejected.

Identity exemptions affect only X20: page `run_id` and batch/source-run IDs must match
their respective complete grammars; workload/task/trace IDs must exactly match the generated,
source-cited `workload_sources` registry reviewed with the dataset. Other fields still receive
full entropy checks, and all other redaction checks always apply to exempt identities.

## Releases

Changes to packaged wheel sources trigger the next methodology GitHub release on merge to `main`.
Documentation, tests and CI changes alone do not create a release. The workflow checks the
released-methodology lock, REUSE and public-text boundary before tagging, then the existing tag
workflow builds the wheel and text bundle. Manual Prepare methodology version changes and manual
tag releases are also supported.
