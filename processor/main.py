"""Interactive R session processor (ECS/Fargate).

Launches a JupyterLab server with an R (IRkernel) kernel on selected files
(staged onto EFS exactly like a normal processor) and keeps the workflow paused
while the user works, then resumes the workflow when the session ends.

The notebook server is JupyterLab — identical machinery to the Python session —
so the frontend speaks the same kernel protocol; only the kernel (IRkernel, name
"ir") differs.

Lifecycle (see lifecycle.py):
  - start JupyterLab bound to localhost (the auth-proxy sidecar is the network
    boundary; it validates the broker's signed token and reverse-proxies in).
  - heartbeat the Step Functions task token on an interval while the server is up.
  - the server stops because: the user closed it (sidecar calls the server's
    /api/shutdown), it went idle (shutdown_no_activity_timeout), it crashed, or
    the platform sent SIGTERM (cancel / post-success teardown).
      * clean stop  -> SendTaskSuccess  -> workflow resumes -> teardown StopTask
      * crash       -> SendTaskFailure  -> workflow Catch -> teardown
      * SIGTERM     -> exit, no token call (platform already drove the stop)
"""

import logging
import os
import signal
import subprocess
import sys
import threading

from processor import metadata
from processor.lifecycle import TaskTokenClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
    force=True,
)
log = logging.getLogger("r-session")


def _int_env(name, default):
    try:
        return int(os.environ.get(name, ""))
    except (TypeError, ValueError):
        return default


def get_config():
    input_dir = os.environ.get("INPUT_DIR", "")
    # Notebook root: default to the execution directory (parent of INPUT_DIR) so
    # the user sees input/ and output/ side by side; overridable.
    default_root = os.path.dirname(input_dir.rstrip("/")) if input_dir else "/"
    return {
        "input_dir": input_dir,
        "output_dir": os.environ.get("OUTPUT_DIR", ""),
        "execution_run_id": os.environ.get("EXECUTION_RUN_ID", "unknown"),
        "task_token": os.environ.get("TASK_TOKEN", ""),
        "session_type": os.environ.get("SESSION_TYPE", "r"),
        "region": os.environ.get("REGION", "") or os.environ.get("AWS_REGION", ""),
        "notebook_root": os.environ.get("NOTEBOOK_ROOT", default_root) or "/",
        # Bind to localhost in production (sidecar proxies); 0.0.0.0 locally so
        # you can hit the server directly during `make run`.
        "bind_ip": os.environ.get("JUPYTER_IP", "127.0.0.1"),
        "port": _int_env("JUPYTER_PORT", 8888),
        "base_url": os.environ.get("JUPYTER_BASE_URL", "/"),
        # Idle backstop: shut the server (and thus the session) down after this
        # many seconds with no kernel/terminal activity.
        "idle_timeout": _int_env("IDLE_TIMEOUT_SECONDS", 3600),
        # Heartbeat cadence — must be < the step's HeartbeatSeconds (120).
        "heartbeat_interval": _int_env("HEARTBEAT_INTERVAL_SECONDS", 60),
    }


def build_jupyter_cmd(cfg):
    idle = cfg["idle_timeout"]
    return [
        "jupyter",
        "lab",
        "--no-browser",
        f"--ServerApp.ip={cfg['bind_ip']}",
        f"--ServerApp.port={cfg['port']}",
        f"--ServerApp.base_url={cfg['base_url']}",
        f"--ServerApp.root_dir={cfg['notebook_root']}",
        # Auth + TLS are handled by the auth-proxy sidecar; the server itself is
        # only reachable through it, so no server-level token/password.
        "--ServerApp.token=",
        "--ServerApp.password=",
        "--ServerApp.allow_origin=*",
        "--ServerApp.disable_check_xsrf=True",
        # The auth-proxy sidecar reaches us at a public per-session hostname.
        # Without this, Jupyter blocks any non-local Host header
        # ("Blocking request with non-local 'Host' ... 403 Forbidden"). The
        # sidecar is still the only network path in (server binds localhost).
        "--ServerApp.allow_remote_access=True",
        # jupyter-lsp ships transitively with jupyterlab and auto-loads a server
        # extension at boot, but no language servers are installed, so it only
        # adds startup time for zero features. Disable it at launch.
        '--ServerApp.jpserver_extensions={"jupyter_lsp": False}',
        # This is the R session image, so the R kernel is the default — a client
        # that starts a kernel without naming one (or hits Jupyter's built-in
        # "python3" default) gets R, not Python.
        "--MappingKernelManager.default_kernel_name=ir",
        # Idle reclaim: stop the server when nothing is running.
        f"--ServerApp.shutdown_no_activity_timeout={idle}",
        f"--MappingKernelManager.cull_idle_timeout={idle}",
        "--MappingKernelManager.cull_interval=120",
        "--MappingKernelManager.cull_connected=True",
    ]


