"""Exercise the real Swift URLSession WebSocket client against an isolated fake OBS."""
import base64
import hashlib
import json
import socket
import struct
import subprocess
import sys
import threading


def exact(sock, n):
    result = b""
    while len(result) < n:
        data = sock.recv(n - len(result))
        if not data: raise EOFError()
        result += data
    return result


def send(sock, packet):
    data = json.dumps(packet).encode()
    header = bytes([0x81, len(data)]) if len(data) < 126 else bytes([0x81, 126]) + struct.pack("!H", len(data))
    sock.sendall(header + data)


def receive(sock):
    a, b = exact(sock, 2)
    size = b & 127
    if size == 126: size = struct.unpack("!H", exact(sock, 2))[0]
    if size == 127: size = struct.unpack("!Q", exact(sock, 8))[0]
    if size > 100000: raise ValueError("oversize test frame")
    mask = exact(sock, 4) if b & 128 else None
    body = exact(sock, size)
    if mask: body = bytes(value ^ mask[i % 4] for i, value in enumerate(body))
    if (a & 15) == 8: raise EOFError()
    return json.loads(body)


def serve(listener, errors):
    try:
        sock, _ = listener.accept()
        with sock:
            sock.settimeout(12)
            request = b""
            while b"\r\n\r\n" not in request:
                request += exact(sock, 1)
            headers = dict(line.split(":", 1) for line in request.decode().split("\r\n")[1:] if ":" in line)
            key = next(v.strip() for k, v in headers.items() if k.lower() == "sec-websocket-key")
            accept = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
            sock.sendall(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: " + accept + "\r\n\r\n").encode())
            send(sock, {"op": 0, "d": {"rpcVersion": 1, "authentication": {"salt": "test-salt", "challenge": "test-challenge"}}})
            auth = receive(sock)
            def sha(value): return base64.b64encode(hashlib.sha256(value.encode()).digest()).decode()
            assert auth["op"] == 1
            assert auth["d"]["authentication"] == sha(sha("test-local-onlytest-salt") + "test-challenge")
            assert auth["d"]["eventSubscriptions"] == 0
            send(sock, {"op": 2, "d": {"negotiatedRpcVersion": 1}})
            types = set()
            profile_attempts = 0
            for _ in range(5):
                packet = receive(sock)
                assert packet["op"] == 6
                d = packet["d"]
                kind = d["requestType"]
                types.add(kind)
                if kind == "GetProfileList":
                    profile_attempts += 1
                    if profile_attempts == 1:
                        send(sock, {"op": 7, "d": {"requestId": d["requestId"], "requestType": kind,
                            "requestStatus": {"result": False, "code": 207, "comment": "OBS is not ready"}}})
                        continue
                failed = kind == "DeliberateFailure"
                send(sock, {"op": 7, "d": {"requestId": d["requestId"], "requestType": kind,
                    "requestStatus": {"result": not failed, "code": 400 if failed else 100, "comment": "test rejection" if failed else ""},
                    "responseData": {"obsVersion": "32.2.2"} if kind == "GetVersion" else {"profiles": ["DenDenMushi Demo"]} if kind == "GetProfileList" else {"outputActive": False}}})
            assert types == {"GetVersion", "GetVirtualCamStatus", "GetProfileList", "DeliberateFailure"}
            assert profile_attempts == 2
    except Exception as exc:
        errors.append(exc)


if __name__ == "__main__":
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0)); listener.listen(1); listener.settimeout(20)
        errors = []
        worker = threading.Thread(target=serve, args=(listener, errors))
        worker.start()
        result = subprocess.run([sys.argv[1], str(listener.getsockname()[1])], timeout=30)
        worker.join(timeout=15)
        if errors: raise errors[0]
        if worker.is_alive(): raise RuntimeError("mock did not finish")
        sys.exit(result.returncode)
