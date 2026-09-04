from pathlib import Path

import pytest

from yrush_trainer.config import ACTION_CARDINALITIES, OBSERVATION_FEATURES, TrainConfig
from yrush_trainer.endpoints import expand_endpoint_template, resolve_endpoints


def test_ppo_defaults_and_spaces_are_fixed() -> None:
    config = TrainConfig(updates=1)
    assert config.learning_rate == 3e-4
    assert config.gamma == 0.999
    assert config.gae_lambda == 0.98
    assert config.clip_range == 0.2
    assert config.target_kl == 0.03
    assert config.entropy_coefficient == 0.01
    assert config.value_coefficient == 0.5
    assert config.maximum_gradient_norm == 0.5
    assert config.rollout_length == 256
    assert config.batch_size == 256
    assert config.optimization_epochs == 10
    assert config.policy_width == 128
    assert ACTION_CARDINALITIES == (3, 3, 2, 2, 5, 5)
    assert OBSERVATION_FEATURES == 513
    config.validate()


def test_toml_is_strict_and_cli_values_override(tmp_path: Path) -> None:
    path = tmp_path / "training.toml"
    path.write_text("[training]\nupdates=4\nlearning_rate=0.001\n", encoding="utf-8")
    config = TrainConfig.from_toml(path, updates=2)
    assert config.updates == 2
    assert config.learning_rate == 0.001
    path.write_text("[training]\nupdates=1\nlegacy_knob=true\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unknown"):
        TrainConfig.from_toml(path)


def test_endpoint_pool_is_unique_and_ordered() -> None:
    endpoints = expand_endpoint_template("yrush-client-{index}.yrush-clients:64123", 3)
    assert [endpoint.ordinal for endpoint in endpoints] == [0, 1, 2]
    assert endpoints[2].address == "yrush-client-2.yrush-clients:64123"
    with pytest.raises(ValueError, match="duplicate"):
        resolve_endpoints(endpoint_values=["client:1", "client:1"])
