import shutil
import sqlite3
from types import SimpleNamespace

import pytest
import pytest_asyncio
from aiohttp.test_utils import TestClient, TestServer

from kurumibot.admin import create_app
from kurumibot.config import Config, ROOT
from kurumibot.history import History
from kurumibot.selection import Selection


@pytest.fixture
def admin_fixture(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    ids = ("v01-p009-auto-01", "v03-p069-auto-01", "v05-p058-manual-450f8c59-4154-4dd1-ad3a-1b8913f3d41b")
    with sqlite3.connect(ROOT / "materials/library-v1/library.sqlite3") as source, sqlite3.connect(root / "library.sqlite3") as target:
        target.execute(source.execute("SELECT sql FROM sqlite_master WHERE name='materials'").fetchone()[0])
        for material_id in ids:
            row = source.execute("SELECT * FROM materials WHERE id=?", (material_id,)).fetchone()
            target.execute("INSERT INTO materials VALUES(" + ",".join("?" for _ in row) + ")", row)
            image_path = source.execute("SELECT image_path FROM materials WHERE id=?", (material_id,)).fetchone()[0]
            (root / image_path).parent.mkdir(exist_ok=True, parents=True)
            shutil.copyfile(ROOT / "materials/library-v1" / image_path, root / image_path)
    default = tmp_path / "default.png"
    shutil.copyfile(ROOT / "pics_sample/1.png", default)
    config = Config("test-app", "test-qq-secret", "test-api-key", library=root,
                    default_image=default, history=tmp_path / "history.sqlite3")
    env = tmp_path / ".env"
    env.write_text("# Keep this comment\nKURUMI_ADMIN_USERNAME=admin\nKURUMI_ADMIN_PASSWORD=test-admin-password\n"
                   "CODEX_OAUTH_PROXY_API_KEY=test-api-key\nQQ_APP_ID=test-app\nQQ_APP_SECRET=test-qq-secret\nUNRELATED='preserve me'\n")
    history = History(config.history)
    event = SimpleNamespace(chat_scope="c2c", chat_id="test-user", user_id="test-user", message_id="one")
    conversation_id = history.claim(event, "老板又让我加班，烦死了")
    history.finish(conversation_id, status="sent", material_id=ids[-1], image_path=str(root / "images" / (ids[-1] + ".png")), elapsed_seconds=2.5, attempts=1)
    history.usage(conversation_id, Selection("deepseek-flash", 3, material_id=ids[-1], scene="加班烦躁", reason="想辞职的共鸣",
                                           usage={"prompt_tokens":1000,"completion_tokens":20,"total_tokens":1020,"prompt_cache_hit_tokens":400}, elapsed_seconds=1.2))
    event.message_id = "pending"
    pending = history.claim(event, "处理中消息")
    history.close()
    return SimpleNamespace(config=config, env=env, ids=ids, pending=pending)


@pytest_asyncio.fixture
async def admin_client(admin_fixture):
    async with TestClient(TestServer(create_app(admin_fixture.config, admin_fixture.env))) as client:
        yield client


async def authenticate(client):
    response = await client.post('/api/login', json={"username":"admin","password":"test-admin-password"})
    assert response.status == 200
    result = await response.json()
    return {"X-CSRF-Token": result["csrf"]}
