"""REST and WebSocket behaviour: auth, access control, history, reports, exports, handoff."""
from __future__ import annotations

import json

import numpy as np
import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.models.entities import ConversationMessage
from app.services.audio.pcm import VAD_FRAME_SAMPLES, float_to_pcm16
from app.websocket import voice

LOUD = float_to_pcm16(np.full(VAD_FRAME_SAMPLES, 0.5, dtype=np.float32))
QUIET = float_to_pcm16(np.zeros(VAD_FRAME_SAMPLES, dtype=np.float32))


async def signup(client, email="a@example.com"):
    response = await client.post("/api/v1/auth/register", json={"email": email, "password": "correct-horse"})
    assert response.status_code == 201
    return {"Authorization": f"Bearer {response.json()['access_token']}"}, response.json()


async def make_session(client, headers, database, texts=("Hello", "Hi there")):
    session = (await client.post("/api/v1/sessions", json={}, headers=headers)).json()
    async with database() as db:
        for index, text in enumerate(texts):
            db.add(
                ConversationMessage(
                    session_id=session["id"],
                    speaker="user" if index % 2 == 0 else "assistant",
                    content=text,
                    sequence_number=index + 1,
                    status="completed",
                    interrupted=False,
                )
            )
        await db.commit()
    return session["id"]


async def test_register_login_and_me(client):
    headers, _ = await signup(client)
    assert (await client.get("/api/v1/auth/me", headers=headers)).json()["email"] == "a@example.com"
    good = await client.post("/api/v1/auth/login", json={"email": "A@example.com", "password": "correct-horse"})
    assert good.status_code == 200
    bad = await client.post("/api/v1/auth/login", json={"email": "a@example.com", "password": "wrong-password"})
    assert bad.status_code == 401


async def test_password_is_hashed(client, database):
    from sqlalchemy import select

    from app.models.entities import User

    await signup(client)
    async with database() as db:
        user = (await db.execute(select(User))).scalar_one()
    assert "correct-horse" not in user.password_hash and user.password_hash.startswith("$argon2")


async def test_duplicate_email_and_weak_input_are_rejected(client):
    await signup(client)
    dup = await client.post("/api/v1/auth/register", json={"email": "a@example.com", "password": "correct-horse"})
    assert dup.status_code == 409
    short = await client.post("/api/v1/auth/register", json={"email": "b@example.com", "password": "short"})
    assert short.status_code == 422
    invalid = await client.post("/api/v1/auth/register", json={"email": "nope", "password": "correct-horse"})
    assert invalid.status_code == 422


async def test_repeated_failed_logins_are_throttled(client):
    from app.core.security import login_throttle

    await signup(client, "t@example.com")
    login_throttle._failures.clear()
    statuses = [
        (await client.post("/api/v1/auth/login", json={"email": "t@example.com", "password": "wrong-password"})).status_code
        for _ in range(9)
    ]
    assert statuses[-1] == 429
    login_throttle._failures.clear()


async def test_endpoints_require_auth(client):
    for path in ("/api/v1/sessions", "/api/v1/agent/settings", "/api/v1/models", "/api/v1/voices"):
        assert (await client.get(path)).status_code == 401
    bad = await client.get("/api/v1/sessions", headers={"Authorization": "Bearer not-a-token"})
    assert bad.status_code == 401


async def test_users_cannot_touch_each_others_sessions(client, database):
    alice, _ = await signup(client, "alice@example.com")
    bob, _ = await signup(client, "bob@example.com")
    session_id = await make_session(client, alice, database)

    for method, path, body in [
        ("get", f"/api/v1/sessions/{session_id}", None),
        ("get", f"/api/v1/sessions/{session_id}/messages", None),
        ("get", f"/api/v1/sessions/{session_id}/report", None),
        ("get", f"/api/v1/sessions/{session_id}/export/txt", None),
        ("patch", f"/api/v1/sessions/{session_id}", {"title": "stolen"}),
        ("post", f"/api/v1/sessions/{session_id}/handoff", {"device_id": "evil"}),
        ("post", f"/api/v1/sessions/{session_id}/end", None),
        ("delete", f"/api/v1/sessions/{session_id}", None),
    ]:
        kwargs = {"headers": bob}
        if body:
            kwargs["json"] = body
        assert (await client.request(method, path, **kwargs)).status_code == 404, path
    assert (await client.get("/api/v1/sessions", headers=bob)).json() == []
    assert (await client.get(f"/api/v1/sessions/{session_id}", headers=alice)).status_code == 200


