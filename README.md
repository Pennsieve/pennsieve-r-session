# pennsieve-r-session

An **interactive** Pennsieve workflow processor: it runs a live JupyterLab server
with an **R (IRkernel)** kernel as a step in a workflow. The user works in the
notebook in their browser; the workflow pauses while they work and resumes when
they close the session.

This is the R counterpart to
[`pennsieve-jupyter-session`](https://github.com/Pennsieve/pennsieve-jupyter-session):
identical human-in-the-loop machinery (`interactive: true` + `sessionType`), with
the Python kernel swapped for R. Because the notebook server is still JupyterLab,
the frontend speaks the same kernel protocol — it just starts the `ir` kernel
instead of `python3`.

## How it fits the platform

```
data-source ─▶ r-session (this) ─▶ (workflow resumes after close)
   files          live R kernel
 staged to       on EFS, browser
   EFS input/     connects in
```

- **Files**: data-sources stage selected files onto EFS at `INPUT_DIR` exactly
  like a normal processor; outputs written to `OUTPUT_DIR` persist on EFS.
- **Pause/resume**: the provisioner launches this container via an
  `ecs:runTask.waitForTaskToken` Step Functions step. The container holds the
  injected `TASK_TOKEN` and returns it when the session ends, resuming the
  workflow; a teardown step then stops the task.
- **Reachability/auth** (production): the container binds JupyterLab to
  `127.0.0.1`. An **auth-proxy sidecar** in the same task is the network
  boundary — it validates the central broker's short-lived signed token and
  reverse-proxies to the kernel. (Those components live in other repos.)

## Lifecycle

| Event | What the container does |
|---|---|
| boot | reads ECS task metadata (task ARN), starts JupyterLab, begins heartbeating the task token |
| alive | `SendTaskHeartbeat` every `HEARTBEAT_INTERVAL_SECONDS` (< the step's 120s `HeartbeatSeconds`) |
| user close | sidecar calls the server's `/api/shutdown` → server exits → `SendTaskSuccess` (with task ARN) → workflow resumes |
| idle | `shutdown_no_activity_timeout` stops the server → same clean-close path |
| crash | non-zero exit → `SendTaskFailure` → workflow Catch → teardown |
| platform stop (cancel/teardown) | `SIGTERM` → exit without a token call (the platform drove the stop) |

In-memory kernel state is intentionally **not** persisted — the `.ipynb` and
files on EFS survive; users re-run cells on reopen.

## The R kernel

R and [IRkernel](https://irkernel.github.io/) are installed in the image, and the
kernelspec is registered system-wide (`IRkernel::installspec(user = FALSE)`) so
JupyterLab lists the **`ir`** kernel. Heavy R/science packages are **not** baked
into this image — provide them via a shared EFS layer (the `layers` mechanism),
keeping the kernel image small.

Each R session auto-loads a few conveniences (see `r/Rprofile.site`):

```r
INPUT_DIR              # files staged from previous steps (read here)
OUTPUT_DIR             # save here -> passed to the next workflow step
LAYERS_DIR             # mounted persistent layers, if any
save_output("x.csv")   # -> path string inside OUTPUT_DIR (creates it if needed)

# e.g.
write.csv(df, save_output("results.csv"))
```

## Environment variables

| Variable | Source | Description |
|---|---|---|
| `INPUT_DIR` / `OUTPUT_DIR` | platform | EFS paths (same as any processor) |
| `EXECUTION_RUN_ID` | platform | workflow run id |
| `TASK_TOKEN` | ASL `runTask.waitForTaskToken` | the token this container returns on close |
| `SESSION_TYPE` | ASL | `r` |
| `REGION` / `AWS_REGION` | platform | for the Step Functions client |
| `NOTEBOOK_ROOT` | optional | JupyterLab root dir (default: parent of `INPUT_DIR`) |
| `JUPYTER_IP` / `JUPYTER_PORT` / `JUPYTER_BASE_URL` | optional | bind + base path (default `127.0.0.1:8888`, `/`) |
| `IDLE_TIMEOUT_SECONDS` | optional | idle backstop (default 3600) |
| `HEARTBEAT_INTERVAL_SECONDS` | optional | heartbeat cadence (default 60) |

## Local development

```sh
make build
make run     # JupyterLab on http://localhost:8888 (no token call without TASK_TOKEN)
make clean
```

`dev.env` sets `JUPYTER_IP=0.0.0.0` and leaves `TASK_TOKEN` blank so the server
runs standalone for local testing. Put test files in `data/input/`. In JupyterLab
choose the **R** kernel.

## Project structure

```
processor/
  main.py        # launch JupyterLab + drive the task-token lifecycle
  lifecycle.py   # Step Functions SendTaskHeartbeat/Success/Failure (boto3)
  metadata.py    # read the ECS task-metadata endpoint (task ARN, IP)
  requirements.txt
r/
  Rprofile.site  # auto-defines INPUT_DIR / OUTPUT_DIR / save_output() per session
entrypoint.sh    # ECS-only (no Lambda mode)
Dockerfile
docker-compose.yml / dev.env / Makefile   # local dev
workflows/
  r-notebook.json   # interactive workflow definition
```
