# Add a benchmark definition

Use `sandbox-benchmark-verify scaffold` to create a versioned methodology definition and its
digest-bound parameters. The scaffold checks for an existing identity or name before creating
files. Reuse an existing definition when it describes the same measurement.

Run `sandbox-benchmark-verify scaffold --id <id> --name <name> --image <OCI digest>
--command-line <command>` from this checkout. It checks for an existing identity or name
before creating files. Reuse an existing definition when it describes the same measurement.

The scaffold writes one versioned YAML definition under `methodology/<id>/<version>.yaml`
and its digest-bound parameters under `parameters/<id>/<version>/`. Names and metric
references derive from that definition; do not register them separately.

For a single image dimension, pass `--measurement startup --sweep <JSON file>`. The sweep
declares `parameter`, display `name`, `unit`, and `values`; each value supplies its number
and a digest-pinned image. All values belong to the same definition. Generated workload
records are parameter instances, not separate definitions.

For a benchmark that uses an existing procedure shape, run `sandbox-benchmark-verify scaffold
--id <id> --shape <shape.json> --draft <draft.yaml> --category <methodology folder>`. The shape
follows `schemas/shape.schema.json`; the draft holds what only you can
write (`schemas/scaffold-draft.schema.json`): the name, notes, metrics, interpretation, validity
checks, source, the failure owners' words and the workloads, whose data keys equal the shape's
`reads`. It may also replace a step's derived `name` or `action`, keyed by shape step id.

The scaffold derives the rest from the shape and `contracts/operations.yaml`: one step per shape
step with its operation, timer draft and timeout, the stages, the sandboxes per trial, 40 trials
with 10 in parallel and 20 to rank, the 2 vCPU / 4 GiB size, the common image, the required
capabilities and the independence answers. It writes the new file
`methodology/<category>/<id>.yaml` and `parameters/<id>/1.0.0/workloads.json`, prints the
methodology path and adds no names row; graduation adds it.

Run `sandbox-benchmark-verify validate` and `sandbox-benchmark-verify text`, then `make check`.
CI generates a temporary definition with the scaffold and checks the current schema,
parameter digests, names and conventions. The generated samples are never added to the
methodology collection. A changed definition is edited in place and needs new evidence.
