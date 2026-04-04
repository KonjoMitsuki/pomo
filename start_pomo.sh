#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PYTHON="${SCRIPT_DIR}/.venv/bin/python"

if [[ ! -x "${VENV_PYTHON}" ]]; then
  echo "エラー: 仮想環境の Python が見つかりません。"
  echo "期待パス: ${VENV_PYTHON}"
  echo "先に仮想環境を作成してください。"
  exit 1
fi

exec "${VENV_PYTHON}" "${SCRIPT_DIR}/timer.py"
