# Package-registry install reference test

This script measures one selected cold-cache install workload from the
[network methodology](../../methodology/04-machine-performance/network.yaml). It times the full
FastAPI clone and pinned checkout, `npm ci` from axios lockfiles, or `pip install` of the pinned
FastAPI test requirements. Each run measures only the selected command after its inputs are ready;
the result combines public-network transfer and local installation work.

Requires Git and Python 3; `npm` and Node.js for `npm_ci`; and `uv` 0.13.4 for `pip_install` (the
lock exporter version pinned in [pyproject.toml](../../pyproject.toml)). Source pins and
input digests come from the methodology: FastAPI commit `69ddc2badf9da1123bba4d360225ae6b085985f6`,
axios commit `e6b79d9cef796ca3a903bb83e51d4752b019fd67`. The repositories and package files are
downloaded at run time and are not copied into this repository. Caches and working files are
isolated under a temporary directory. Run one workload with:

```sh
./run.sh git_clone
./run.sh npm_ci
./run.sh pip_install
```

Each command prints one methodology `install_runtime_ms` record as JSON Lines (test `install`, variant
`git-clone`, `npm-ci` or `pip-install`). Captured inputs to the parser
from one devbox run of each workload are in `tests/fixtures/reference-tests/network/`.
