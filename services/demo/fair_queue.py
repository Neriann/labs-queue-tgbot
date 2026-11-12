import random

from services.fair_queue import QueueMember, generate_queue, update_debts


def round(user_count: int, round_count: int, alpha: float, fairness_power: float) -> dict[int, list]:
    participants = [QueueMember(user_id=i + 1, debt=0.5) for i in range(user_count)]

    rng = random.Random(67)

    print("=== Fair queue demo ===")
    print("Participants:")
    for member in participants:
        print(f"  user_id={member.user_id:>3} debt={member.debt:.2f}")

    stat: dict[int, list] = dict()
    for round_index in range(round_count):
        order = generate_queue(participants, rng=rng, fairness_power=fairness_power)
        print(f"\nRound {round_index + 1}:")
        print("Queue order:")
        for position, member in enumerate(order, start=1):
            if member.user_id not in stat:
                stat[member.user_id] = []
            stat[member.user_id].append(position)
            print(f"  {position}. user_id={member.user_id:>3} debt={member.debt:.2f}")

        next_debts = update_debts(order, alpha=alpha)
        participants = [
            QueueMember(user_id=member.user_id, debt=next_debts[member.user_id])
            for member in participants
        ]
    return stat


def main() -> None:

    round_count = 10
    user_count = 15
    stat = round(user_count=user_count, round_count=round_count, alpha=0.35, fairness_power=2.0)

    mse = 0
    target = user_count / 2
    for user_id, positions in stat.items():
        avg_position = sum(positions) / len(positions)
        mse += (avg_position - target) ** 2
        print(f"\nUser {user_id} positions: {positions}")
        print(f"  average position: {avg_position:.2f}")

    print(f"\nMean squared error from target position {target:.2f}: {mse:.2f}")


if __name__ == "__main__":
    main()
