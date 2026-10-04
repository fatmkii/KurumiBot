import hashlib
import os
from pathlib import Path
import pwd
import shlex
import shutil
import sqlite3
import subprocess
from unittest.mock import MagicMock
import zipfile

from dotenv import dotenv_values
import pytest

from deploy.prepare import DeploymentError, initialize_library, prepare
from kurumibot.config import ROOT
from kurumibot.library import Library


@pytest.fixture
def deployment(tmp_path, admin_fixture, monkeypatch):
    monkeypatch.setattr(os, "environ", os.environ.copy())
    root = tmp_path / "server with spaces 100%"
    (root / "materials/library-v1").mkdir(parents=True)
    (root / "deploy").mkdir()
    shutil.copy(ROOT / "deploy/kurumibot.conf", root / "deploy/kurumibot.conf")
    shutil.copy(admin_fixture.config.library / "library.sqlite3", root / "materials/library-v1/library.sqlite3")
    archive = root / "materials/library-v1.zip"
    with zipfile.ZipFile(archive, "w") as package:
        for path in admin_fixture.config.library.rglob("*"):
            if path.is_file():
                package.write(path, Path("library-v1") / path.relative_to(admin_fixture.config.library))
    (root / "materials/library-v1.zip.sha256").write_text(hashlib.sha256(archive.read_bytes()).hexdigest() + "  library-v1.zip\n")
    fallback = next((admin_fixture.config.library / "images").iterdir()).name
    (root / ".env").write_text(
        "# keep comment\nQQ_APP_ID=test-app\nQQ_APP_SECRET=test-secret\nDEEPSEEK_API_KEY=test-key\n"
        "KURUMI_LIBRARY=materials/library-v1\n"
        f"KURUMI_DEFAULT_IMAGE=materials/library-v1/images/{fallback}\n"
        "KURUMI_ADMIN_HOST=192.168.1.20\n"
    )
    for name in list(os.environ):
        if name.startswith(("KURUMI_", "QQ_", "QQBOT_", "DEEPSEEK_")):
            monkeypatch.delenv(name)
    monkeypatch.setattr("kurumibot.config.ROOT", root)
    monkeypatch.setattr("deploy.prepare.socket.socket", MagicMock())
    return root


def test_fresh_install_and_repeat_preserve_material_edits(deployment):
    root = deployment
    username = pwd.getpwuid(os.getuid()).pw_name
    prepare(root, "/usr/bin/uv", username)
    values = dotenv_values(root / ".env")
    assert values["KURUMI_LIBRARY"] == "data/materials/library-v1"
    assert values["KURUMI_DEFAULT_IMAGE"].startswith("data/materials/library-v1/images/")
    assert values["KURUMI_ADMIN_PASSWORD"]
    assert (root / ".env").stat().st_mode & 0o777 == 0o600
    assert "# keep comment" in (root / ".env").read_text()
    library = root / values["KURUMI_LIBRARY"] / "library.sqlite3"
    with sqlite3.connect(library) as db:
        db.execute("UPDATE materials SET meaning='server edit'")
    prepare(root, "/usr/bin/uv", username)
    assert dotenv_values(root / ".env")["KURUMI_ADMIN_PASSWORD"] == values["KURUMI_ADMIN_PASSWORD"]
    with sqlite3.connect(library) as db:
        assert db.execute("SELECT DISTINCT meaning FROM materials").fetchall() == [("server edit",)]
    generated = (root / "data/kurumibot.conf").read_text().replace("%%", "%")
    assert "__ROOT__" not in generated
    command = next(line.removeprefix("command=") for line in generated.splitlines() if line.startswith("command="))
    assert shlex.split(command)[5] == str(root / ".env")
    assert "--offline --no-sync" in command


def test_migrate_source_database_with_uncheckpointed_wal(deployment):
    source = deployment / "materials/library-v1/library.sqlite3"
    with sqlite3.connect(source) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("UPDATE materials SET meaning='old server edit'")
        db.commit()
        target = initialize_library(deployment)
        with sqlite3.connect(target / "library.sqlite3") as copied:
            assert copied.execute("SELECT DISTINCT meaning FROM materials").fetchall() == [("old server edit",)]


