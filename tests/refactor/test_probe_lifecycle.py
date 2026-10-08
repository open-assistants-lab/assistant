"""Mocked transport refusal checks only; never claimed as Linux isolation proof."""

import json
import subprocess

import pytest


class DockerDouble:
    def __init__(self, mode="normal"):
        self.mode = mode
        self.commands = []
        self.exists = False
        self.cid = "c" * 64
        self.name = ""
        self.image = "sha256:" + "a" * 64

    def __call__(self, command, **kwargs):
        self.commands.append(command)
        op = command[3]
        rc = 0
        out = ""
        if op == "image":
            out = json.dumps([{"Id": self.image, "Os": "linux", "Architecture": "arm64"}])
        elif op == "create":
            self.name = command[command.index("--name") + 1]
            self.exists = True
            out = self.cid if self.mode != "missing_cid" else ""
        elif op == "start":
            out = "synthetic result"
        elif op == "ps":
            if self.mode == "api_error":
                rc = 1
            elif self.exists:
                out = ("d" * 64 if self.mode == "wrong_exact_cid" else self.cid) + "\n"
        elif op == "inspect":
            label = self.name if self.mode != "wrong_label" else "unrelated"
            out = json.dumps(
                [
                    {
                        "Id": command[4] if self.mode == "wrong_exact_cid" else self.cid,
                        "Image": self.image,
                        "Config": {"Labels": {"assistant.authority.probe": label}},
                    }
                ]
            )
        elif op == "rm":
            if self.mode != "removal_uncertain":
                self.exists = False
        return subprocess.CompletedProcess(command, rc, out, "API unavailable" if rc else "")


def execute(monkeypatch, mode="normal"):
    from tests.refactor.linux import probe_helpers

    fake = DockerDouble(mode)
    monkeypatch.setattr(probe_helpers.subprocess, "run", fake)
    return fake, lambda: probe_helpers.run_recorded_probe(
        "fixed", docker_host="unix:///synthetic/socket", image=fake.image
    )


def test_cleanup_does_not_remove_unlabelled_container(monkeypatch):
    fake, run = execute(monkeypatch, "wrong_label")
    with pytest.raises(RuntimeError):
        run()
    assert not any(c[3] == "rm" for c in fake.commands)


def test_cleanup_rejects_another_full_id_even_with_matching_label(monkeypatch):
    fake, run = execute(monkeypatch, "wrong_exact_cid")
    with pytest.raises(RuntimeError):
        run()
    assert not any(c[3] == "rm" for c in fake.commands)


def test_cleanup_api_error_is_uncertain_not_absent(monkeypatch):
    fake, run = execute(monkeypatch, "api_error")
    with pytest.raises(RuntimeError):
        run()
    assert not any(c[3] == "rm" for c in fake.commands)


def test_cleanup_requires_confirmed_removal(monkeypatch):
    _, run = execute(monkeypatch, "removal_uncertain")
    with pytest.raises(RuntimeError):
        run()


def test_missing_cid_requires_owned_name_inventory(monkeypatch):
    fake, run = execute(monkeypatch, "missing_cid")
    with pytest.raises(RuntimeError):
        run()
    assert any(c[3] == "rm" for c in fake.commands)
    assert not fake.exists


def test_recorded_facts_and_legacy_return_are_preserved(monkeypatch):
    fake, run = execute(monkeypatch)
    process, facts = run()
    assert process.stdout == "synthetic result"
    assert facts["image_id"] == fake.image and facts["cleanup_confirmed"] is True
    from tests.refactor.linux.probe_helpers import run_probe

    assert isinstance(
        run_probe("fixed", docker_host="unix:///synthetic/socket", image=fake.image),
        subprocess.CompletedProcess,
    )


def test_mutable_image_is_refused_before_any_docker_call(monkeypatch):
    fake, _ = execute(monkeypatch)
    from tests.refactor.linux.probe_helpers import run_recorded_probe

    with pytest.raises(ValueError):
        run_recorded_probe("fixed", docker_host="unix:///synthetic/socket", image="latest")
    assert fake.commands == []
