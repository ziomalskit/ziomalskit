# syntax=docker/dockerfile:1@sha256:4edf897a3ffa55b89f906fc8cc78afdb3f1834cc9c7083565e611a8a7d5fe99e
# NVIDIA's linux/amd64 manifest, resolved from the registry 2026-11-05.
FROM nvidia/cuda:13.0.0-devel-ubuntu24.04@sha256:435220c0fef35cbf712e11999f8670a83835ef3cdd18564e5e8122f83078c88c
ARG AJ_REVISION
ENV WORKSPACE=/workspace COMFY_ROOT=/workspace/ComfyUI PANEL_ROOT=/workspace/H3_VAST_MOBILE \
    COMFY_PYTHON=/opt/aj/venv/bin/python PYTHON_BIN=/opt/aj/venv/bin/python \
    H3_CUDA_VERSION=13.0 H3_ALLOW_SUBMISSIONS=0 H3_ENABLE_TERMINAL=0 \
    PIP_NO_CACHE_DIR=1 LANG=C.UTF-8 LC_ALL=C.UTF-8 \
    PATH=/opt/aj/venv/bin:/usr/local/cuda/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
COPY h3 /opt/aj/ziomalskit/h3
# Optional CA is a build secret, never an image layer. No GPU or models here.
RUN --mount=type=secret,id=proxy_ca \
    bash /opt/aj/ziomalskit/h3/docker/build-image.sh "$AJ_REVISION"
COPY h3/docker/aj-docker /usr/local/bin/aj-docker
ENTRYPOINT ["/usr/bin/tini", "--", "/opt/aj/ziomalskit/h3/docker/entrypoint.sh"]
# Deliberately no EXPOSE and no startup of AJ/ComfyUI or provider actions.
