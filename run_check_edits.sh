#!/bin/bash

cd /path/to/daily-lotus  # set this to your project root

# Activate your virtual environment (if using one)
source .venv/bin/activate  # or use `eval "$(uv venv activate)"` if using uv venv

# Log rotation is handled internally by daily_lotus.logging_config (see deploy/lotus-daily.logrotate for logrotate)
uv run daily_lotus/check_edits.py "$@"