async def test_history_search_rename_delete(client, database):
    headers, _ = await signup(client)
    a = await make_session(client, headers, database, ("Tell me about whales", "They are big"))
    b = await make_session(client, headers, database, ("Tell me about cats", "They purr"))
    assert len((await client.get("/api/v1/sessions", headers=headers)).json()) == 2

    found = (await client.get("/api/v1/sessions", params={"q": "whales"}, headers=headers)).json()
    assert [s["id"] for s in found] == [a]
    assert found[0]["message_count"] == 2
    # Search metacharacters are literal text, not wildcards.
    assert (await client.get("/api/v1/sessions", params={"q": "%"}, headers=headers)).json() == []

    renamed = await client.patch(f"/api/v1/sessions/{b}", json={"title": "Cats"}, headers=headers)
    assert renamed.json()["title"] == "Cats"
    assert (await client.delete(f"/api/v1/sessions/{b}", headers=headers)).status_code == 204
    assert (await client.get(f"/api/v1/sessions/{b}", headers=headers)).status_code == 404
    async with database() as db:
        from sqlalchemy import select

        remaining = (await db.execute(select(ConversationMessage))).scalars().all()
    assert all(m.session_id == a for m in remaining), "messages of deleted session are removed"


async def test_report_and_exports_use_saved_data_only(client, database):
    headers, _ = await signup(client)
    session_id = await make_session(client, headers, database, ("<b>Hello</b> & co", "Hi there"))
    report = (await client.get(f"/api/v1/sessions/{session_id}/report", headers=headers)).json()
    assert report["session"]["user_messages"] == 1
    assert report["session"]["ai_responses"] == 1
    assert report["cost"]["external_api_cost_usd"] == 0
    assert report["performance"]["average_response_latency_ms"] is None, "no measurement, no number"
    assert report["cost"]["electricity_estimate"] is None

    txt = await client.get(f"/api/v1/sessions/{session_id}/export/txt", headers=headers)
    assert "Hi there" in txt.text and "attachment" in txt.headers["content-disposition"]
    data = await client.get(f"/api/v1/sessions/{session_id}/export/json", headers=headers)
    assert data.json()["transcript"][1]["content"] == "Hi there"
    pdf = await client.get(f"/api/v1/sessions/{session_id}/export/pdf", headers=headers)
    assert pdf.content.startswith(b"%PDF"), "markup in a transcript must not break the PDF"
    assert (await client.get(f"/api/v1/sessions/{session_id}/export/exe", headers=headers)).status_code == 422


async def test_agent_settings_round_trip_and_validation(client):
    headers, _ = await signup(client)
    defaults = (await client.get("/api/v1/agent/settings", headers=headers)).json()
    assert defaults["attention_mode"] == "always"
    body = {**defaults, "language": "ur", "attention_mode": "wake_word", "personality": "Calm.", "vad_sensitivity": 0.8}
    assert (await client.put("/api/v1/agent/settings", json=body, headers=headers)).status_code == 200
    assert (await client.get("/api/v1/agent/settings", headers=headers)).json()["language"] == "ur"
    bad = {**body, "vad_sensitivity": 7}
    assert (await client.put("/api/v1/agent/settings", json=bad, headers=headers)).status_code == 422


async def test_health_reports_components(client):
    data = (await client.get("/api/v1/health")).json()
    assert data["database"]["state"] == "ready"
    assert data["ollama"]["models"] == ["scripted-llm"]


# ---- WebSocket ---------------------------------------------------------------------


def wait_event(ws, kind, limit=400):
    """Read frames until an event of this type arrives, acknowledging audio as the browser would."""
    seen = []
    for _ in range(limit):
        message = ws.receive()
        if message["type"] == "websocket.close":
            raise AssertionError(f"socket closed ({message.get('code')}) waiting for {kind}; saw {seen}")
        if message.get("text"):
            event = json.loads(message["text"])
            seen.append(event["type"])
            if event["type"] == "agent.audio.chunk":
                ws.send_text(json.dumps({
                    "type": "playback.progress", "response_id": event["response_id"],
                    "chunk_seq": event["chunk_seq"], "played_ms": event["duration_ms"],
                }))
            if event["type"] == kind:
                return event
    raise AssertionError(f"{kind} not received; saw {seen}")


def speak(ws):
    for _ in range(20):
        ws.send_bytes(LOUD)
    for _ in range(40):
        ws.send_bytes(QUIET)


@pytest.fixture
def setup_ws(app, database):
    voice.hub.sessions.clear()
    with TestClient(app) as http:
        token = http.post(
            "/api/v1/auth/register", json={"email": "ws@example.com", "password": "correct-horse"}
        ).json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        session_id = http.post("/api/v1/sessions", json={}, headers=headers).json()["id"]
        yield http, token, headers, session_id


def start_message(token, session_id, device="web-1"):
    return json.dumps({"type": "session.start", "token": token, "session_id": session_id, "device_id": device})


