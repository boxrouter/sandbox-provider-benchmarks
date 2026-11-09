# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Run and measurement schemas keep requested and observed placement explicit."""

import pytest
from jsonschema import Draft202012Validator, ValidationError

from sandbox_benchmark_verifier.catalog import load_schema


def _run(region: object) -> dict[str, object]:
    return {
        "run_id": "2026-11-01-a",
        "methodologies": {"sandbox.create.sequential_tti": "1.0.0"},
        "region": region,
        "machine_configs": [{"id": "2x4", "vcpu": 2, "memory_gib": 4}],
        "providers": [
            {
                "provider": "e2b",
                "sdk_version": "unknown",
                "api_version": "unknown",
                "settings": {},
            }
        ],
        "price_sheet_version": "0.4.0",
        "started_at": "2026-11-01T09:00:00Z",
        "finished_at": "2026-11-01T09:30:00Z",
        "account_warmth": {"state": "unknown"},
        "publication": {
            "accounts": "dedicated",
            "visibility": "public",
            "summary_sha256": "a" * 64,
            "methodology_commit": "b" * 40,
        },
    }


def _measurement(region: object) -> dict[str, object]:
    return {
        "provider": "e2b",
        "methodology": {"id": "sandbox.create.sequential_tti", "version": "1.0.0"},
        "run_id": "2026-11-01-a",
        "trial": 1,
        "timestamp": "2026-11-01T09:12:03Z",
        "region": region,
        "machine_config": "2x4",
        "measurement": "time_to_interactive",
        "value": 412.7,
        "unit": "ms",
        "success": True,
        "exclusion_reason": None,
    }


def test_region_schemas_use_versioned_structured_placement() -> None:
    run = load_schema("run")
    measurement = load_schema("measurement")
    assert run["$id"].endswith("/0.2/run.schema.json")
    assert measurement["$id"].endswith("/0.2/measurement.schema.json")

    run_validator = Draft202012Validator(run)
    measurement_validator = Draft202012Validator(measurement)
    requested = {"value": "us", "source": "recorded"}
    unknown_request = {
        "value": None,
        "source": "unknown",
        "reason": "The run did not record a requested region",
    }
    unknown_observation = {
        "value": None,
        "source": "unknown",
        "reason": "Core does not report the sandbox's region",
    }

    run_validator.validate(_run({"requested": requested}))
    run_validator.validate(_run({"requested": unknown_request}))
    with pytest.raises(ValidationError):
        run_validator.validate(_run("unknown"))

    measurement_validator.validate(
        _measurement({"requested": requested, "observed": unknown_observation})
    )
    measurement_validator.validate(
        _measurement(
            {
                "requested": unknown_request,
                "observed": {"value": "us-east-1", "source": "measured"},
            }
        )
    )
    with pytest.raises(ValidationError):
        measurement_validator.validate(
            _measurement(
                {
                    "requested": requested,
                    "observed": {"value": None, "source": "unknown"},
                }
            )
        )
    with pytest.raises(ValidationError):
        measurement_validator.validate(_measurement("unknown"))
