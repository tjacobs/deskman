#!/usr/bin/env bash
# Install the cloud sync dependencies into .venv with uv.
# Usage: ./install.sh

# Exit on error, undefined variables, and pipe failure
set -euo pipefail

# Config venv and packages
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${SCRIPT_DIR}/.venv"
PYTHON_VERSION="3.12"
PYTHON_PACKAGES=(boto3)
UV_INSTALL_URL="https://astral.sh/uv/install.sh"
UV_BIN_DIR="${HOME}/.local/bin"

# Main
main() {
    install_uv
    create_venv
    install_packages
    echo "Done. Next: ./sync.py"
}

# Install uv when missing
install_uv() {
    export PATH="${UV_BIN_DIR}:${PATH}"
    if command -v uv >/dev/null 2>&1; then
        echo "uv already installed, $(uv --version)."
        return
    fi
    curl -LsSf "${UV_INSTALL_URL}" | sh
}

# Create the venv once
create_venv() {
    if [[ -x "${VENV_DIR}/bin/python" ]]; then
        echo "Venv already exists at ${VENV_DIR}."
        return
    fi
    uv venv --python "${PYTHON_VERSION}" "${VENV_DIR}"
}

# Install the S3 client
install_packages() {
    uv pip install --python "${VENV_DIR}/bin/python" "${PYTHON_PACKAGES[@]}"
}

# Run
main
