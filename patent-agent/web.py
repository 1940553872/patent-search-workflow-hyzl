"""Loopback-only UI. No database API, login credential or model API is used."""
from __future__ import annotations
import base64
import json
import secrets
import socket
import sys
import threading
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from urllib.request import urlopen

from shared.storage import APP, encode


def serve(workflow, port=8765, open_browser=False):
    if not 1024 <= port <= 65535:
        raise ValueError("端口应在 1024–65535 范围")
    token = secrets.token_urlsafe(32)
    lock = threading.RLock()
    url = f"http://127.0.0.1:{port}"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            # Do not log patent text, tokens or URL query content.
            return

        def send(self, status, body, content_type="application/json; charset=utf-8"):
            if isinstance(body, (dict, list)):
                body = encode(body).encode("utf-8")
            elif isinstance(body, str):
                body = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(body)

        def allowed_host(self):
            return self.headers.get("Host") in (f"127.0.0.1:{port}", f"localhost:{port}")

        def do_GET(self):
            if not self.allowed_host():
                return self.send(403, {"error": "仅允许本机访问"})
            parsed = urlparse(self.path)
            try:
                with lock:
                    if parsed.path == "/api/bootstrap":
                        return self.send(200, {"csrf": token, "data_dir": str(workflow.store.root)})
                    if parsed.path == "/api/runs":
                        return self.send(200, [{k: r[k] for k in ("run_id", "title", "stage", "status", "updated_at")} for r in workflow.store.list()])
                    if parsed.path == "/api/run":
                        rid = parse_qs(parsed.query).get("id", [""])[0]
                        run = workflow.view(rid)
                        run["trace"] = workflow.store.traces(rid)[-30:]
                        return self.send(200, run)
                    if parsed.path.startswith("/report/"):
                        rid = parsed.path.rsplit("/", 1)[-1]
                        path = workflow.store.run_dir(rid) / "report.html"
                        return self.send(200, path.read_bytes(), "text/html; charset=utf-8")
                    files = {"/": ("index.html", "text/html; charset=utf-8"), "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                             "/style.css": ("style.css", "text/css; charset=utf-8")}
                    if parsed.path in files:
                        name, content_type = files[parsed.path]
                        return self.send(200, (APP / "ui" / name).read_bytes(), content_type)
                    return self.send(404, {"error": "不存在的地址"})
            except (ValueError, OSError) as exc:
                self.send(400, {"error": str(exc)})

        def do_POST(self):
            if not self.allowed_host() or self.headers.get("X-CSRF-Token") != token:
                return self.send(403, {"error": "页面验证失效，请刷新"})
            if self.headers.get("Origin") not in (None, url, f"http://localhost:{port}"):
                return self.send(403, {"error": "拒绝跨站请求"})
            if self.path != "/api/action":
                return self.send(404, {"error": "不存在的操作"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 140 * 1024 * 1024:
                    raise ValueError("请求为空或文件超过 100 MB")
                data = json.loads(self.rfile.read(length))
                action = data.get("action")
                rid = data.get("run_id", "")
                with lock:
                    if action == "create":
                        result = workflow.create(data["request"])
                    elif action in ("packet", "advance", "ingest_pdfs", "export", "queries", "prompts"):
                        result = getattr(workflow, action)(rid)
                    elif action == "submit":
                        result = workflow.submit(rid, data["stage"], data["envelope"])
                    elif action == "confirm":
                        result = workflow.confirm(rid, data["gate"], data["by"])
                    elif action == "confirm_pdf":
                        result = workflow.confirm_pdf(rid, data["key"], data["by"], int(data["pages"]), data.get("scope", "full"))
                    elif action == "replace_pdf":
                        result = workflow.replace_pdf(rid, data["key"], data["path"])
                    elif action == "stop":
                        result = workflow.stop(rid, data["reason"])
                    elif action == "update_spec":
                        result = workflow.update_spec(rid, data["spec"], data["by"])
                    elif action == "replan":
                        result = workflow.replan(rid, data["reason"], data["by"])
                    elif action == "authorize":
                        result = workflow.authorize(rid, data.get("sources", []), data.get("models", []))
                    elif action == "import":
                        result = workflow.import_file(rid, data.get("path") or None, data["query"], data.get("status", "complete"), int(data.get("page", 1)), data.get("note", ""), data.get("execution"))
                    elif action == "upload":
                        name = Path(data["name"]).name
                        if name != data["name"] or name in (".", ".."):
                            raise ValueError("文件名无效")
                        suffix = Path(name).suffix.lower()
                        blob = base64.b64decode(data["base64"], validate=True)
                        if len(blob) > 100 * 1024 * 1024:
                            raise ValueError("文件超过 100 MB")
                        directory = workflow.store.run_dir(rid)
                        if suffix == ".pdf":
                            path = directory / "pdf_inbox" / name
                            if path.exists() and path.read_bytes() != blob:
                                raise ValueError("同名 PDF 已存在；请保留最新文件后使用明确替换操作")
                            path.write_bytes(blob)
                            result = workflow.ingest_pdfs(rid)
                        elif suffix in (".csv", ".xlsx", ".json"):
                            path = directory / ("upload-" + uuid.uuid4().hex + suffix)
                            try:
                                path.write_bytes(blob)
                                result = workflow.import_file(rid, path, data["query"], data.get("status", "complete"), int(data.get("page", 1)), data.get("note", ""), data.get("execution"))
                            finally:
                                path.unlink(missing_ok=True)
                        else:
                            raise ValueError("支持 CSV、XLSX、JSON 和 PDF")
                    else:
                        raise ValueError("不支持的操作")
                self.send(200, result)
            except (ValueError, OSError, KeyError, TypeError) as exc:
                self.send(400, {"error": str(exc)})

    # Windows permits duplicate HTTPServer binds with SO_REUSEADDR. Use an
    # exclusive socket and recognize an already-running copy before binding.
    try:
        with urlopen(url + "/api/bootstrap", timeout=1) as response:
            existing = json.loads(response.read())
        already_running = Path(existing.get("data_dir", "")).resolve() == workflow.store.root
    except Exception:
        already_running = False
    if already_running:
        print(f"本项目工作台已在运行：{url}", flush=True)
        if open_browser:
            webbrowser.open(url)
        return 0

    class LocalHTTPServer(ThreadingHTTPServer):
        allow_reuse_address = sys.platform != "win32"
        allow_reuse_port = False

        def server_bind(self):
            if sys.platform == "win32":
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            super().server_bind()

    try:
        server = LocalHTTPServer(("127.0.0.1", port), Handler)
    except OSError:
        try:
            with urlopen(url + "/api/bootstrap", timeout=2) as response:
                existing = json.loads(response.read())
            if Path(existing.get("data_dir", "")).resolve() != workflow.store.root:
                raise ValueError("该端口正在运行其他工作台")
        except Exception as exc:
            raise ValueError(f"端口 {port} 已被占用，请使用 serve --port 指定其他端口") from exc
        print(f"本项目工作台已在运行：{url}", flush=True)
        if open_browser:
            webbrowser.open(url)
        return 0
    print(f"专利检索工作流已启动：{url}\n关闭此终端或按 Ctrl+C 停止。", flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
