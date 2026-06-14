"""ECS task self-introspection.

The interactive session is driven by the container itself (no node-side session
store), so the container discovers its own identity from the ECS task-metadata
endpoint: the task ARN (so the Step Functions teardown step can StopTask it) and
its private IP (informational / for logs).
"""

import logging
import os

import requests

log = logging.getLogger("r-session")


def task_arn_and_ip():
    """Return (task_arn, private_ip) from the ECS task-metadata endpoint.

    Both are None when not running on ECS (e.g. local `make run`) or if the
    endpoint is unreachable — callers must tolerate that.
    """
    uri = os.environ.get("ECS_CONTAINER_METADATA_URI_V4")
    if not uri:
        return None, None
    try:
        task = requests.get(f"{uri}/task", timeout=2).json()
    except (requests.RequestException, ValueError) as e:
        log.warning("ECS task-metadata fetch failed: %s", e)
        return None, None

    task_arn = task.get("TaskARN")
    ip = None
    for container in task.get("Containers", []):
        for net in container.get("Networks", []):
            addrs = net.get("IPv4Addresses") or []
            if addrs:
                ip = addrs[0]
                break
        if ip:
            break
    return task_arn, ip