def test_ws_rejects_bad_token_and_other_users_session(setup_ws):
    http, token, _, session_id = setup_ws
    with http.websocket_connect("/ws/v1/voice") as ws:
        ws.send_text(start_message("garbage", session_id))
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_text()
        assert closed.value.code == voice.CLOSE_UNAUTHORIZED

    other = http.post(
        "/api/v1/auth/register", json={"email": "other@example.com", "password": "correct-horse"}
    ).json()["access_token"]
    with http.websocket_connect("/ws/v1/voice") as ws:
        ws.send_text(start_message(other, session_id))
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_text()
        assert closed.value.code == voice.CLOSE_FORBIDDEN


def test_ws_full_turn_persists_transcript_and_report(setup_ws):
    http, token, headers, session_id = setup_ws
    with http.websocket_connect("/ws/v1/voice") as ws:
        ws.send_text(start_message(token, session_id))
        assert wait_event(ws, "session.started")["external_api_cost"] == 0
        speak(ws)
        assert wait_event(ws, "transcript.final")["text"] == "hello there"
        completed = wait_event(ws, "agent.response.completed")
        assert completed["interrupted"] is False
        ws.send_text(json.dumps({"type": "session.end"}))
        wait_event(ws, "session.ended")

    report = http.get(f"/api/v1/sessions/{session_id}/report", headers=headers).json()
    assert report["session"]["status"] == "ended"
    assert report["session"]["end_time"] is not None
    assert [m["speaker"] for m in report["transcript"]] == ["user", "assistant"]
    assert report["performance"]["average_response_latency_ms"] > 0
    assert report["cost"]["stt_model"] == "scripted-stt"


def test_ws_reconnect_after_unexpected_drop_keeps_context(setup_ws):
    http, token, headers, session_id = setup_ws
    with http.websocket_connect("/ws/v1/voice") as ws:
        ws.send_text(start_message(token, session_id))
        wait_event(ws, "session.started")
        speak(ws)
        wait_event(ws, "agent.response.completed")
    # The socket dropped without session.end, as with a refresh or lost network.
    live = voice.hub.get(session_id)
    assert live is not None and live.socket is None

    with http.websocket_connect("/ws/v1/voice") as ws:
        ws.send_text(start_message(token, session_id))
        started = wait_event(ws, "session.started")
        assert started["resumed"] is True
        assert len(voice.hub.get(session_id).engine.history) == 2
        ws.send_text(json.dumps({"type": "session.end"}))
        wait_event(ws, "session.ended")


def test_ws_second_device_is_refused_until_handoff(setup_ws):
    http, token, headers, session_id = setup_ws
    with http.websocket_connect("/ws/v1/voice") as web:
        web.send_text(start_message(token, session_id, "web-1"))
        wait_event(web, "session.started")

        with http.websocket_connect("/ws/v1/voice") as phone:
            phone.send_text(start_message(token, session_id, "phone-1"))
            with pytest.raises(WebSocketDisconnect) as closed:
                phone.receive_text()
            assert closed.value.code == voice.CLOSE_DEVICE_CONFLICT

        result = http.post(f"/api/v1/sessions/{session_id}/handoff", json={"device_id": "phone-1"}, headers=headers)
        assert result.json() == {"id": session_id, "owner_device_id": "phone-1", "previous_device_id": "web-1"}
        wait_event(web, "session.handoff")
        for _ in range(50):
            frame = web.receive()
            if frame["type"] == "websocket.close":
                break
        assert frame["type"] == "websocket.close" and frame["code"] == voice.CLOSE_HANDOFF

    with http.websocket_connect("/ws/v1/voice") as phone:
        phone.send_text(start_message(token, session_id, "phone-1"))
        assert wait_event(phone, "session.started")["session_id"] == session_id
        phone.send_text(json.dumps({"type": "session.end"}))
        wait_event(phone, "session.ended")


def test_ws_malformed_control_message_does_not_kill_the_session(setup_ws):
    http, token, _, session_id = setup_ws
    with http.websocket_connect("/ws/v1/voice") as ws:
        ws.send_text(start_message(token, session_id))
        wait_event(ws, "session.started")
        ws.send_text("{not json")
        assert wait_event(ws, "error")["code"] == "bad_message"
        ws.send_text(json.dumps({"type": "ping", "t": 5}))
        assert wait_event(ws, "pong")["t"] == 5
        ws.send_text(json.dumps({"type": "session.end"}))
        wait_event(ws, "session.ended")


def test_ws_pause_resume_control_messages(setup_ws):
    http, token, _, session_id = setup_ws
    with http.websocket_connect("/ws/v1/voice") as ws:
        ws.send_text(start_message(token, session_id))
        wait_event(ws, "session.started")
        ws.send_text(json.dumps({"type": "session.pause"}))
        assert wait_event(ws, "session.paused")["listening_for_resume"] is False
        ws.send_text(json.dumps({"type": "session.resume"}))
        wait_event(ws, "session.resumed")
        ws.send_text(json.dumps({"type": "session.end"}))
        wait_event(ws, "session.ended")
