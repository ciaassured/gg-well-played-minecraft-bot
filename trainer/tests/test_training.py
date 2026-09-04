from pathlib import Path

from yrush_trainer.run_directory import RunDirectory
from yrush_trainer.training import evaluation_checkpoints


def test_bounded_evaluation_compares_only_untrained_and_latest(tmp_path: Path) -> None:
    run = RunDirectory.create(tmp_path, {"updates": 4}, "bounded-evaluation")
    for version in range(1, 5):
        run.candidate_checkpoint(version).touch()

    assert evaluation_checkpoints(run) == (
        run.untrained_checkpoint,
        run.latest_checkpoint,
    )
