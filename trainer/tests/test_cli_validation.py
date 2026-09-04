from pathlib import Path

from yrush_trainer.cli import _validate_stage
from yrush_trainer.config import TrainConfig
from yrush_trainer.run_directory import RunDirectory


def test_stage_accepts_recovered_server_restart_baseline(tmp_path: Path) -> None:
    run = RunDirectory.create(tmp_path, {"updates": 1}, "recovered-server")
    run.write_json(
        "metrics/summary.json",
        {
            "ppo_updates": [{"kl": 0.0, "entropy": 1.0}],
            "pool": {
                "action_distributions": [
                    [1, 1, 1],
                    [1, 1, 1],
                    [1, 1],
                    [1, 1],
                    [1, 1, 1, 1, 1],
                    [1, 1, 1, 1, 1],
                ],
                "min_client_ticks_per_action": 4,
            },
            "server_restart_count": 3,
        },
    )

    _validate_stage("canary", run, TrainConfig(updates=1))
