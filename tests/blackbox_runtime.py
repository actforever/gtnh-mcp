"""Test-only subprocess launcher. Never included in the production image.

Only transport endpoints and the external Docker/game server are substituted.
The MCP server, helper API, Docker SDK, RCON client and restore manager are real.
"""

import json
import os
import re
import socketserver
import struct
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def upstream():
    state = {
        "running": True,
        "policy": {"Name": "unless-stopped", "MaximumRetryCount": 0},
        "commands": [],
        "docker_calls": [],
        "fail_next_start": False,
        "ignore_stop": False,
    }

    class DockerHandler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, data, status=200):
            body = json.dumps(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = re.sub(r"^/v[0-9.]+", "", self.path)
            if path == "/version":
                self.reply(
                    {"ApiVersion": "1.45", "MinAPIVersion": "1.24", "Version": "test"}
                )
            elif path == "/observations":
                self.reply(state)
            elif path in {"/containers/gtnh/json", "/containers/" + "a" * 64 + "/json"}:
                self.reply(
                    {
                        "Id": "a" * 64,
                        "Name": "/gtnh",
                        "Config": {"Image": "test"},
                        "State": {
                            "Status": "running" if state["running"] else "exited",
                            "Running": state["running"],
                        },
                        "HostConfig": {"RestartPolicy": state["policy"]},
                    }
                )
            else:
                self.reply({"message": "not found"}, 404)

        def do_POST(self):
            path = re.sub(r"^/v[0-9.]+", "", self.path)
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            data = json.loads(body) if body else {}
            if path == "/behavior":
                state.update(
                    {
                        key: value
                        for key, value in data.items()
                        if key in {"fail_next_start", "ignore_stop"}
                    }
                )
                self.reply({"ok": True})
                return
            state["docker_calls"].append(path)
            if path == "/containers/" + "a" * 64 + "/update":
                state["policy"] = data["RestartPolicy"]
                self.reply({"Warnings": []})
            elif path == "/containers/" + "a" * 64 + "/start":
                state["running"] = not state["fail_next_start"]
                state["fail_next_start"] = False
                self.send_response(204)
                self.end_headers()
            else:
                self.reply({"message": "not found"}, 404)

    class RconHandler(socketserver.BaseRequestHandler):
        def read(self, length):
            data = bytearray()
            while len(data) < length:
                part = self.request.recv(length - len(data))
                if not part:
                    raise EOFError
                data.extend(part)
            return bytes(data)

        def handle(self):
            self.request.settimeout(5)
            try:
                authenticated = False
                while state["running"]:
                    length = struct.unpack("<i", self.read(4))[0]
                    packet = self.read(length)
                    request_id, kind = struct.unpack("<ii", packet[:8])
                    value = packet[8:-2].decode()
                    response = ""
                    if kind == 3:
                        authenticated = value == os.environ["RCON_PASSWORD"]
                        if not authenticated:
                            request_id = -1
                    elif kind == 2 and authenticated:
                        state["commands"].append(value)
                        if value == "stop":
                            if not state["ignore_stop"]:
                                state["running"] = False
                            return
                        response = (
                            "There are 2 players online: Alice, Bob"
                            if value == "list"
                            else "OK: " + value
                        )
                    else:
                        return
                    reply = (
                        struct.pack("<ii", request_id, 2 if kind == 3 else 0)
                        + response.encode()
                        + b"\0\0"
                    )
                    self.request.sendall(struct.pack("<i", len(reply)) + reply)
            except (EOFError, OSError):
                return

    class RconServer(socketserver.ThreadingTCPServer):
        daemon_threads = True

    rcon = RconServer(("127.0.0.1", int(os.environ["RCON_PORT"])), RconHandler)
    threading.Thread(target=rcon.serve_forever, daemon=True).start()
    ThreadingHTTPServer(
        ("127.0.0.1", int(os.environ["BLACKBOX_DOCKER_PORT"])), DockerHandler
    ).serve_forever()


def helper():
    import docker
    import uvicorn

    from gtnh_mcp.config import Settings
    from gtnh_mcp.container import ContainerControl
    from gtnh_mcp.helper import create_app
    from gtnh_mcp.restore import RestoreManager

    settings = Settings.from_env()
    docker_client = docker.DockerClient(
        base_url="http://127.0.0.1:" + os.environ["BLACKBOX_DOCKER_PORT"], timeout=3
    )
    manager = RestoreManager(settings, ContainerControl(settings, client=docker_client))
    uvicorn.run(
        create_app(settings, manager),
        host="127.0.0.1",
        port=int(os.environ["BLACKBOX_HELPER_PORT"]),
        access_log=False,
    )


def mcp():
    import httpx

    from gtnh_mcp.config import Settings
    from gtnh_mcp.server import HelperClient, create_server

    class LoopbackHelperClient(HelperClient):
        # Test-only TCP endpoint; production uses the UDS transport.
        rpc_url = "http://127.0.0.1:" + os.environ["BLACKBOX_HELPER_PORT"] + "/rpc"

        def connection(self):
            return httpx.AsyncClient(timeout=30)

    settings = Settings.from_env()
    create_server(settings, helper=LoopbackHelperClient(settings)).run(
        transport="http",
        host="127.0.0.1",
        port=settings.mcp_port,
        path="/mcp",
        stateless_http=True,
        json_response=True,
        show_banner=False,
    )


if __name__ == "__main__":
    # Explicit modes only, no production entry point or configuration enables these.
    {"upstream": upstream, "helper": helper, "mcp": mcp}[sys.argv[1]]()
