import random

import pytest

from services.fair_queue import QueueMember, generate_queue, update_debts


def test_high_debt_is_first_more_often_over_1000_draws():
    low = QueueMember(1, 0.0)
    high = QueueMember(2, 1.0)
    rng = random.Random(731)
    high_first = sum(generate_queue([low, high], rng=rng)[0] == high for _ in range(1000))

    assert high_first > 950
    assert 1000 - high_first > 0


def test_order_matches_direct_efraimidis_spirakis_keys():
    members = [QueueMember(10, 0.2), QueueMember(20, 0.7), QueueMember(30, 0.9)]
    expected_rng = random.Random(25)

    def key(member: QueueMember) -> float:
        weight = (0.1 + member.debt) ** 2
        return expected_rng.uniform(1e-9, 1.0) ** (1 / weight)

    assert generate_queue(members, rng=random.Random(25)) == sorted(members, key=key, reverse=True)


@pytest.mark.parametrize("members", [[], [QueueMember(1, 0.65)]])
def test_empty_and_single_member(members):
    assert generate_queue(members) == members
    assert update_debts(members) == {member.user_id: member.debt for member in members}


def test_ema_uses_normalized_positions_and_preview_preserves_debts():
    order = [QueueMember(1, 0.5), QueueMember(2, 0.2), QueueMember(3, 0.5)]
    assert update_debts(order, commit=False) == {1: 0.5, 2: 0.2, 3: 0.5}
    assert update_debts(order) == pytest.approx({1: 0.375, 2: 0.275, 3: 0.625})
    assert [member.debt for member in order] == [0.5, 0.2, 0.5]
