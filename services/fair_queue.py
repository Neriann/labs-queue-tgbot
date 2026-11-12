"""Weighted sampling without replacement and per-chat EMA."""

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass

INITIAL_DEBT = 0.5
DEFAULT_FAIRNESS_POWER = 1.8
DEFAULT_ALPHA = 0.5


@dataclass(frozen=True, slots=True)
class QueueMember:
    user_id: int
    debt: float


def log_priority_multiplier(debt: float, fairness_power: float) -> float:
    """Return log10(weight / weight_of_new_member)."""
    return fairness_power * math.log10((0.1 + debt) / (0.1 + INITIAL_DEBT))


def generate_queue(
    members: Sequence[QueueMember],
    *,
    fairness_power: float = DEFAULT_FAIRNESS_POWER,
    rng: random.Random | None = None,
) -> list[QueueMember]:
    """Sort by decreasing Efraimidis–Spirakis key without underflow."""
    if len(members) < 2:
        return list(members)

    uniform = random.uniform if rng is None else rng.uniform


    def score(member: QueueMember) -> float:
        u = uniform(1e-9, 1.0)
        if u == 1.0:
            return -math.inf
        # log10(-log(key)) with a common offset from normalizing all weights.
        return math.log10(-math.log(u)) - log_priority_multiplier(member.debt, fairness_power)

    return sorted(members, key=score)


def update_debts(
    order: Sequence[QueueMember],
    *,
    alpha: float = DEFAULT_ALPHA,
    commit: bool = True,
) -> dict[int, float]:
    """Return debts to persist. Preview mode returns the input debts."""
    if not commit or len(order) < 2:
        return {member.user_id: member.debt for member in order}

    last_rank = len(order) - 1
    return {
        member.user_id: alpha * member.debt + (1 - alpha) * (rank / last_rank)
        for rank, member in enumerate(order)
    }
