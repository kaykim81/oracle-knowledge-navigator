#!/usr/bin/env bash
set -e

# The repo checkout is owned by a different user than `vscode`, so git refuses
# to run ("dubious ownership"). Mark the workspace as safe before any git call.
git config --global --add safe.directory "$PWD"

REPO_ROOT="$(git rev-parse --show-toplevel)"
echo "source $REPO_ROOT/02-tools/.bashrc" >> ~/.bashrc

git config --global user.name 'Kay Kim'
git config --global user.email 'kaykim81@gmail.com'

sudo apt-get update -qq
sudo apt-get install -y \
    libglib2.0-0 libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
    libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 \
    libasound2t64 libpango-1.0-0 libpangocairo-1.0-0 libgtk-3-0 \
    fonts-nanum

npm install -g @google/gemini-cli
npm install -g @anthropic-ai/claude-code