def setup_workspace(cfg):
    """Prepare the notebook root so the file browser shows a clean input/ +
    output/ view. NOTEBOOK_ROOT is a dedicated dir; we symlink input -> INPUT_DIR
    and output -> OUTPUT_DIR into it (real paths, so saving to output/ writes to
    the actual OUTPUT_DIR that flows to the next workflow step). Runs before the
    server launches so the links exist when the browser first lists the root.
    """
    root = cfg["notebook_root"]
    if not root or root == "/":
        return
    try:
        os.makedirs(root, exist_ok=True)
    except OSError as e:  # noqa: BLE001
        log.warning("could not create notebook root %s: %s", root, e)
        return
    if cfg["output_dir"]:
        try:
            os.makedirs(cfg["output_dir"], exist_ok=True)
        except OSError as e:  # noqa: BLE001
            log.warning("could not create OUTPUT_DIR %s: %s", cfg["output_dir"], e)
    for name, target in (("input", cfg["input_dir"]), ("output", cfg["output_dir"])):
        if not target:
            continue
        link = os.path.join(root, name)
        try:
            if os.path.islink(link) or os.path.exists(link):
                continue
            os.symlink(target, link)
            log.info("workspace: %s -> %s", link, target)
        except OSError as e:  # noqa: BLE001 — never block startup over a symlink
            log.warning("could not symlink %s -> %s: %s", link, target, e)


def main():
    cfg = get_config()
    setup_workspace(cfg)
    task_arn, ip = metadata.task_arn_and_ip()
    tokens = TaskTokenClient(cfg["task_token"], cfg["region"], task_arn)

    log.info("=" * 60)
    log.info("Interactive R session (JupyterLab + IRkernel)")
    log.info("  executionRunId: %s", cfg["execution_run_id"])
    log.info("  sessionType:    %s", cfg["session_type"])
    log.info("  taskArn:        %s", task_arn or "(local)")
    log.info("  privateIp:      %s", ip or "(local)")
    log.info("  notebook root:  %s", cfg["notebook_root"])
    log.info("  bind:           %s:%d  base_url=%s", cfg["bind_ip"], cfg["port"], cfg["base_url"])
    log.info("  idle timeout:   %ds", cfg["idle_timeout"])
    log.info("  task token:     %s", "present" if tokens.enabled else "absent (local mode)")
    log.info("=" * 60)

    proc = subprocess.Popen(build_jupyter_cmd(cfg))

    stop = threading.Event()
    platform_terminated = threading.Event()

    def on_sigterm(_signum, _frame):
        # Platform-initiated stop (cancel, or teardown after we already resumed
        # the workflow). Do NOT send task success — the platform owns this stop.
        log.info("SIGTERM received; shutting down (platform-initiated)")
        platform_terminated.set()
        stop.set()
        proc.terminate()

    signal.signal(signal.SIGTERM, on_sigterm)

    def heartbeat_loop():
        while not stop.wait(cfg["heartbeat_interval"]):
            if proc.poll() is not None:
                return
            try:
                tokens.heartbeat()
            except Exception as e:  # noqa: BLE001 — invalid token => execution gone; stop trying
                log.warning("heartbeat failed (stopping heartbeat loop): %s", e)
                return

    threading.Thread(target=heartbeat_loop, daemon=True).start()

    rc = proc.wait()
    stop.set()

    if platform_terminated.is_set():
        log.info("Exited due to platform stop; not returning the task token")
        sys.exit(0)

    if rc == 0:
        # Clean stop: idle shutdown or user close (sidecar -> /api/shutdown).
        tokens.success(closed_by="session-ended")
        sys.exit(0)

    log.error("JupyterLab exited with code %d", rc)
    tokens.failure(f"jupyter server exited with code {rc}")
    sys.exit(rc)


if __name__ == "__main__":
    main()
