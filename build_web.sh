#!/usr/bin/env bash
set -euo pipefail

# 从相邻的 TypeScript 项目构建 React，并把产物放进 Python 服务的 web_dist/。
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WEB_PROJECT="${ROOT}/../expense-assistant"

if [[ ! -d "${WEB_PROJECT}/web" ]]; then
  echo "找不到 ${WEB_PROJECT}/web" >&2
  exit 1
fi

cd "${WEB_PROJECT}"
npm run build:web

rm -rf "${ROOT}/web_dist"
cp -R "${WEB_PROJECT}/public" "${ROOT}/web_dist"

echo "web_dist updated: ${ROOT}/web_dist"
