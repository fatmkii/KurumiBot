#!/usr/bin/env bash
set -euo pipefail
umask 077

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

if [[ "$EUID" == 0 ]]; then
    echo '请用拥有项目目录的普通用户执行 bash deploy/install.sh；脚本会按需调用 sudo。' >&2
    exit 1
fi
source /etc/os-release
if [[ "$ID" != ubuntu ]] || [[ ! -d /run/systemd/system ]]; then
    echo '需要运行 systemd 的 Ubuntu 服务器。' >&2
    exit 1
fi
if [[ ! -f .env ]]; then
    cp .env.example .env
    echo '已创建 .env。请填写 QQ_APP_ID、QQ_APP_SECRET、DEEPSEEK_API_KEY 后重新执行。' >&2
    exit 1
fi
chmod 600 .env
sudo -v
sudo apt-get update
sudo apt-get install -y ca-certificates curl supervisor

if command -v uv >/dev/null 2>&1; then
    uv_bin="$(command -v uv)"
elif [[ -x "$repo_root/.tools/uv" ]]; then
    uv_bin="$repo_root/.tools/uv"
else
    mkdir -p .tools
    curl --proto '=https' --tlsv1.2 -fsSL https://astral.sh/uv/install.sh -o .tools/install-uv.sh
    UV_INSTALL_DIR="$repo_root/.tools" UV_NO_MODIFY_PATH=1 sh .tools/install-uv.sh
    uv_bin="$repo_root/.tools/uv"
fi
export UV_CACHE_DIR="$repo_root/data/uv-cache"
"$uv_bin" python install 3.12
"$uv_bin" sync --locked --no-dev
# 预检不会调用 QQ 或 DeepSeek；失败时不替换 Supervisor 配置。
"$uv_bin" run --offline --no-sync python -m deploy.prepare "$uv_bin" "$(id -un)"

sudo install -m 644 data/kurumibot.conf /etc/supervisor/conf.d/kurumibot.conf
sudo systemctl enable --now supervisor
sudo supervisorctl reread
sudo supervisorctl update
sudo supervisorctl restart kurumibot kurumibot-admin
sudo supervisorctl status kurumibot kurumibot-admin
echo '部署完成。后台地址见上方预检输出；登录密码保存在 .env 的 KURUMI_ADMIN_PASSWORD。'
echo 'QQ 是否连接成功请检查：sudo supervisorctl tail kurumibot'
