from jump_trainer.cli import (
    FINAL_ACCEPTANCE_FAILURE_EXIT_CODE,
    _final_acceptance_failed,
)


def test_only_failed_final_acceptance_requests_a_nonzero_exit() -> None:
    assert FINAL_ACCEPTANCE_FAILURE_EXIT_CODE == 3
    assert not _final_acceptance_failed({})
    assert not _final_acceptance_failed({"acceptance": {"passed": True}})
    assert _final_acceptance_failed({"acceptance": {"passed": False}})
