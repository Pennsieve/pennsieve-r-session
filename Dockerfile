# JupyterLab serving an R (IRkernel) session. Mirrors pennsieve-jupyter-session
# but swaps the Python kernel for R: the notebook server is still JupyterLab and
# speaks the same kernel protocol, so the existing frontend works unchanged — it
# just starts the "ir" kernel instead of "python3".
FROM rocker/r-ver:4.4.1

WORKDIR /app

# System libraries IRkernel (and most CRAN packages) need: zmq for the kernel
# transport, curl/ssl/xml for typical packages. Python provides JupyterLab — the
# notebook server itself — plus the Step Functions lifecycle deps.
RUN apt-get update && apt-get install -y --no-install-recommends \
      python3 python3-venv \
      libzmq3-dev libcurl4-openssl-dev libssl-dev libxml2-dev \
    && rm -rf /var/lib/apt/lists/*

# JupyterLab + lifecycle deps in an isolated venv (avoids PEP 668 system-pip
# restrictions and keeps a clean PATH). Heavy R science packages are NOT baked
# in — they live on a shared EFS layer (the `layers` mechanism), keeping the
# kernel image small and the layer reusable across sessions.
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY processor/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Install IRkernel and register it system-wide (user = FALSE) so JupyterLab lists
# the "ir" kernel for every session. jupyter is already on PATH (venv), so the
# kernelspec lands in the venv prefix JupyterLab reads from.
RUN R -e "install.packages('IRkernel', repos='https://cloud.r-project.org')" \
    && R -e "IRkernel::installspec(user = FALSE)"

# ipykernel ships a "python3" kernelspec transitively with jupyterlab. This is an
# R session image, so drop it — JupyterLab then offers only R, and nothing can
# accidentally launch Python. (|| true: harmless if it isn't present.)
RUN jupyter kernelspec remove -f python3 || true

COPY processor/ /app/processor/
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# R startup conveniences: auto-define INPUT_DIR / OUTPUT_DIR / save_output() in
# every R session (R sources this site profile at startup), so users don't have
# to discover the env vars. Mirrors the Python session's 00-pennsieve.py.
COPY r/Rprofile.site /app/r/Rprofile.site
ENV R_PROFILE=/app/r/Rprofile.site

ENV PYTHONPATH="/app"
# Writable config/runtime dirs (the EFS root_dir is the user's workspace).
ENV JUPYTER_CONFIG_DIR="/tmp/jupyter" \
    JUPYTER_RUNTIME_DIR="/tmp/jupyter/runtime" \
    HOME="/home/appuser"

# Run as uid/gid 1000, not root: JupyterLab refuses to run as root, and 1000
# matches the EFS access point the provisioner creates (owner uid/gid 1000) so
# the kernel can read/write the EFS-staged files.
RUN groupadd -g 1000 appuser && useradd -u 1000 -g 1000 -m -s /bin/bash appuser
USER 1000:1000

# Interactive sessions run only as ECS/Fargate tasks (a long-lived kernel can't
# run on Lambda), so there is no dual-mode entrypoint.
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["python3", "-m", "processor.main"]
