#!/bin/sh
# Interactive sessions run only on ECS/Fargate (no Lambda mode). This thin
# entrypoint exists for parity with other Pennsieve processors and to keep the
# image's start command in one place.
exec "$@"
