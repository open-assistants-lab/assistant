"""Pinned provenance claims must refer to the same approved upstream source URL."""

import pytest

from tests.refactor.test_seccomp_review import provenance, validate


@pytest.mark.parametrize("url", [
    "https://github.com/moby/moby/blob/main/vendor/github.com/moby/profiles/seccomp/default.json",
    "https://github.com/moby/moby/blob/" + "c" * 40 + "/vendor/github.com/moby/profiles/seccomp/default.json",
    "https://github.com/unrelated/project/blob/" + "a" * 40 + "/default.json",
    "https://github.com.evil.test/moby/moby/blob/" + "a" * 40 + "/default.json",
    "https://secret@github.com/moby/moby/blob/" + "a" * 40 + "/default.json",
    "http://github.com/moby/moby/blob/" + "a" * 40 + "/default.json",
    "https://github.com/moby/moby/blob/" + "a" * 40 + "/vendor/github.com/moby/profiles/seccomp/default.json?token=not-public",
    "https://github.com/moby/moby/blob/" + "a" * 40 + "/vendor/github.com/moby/profiles/seccomp/default.json#other",
])
def test_source_link_must_match_pinned_official_baseline(url):
    record = provenance()
    record["source_url"] = url
    with pytest.raises(ValueError):
        validate(record)


@pytest.mark.parametrize("repository,path", [
    ("moby", "vendor/github.com/moby/profiles/seccomp/default.json"),
    ("profiles", "seccomp/default.json"),
])
def test_known_official_pinned_source_links_accepted(repository, path):
    record = provenance()
    record["source_url"] = f"https://github.com/moby/{repository}/blob/{record['source_revision']}/{path}"
    validate(record)
