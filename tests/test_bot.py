import asyncio
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from kurumibot.config import Config
from kurumibot.history import History
from kurumibot.library import Library
from kurumibot.qq import QQSender, parse_inbound
from kurumibot.selection import Selection, Selector
from kurumibot.service import BotService


@pytest.fixture
def setup_bot(tmp_path):
    image = tmp_path / "image.png"
    image.write_bytes(b"test-image")
    default = tmp_path / "default.png"
    default.write_bytes(b"fallback")
    db = sqlite3.connect(tmp_path / "library.sqlite3")
    db.execute("CREATE TABLE materials(id TEXT PRIMARY KEY,image_path TEXT,quote_traditional TEXT,"
               "quote_simplified TEXT,emotion_tags TEXT,meaning TEXT,scenarios TEXT,enabled INTEGER)")
    db.execute("INSERT INTO materials VALUES('a','image.png','賺爆','赚爆','[\"兴奋\"]','自信','赚钱',1)")
    db.commit()
    config = Config("appid", "secret", "key", library=tmp_path, default_image=default,
                    history=tmp_path / "history.sqlite3", user_interval=0, send_timeout=.02)
    library, history = Library(tmp_path), History(config.history)
    selector = SimpleNamespace(select=AsyncMock(return_value=Selection("test", 1, material_id="a")))
    sender = SimpleNamespace(send=AsyncMock(return_value={"id": "sent"}), retryable=lambda _: True)
    service = BotService(config, library, history, selector, sender, lambda *a, **k: None)
    yield SimpleNamespace(config=config, library=library, history=history, selector=selector,
                          sender=sender, service=service, db=db, image=image)
    history.close()
    library.close()
    db.close()


def event(message="m", user="u", content="今天要赚爆了", scope="c2c", chat=None):
    return SimpleNamespace(chat_scope=scope, chat_id=chat or user, user_id=user, message_id=message, content=content)


def last_row(s):
    return dict(s.history.db.execute("SELECT * FROM conversations ORDER BY id DESC LIMIT 1").fetchone())


async def test_reply_and_deduplicate_across_restart(setup_bot):
    s = setup_bot
    await asyncio.gather(s.service.handle(event()), s.service.handle(event()))
    assert s.sender.send.await_count == s.selector.select.await_count == 1
    assert last_row(s)["status"] == "sent"
    assert last_row(s)["material_id"] == "a"
    s.history.close()
    s.history = History(s.config.history)
    s.service.history = s.history
    await s.service.handle(event())
    assert s.sender.send.await_count == 1
    assert s.history.db.execute("SELECT count(*) FROM ai_usage").fetchone()[0] == 1


@pytest.mark.parametrize("selection,fallback", [
    (Selection("test", 1), "no_match"),
    (Selection("test", 1, error_type="TimeoutError"), "ai_TimeoutError"),
    (Selection("test", 1, material_id="not-a-candidate"), "material_unavailable"),
])
async def test_selection_fallback(setup_bot, selection, fallback):
    s = setup_bot
    s.selector.select.return_value = selection
    await s.service.handle(event())
    assert s.sender.send.call_args.args[1] == s.image
    assert last_row(s)["fallback_reason"] == fallback


@pytest.mark.parametrize("change", ["disable", "delete"])
async def test_recheck_material_after_ai(setup_bot, change):
    s = setup_bot

    async def select(*_):
        if change == "disable":
            s.db.execute("UPDATE materials SET enabled=0")
            s.db.commit()
        else:
            s.image.unlink()
        return Selection("test", 1, material_id="a")

    s.selector.select.side_effect = select
    await s.service.handle(event())
    assert last_row(s)["fallback_reason"] == "material_unavailable"
    assert last_row(s)["error_type"] == "no_available_images"
    s.sender.send.assert_not_awaited()


