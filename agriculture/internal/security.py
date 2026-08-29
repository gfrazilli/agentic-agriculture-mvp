"""Shared security primitives for task dispatch and task receipt."""

import secrets

TASK_SECRET_HEADER = "X-Internal-Task-Secret"
CLOUD_TASK_NAME_HEADER = "X-CloudTasks-TaskName"
MINIMUM_TASK_SECRET_LENGTH = 32


def task_secret_is_valid(value: str) -> bool:
    return len(value) >= MINIMUM_TASK_SECRET_LENGTH and all(
        33 <= ord(character) <= 126 for character in value
    )


def task_secrets_match(provided: str, expected: str) -> bool:
    """Compare arbitrary Unicode input without compare_digest's ASCII restriction."""
    return secrets.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))
