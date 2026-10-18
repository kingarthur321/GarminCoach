"""Allowlisted localhost dashboard with bounded on-demand coaching."""
import json
import secrets
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs
from zoneinfo import ZoneInfo
from .core import ROOT, Invalid, now, require, weekly_distance
from .workouts import Workouts, session_key
from .dashboard import saved_details
from .chat import CoachChat
from .anthropic_api import key_available, model_name
from .maps import TileCache, preferences, save_preferences
from .memory import create as create_memory, update as update_memory, delete as delete_memory, read_memory
from .mcp import MCP
from .proposals import review_chat_proposal, session_id_for_chat
from .planned import import_planned, read_planned

STATIC = {"/map.js": ("map.js", "text/javascript; charset=utf-8"), "/": ("index.html", "text/html; charset=utf-8"), "/app.js": ("app.js", "text/javascript; charset=utf-8"), "/style.css": ("style.css", "text/css; charset=utf-8")}


def make_server(store, port=8765, chat_executor=None, workout_client_factory=None, workout_read_factory=None):
    token = secrets.token_urlsafe(32)
    def publish_requested_workout(chat_id):
        session = review_chat_proposal(store, chat_id, auto_request=True)
        with (workout_client_factory() if workout_client_factory else MCP(writes=True)) as client:
            entry = Workouts(store, client).publish(session["id"])
        return {"state": entry["state"], "session_id": session["id"]}
    chat = CoachChat(store, executor=chat_executor, workout_publisher=publish_requested_workout)
    tiles = TileCache(store)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass  # Never log personal request contents.

        def send(self, status, content, mime="application/json; charset=utf-8"):
            blob = content if isinstance(content, bytes) else json.dumps(content, ensure_ascii=False, allow_nan=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(blob)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
            try:
                self.end_headers()
                self.wfile.write(blob)
            except (BrokenPipeError, ConnectionResetError):
                pass  # Navigation can cancel in-flight tiles; the private cache remains valid.

        def allowed_host(self):
            return self.headers.get("Host") in (f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}")

        def do_GET(self):
            if not self.allowed_host():
                return self.send(403, {"error": "Local host required"})
            route = urlsplit(self.path).path
            if route.startswith('/api/map/tile/'):
                local_origins = (f'http://127.0.0.1:{self.server.server_port}', f'http://localhost:{self.server.server_port}')
                referer = self.headers.get('Referer', '')
                parsed = urlsplit(referer)
                if f'{parsed.scheme}://{parsed.netloc}' not in local_origins:
                    return self.send(403, {'error': 'Local map request required'})
                try:
                    return self.send(200, tiles.get(route, referer), 'image/png')
                except Invalid as exc:
                    return self.send(503, {'error': str(exc)})
            if route in STATIC:
                filename, mime = STATIC[route]
                return self.send(200, (ROOT / "web" / filename).read_bytes(), mime)
            if route == "/api/coach/job":
                try:
                    return self.send(200, chat.get(parse_qs(urlsplit(self.path).query).get("id", [None])[0]))
                except Invalid as exc:
                    return self.send(404, {"error": str(exc)})
            if route != "/api/dashboard":
                return self.send(404, {"error": "Unknown endpoint"})
            try:
                with store.lock():
                    bundle = store.current()
                    profile = store.profile()
                    registry = store.read("workout_registry")
                    payload = {"profile": profile, "snapshot": bundle, "feedback": store.read("feedback"), "registry": registry,
                               "status": store.read("run_status"), "weekly_distance": weekly_distance(bundle["activities"]) if bundle else [], "server_time": now(), "csrf_token": token,
                               "session_keys": {s["id"]: session_key(s) for s in bundle["training_plan"]["sessions"]} if bundle else {},
                               "map": preferences(store), "details": saved_details(store, bundle), "import_report": store.read("garmin_import_report"), "coach_chat": store.read("coach_chat", {"items": []}),
                               "athlete_memory": read_memory(store),
                               "garmin_planned_runs": read_planned(store),
                               "coach_connection": {"provider": "Claude API", "model": model_name(), "configured": key_available() or chat_executor is not None, "active_job": chat.current_job()}}
                self.send(200, payload)
            except (Invalid, ValueError, KeyError, TypeError):
                self.send(500, {"error": "Saved state invalid; previous files retained. Run python3 -m coach validate."})

        def do_POST(self):
            origin = self.headers.get("Origin")
            local_origins = (f"http://127.0.0.1:{self.server.server_port}", f"http://localhost:{self.server.server_port}")
            if not self.allowed_host() or origin not in local_origins or self.headers.get("X-Coach-Token") != token:
                return self.send(403, {"error": "Same-origin dashboard request required"})
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                return self.send(415, {"error": "JSON required"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 32768:
                    return self.send(413, {"error": "Request too large"})
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise Invalid("Request must be an object")
                route = urlsplit(self.path).path
                if route == "/api/map/preferences":
                    save_preferences(store, body)
                    return self.send(200, preferences(store))
                elif route == "/api/coach":
                    return self.send(202, chat.start(body))
                elif route == "/api/coach/cancel":
                    chat.cancel(body.get("id"))
                    return self.send(200, {"cancelled": True})
                elif route == "/api/memory/create":
                    return self.send(200, create_memory(store, body))
                elif route == "/api/memory/update":
                    return self.send(200, update_memory(store, body))
                elif route == "/api/memory/delete":
                    delete_memory(store, body)
                    return self.send(200, {"deleted": True})
                elif route == "/api/proposal/review":
                    require(set(body) == {"chat_id"}, "Unexpected proposal review fields")
                    session = review_chat_proposal(store, body["chat_id"])
                    return self.send(200, {"session_id": session["id"], "selected": True})
                elif route == "/api/workout/publish":
                    require(set(body) == {"session_id"}, "Unexpected workout publish fields")
                    session_id = body["session_id"]
                    require(isinstance(session_id, str) and session_id.startswith("oneoff-chat-"), "Only reviewed chat workouts can be sent here")
                    session_id_for_chat(session_id.removeprefix("oneoff-chat-"))
                    with (workout_client_factory() if workout_client_factory else MCP(writes=True)) as client:
                        entry = Workouts(store, client).publish(session_id)
                    return self.send(200, {"state": entry["state"], "session_id": session_id,
                                           "watch_verified": bool(entry.get("watch_verified_at"))})
                elif route == "/api/workout/reconcile":
                    require(body == {}, "Unexpected reconciliation fields")
                    with (workout_read_factory() if workout_read_factory else MCP(writes=False)) as client:
                        registry = Workouts(store, client).reconcile_all()
                    return self.send(200, {"states": {key: item["state"] for key, item in registry["entries"].items()}})
                elif route == "/api/garmin/planned/refresh":
                    require(body == {}, "Unexpected Garmin planned-run refresh fields")
                    today = datetime.now(ZoneInfo(store.profile()["timezone"])).date()
                    with (workout_read_factory() if workout_read_factory else MCP(writes=False)) as client:
                        planned = import_planned(store, client, start=today)
                    return self.send(200, {"count": len(planned["items"]), "imported_at": planned["imported_at"]})
                elif route == "/api/workout/watch-verified":
                    require(set(body) == {"session_id", "note"}, "Unexpected watch verification fields")
                    session_id = body["session_id"]
                    require(isinstance(session_id, str) and session_id.startswith("oneoff-chat-"), "Only reviewed chat workouts can be confirmed here")
                    session_id_for_chat(session_id.removeprefix("oneoff-chat-"))
                    Workouts(store, None).watch_verified(session_id, body["note"])
                    return self.send(200, {"watch_verified": True})
                elif route == "/api/profile":
                    # Scheduler activation is an explicit local CLI operation.
                    current = store.profile()
                    body["automation"]["enabled"] = current["automation"]["enabled"]
                    store.save_profile(body)
                elif route == "/api/feedback":
                    store.feedback(body)
                elif route == "/api/session":
                    store.change_session(body.get("session_id"), body)
                elif route == "/api/select":
                    Workouts(store, None).select(body.get("session_id"))
                else:
                    return self.send(404, {"error": "Unknown endpoint"})
                return self.send(200, {"saved": True, "review_required": store.read("run_status").get("review_required", False)})
            except (Invalid, ValueError, KeyError, TypeError) as exc:
                return self.send(400, {"error": str(exc) if isinstance(exc, Invalid) else "Invalid request fields"})
            except OSError:
                return self.send(503, {"error": "Garmin connection unavailable. Check the local authentication tool and retry after reviewing status."})
    class DashboardServer(ThreadingHTTPServer):
        def server_close(self):
            chat.close()
            super().server_close()
    return DashboardServer(("127.0.0.1", port), Handler)


def serve(store, port=8765, chat_executor=None):
    server = make_server(store, port, chat_executor=chat_executor)
    print(f"Dashboard: http://127.0.0.1:{server.server_port} (Ctrl-C to stop)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