async def test_empty_library_does_not_kill_next_message(setup_bot):
    s = setup_bot
    s.db.execute("UPDATE materials SET enabled=0")
    s.db.commit()
    await s.service.handle(event(content=""))
    assert last_row(s)["error_type"] == "no_available_images"
    s.sender.send.assert_not_awaited()
    s.db.execute("UPDATE materials SET enabled=1")
    s.db.commit()
    await s.service.handle(event(message="next"))
    assert last_row(s)["status"] == "sent"


async def test_rate_limit_and_ignore_unsupported_scope(setup_bot):
    s = setup_bot
    s.config.user_interval = 5
    await s.service.handle(event())
    await s.service.handle(event(message="second"))
    assert last_row(s)["status"] == "rate_limited"
    await s.service.handle(event(message="guild", scope="guild"))
    assert s.sender.send.await_count == 1
    await s.service.handle(event(message="other", user="other"))
    assert s.sender.send.await_count == 2


async def test_concurrency_limit_skips_instead_of_queueing(setup_bot):
    s = setup_bot
    s.config.ai_concurrency = 1
    entered, release = asyncio.Event(), asyncio.Event()

    async def select(*_):
        entered.set()
        await release.wait()
        return Selection("test", 1, material_id="a")

    s.selector.select.side_effect = select
    first = asyncio.create_task(s.service.handle(event()))
    await entered.wait()
    await s.service.handle(event(message="other", user="other"))
    assert last_row(s)["status"] == "busy"
    release.set()
    await first
    assert s.sender.send.await_count == 1
    assert s.service.active == 0


async def test_send_timeout_has_finite_retries(setup_bot):
    s = setup_bot

    async def send(*_):
        await asyncio.sleep(1)

    s.sender.send.side_effect = send
    await s.service.handle(event())
    assert last_row(s)["status"] == "failed"
    assert last_row(s)["error_type"] == "TimeoutError"
    assert s.sender.send.await_count == 2


@pytest.mark.parametrize("scope", ["c2c", "group"])
async def test_retry_success_and_same_reply_identity(setup_bot, scope):
    s = setup_bot
    send = AsyncMock(side_effect=[RuntimeError("temporary"), {"id": "sent"}])
    unused = AsyncMock()
    api = SimpleNamespace(post_c2c_message=send if scope == "c2c" else unused,
                          post_group_message=send if scope == "group" else unused)
    uploader = SimpleNamespace(upload=AsyncMock(return_value="credential"))
    s.service.sender = QQSender(api, uploader)
    await s.service.handle(event(scope=scope, chat="target"))
    assert last_row(s)["status"] == "sent"
    assert last_row(s)["attempts"] == 2
    messages = [call.args[1] for call in send.call_args_list]
    assert all(m.msg_id == "m" and m.msg_seq == 1 for m in messages)
    assert all(call.args[0] == "target" for call in send.call_args_list)
    assert all(call.kwargs["chat_type"] == scope and call.kwargs["chat_id"] == "target"
               for call in uploader.upload.call_args_list)
    unused.assert_not_awaited()


@pytest.mark.parametrize("content,cleaned", [
    (" @久留美  今天亏麻了 ", "今天亏麻了"),
    (" <@!12345> 今天亏麻了 ", "今天亏麻了"),
    ("<@12345>今天亏麻了", "今天亏麻了"),
    ("<@!12345>", ""),
    ("@久留美", ""),
])
def test_group_at_event_parse(content, cleaned):
    parsed = parse_inbound("GROUP_AT_MESSAGE_CREATE", {
        "id": "group-message", "group_openid": "test-group",
        "author": {"member_openid": "test-member"}, "content": content,
    })
    assert parsed.chat_scope == "group"
    assert parsed.chat_id == "test-group"
    assert parsed.user_id == "test-member"
    assert parsed.message_id == "group-message"
    assert parsed.content == cleaned


@pytest.mark.parametrize("event_type", ["GROUP_MESSAGE_CREATE", "AT_MESSAGE_CREATE", "GROUP_ADD_ROBOT", "READY"])
def test_only_c2c_and_group_at_events_accepted(event_type):
    assert parse_inbound(event_type, {"id": "m", "group_openid": "g", "content": "@久留美 测试"}) is None


