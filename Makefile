.PHONY: check format lint lint-reuse lint-text lint-ruff lint-mypy lint-pyright lint-pylint public-text-check \
	release-check release-text release-validate release-pytest
UV := $(abspath .venv/bin/uv)
# pytest workers: half the cores up to 8 (the machine is shared); set PYTEST_WORKERS to change.
PYTEST_WORKERS ?= $(shell cores=$$(nproc); n=$$((cores / 2)); echo $$((n < 1 ? 1 : n > 8 ? 8 : n)))

LINT_STEPS := lint-reuse lint-text lint-ruff lint-mypy lint-pyright lint-pylint
# The lint steps run side by side (about 35 s instead of 90 s); -O prints each step's output when it finishes, and
# lint fails when any step fails.
lint:
	@$(MAKE) --no-print-directory -O -j$(words $(LINT_STEPS)) $(LINT_STEPS)

lint-reuse:
	bash scripts/check_reuse.sh

lint-text:
	$(UV) run --locked python scripts/scan-public-text-blocklist.py

lint-ruff:
	$(UV) run --locked ruff format --check .
	$(UV) run --locked ruff check .

# Each reference parser is a module named `parse`, so mypy checks them one at a time.
lint-mypy:
	$(UV) run --locked mypy
	@for parser in reference-tests/*/parse.py; do $(UV) run --locked mypy "$$parser" || exit 1; done

lint-pyright:
	$(UV) run --locked pyright

lint-pylint:
	$(UV) run --locked pylint src/sandbox_benchmark_verifier

check:
	@$(call stage,lint,$(MAKE) --no-print-directory lint)
	@$(call stage,procedures,$(UV) run --locked python -m sandbox_benchmark_verifier.procedures)
	@$(call stage,pytest,$(UV) run --locked pytest -n $(PYTEST_WORKERS))
	@$(call stage,text,$(UV) run --locked sandbox-benchmark-verify text)
	@$(call stage,validate,$(UV) run --locked sandbox-benchmark-verify validate)
	@$(call stage,lock,$(UV) run --locked python -m sandbox_benchmark_verifier.lock check)
	@$(call stage,reproduce,$(UV) run --locked sandbox-benchmark-verify reproduce)
	@$(call stage,build,task_build_dir=$$(mktemp -d); trap 'rm -rf "$$task_build_dir"' EXIT HUP INT TERM; \
		$(UV) build --out-dir "$$task_build_dir" && \
		$(UV) run --locked python tests/check_distribution.py "$$task_build_dir" && \
		$(UV) run --locked python scripts/scan-public-text-blocklist.py "$$task_build_dir"/*)

# Run a stage and print its wall time: $(call stage,<name>,<command>).
stage = start=$$(date +%s); $(2); status=$$?; echo "stage $(1): $$(( $$(date +%s) - start )) s"; exit $$status

# Optional release-candidate validation. This target runs the full pytest suite; it is not a merge gate.
# CI runs `make check` on main, and the tag-release workflow performs its own lock, text and distribution checks.
# The three long stages run side by side; -O prints each one's output when it finishes.
release-check:
	@$(call stage,lint,$(MAKE) --no-print-directory lint)
	@$(call stage,procedures,$(UV) run --locked python -m sandbox_benchmark_verifier.procedures)
	@$(call stage,lock,$(UV) run --locked python -m sandbox_benchmark_verifier.lock check)
	@$(MAKE) --no-print-directory -O -j3 release-text release-validate release-pytest

release-text:
	@$(call stage,text,$(UV) run --locked sandbox-benchmark-verify text)

release-validate:
	@$(call stage,validate,$(UV) run --locked sandbox-benchmark-verify validate)

release-pytest:
	@$(call stage,pytest,$(UV) run --locked pytest -q -n $(PYTEST_WORKERS))

format:
	$(UV) run --locked ruff format .
	$(UV) run --locked ruff check --fix .
	$(UV) run --locked ruff format .
