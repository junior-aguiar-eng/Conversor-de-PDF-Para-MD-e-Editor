"""Adaptador HTTPS do serviço de licenças baseado na biblioteca padrão."""

from __future__ import annotations

import json
import ssl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .api import ApiResponse, LicenseServiceApi

MAX_REQUEST_BYTES = 32 * 1024


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Campo JSON duplicado: {key}.")
        result[key] = value
    return result


def build_handler(api: LicenseServiceApi) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "NexoJurisLicenseService/1"

        def do_GET(self) -> None:  # noqa: N802
            self._dispatch(None)

        def do_POST(self) -> None:  # noqa: N802
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > MAX_REQUEST_BYTES:
                    self._send(ApiResponse(413, {"error": "invalid_size", "message": "Corpo JSON ausente ou excessivo."}))
                    return
                if self.headers.get_content_type() != "application/json":
                    self._send(ApiResponse(415, {"error": "invalid_media_type", "message": "Use application/json."}))
                    return
                body = json.loads(self.rfile.read(length), object_pairs_hook=_reject_duplicate_keys)
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
                self._send(ApiResponse(400, {"error": "invalid_json", "message": str(error)}))
                return
            self._dispatch(body)

        def _dispatch(self, body: dict[str, Any] | None) -> None:
            response = api.handle(
                self.command,
                self.path,
                body=body,
                headers=dict(self.headers.items()),
                client_ip=self.client_address[0],
            )
            self._send(response)

        def _send(self, response: ApiResponse) -> None:
            encoded = json.dumps(response.body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            self.send_response(response.status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Strict-Transport-Security", "max-age=31536000")
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, format: str, *args: Any) -> None:
            return

    return Handler


def serve_https(
    api: LicenseServiceApi,
    *,
    host: str,
    port: int,
    certificate: str | Path,
    private_key: str | Path,
) -> None:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(str(certificate), str(private_key))
    server = ThreadingHTTPServer((host, port), build_handler(api))
    server.socket = context.wrap_socket(server.socket, server_side=True)
    server.serve_forever()