@pytest.mark.parametrize("content", ["<@!12345> 今天亏麻了", "<@!12345>"])
async def test_group_at_full_pipeline(setup_bot, content):
    s = setup_bot
    api = SimpleNamespace(post_c2c_message=AsyncMock(), post_group_message=AsyncMock(return_value={"id": "group-reply"}))
    uploader = SimpleNamespace(upload=AsyncMock(return_value="credential"))
    s.service.sender = QQSender(api, uploader)
    inbound = parse_inbound("GROUP_AT_MESSAGE_CREATE", {
        "id": "group-message", "group_openid": "test-group",
        "author": {"member_openid": "test-member"}, "content": content,
    })
    await s.service.handle(inbound)
    await s.service.handle(inbound)
    assert api.post_group_message.await_count == uploader.upload.await_count == 1
    api.post_c2c_message.assert_not_awaited()
    assert uploader.upload.call_args.kwargs["chat_id"] == "test-group"
    assert uploader.upload.call_args.kwargs["chat_type"] == "group"
    assert api.post_group_message.call_args.args[0] == "test-group"
    assert api.post_group_message.call_args.args[1].msg_id == "group-message"
    assert last_row(s)["scope"] == "group"
    assert last_row(s)["status"] == "sent"
    if inbound.content:
        assert s.selector.select.call_args.args[0] == "今天亏麻了"
    else:
        s.selector.select.assert_not_awaited()
        assert last_row(s)["fallback_reason"] == "empty_message"
        assert uploader.upload.call_args.kwargs["source"] == str(s.image)


async def test_dedup_scoped_to_channel_and_conversation(setup_bot):
    s = setup_bot
    messages = [event(scope="group", chat="group-1"), event(scope="group", chat="group-2"), event(chat="group-1")]
    for inbound in messages + messages:
        await s.service.handle(inbound)
    assert s.selector.select.await_count == s.sender.send.await_count == 3
    assert s.history.db.execute("SELECT count(*) FROM conversations").fetchone()[0] == 3


async def test_group_rate_limit_per_user_across_groups(setup_bot):
    s = setup_bot
    s.config.user_interval = 5
    await s.service.handle(event(scope="group", chat="group-1"))
    await s.service.handle(event(message="next", scope="group", chat="group-2"))
    assert last_row(s)["status"] == "rate_limited"
    await s.service.handle(event(message="other", user="other-user", scope="group", chat="group-1"))
    assert last_row(s)["status"] == "sent"
    assert s.sender.send.await_count == 2


