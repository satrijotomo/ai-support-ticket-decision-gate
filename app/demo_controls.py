from pathlib import Path

from app.db import consume_demo_control, initialize_database, set_demo_control


FAIL_NEXT_ASSIGNMENT = "fail-next-assignment"


def arm_fail_next_assignment(database_path: str | Path) -> None:
    initialize_database(database_path)
    set_demo_control(database_path, FAIL_NEXT_ASSIGNMENT, armed=True)


def consume_fail_next_assignment(database_path: str | Path) -> bool:
    return consume_demo_control(database_path, FAIL_NEXT_ASSIGNMENT)