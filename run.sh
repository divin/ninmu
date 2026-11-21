#!/usr/bin/env bash
# Run Ninmu locally in developer mode (uses .env in the project root).
cd "$(dirname "$0")"
exec uv run ninmu
