import socket
import struct
import threading
import time

import pytest
from filelock import FileLock

from gtnh_mcp.auth import Actor, Denied, decode_actor, encode_actor, verify
from gtnh_mcp.rcon import OperationError, RconService, command_for


def token(settings):
    return settings.auth_secret.get_secret_value()


def test_auth(settings):
    actor = verify(token(settings), settings)
    assert actor.key == "api-client"
    assert (
        verify(token(settings), settings, encode_actor(Actor("test:group:admin"))).key
        == "test:group:admin"
    )


@pytest.mark.parametrize(
    "invalid",
    [
        "",
        "invalid",
        "eyJhbGciOiJIUzI1NiJ9.old.jwt",
        "密钥错误",
    ],
)
def test_invalid_auth(settings, invalid):
    with pytest.raises(Denied):
        verify(invalid, settings)


def test_tampered_token(settings):
    encoded = token(settings)
    with pytest.raises(Denied):
        verify(encoded[:-8] + "AAAAAAAA", settings)


@pytest.mark.parametrize(
    "key", ["api-client", "qq_official:Group_OpenID:User_OpenID", "旧任务:用户"]
)
def test_opaque_actor_roundtrip(key):
    assert decode_actor(encode_actor(Actor(key))).key == key


@pytest.mark.parametrize(
    "value", ["", "%%", "YWJj\n", "AA", "YQ=wrong", "_w", "A" * 2000]
)
def test_bad_actor_header(value):
    with pytest.raises(Denied):
        decode_actor(value)


@pytest.mark.parametrize(
    "action,value",
    [
        ("announce", "hello\nstop"),
        ("announce", "\0"),
        ("announce", "x" * 301),
        ("whitelist_add", "a b"),
        ("whitelist_remove", "../admin"),
        ("players", "stop"),
        ("stop", ""),
    ],
)
def test_command_validation(action, value):
    with pytest.raises(OperationError):
        command_for(action, value)


def test_operation_lock_and_marker(settings):
    with FileLock(settings.lock_path):
        with pytest.raises(OperationError):
            RconService(settings).execute("save")
    (settings.runtime_dir / "maintenance.json").write_text("{}")
    with pytest.raises(OperationError):
        RconService(settings).execute("save")


def recv_exact(client, length):
    data = b""
    while len(data) < length:
        part = client.recv(length - len(data))
        if not part:
            raise EOFError
        data += part
    return data


@pytest.mark.parametrize("mode", ["success", "auth_failure", "disconnect", "timeout"])
def test_real_rcon_socket(settings, mode):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    settings.rcon_port = listener.getsockname()[1]
    settings.rcon_timeout = 1
    commands = []

    def serve():
        with listener, listener.accept()[0] as client:
            client.settimeout(3)
            for _ in range(2):
                packet = recv_exact(
                    client, struct.unpack("<i", recv_exact(client, 4))[0]
                )
                kind = struct.unpack("<ii", packet[:8])[1]
                if kind == 2:
                    commands.append(packet[8:-2].decode())
                    if mode == "disconnect":
                        return
                    if mode == "timeout":
                        time.sleep(1.2)
                        return
                reply = (
                    struct.pack(
                        "<ii",
                        -1 if mode == "auth_failure" else 0,
                        2 if kind == 3 else 0,
                    )
                    + (b"" if kind == 3 else b"There are 2 players online")
                    + b"\0\0"
                )
                framed = struct.pack("<i", len(reply)) + reply
                client.sendall(framed[:2])
                client.sendall(framed[2:])
                if mode == "auth_failure":
                    return

    worker = threading.Thread(target=serve)
    worker.start()
    try:
        if mode == "success":
            assert "2 players" in RconService(settings).execute("players")
        else:
            with pytest.raises(OperationError):
                RconService(settings).execute("players")
        assert len(commands) == (0 if mode == "auth_failure" else 1)
    finally:
        worker.join(timeout=4)
        assert not worker.is_alive()
