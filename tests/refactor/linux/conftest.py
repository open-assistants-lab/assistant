"""Actual Docker execution is opt-in, never implied by a normal unit suite."""

from functools import partial

import pytest

from tests.refactor.linux.probe_helpers import run_probe


def pytest_addoption(parser):
    parser.addoption("--linux-probe", action="store_true", default=False,
                     help="explicitly run isolated cached-image Docker preflight")
    parser.addoption("--docker-host", default="", help="explicit local Docker Unix socket")


@pytest.fixture(autouse=True)
def authorized_linux_probe(request):
    if not request.config.getoption("--linux-probe"):
        pytest.skip("Linux Docker probe requires explicit execution authorization")


@pytest.fixture
def probe(request):
    return partial(run_probe, docker_host=request.config.getoption("--docker-host"))
