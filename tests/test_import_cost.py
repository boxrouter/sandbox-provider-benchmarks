# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Importing the verifier parses no YAML: a fresh process that only imports it (the runner's
reconnect rounds start one per round) must not pay for the methodology's documents."""

import subprocess
import sys

IMPORT_EVERY_MODULE = """
import importlib, pkgutil, yaml

def refuse(*args, **kwargs):
    raise AssertionError("YAML parsed at import")

yaml.load = yaml.load_all = yaml.compose = yaml.compose_all = yaml.parse = refuse
import sandbox_benchmark_verifier as package

for module in pkgutil.iter_modules(package.__path__):
    importlib.import_module(f"{package.__name__}.{module.name}")
"""


def test_importing_every_verifier_module_parses_no_yaml() -> None:
    done = subprocess.run(
        [sys.executable, "-c", IMPORT_EVERY_MODULE], capture_output=True, text=True, check=False
    )
    assert done.returncode == 0, done.stderr
