"""Step Functions task-token lifecycle for an interactive session.

The provisioner launches this container via an ecs:runTask.waitForTaskToken
state, so the workflow execution PAUSES until the task token is returned. The
container holds the token (injected as TASK_TOKEN) and drives its own lifecycle:

  - heartbeat() while the kernel is alive, so a dead kernel / abandoned session
    trips the step's HeartbeatSeconds timeout and is reclaimed.
  - success() when the session ends cleanly (idle shutdown or user close), with
    the task ARN in the output so the workflow's teardown step can StopTask.
  - failure() if the kernel exits abnormally.

When no token is present (local dev), every call is a no-op so the container
still runs a plain Jupyter server.
"""

import json
import logging

log = logging.getLogger("r-session")


class TaskTokenClient:
    def __init__(self, token, region, task_arn):
        self.token = token
        self.task_arn = task_arn or ""
        self._sfn = None
        if token:
            import boto3  # imported lazily so local runs need no AWS deps configured

            self._sfn = boto3.client("stepfunctions", region_name=region or None)

    @property
    def enabled(self):
        return bool(self._sfn and self.token)

    def heartbeat(self):
        """Forward a heartbeat. Raises on an invalid/expired token so the caller
        can stop the loop (the execution is gone)."""
        if not self.enabled:
            return
        self._sfn.send_task_heartbeat(taskToken=self.token)

    def success(self, closed_by="session-ended"):
        if not self.enabled:
            return
        output = json.dumps(
            {"taskArn": self.task_arn, "status": "closed", "closedBy": closed_by}
        )
        try:
            self._sfn.send_task_success(taskToken=self.token, output=output)
            log.info("sent task success (closedBy=%s)", closed_by)
        except Exception as e:  # noqa: BLE001 — best-effort; token may already be resolved
            log.warning("send_task_success failed (token may be resolved): %s", e)

    def failure(self, reason):
        if not self.enabled:
            return
        try:
            self._sfn.send_task_failure(
                taskToken=self.token,
                error="InteractiveSessionFailed",
                cause=reason,
            )
            log.info("sent task failure: %s", reason)
        except Exception as e:  # noqa: BLE001
            log.warning("send_task_failure failed: %s", e)
