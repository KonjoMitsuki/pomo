#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DOT_VENV_PYTHON="${SCRIPT_DIR}/.venv/bin/python"
VENV_PYTHON="${SCRIPT_DIR}/venv/bin/python"
ENV_FILE="${SCRIPT_DIR}/.env"

if [[ -x "${DOT_VENV_PYTHON}" ]]; then
  PYTHON_BIN="${DOT_VENV_PYTHON}"
elif [[ -x "${VENV_PYTHON}" ]]; then
  PYTHON_BIN="${VENV_PYTHON}"
else
  if ! command -v python3 >/dev/null 2>&1; then
    echo "エラー: python3 が見つからないため仮想環境を作成できません。"
    exit 1
  fi

  echo "仮想環境が見つからないため .venv を作成します..."
  python3 -m venv "${SCRIPT_DIR}/.venv"
  PYTHON_BIN="${DOT_VENV_PYTHON}"
fi

if [[ -z "${DISCORD_BOT_TOKEN:-}" ]] && [[ -f "${ENV_FILE}" ]]; then
  set -a
  # shellcheck source=/dev/null
  source "${ENV_FILE}"
  set +a
fi

exec "${PYTHON_BIN}" "${SCRIPT_DIR}/src/timer.py"
