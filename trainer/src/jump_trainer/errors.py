"""Failures that must never become reinforcement-learning transitions."""


class InfrastructureError(RuntimeError):
    """The remote benchmark could not produce a valid transition."""


class ProtocolTimeout(InfrastructureError):
    """The client missed a bounded protocol deadline."""


class ProtocolStateError(InfrastructureError):
    """The peer violated the versioned reset/action state machine."""
