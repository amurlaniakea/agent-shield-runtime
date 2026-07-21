from enum import Enum

class ShieldVerdict(Enum):
    """Verdicts from the Agent Shield runtime."""
    ALLOW = "allow"
    BLOCK = "block"
    CONFIRM = "confirm"