from __future__ import annotations

import pytest

from worker import cloud_llm
from worker.errors import CallerNotAllowed, ProspectDataRefused


@pytest.mark.parametrize("flag", [True, None, 0, "", "False", 1])
def test_refuses_anything_but_explicit_false(flag):
    with pytest.raises(ProspectDataRefused):
        cloud_llm.complete(contains_prospect_data=flag, system="s", prompt="p", prompt_version="v1")


def test_flag_is_required():
    with pytest.raises(TypeError):
        cloud_llm.complete(system="s", prompt="p", prompt_version="v1")  # type: ignore[call-arg]


def test_refuses_callers_off_the_allowlist():
    assert __name__ not in cloud_llm.ALLOWED_CALLERS
    with pytest.raises(CallerNotAllowed):
        cloud_llm.complete(
            contains_prospect_data=False, system="s", prompt="p", prompt_version="v1"
        )


def test_allowlist_is_empty_in_phase_0():
    assert cloud_llm.ALLOWED_CALLERS == frozenset()
