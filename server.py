#!/usr/bin/env python3
"""Servidor local de Juliet Pastas.

Sirve el sitio y el ABM de imágenes en /gestion/.
La clave inicial es la variable de entorno JULIET_PIN, o "juliet" si no está definida.
"""

from __future__ import annotations

import json
import os
import re
import secrets
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parent
IMAGENES = ROOT / "IMAGENES"
CATALOGO = ROOT / "catalogo.json"
PIN = os.environ.get("JULIET_PIN", "juliet")
SESIONES: set[str] = set()
MAX_BYTES = 8 * 1024 * 1024
EXTENSIONES = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
HOST = os.environ.get("JULIET_HOST", "127.0.0.1")
PORT = int(os.environ.get("JULIET_PORT", "8080"))


def ahora() -> str:
    return datetime.now(timezone.utc).isoformat()


def catalogo_vacio() -> dict:
    return {
        "actualizado": ahora(),
        "logo": None,
        "slots": {},
        "galeria": [],
        "meta": {},
    }


def cargar_catalogo() -> dict:
    if not CATALOGO.exists():
        return catalogo_vacio()
    try:
        data = json.loads(CATALOGO.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return catalogo_vacio()
    if not isinstance(data, dict):
        return catalogo_vacio()
    data.setdefault("logo", None)
    data.setdefault("slots", {})
    data.setdefault("galeria", [])
    data.setdefault("meta", {})
    return data


def guardar_catalogo(data: dict) -> None:
    data["actualizado"] = ahora()
    CATALOGO.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def nombre_seguro(nombre: str) -> str:
    nombre = Path(unquote(nombre or "")).name
    if not nombre or nombre in {".", ".."} or "/" in nombre or "\\" in nombre:
        raise ValueError("Nombre de archivo inválido")
    if Path(nombre).suffix.lower() not in EXTENSIONES:
        raise ValueError("Solo se aceptan imágenes JPG, PNG, WEBP o GIF")
    return nombre


def ruta_imagen(nombre: str) -> Path:
    nombre = nombre_seguro(nombre)
    ruta = (IMAGENES / nombre).resolve()
    if ruta.parent != IMAGENES.resolve() or not ruta.is_file():
        raise FileNotFoundError(nombre)
    if ruta.suffix.lower() not in EXTENSIONES:
        raise ValueError("Tipo de archivo no permitido")
    return ruta


def es_imagen(datos: bytes, extension: str) -> bool:
    if extension in {".jpg", ".jpeg"}:
        return datos.startswith(b"\xff\xd8\xff")
    if extension == ".png":
        return datos.startswith(b"\x89PNG\r\n\x1a\n")
    if extension == ".gif":
        return datos.startswith(b"GIF87a") or datos.startswith(b"GIF89a")
    if extension == ".webp":
        return datos.startswith(b"RIFF") and datos[8:12] == b"WEBP"
    return False


def nombre_unico(original: str, content_type: str) -> str:
    ext = Path(original).suffix.lower()
    if ext not in EXTENSIONES:
        ext = {
            "image/jpeg": ".jpg",
            "image/png": ".png",
            "image/webp": ".webp",
            "image/gif": ".gif",
        }.get(content_type, ".jpg")
    stem = Path(original).stem
    stem = re.sub(r"[^A-Za-z0-9_-]+", "-", stem).strip("-")[:40] or "foto"
    sello = datetime.now().strftime("%Y%m%d-%H%M%S")
    candidato = f"{stem}-{sello}{ext}"
    n = 2
    while (IMAGENES / candidato).exists():
        candidato = f"{stem}-{sello}-{n}{ext}"
        n += 1
    return candidato


def listar_imagenes() -> list[dict]:
    IMAGENES.mkdir(exist_ok=True)
    items = []
    for path in IMAGENES.iterdir():
        if not path.is_file() or path.suffix.lower() not in EXTENSIONES:
            continue
        stat = path.stat()
        items.append(
            {
                "archivo": path.name,
                "bytes": stat.st_size,
                "modificado": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
            }
        )
    items.sort(key=lambda item: item["archivo"].lower())
    return items


def quitar_referencias(catalogo: dict, archivo: str) -> None:
    logo = catalogo.get("logo")
    if isinstance(logo, dict) and logo.get("archivo") == archivo:
        catalogo["logo"] = None
    slots = catalogo.get("slots") or {}
    for clave, slot in list(slots.items()):
        if isinstance(slot, dict) and slot.get("archivo") == archivo:
            slots.pop(clave, None)
    catalogo["galeria"] = [
        item
        for item in catalogo.get("galeria") or []
        if not (isinstance(item, dict) and item.get("archivo") == archivo)
    ]
    meta = catalogo.get("meta") or {}
    meta.pop(archivo, None)
    catalogo["meta"] = meta


def validar_catalogo(data: dict) -> dict:
    if not isinstance(data, dict):
        raise ValueError("El catálogo tiene que ser un objeto")
    logo = data.get("logo")
    if logo is not None:
        if not isinstance(logo, dict) or not logo.get("archivo"):
            raise ValueError("Logo inválido")
        logo = {
            "archivo": nombre_seguro(str(logo["archivo"])),
            "alt": str(logo.get("alt") or "")[:180],
        }
    slots = {}
    for clave, slot in (data.get("slots") or {}).items():
        if not isinstance(clave, str) or not re.fullmatch(r"[a-z0-9-]{1,40}", clave):
            continue
        if not isinstance(slot, dict) or not slot.get("archivo"):
            continue
        slots[clave] = {
            "archivo": nombre_seguro(str(slot["archivo"])),
            "alt": str(slot.get("alt") or "")[:180],
        }
    galeria = []
    for item in data.get("galeria") or []:
        if not isinstance(item, dict) or not item.get("archivo"):
            continue
        galeria.append(
            {
                "archivo": nombre_seguro(str(item["archivo"])),
                "titulo": str(item.get("titulo") or "")[:120],
            }
        )
    meta = {}
    for archivo, info in (data.get("meta") or {}).items():
        nombre = nombre_seguro(str(archivo))
        if not isinstance(info, dict):
            continue
        meta[nombre] = {
            "titulo": str(info.get("titulo") or "")[:120],
            "notas": str(info.get("notas") or "")[:500],
        }
    return {
        "actualizado": ahora(),
        "logo": logo,
        "slots": slots,
        "galeria": galeria,
        "meta": meta,
    }


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def log_message(self, fmt: str, *args) -> None:
        print("[%s] %s" % (self.log_date_time_string(), fmt % args))

    def end_headers(self) -> None:
        path = urlparse(self.path).path
        if path.startswith("/api/") or path.endswith(".json"):
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def list_directory(self, path):
        self.send_error(404, "Sin listado")
        return None

    def _json(self, data, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _sesion(self) -> str:
        raw = self.headers.get("Cookie", "")
        for parte in raw.split(";"):
            clave, _, valor = parte.strip().partition("=")
            if clave == "juliet_sesion":
                return valor
        return ""

    def _autenticado(self) -> bool:
        token = self._sesion()
        return bool(token) and token in SESIONES

    def _exigir_auth(self) -> bool:
        if self._autenticado():
            return True
        self._json({"error": "Tenés que ingresar la clave de gestión."}, 401)
        return False

    def _leer_cuerpo(self) -> bytes:
        largo = int(self.headers.get("Content-Length", "0") or 0)
        if largo < 0 or largo > MAX_BYTES + 1024:
            raise ValueError("La imagen supera el máximo de 8 MB")
        return self.rfile.read(largo)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/gestion":
            self.send_response(302)
            self.send_header("Location", "/gestion/")
            self.end_headers()
            return
        if path == "/api/estado":
            self._json({"ok": True, "auth": self._autenticado()})
            return
        if path == "/api/imagenes":
            self._json(listar_imagenes())
            return
        if path == "/api/catalogo":
            self._json(cargar_catalogo())
            return
        if path in {"/gestion/", "/gestion/index.html"}:
            self.path = "/gestion/index.html"
        return super().do_GET()

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/login":
            self._login()
            return
        if path == "/api/logout":
            SESIONES.discard(self._sesion())
            self.send_response(204)
            self.send_header("Set-Cookie", "juliet_sesion=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax")
            self.end_headers()
            return
        if path == "/api/imagenes":
            if not self._exigir_auth():
                return
            self._subir()
            return
        self._json({"error": "Ruta no encontrada"}, 404)

    def do_PUT(self) -> None:
        if urlparse(self.path).path != "/api/catalogo":
            self._json({"error": "Ruta no encontrada"}, 404)
            return
        if not self._exigir_auth():
            return
        try:
            cuerpo = self._leer_cuerpo()
            data = validar_catalogo(json.loads(cuerpo.decode("utf-8")))
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            self._json({"error": str(exc) or "Catálogo inválido"}, 400)
            return
        guardar_catalogo(data)
        self._json(data)

    def do_DELETE(self) -> None:
        path = urlparse(self.path).path
        if path != "/api/imagenes":
            self._json({"error": "Ruta no encontrada"}, 404)
            return
        if not self._exigir_auth():
            return
        query = parse_qs(urlparse(self.path).query)
        archivo = (query.get("archivo") or [""])[0]
        try:
            ruta = ruta_imagen(archivo)
        except (ValueError, FileNotFoundError):
            self._json({"error": "No encontré esa imagen"}, 404)
            return
        ruta.unlink()
        catalogo = cargar_catalogo()
        quitar_referencias(catalogo, ruta.name)
        guardar_catalogo(catalogo)
        self._json({"ok": True, "catalogo": catalogo})

    def _login(self) -> None:
        try:
            data = json.loads(self._leer_cuerpo().decode("utf-8"))
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
            self._json({"error": "Clave inválida"}, 400)
            return
        if not secrets.compare_digest(str(data.get("pin") or ""), PIN):
            self._json({"error": "Clave incorrecta"}, 401)
            return
        token = secrets.token_urlsafe(24)
        SESIONES.add(token)
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header(
            "Set-Cookie",
            f"juliet_sesion={token}; Path=/; Max-Age=43200; HttpOnly; SameSite=Lax",
        )
        body = b'{"ok":true}'
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _subir(self) -> None:
        query = parse_qs(urlparse(self.path).query)
        original = (query.get("nombre") or ["foto.jpg"])[0]
        try:
            datos = self._leer_cuerpo()
        except ValueError as exc:
            self._json({"error": str(exc)}, 400)
            return
        if not datos:
            self._json({"error": "La imagen está vacía"}, 400)
            return
        try:
            destino = nombre_unico(nombre_seguro(original) if original else "foto.jpg", self.headers.get("Content-Type", ""))
        except ValueError as exc:
            self._json({"error": str(exc)}, 400)
            return
        if not es_imagen(datos, Path(destino).suffix.lower()):
            self._json({"error": "El archivo no es una imagen JPG, PNG, WEBP o GIF"}, 400)
            return
        IMAGENES.mkdir(exist_ok=True)
        (IMAGENES / destino).write_bytes(datos)
        self._json({"ok": True, "archivo": destino}, 201)


def main() -> None:
    IMAGENES.mkdir(exist_ok=True)
    puerto = PORT
    server = None
    ultimo_error = None
    for candidato in range(PORT, PORT + 20):
        try:
            server = ThreadingHTTPServer((HOST, candidato), Handler)
            puerto = candidato
            break
        except OSError as exc:
            ultimo_error = exc
    if server is None:
        raise SystemExit(f"No pude abrir un puerto desde {PORT}: {ultimo_error}")
    print(f"Juliet Pastas en http://{HOST}:{puerto}/", flush=True)
    print(f"Gestión de imágenes en http://{HOST}:{puerto}/gestion/", flush=True)
    print("Clave de gestión: la variable JULIET_PIN, o 'juliet' si no la definiste.", flush=True)
    print("Ctrl+C para detener.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServidor detenido.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