@pytest.mark.parametrize("parsed,finish,error", [
    ({"id": "a", "scene": "赚钱", "reason": "自信"}, "stop", None),
    ({"id": None, "scene": "不匹配", "reason": "无合适台词"}, "stop", None),
    ({"id": "unknown", "scene": "", "reason": ""}, "stop", "invalid_selection_id"),
    ({"id": "null", "scene": "", "reason": ""}, "stop", "invalid_selection_id"),
    ({"id": ["a"], "scene": "", "reason": ""}, "stop", "invalid_selection_id"),
    ({"id": "a"}, "stop", "invalid_selection_metadata"),
    ({"id": "a", "scene": None, "reason": ""}, "stop", "invalid_selection_metadata"),
    ({"id": "a", "scene": "", "reason": ""}, "length", "selection_output_truncated"),
    ({"id": "a", "scene": "", "reason": ""}, "content_filter", "incomplete_selection"),
])
async def test_model_output_validation(setup_bot, parsed, finish, error):
    s = setup_bot

    def response(request):
        body = json.loads(request.content)
        assert len(body["messages"]) == 2
        assert "image_path" not in body["messages"][1]["content"]
        return httpx.Response(200, json={"choices": [{"finish_reason": finish,
            "message": {"content": json.dumps(parsed)}}], "usage": {"total_tokens": 123}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as http:
        result = await Selector(http, s.config).select("测试", s.library.candidates())
    assert result.error_type == error
    assert result.usage == {"total_tokens": 123}
    if error:
        assert result.material_id is None


@pytest.mark.parametrize("mode", ["timeout", "http", "malformed"])
async def test_ai_failures_are_safe_and_bounded(setup_bot, mode):
    s = setup_bot
    s.config.ai_timeout = .01

    async def response(request):
        if mode == "timeout":
            await asyncio.sleep(1)
        if mode == "http":
            return httpx.Response(401, text="secret-api-body")
        return httpx.Response(200, json={"choices": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as http:
        result = await Selector(http, s.config).select("测试", s.library.candidates())
    assert result.error_type in ("TimeoutError", "http_401", "IndexError")
    assert "secret" not in result.error_type


async def test_unexpected_value_error_does_not_leak_message(setup_bot):
    s = setup_bot

    def response(request):
        raise ValueError("provider-body-with-secret")

    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as http:
        result = await Selector(http, s.config).select("测试", s.library.candidates())
    assert result.error_type == "ValueError"


def test_path_traversal_and_disabled_excluded(setup_bot, tmp_path):
    s = setup_bot
    s.db.execute("UPDATE materials SET image_path='../outside.png'")
    s.db.commit()
    (tmp_path.parent / "outside.png").write_bytes(b"outside")
    assert s.library.candidates() == []
    assert s.library.selected_image("a", {"a"}) is None
    s.db.execute("UPDATE materials SET image_path='image.png',enabled=0")
    s.db.commit()
    assert s.library.candidates() == []


def test_published_library():
    library = Library(Path(__file__).resolve().parents[1] / "materials/library-v1")
    try:
        candidates = library.candidates()
        assert len(candidates) == 245
        assert len({c["id"] for c in candidates}) == 245
        assert all(library.selected_image(c["id"], {c["id"]}).is_file() for c in candidates)
    finally:
        library.close()


async def test_recent_replies_scoped_bounded_and_persistent(setup_bot):
    s = setup_bot
    for i in range(12):
        await s.service.handle(event(message=str(i), content=f"留言{i}"))
    replies = s.selector.select.call_args.args[2]
    assert len(replies) == 10
    assert [r["message"] for r in replies] == [f"留言{i}" for i in range(1, 11)]
    assert all(r["material_id"] == "a" for r in replies)
    s.history.close()
    s.history = History(s.config.history)
    s.service.history = s.history
    assert len(s.history.recent_replies(event())) == 10
    assert s.history.recent_replies(event(scope="group", chat="u")) == []
    assert s.history.recent_replies(event(user="other")) == []
    skipped = s.history.claim(event(message="failed"), "失败留言")
    s.history.finish(skipped, status="failed", material_id="a")
    assert s.history.recent_replies(event())[-1]["message"] == "留言11"


async def test_random_fallback_avoids_recent_and_records_id(setup_bot):
    s = setup_bot
    s.db.execute("INSERT INTO materials SELECT 'b',image_path,quote_traditional,quote_simplified,"
                 "emotion_tags,meaning,scenarios,enabled FROM materials WHERE id='a'")
    s.db.commit()
    await s.service.handle(event())
    await s.service.handle(event(message="empty", content=""))
    assert last_row(s)["material_id"] == "b"
    assert s.history.recent_replies(event())[-1]["fallback_reason"] == "empty_message"
    assert s.library.random_image({"a", "b"})[0] in {"a", "b"}
    s.db.execute("UPDATE materials SET enabled=0 WHERE id='b'")
    s.db.commit()
    assert s.library.random_image()[0] == "a"


async def test_selector_receives_reply_context(setup_bot):
    s = setup_bot
    replies = [{"message": "前一句", "material_id": "a", "scene": "兴奋", "reason": "接梗"}]

    def response(request):
        body = json.loads(request.content)
        payload = json.loads(body["messages"][1]["content"])
        assert payload["recent_replies"] == replies
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop",
            "message": {"content": json.dumps({"id": "a", "scene": "", "reason": ""})}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as http:
        result = await Selector(http, s.config).select("测试", s.library.candidates(), replies)
    assert result.error_type is None
