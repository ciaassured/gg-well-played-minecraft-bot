"""Failures that must abort a run instead of becoming learning transitions."""


class InfrastructureError(RuntimeError):
    """A configured client or server dependency could not maintain the fixed pool."""


class ProtocolTimeout(InfrastructureError):
    """A bounded trainer-to-Fabric deadline elapsed."""


class ProtocolStateError(InfrastructureError):
    """A peer violated version, ordering, identity, or action invariants."""


class CheckpointCompatibilityError(ValueError):
    """A saved model does not implement the YRush PPO contract."""