def test_corrupt_package_never_publishes_partial_library(deployment):
    with (deployment / "materials/library-v1.zip").open("ab") as stream:
        stream.write(b"corrupted")
    with pytest.raises(DeploymentError, match="SHA256"):
        initialize_library(deployment)
    assert not (deployment / "data/materials/library-v1").exists()


def test_missing_key_does_not_initialize_or_generate_config(deployment):
    env = deployment / ".env"
    env.write_text(env.read_text().replace("DEEPSEEK_API_KEY=test-key", "DEEPSEEK_API_KEY="))
    with pytest.raises(DeploymentError, match="DEEPSEEK_API_KEY"):
        prepare(deployment, "/usr/bin/uv", pwd.getpwuid(os.getuid()).pw_name)
    assert not (deployment / "data").exists()


def test_missing_image_blocks_deployment_without_overwriting_existing_db(deployment):
    target = initialize_library(deployment)
    next((target / "images").iterdir()).unlink()
    with pytest.raises(DeploymentError, match="缺失图片"):
        prepare(deployment, "/usr/bin/uv", pwd.getpwuid(os.getuid()).pw_name)
    assert not (deployment / "data/kurumibot.conf").exists()


@pytest.mark.parametrize("host", ["0.0.0.0", "8.8.8.8", "::1", "invalid-host"])
def test_invalid_admin_host_blocks_deployment(deployment, host):
    env = deployment / ".env"
    env.write_text(env.read_text().replace("192.168.1.20", host))
    with pytest.raises(DeploymentError, match="私有 IPv4"):
        prepare(deployment, "/usr/bin/uv", pwd.getpwuid(os.getuid()).pw_name)


def test_tracked_release_package_contains_complete_runtime_library(tmp_path):
    materials = tmp_path / "materials"
    materials.mkdir()
    for name in ("library-v1.zip", "library-v1.zip.sha256"):
        (materials / name).symlink_to(ROOT / "materials" / name)
    target = initialize_library(tmp_path)
    library = Library(target)
    try:
        assert len(library.candidates()) == 245
    finally:
        library.close()
    assert (target / "images/v06-p088-manual-9c6ecdf5-9300-4006-8a48-c51279382a9e.png").is_file()


@pytest.mark.parametrize("missing,apt_exit", [("", 0), ("curl", 0), ("curl", 1)])
def test_system_dependencies_skip_apt_when_installed(tmp_path, missing, apt_exit):
    # 执行脚本中的真实依赖安装段，模拟 dpkg 状态及 apt 失败；不修改本机系统。
    block = (ROOT / "deploy/install.sh").read_text().split("sudo -v\n", 1)[1].split("\nif command -v uv", 1)[0]
    commands = tmp_path / "commands"
    commands.mkdir()
    query = commands / "dpkg-query"
    query.write_text('#!/bin/bash\nif [[ "${@: -1}" == "$TEST_MISSING" ]]; then exit 1; fi\nprintf "install ok installed"\n')
    sudo = commands / "sudo"
    sudo.write_text('#!/bin/bash\nprintf "%s\\n" "$*" >> "$TEST_APT_LOG"\nexit "$TEST_APT_EXIT"\n')
    query.chmod(0o755)
    sudo.chmod(0o755)
    log = tmp_path / "apt.log"
    env = {**os.environ, "PATH": str(commands) + ":" + os.environ["PATH"],
           "TEST_MISSING": missing, "TEST_APT_LOG": str(log), "TEST_APT_EXIT": str(apt_exit)}
    result = subprocess.run(["bash", "-c", "set -euo pipefail\n" + block], env=env, capture_output=True, text=True)
    if not missing:
        assert result.returncode == 0
        assert not log.exists()
        assert "跳过 apt" in result.stdout
    elif apt_exit:
        assert result.returncode == 1
        assert "apt/dpkg" in result.stderr
    else:
        assert result.returncode == 0
        assert log.read_text().splitlines() == ["apt-get update", "apt-get install -y curl"]
