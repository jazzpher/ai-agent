"""Docker sandbox runs as the host user (no Docker needed)."""
import inspect
import os
from unittest import mock

import pytest

import sandbox_session


def _docker_class():
    for _, obj in inspect.getmembers(sandbox_session, inspect.isclass):
        if obj.__module__ == "sandbox_session" and hasattr(obj, "_init_docker"):
            return obj
    raise AssertionError("no class with _init_docker")


@pytest.mark.skipif(not hasattr(os, "getuid"), reason="POSIX only")
def test_container_user_args_use_host_ids():
    args = sandbox_session._container_user_args()
    assert args == ["--user", f"{os.getuid()}:{os.getgid()}", "-e", "HOME=/tmp"]


def test_no_user_args_without_getuid():
    with mock.patch.object(sandbox_session.os, "getuid", None, create=True):
        assert sandbox_session._container_user_args() == []


@pytest.mark.skipif(not hasattr(os, "getuid"), reason="POSIX only")
def test_docker_run_passes_user_before_image():
    cls = _docker_class()
    sb = cls.__new__(cls)
    sb._container_name = "ira-test-sbx"
    sb._container_id = None
    calls = []

    def fake_run(cmd, *a, **k):
        calls.append(cmd)
        return mock.Mock(returncode=0, stdout="abc123", stderr="")

    with mock.patch.object(cls, "_check_docker", return_value=True), \
         mock.patch.object(sandbox_session.subprocess, "run", side_effect=fake_run):
        assert sb._init_docker() is True
    run = next(c for c in calls if c[:3] == ["docker", "run", "-d"])
    i = run.index("--user")
    assert run[i + 1] == f"{os.getuid()}:{os.getgid()}"
    assert i < run.index("python:3.12-alpine")
    assert "--cap-drop" in run and "--read-only" in run
