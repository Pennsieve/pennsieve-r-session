# Workflow definitions

`r-notebook.json` is the workflow definition that exposes this container as an
**interactive** workflow step. POST it to workflow-service's `/definitions`
endpoint at deploy time (set `organizationId` and pin `tag` to the released
version).

## Interactive contract

The `r-session` processor node carries two extra fields beyond a normal
processor:

- `"interactive": true` — declares this is a **user-in-the-loop** container that
  requires the wait logic. The provisioner branches any node with this flag onto
  an `ecs:runTask.waitForTaskToken` step (pause → resume on close → teardown),
  regardless of `sessionType`.
- `"sessionType": "r"` — selects the container image + frontend kernel; it does
  **not** change the wait logic. (The Python session uses `"jupyter"`.)

`computeType` (`standard` = Fargate, `gpu` = EC2) is orthogonal — a GPU R
notebook is the same node with `computeType: "gpu"`.

The node's compute node must enable interactive sessions
(`max_interactive_sessions > 0`); otherwise the ASL converter rejects the run.

## Frontend note

The notebook server is JupyterLab, so the existing frontend talks to it over the
same Jupyter kernel protocol (`@jupyterlab/services`). The only kernel installed
in this image is **`ir`** (IRkernel) — there is no `python3` kernel — so the
frontend must start the `ir` kernel for an `r` session (the launcher should pick
the kernel name from `sessionType`).

## Persisting outputs (optional follow-up)

This minimal definition has no data-target, so work persists only on EFS for the
life of the execution. To save the notebook and outputs back to the dataset, add
a data-target node (e.g. `Pennsieve Package Asset` / `Pennsieve Dataset`)
depending on `r-session`, as other workflows do.
