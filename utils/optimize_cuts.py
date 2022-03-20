import itertools
from typing import DefaultDict
from ortools.linear_solver import pywraplp

SOLVER_TIME_LIMIT = 1 * 60 * 1000
SAW_BLADE_THICKNESS = 0.25

CUT_LENGTH_AND_COUNTS = {
    "2x4": [
        # Bed A & B
        # (18.5, 8),
        # (27.0, 2),
        # (22.0, 4),
        # (45.0, 2),
        # Bed C
        # (18.5, 4),
        # (34.5, 2),
        # (38.5, 2),
        # (41.5, 1),
        # Barrel Stand
        (20.5, 2),
        (24.5, 5),
        (11.5, 4),
    ],
    "1x6": [
        # Bed A & B
        # (25.0, 12),
        # (35.5, 6),
        # (34.0, 4),
        # (53.5, 6),
        # (52.0, 4),
        # Bed C
        (41.5, 6),
        (43.0, 6),
        (41.5, 7),
        # Barrel Stand
        (29.0, 2),
        (27.5, 2),

    ],
}

BOARD_LENGTH_AND_COUNTS = [
    # (8 * 12, 50),
    # (10 * 12, 50),
    # (12 * 12, 50),
    # Barrel Stand
    (107, 2),
]


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--board-type", choices=("2x4", "1x6"), required=True)
    args = parser.parse_args()

    LENGTH_AND_COUNTS = CUT_LENGTH_AND_COUNTS[args.board_type]

    def create_data_model():
        weights = []
        for length, count in LENGTH_AND_COUNTS:
            for _ in range(count):
                weights.append(length + SAW_BLADE_THICKNESS)

        capacities = []
        for length, count in BOARD_LENGTH_AND_COUNTS:
            for _ in range(count):
                capacities.append(length)

        data = {}
        data["weights"] = weights
        data["items"] = list(range(len(weights)))
        data["bin_capacities"] = capacities
        data["bins"] = list(range(len(data["bin_capacities"])))

        return data

    data = create_data_model()

    solver = pywraplp.Solver.CreateSolver("SCIP")

    # Variables
    # x[i, j] = 1 if item i is packed in bin j.
    x = {}
    for i in data["items"]:
        for j in data["bins"]:
            x[(i, j)] = solver.IntVar(0, 1, "x_%i_%i" % (i, j))

    # y[j] = 1 if bin j is used.
    y = {}
    for j in data["bins"]:
        y[j] = solver.IntVar(0, 1, "y[%i]" % j)

    # Constraints
    # Each item must be in exactly one bin.
    for i in data["items"]:
        solver.Add(sum(x[i, j] for j in data["bins"]) == 1)

    # The amount packed in each bin cannot exceed its capacity.
    for j in data["bins"]:
        solver.Add(
            sum(x[(i, j)] * data["weights"][i] for i in data["items"])
            <= y[j] * data["bin_capacities"][j]
        )

    # Minimize total amount of bin capacity used
    solver.Minimize(
        solver.Sum([y[j] * data["bin_capacities"][j] for j in data["bins"]])
    )

    solver.SetTimeLimit(SOLVER_TIME_LIMIT)
    status = solver.Solve()

    if status in (pywraplp.Solver.OPTIMAL, pywraplp.Solver.FEASIBLE):
        from collections import defaultdict

        board_cuts_and_length_to_count = defaultdict(int)
        for bin in data["bins"]:
            if y[bin].solution_value() == 1:
                cur_cuts_and_len = (
                    tuple(
                        sorted(
                            (
                                data["weights"][item] - SAW_BLADE_THICKNESS
                                for item in data["items"]
                                if x[item, bin].solution_value() > 0
                            ),
                            reverse=True,
                        )
                    ),
                    data["bin_capacities"][bin],
                )
                board_cuts_and_length_to_count[cur_cuts_and_len] += 1

        total_num_boards = 0
        total_board_length_used = 0
        total_board_length_wasted = 0

        board_length_to_count = defaultdict(int)

        for board_length, group in itertools.groupby(
            sorted(
                (
                    length,
                    cuts,
                    count,
                    length - sum(cuts) - len(cuts) * SAW_BLADE_THICKNESS,
                )
                for (cuts, length), count in board_cuts_and_length_to_count.items()
            ),
            key=lambda tup: tup[0],
        ):
            print(f"{board_length / 12:.1f} ft:")
            for _, cuts, count, wasted_length in group:
                total_num_boards += count
                board_length_to_count[board_length] += count
                total_board_length_used += board_length * count
                total_board_length_wasted += wasted_length * count

                print(
                    "\t[{}] -> {} ({:.1f}in)".format(
                        ", ".join(map(str, cuts)), count, wasted_length
                    )
                )
            print()

        print("Number of boards used: {}".format(total_num_boards))
        print("Total board length (ft): {:.1f}".format(total_board_length_used / 12))
        print(
            "Total board length wasted (ft): {:.1f} ({:.1f}%)".format(
                total_board_length_wasted / 12,
                total_board_length_wasted / total_board_length_used * 100,
            )
        )
        print("Time = ", solver.WallTime(), " milliseconds")
    else:
        print("Failed to find solution.")


if __name__ == "__main__":
    main()
