"""
Поток захвата кадра + детекция (YOLO при наличии) + подсказки «куда сдвинуть кадр».
Пока без PTZ: только визуальные стрелки; позже та же логика пойдёт на привод камеры / дрон.
"""

from __future__ import annotations

import logging
import math
import os
import shutil
import subprocess
import sys
import threading
import time
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import parse_qsl, quote, urlparse, urlencode, urlunparse

import cv2
import numpy as np

log = logging.getLogger("vision.pipeline")


def _strip_outer_quotes(s: str) -> str:
    """Убираем обрамляющие кавычки из .env вроде PASSWORD=\"secret\" (если dotenv оставил их)."""
    s = s.strip()
    if len(s) >= 2 and ((s[0] == s[-1] == '"') or (s[0] == s[-1] == "'")):
        return s[1:-1]
    return s


def _env_camera_user() -> str:
    return _strip_outer_quotes((os.environ.get("VISION_CAMERA_USER") or "").strip())


def _env_camera_password() -> str:
    return _strip_outer_quotes(os.environ.get("VISION_CAMERA_PASSWORD") or "")


def _env_rtsp_user() -> str:
    if os.environ.get("VISION_CAMERA_RTSP_USER") is not None:
        return _strip_outer_quotes((os.environ.get("VISION_CAMERA_RTSP_USER") or "").strip())
    return _env_camera_user()


def _env_rtsp_password() -> str:
    if os.environ.get("VISION_CAMERA_RTSP_PASSWORD") is not None:
        return _strip_outer_quotes(os.environ.get("VISION_CAMERA_RTSP_PASSWORD") or "")
    return _env_camera_password()


def _safe_url_for_log(url: str) -> str:
    """URL без пароля — только для логов (path + query, чтобы видеть channel/subtype)."""
    try:
        p = urlparse(url)
        host = p.hostname or ""
        port = f":{p.port}" if p.port else ""
        pq = f"?{p.query}" if p.query else ""
        if p.username is not None:
            return f"{p.scheme}://***:***@{host}{port}{p.path or ''}{pq}"
        return f"{p.scheme}://{host}{port}{p.path or ''}{pq}"
    except Exception:
        return "<url>"


def _resolve_stream_url(raw: str) -> str:
    """
    Собирает URL IP-камеры: схема, логин/пароль из VISION_CAMERA_*,
    при необходимости добавляет VISION_CAMERA_STREAM_PATH к «голому» хосту.
    """
    src = (raw or "").strip()
    if not src:
        return src

    user = _env_camera_user()
    password = _env_camera_password()

    extra_path = (os.environ.get("VISION_CAMERA_STREAM_PATH") or "").strip()
    if extra_path and not extra_path.startswith("/"):
        extra_path = "/" + extra_path

    if "://" not in src:
        src = "http://" + src.lstrip("/")

    p = urlparse(src)
    path = p.path or ""
    if extra_path and p.scheme in ("http", "https", "rtsp", "rtsps") and path in ("", "/"):
        src = urlunparse((p.scheme, p.netloc, extra_path, "", p.query, p.fragment))
        p = urlparse(src)

    if user and p.scheme in ("http", "https", "rtsp", "rtsps") and p.username is None:
        host = p.hostname or ""
        if not host:
            return src
        port = p.port
        uq = quote(user, safe="")
        pq = quote(password, safe="")
        netloc = f"{uq}:{pq}@{host}:{port}" if port else f"{uq}:{pq}@{host}"
        src = urlunparse((p.scheme, netloc, p.path, p.params, p.query, p.fragment))

    return src


# Типовые MJPEG/субпотоки (Dahua/Xiongmai/Foscam и т.д.) — порядок: сначала самые частые.
_DEFAULT_HTTP_STREAM_PATHS: tuple[str, ...] = (
    "/cgi-bin/mjpg/video.cgi?channel=1&subtype=1",
    "/cgi-bin/mjpg/video.cgi?channel=0&subtype=1",
    "/cgi-bin/mjpg/video.cgi?channel=1&subtype=0",
    "/cgi-bin/mjpg/video.cgi",
    "/videostream.cgi",
    "/video.mjpg",
    "/mjpeg/1/video.mjpeg",
    "/stream",
    "/live",
    "/axis-cgi/mjpg/video.cgi",
    "/mjpeg.cgi",
    "/img/video.mjpeg",
)


def _http_source_needs_path_probe(raw: str) -> bool:
    """Только хост (или /) — можно перебрать пути и порты."""
    s = (raw or "").strip()
    if "://" not in s:
        s = "http://" + s.lstrip("/")
    p = urlparse(s)
    if p.scheme.lower() not in ("http", "https"):
        return False
    path = (p.path or "").strip()
    return path in ("", "/")


def _bare_http_host_from_source(raw: str) -> str | None:
    """Имя хоста из VISION_VIDEO_SOURCE вроде 192.168.1.5 или http://192.168.1.5/."""
    raw_h = (raw or "").strip()
    if "://" not in raw_h:
        raw_h = "http://" + raw_h.lstrip("/")
    h = (urlparse(raw_h).hostname or "").strip()
    return h or None


def _stream_paths_from_env() -> list[str]:
    raw = (os.environ.get("VISION_CAMERA_STREAM_PATH") or "").strip()
    if not raw:
        return []
    if ";" in raw:
        return [x.strip() for x in raw.split(";") if x.strip()]
    if "," in raw:
        return [x.strip() for x in raw.split(",") if x.strip()]
    return [raw]


def _ordered_stream_paths() -> list[str]:
    """Сначала пути из VISION_CAMERA_STREAM_PATH, затем типовые. Дубликаты по полной строке path+query."""
    user_paths = _stream_paths_from_env()
    seen: set[str] = set()
    out: list[str] = []
    for p in user_paths + list(_DEFAULT_HTTP_STREAM_PATHS):
        norm = p if p.startswith("/") else "/" + p.lstrip("/")
        key = norm.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(norm)
    return out


def _iter_http_stream_urls(raw: str) -> list[str]:
    """
    Собирает список полных URL с логином/паролем из env: разные пути и (если порт не задан) 80, 8080, 81, 8000.
    """
    user = _env_camera_user()
    password = _env_camera_password()
    raw_p = (raw or "").strip()
    if "://" not in raw_p:
        raw_p = "http://" + raw_p.lstrip("/")
    pb = urlparse(raw_p)
    if pb.scheme.lower() not in ("http", "https"):
        return [_resolve_stream_url(raw)]
    host = pb.hostname or ""
    if not host:
        return [_resolve_stream_url(raw)]
    scheme = pb.scheme
    port = pb.port
    paths = _ordered_stream_paths()

    def split_path_query(pq: str) -> tuple[str, str]:
        pq = pq.strip()
        if "?" in pq:
            po, _, qy = pq.partition("?")
            return po if po.startswith("/") else "/" + po.lstrip("/"), qy
        po = pq if pq.startswith("/") else "/" + pq.lstrip("/")
        return po, ""

    def netloc_for(p_override: int | None) -> str:
        if pb.username is not None:
            return pb.netloc
        pr = p_override if p_override is not None else port
        if user:
            uq, pq = quote(user, safe=""), quote(password, safe="")
            if pr:
                return f"{uq}:{pq}@{host}:{pr}"
            return f"{uq}:{pq}@{host}"
        if pr:
            return f"{host}:{pr}"
        return host

    urls: list[str] = []
    port_opts: list[int | None]
    if port is not None:
        port_opts = [port]
    else:
        port_opts = _probe_http_ports()

    for pr in port_opts:
        nl = netloc_for(pr)
        for rel in paths:
            path_only, qy = split_path_query(rel)
            urls.append(urlunparse((scheme, nl, path_only, "", qy, "")))

    dedup: list[str] = []
    seen_u: set[str] = set()
    for u in urls:
        if u in seen_u:
            continue
        seen_u.add(u)
        dedup.append(u)
    return dedup


def _probe_http_ports() -> list[int | None]:
    """
    Порты для перебора HTTP(S). По умолчанию только 80 (без :порт в URL) —
    иначе FFmpeg долго ждёт закрытые 8080/81 и сыпет tcp -138 в консоль.
    Расширить: VISION_CAMERA_PROBE_PORTS=80,8080,81,8000
    """
    raw = (os.environ.get("VISION_CAMERA_PROBE_PORTS") or "80").strip()
    if not raw:
        return [None]
    out: list[int | None] = []
    for part in raw.replace(";", ",").split(","):
        part = part.strip().lower()
        if not part:
            continue
        if part in ("80", "http"):
            if None not in out:
                out.append(None)
        elif part.isdigit():
            p = int(part)
            if p == 80:
                if None not in out:
                    out.append(None)
            else:
                out.append(p)
    return out if out else [None]


def _rtsp_paths_ordered() -> list[str]:
    raw = (os.environ.get("VISION_CAMERA_RTSP_PATH") or "").strip()
    user_paths: list[str] = []
    if raw:
        user_paths = [x.strip() for x in raw.replace(";", ",").split(",") if x.strip()]
    # Dahua / Hikvision / TVT / обобщённые (разные query — разные полные URL)
    defaults: tuple[str, ...] = (
        # TVT: в веб-морде «Сеть → Дополнительно → RTSP» обычно rtsp://IP:554/profile1 | profile2
        "/profile1",
        "/profile2",
        "/cam/realmonitor?channel=1&subtype=1",
        "/cam/realmonitor?channel=1&subtype=0",
        "/cam/realmonitor?channel=1&subtype=1&unicast=true&proto=Onvif",
        "/Streaming/Channels/102",
        "/Streaming/Channels/101",
        "/stream1",
        "/stream2",
        "/live",
        "/11",
        "/h264/ch1/sub/av_stream",
        "/ucast/11",
        # TVT (часто path «/» + query; sub stream = streamType=sub как в веб-морде)
        "/?chID=1&streamType=main&linkType=tcp",
        "/?chID=1&streamType=sub&linkType=tcp",
        "/?chID=1&streamType=main&linkType=tcpst",
        "/?chID=1&streamType=sub&linkType=tcpst",
        "/?chID=0&streamType=main&linkType=tcp",
        "/?chID=0&streamType=sub&linkType=tcp",
    )
    seen: set[str] = set()
    out: list[str] = []
    for p in user_paths + list(defaults):
        norm = p if p.startswith("/") else "/" + p.lstrip("/")
        key = norm.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(norm)
    return out


def _probe_rtsp_ports() -> list[int]:
    # Только 554 по умолчанию — 8554 часто закрыт → 30 с таймаут на каждый путь
    raw = (os.environ.get("VISION_CAMERA_RTSP_PORTS") or "554").strip()
    ports: list[int] = []
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if part.isdigit():
            ports.append(int(part))
    return ports if ports else [554]


def _merge_rtsp_query_credentials(path_only: str, qy: str, user: str, password: str, *, style: str) -> str:
    """style: 'onvif' (username/password) или 'dahua' (user/passwd)."""
    pairs = list(parse_qsl(qy, keep_blank_values=True))
    keys_lower = {k.lower() for k, _ in pairs}
    if style == "dahua":
        if "user" not in keys_lower:
            pairs.append(("user", user))
        if "passwd" not in keys_lower:
            pairs.append(("passwd", password))
    else:
        if "username" not in keys_lower:
            pairs.append(("username", user))
        if "password" not in keys_lower:
            pairs.append(("password", password))
    return urlencode(pairs, doseq=True)


def _merge_opencv_ffmpeg_rtsp_options() -> None:
    """TCP для RTSP (UDP часто режется); можно дополнить через VISION_FFMPEG_CAPTURE_OPTIONS."""
    extra = (os.environ.get("VISION_FFMPEG_CAPTURE_OPTIONS") or "").strip()
    cur = (os.environ.get("OPENCV_FFMPEG_CAPTURE_OPTIONS") or "").strip()
    want_tcp = "rtsp_transport;tcp"
    merged_parts: list[str] = []
    for part in (cur, extra):
        if part:
            merged_parts.extend([x.strip() for x in part.split("|") if x.strip()])
    if not any("rtsp_transport" in x for x in merged_parts):
        merged_parts.insert(0, want_tcp)
    if merged_parts:
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "|".join(merged_parts)


def _iter_rtsp_stream_urls(raw: str) -> list[str]:
    """Типовые RTSP URL для того же хоста, что и в VISION_VIDEO_SOURCE."""
    raw_p = (raw or "").strip()
    if "://" not in raw_p:
        raw_p = "http://" + raw_p.lstrip("/")
    pb = urlparse(raw_p)
    host = pb.hostname or ""
    if not host:
        return []

    user = _env_rtsp_user()
    password = _env_rtsp_password()

    paths = _rtsp_paths_ordered()
    ports = _probe_rtsp_ports()

    def split_path_query(pq: str) -> tuple[str, str]:
        pq = pq.strip()
        if "?" in pq:
            po, _, qy = pq.partition("?")
            return po if po.startswith("/") else "/" + po.lstrip("/"), qy
        po = pq if pq.startswith("/") else "/" + pq.lstrip("/")
        return po, ""

    urls: list[str] = []
    for port in ports:
        net_plain = f"{host}:{port}"
        for rel in paths:
            path_only, qy = split_path_query(rel)
            if user:
                uq, pq = quote(user, safe=""), quote(password, safe="")
                # 1) классика user:pass@host
                urls.append(urlunparse(("rtsp", f"{uq}:{pq}@{host}:{port}", path_only, "", qy, "")))
                # 2) логин только в query (часть Dahua / встроенных вебов)
                q2 = _merge_rtsp_query_credentials(path_only, qy, user, password, style="onvif")
                urls.append(urlunparse(("rtsp", net_plain, path_only, "", q2, "")))
                # 3) user + passwd в query (ещё один распространённый вариант)
                q3 = _merge_rtsp_query_credentials(path_only, qy, user, password, style="dahua")
                urls.append(urlunparse(("rtsp", net_plain, path_only, "", q3, "")))
            else:
                urls.append(urlunparse(("rtsp", net_plain, path_only, "", qy, "")))

    dedup: list[str] = []
    seen_u: set[str] = set()
    for u in urls:
        if u in seen_u:
            continue
        seen_u.add(u)
        dedup.append(u)
    return dedup


def _url_scheme(url: str) -> str:
    u = (url or "").strip()
    if "://" not in u:
        return ""
    return (urlparse(u).scheme or "").lower()


class HttpMjpegFallbackCapture:
    """
    Обход OpenCV на Windows: CAP_ANY для http://… иногда выбирает CAP_IMAGES и падает на URL с %.
    Тянем поток через requests и вырезаем JPEG по маркерам ff d8 / ff d9.
    Совместимо с VideoCapture: isOpened(), read(), release(), set() — заглушка.
    """

    def __init__(self, url: str) -> None:
        self._url = url
        self._r = None
        self._buf = bytearray()
        self._opened = False

    def connect(self) -> bool:
        try:
            import requests

            self._r = requests.get(self._url, stream=True, timeout=(8, 120), allow_redirects=True)
            self._r.raise_for_status()
            self._opened = True
            return True
        except Exception as e:
            log.debug("HTTP MJPEG: не удалось подключиться: %s", e)
            self._release_response()
            self._opened = False
            return False

    def _release_response(self) -> None:
        if self._r is not None:
            try:
                self._r.close()
            except Exception:
                pass
        self._r = None

    def isOpened(self) -> bool:
        return self._opened and self._r is not None

    def set(self, _prop: int, _val: float) -> bool:
        return True

    def read(self) -> tuple[bool, np.ndarray | None]:
        if not self.isOpened():
            return False, None
        assert self._r is not None
        raw = self._r.raw
        for _ in range(2000):
            jpeg = self._pop_jpeg_from_buffer()
            if jpeg is not None:
                arr = np.frombuffer(jpeg, dtype=np.uint8)
                img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                if img is not None and img.size > 0:
                    return True, img
            try:
                chunk = raw.read(8192)
            except Exception as e:
                log.warning("HTTP MJPEG: read error: %s", e)
                return False, None
            if not chunk:
                return False, None
            self._buf.extend(chunk)
            if len(self._buf) > 12_000_000:
                del self._buf[:6_000_000]
        return False, None

    def _pop_jpeg_from_buffer(self) -> bytes | None:
        buf = self._buf
        soi = buf.find(b"\xff\xd8")
        if soi < 0:
            if len(buf) > 512_000:
                del buf[:256_000]
            return None
        if soi > 0:
            del buf[:soi]
        eoi = buf.find(b"\xff\xd9", 2)
        if eoi < 0:
            return None
        jpeg = bytes(buf[: eoi + 2])
        del buf[: eoi + 2]
        return jpeg

    def release(self) -> None:
        self._release_response()
        self._buf.clear()
        self._opened = False


def _http_bases_for_snapshots(host: str) -> list[str]:
    """Те же порты, что и для HTTP MJPEG (VISION_CAMERA_PROBE_PORTS)."""
    bases: list[str] = []
    for pr in _probe_http_ports():
        if pr is None:
            bases.append(f"http://{host}")
        else:
            bases.append(f"http://{host}:{pr}")
    return bases or [f"http://{host}"]


def _snapshot_url_bases(host: str) -> list[str]:
    """HTTP-базы для snapshot; при VISION_SNAPSHOT_TRY_HTTPS=1 — ещё https://хост и :8443."""
    bases = list(_http_bases_for_snapshots(host))
    if _env_bool("VISION_SNAPSHOT_TRY_HTTPS", False):
        seen = set(bases)
        for b in (f"https://{host}", f"https://{host}:8443"):
            if b not in seen:
                bases.append(b)
                seen.add(b)
    return bases


def _http_snapshot_cred_query_urls(host: str, user: str, password: str) -> list[str]:
    """Логин в query (часть Dahua-клонов и старых прошивок), без Digest — пробуем также без auth в connect()."""
    if not user:
        return []
    triples = (
        {"username": user, "password": password},
        {"user": user, "passwd": password},
        {"loginuse": user, "loginpas": password},
    )
    rels: list[str] = []
    for d in triples:
        q = urlencode(d)
        rels.extend(
            (
                f"/cgi-bin/snapshot.cgi?channel=1&subtype=0&{q}",
                f"/cgi-bin/snapshot.cgi?channel=1&subtype=1&{q}",
                f"/cgi-bin/snapshot.cgi?{q}",
                f"/cgi-bin/current.cgi?cmd=snap&{q}",
            )
        )
    seen: set[str] = set()
    out: list[str] = []
    for base in _snapshot_url_bases(host):
        for rel in rels:
            u = base.rstrip("/") + rel
            if u in seen:
                continue
            seen.add(u)
            out.append(u)
    return out


def _collect_snapshot_probe_urls(host: str, user: str, password: str) -> list[str]:
    """Все snapshot URL: типовые пути + варианты с учёткой в query."""
    chunks = [_http_snapshot_probe_urls(host)]
    if user and password:
        chunks.append(_http_snapshot_cred_query_urls(host, user, password))
    seen: set[str] = set()
    out: list[str] = []
    for part in chunks:
        for u in part:
            if u in seen:
                continue
            seen.add(u)
            out.append(u)
    return out


def _http_snapshot_probe_urls(host: str) -> list[str]:
    paths = (
        "/cgi-bin/snapshot.cgi",
        "/cgi-bin/snapshot.cgi?channel=1&subtype=0",
        "/cgi-bin/snapshot.cgi?channel=1&subtype=1",
        "/cgi-bin/snapshot.cgi?channel=0&subtype=0",
        "/cgi-bin/snapshot.cgi?channel=0&subtype=1",
        "/cgi-bin/snapshot.cgi?channel=1",
        "/cgi-bin/snapshot.cgi?chn=0",
        "/cgi-bin/snapshot.cgi?channel=0",
        "/cgi-bin/current.cgi?cmd=snap",
        "/ISAPI/Streaming/channels/1/picture",
        "/onvif-http/snapshot",
        "/snap.jpg",
        "/snap.jpg?JpegCam=0",
        "/snap.jpg?JpegCam=1",
        "/jpg/image.jpg",
    )
    seen: set[str] = set()
    out: list[str] = []
    for base in _snapshot_url_bases(host):
        for p in paths:
            u = base.rstrip("/") + (p if p.startswith("/") else "/" + p)
            if u in seen:
                continue
            seen.add(u)
            out.append(u)
    return out


class HttpSnapshotPollCapture:
    """
    Когда RTSP даёт 401, а веб-логин тот же — часто работает GET JPEG (Digest или Basic).
    Не поток: опрос снимков; FPS ограничен (VISION_SNAPSHOT_MIN_INTERVAL_SEC).
    """

    def __init__(self, urls: list[str], user: str, password: str) -> None:
        self._urls = list(urls)
        self._user = user
        self._password = password
        self._session = None
        self._auth: object | None = None
        self._working: str | None = None
        self._opened = False
        self._last_pull = -1e9
        self._min_itv = max(0.05, _env_float("VISION_SNAPSHOT_MIN_INTERVAL_SEC", 0.12))

    def connect(self) -> bool:
        import requests
        from requests.auth import HTTPDigestAuth

        self._session = requests.Session()
        tls_insecure = _env_bool("VISION_SNAPSHOT_INSECURE_TLS", False)

        def verify_for(url: str) -> bool:
            if not url.lower().startswith("https:"):
                return True
            return not tls_insecure

        if tls_insecure:
            try:
                import urllib3

                urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            except Exception:
                pass

        auths: tuple[object | None, ...] = (
            HTTPDigestAuth(self._user, self._password),
            (self._user, self._password),
            None,
        )
        for u in self._urls:
            vfy = verify_for(u)
            for auth in auths:
                try:
                    kw: dict = {"timeout": 6, "verify": vfy}
                    if auth is not None:
                        kw["auth"] = auth
                    r = self._session.get(u, **kw)
                    if (
                        r.ok
                        and len(r.content) > 200
                        and r.content[:2] == b"\xff\xd8"
                    ):
                        self._working = u
                        self._auth = auth
                        self._opened = True
                        log.info("HTTP snapshot: %s", _safe_url_for_log(u))
                        return True
                except Exception:
                    continue
        self._session.close()
        self._session = None
        log.warning(
            "HTTP snapshot: ни один URL не дал JPEG. Попробуйте VISION_CAMERA_PROBE_PORTS=80,8080; "
            "для HTTPS — VISION_SNAPSHOT_TRY_HTTPS=1 и при самоподписанном сертификате VISION_SNAPSHOT_INSECURE_TLS=1; "
            "в веб-морде камеры включите RTSP/снимок и проверьте пароль потока."
        )
        return False

    def isOpened(self) -> bool:
        return self._opened and self._working is not None and self._session is not None

    def set(self, _prop: int, _val: float) -> bool:
        return True

    def read(self) -> tuple[bool, np.ndarray | None]:
        if not self.isOpened():
            return False, None
        assert self._session is not None and self._working is not None
        now = time.perf_counter()
        dt = now - self._last_pull
        if dt < self._min_itv:
            time.sleep(self._min_itv - dt)
        try:
            vfy = True
            if self._working.lower().startswith("https:") and _env_bool(
                "VISION_SNAPSHOT_INSECURE_TLS", False
            ):
                vfy = False
            r = self._session.get(
                self._working, auth=self._auth, timeout=6, verify=vfy
            )
            if not r.ok or len(r.content) < 200 or r.content[:2] != b"\xff\xd8":
                self._last_pull = time.perf_counter()
                return False, None
            arr = np.frombuffer(r.content, dtype=np.uint8)
            img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            self._last_pull = time.perf_counter()
            if img is not None and img.size > 0:
                return True, img
        except Exception:
            pass
        self._last_pull = time.perf_counter()
        return False, None

    def release(self) -> None:
        if self._session is not None:
            try:
                self._session.close()
            except Exception:
                pass
        self._session = None
        self._working = None
        self._auth = None
        self._opened = False

    @property
    def working_url(self) -> str | None:
        return self._working


class FfmpegRtspMjpegPipeCapture:
    """
    RTSP через системный ffmpeg в stdout как MJPEG — обход OpenCV/libav при 401 Digest
    или иных отличиях от полноценного ffmpeg.
    Нужен ffmpeg в PATH или VISION_FFMPEG_BIN.
    """

    def __init__(self, url: str, *, ffmpeg_exe: str) -> None:
        self._url = url
        self._ffmpeg = ffmpeg_exe
        self._p: subprocess.Popen | None = None
        self._buf = bytearray()
        self._opened = False
        self._stderr_logged = False

    def start(self) -> bool:
        cmd = [
            self._ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-rtsp_transport",
            "tcp",
            "-i",
            self._url,
            "-an",
            "-f",
            "mjpeg",
            "-q:v",
            "5",
            "-",
        ]
        kw: dict = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
        }
        if sys.platform == "win32":
            kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            self._p = subprocess.Popen(cmd, **kw)
        except Exception as e:
            log.debug("ffmpeg CLI spawn: %s", e)
            return False
        self._opened = True
        return True

    def isOpened(self) -> bool:
        return self._opened and self._p is not None and self._p.poll() is None

    def set(self, _prop: int, _val: float) -> bool:
        return True

    def read(self) -> tuple[bool, np.ndarray | None]:
        if not self.isOpened():
            return False, None
        assert self._p is not None and self._p.stdout is not None
        raw = self._p.stdout
        for _ in range(4000):
            jpeg = self._pop_jpeg_from_buffer()
            if jpeg is not None:
                arr = np.frombuffer(jpeg, dtype=np.uint8)
                img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                if img is not None and img.size > 0:
                    return True, img
            try:
                chunk = raw.read(16384)
            except Exception:
                return False, None
            if not chunk:
                return False, None
            self._buf.extend(chunk)
            if len(self._buf) > 12_000_000:
                del self._buf[:6_000_000]
        return False, None

    def _pop_jpeg_from_buffer(self) -> bytes | None:
        buf = self._buf
        soi = buf.find(b"\xff\xd8")
        if soi < 0:
            if len(buf) > 512_000:
                del buf[:256_000]
            return None
        if soi > 0:
            del buf[:soi]
        eoi = buf.find(b"\xff\xd9", 2)
        if eoi < 0:
            return None
        jpeg = bytes(buf[: eoi + 2])
        del buf[: eoi + 2]
        return jpeg

    def release(self) -> None:
        if self._p is not None:
            try:
                self._p.terminate()
                self._p.wait(timeout=4)
            except Exception:
                try:
                    self._p.kill()
                except Exception:
                    pass
            rc = self._p.returncode
            err_txt = ""
            if self._p.stderr is not None:
                try:
                    err_b = self._p.stderr.read() or b""
                    err_txt = err_b.decode("utf-8", errors="replace").strip()
                except Exception:
                    pass
            if err_txt and not self._stderr_logged:
                self._stderr_logged = True
                snippet = err_txt[:1800]
                if rc not in (0, None):
                    log.warning("ffmpeg RTSP завершился (rc=%s): %s", rc, snippet)
                else:
                    log.debug("ffmpeg stderr: %s", snippet[:600])
            self._p = None
        self._buf.clear()
        self._opened = False


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    v = (os.environ.get(name) or "").strip().lower()
    if not v:
        return default
    return v in ("1", "true", "yes", "on")


def _discover_v4l_usb_capture_paths() -> list[str]:
    """
    На Raspberry Pi индекс OpenCV 0 часто попадает не в USB-камеру.
    Ищем /dev/video* под устройствами Camera/USB в выводе v4l2-ctl.
    """
    extra = (os.environ.get("VISION_V4L_DEVICES") or "").strip()
    if extra:
        return [p.strip() for p in extra.split(",") if p.strip().startswith("/dev/video")]

    paths: list[str] = []
    try:
        proc = subprocess.run(
            ["v4l2-ctl", "--list-devices"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
        text = proc.stdout or ""
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return paths

    usb_block = False
    for line in text.splitlines():
        if line.startswith(("\t", " ")):
            dev = line.strip()
            if usb_block and dev.startswith("/dev/video"):
                paths.append(dev)
        else:
            low = line.lower()
            usb_block = any(k in low for k in ("camera", "webcam", "uvc", "usb"))
    return paths


@dataclass
class AimHint:
    horizontal: str  # left | right | center
    vertical: str  # up | down | center


@dataclass
class TrackerSnapshot:
    track_id: int | None
    cx: float
    cy: float
    confidence: float
    lost: bool
    backend: str
    hint: AimHint | None
    fps: float = 0.0
    frame_w: int = 0
    frame_h: int = 0
    cls_name: str | None = None
    camera_status: str = ""  # ok | no_device | no_frames | …
    target_locked: bool = False
    lock_note: str = ""  # пусто | ok | none_in_center | no_model | …
    detection_enabled: bool = True


def _import_picamera2():
    try:
        from picamera2 import Picamera2

        return Picamera2
    except ImportError:
        extra = "/usr/lib/python3/dist-packages"
        if extra not in sys.path:
            sys.path.append(extra)
        try:
            from picamera2 import Picamera2

            return Picamera2
        except ImportError:
            return None


class Picamera2Capture:
    """CSI Raspberry Pi Camera через Picamera2. Совместимо с VideoCapture: isOpened/read/release."""

    def __init__(self, width: int = 640, height: int = 480) -> None:
        Picamera2 = _import_picamera2()
        if Picamera2 is None:
            raise RuntimeError("picamera2 не установлен (sudo apt install python3-picamera2)")
        self._cam = Picamera2()
        size = (max(320, int(width)), max(240, int(height)))
        cfg = self._cam.create_preview_configuration(main={"size": size, "format": "RGB888"})
        self._cam.configure(cfg)
        self._cam.start()
        time.sleep(0.25)
        self._opened = True

    def isOpened(self) -> bool:
        return self._opened

    def set(self, _prop: int, _val: float) -> bool:
        return True

    def get(self, _prop: int) -> float:
        return 0.0

    def read(self) -> tuple[bool, np.ndarray | None]:
        if not self._opened:
            return False, None
        try:
            frame = self._cam.capture_array()
        except Exception as e:
            log.debug("Picamera2 capture: %s", e)
            return False, None
        if frame is None:
            return False, None
        if frame.ndim == 3 and frame.shape[2] == 3:
            frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
        return True, frame

    def release(self) -> None:
        self._opened = False
        try:
            self._cam.stop()
        except Exception:
            pass
        try:
            self._cam.close()
        except Exception:
            pass


class VisionPipeline:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._snap = TrackerSnapshot(
            track_id=None,
            cx=0.0,
            cy=0.0,
            confidence=0.0,
            lost=True,
            backend="stub",
            hint=None,
            camera_status="init",
            target_locked=False,
            lock_note="",
        )
        self._stop = threading.Event()
        self._capture_thread: threading.Thread | None = None
        self._inference_thread: threading.Thread | None = None
        self._preview_jpeg: bytes | None = None
        self._preview_seq = 0
        self._preview_ready = threading.Condition()
        self._frame_seq = 0
        self._frame_lock = threading.Lock()
        self._frame_cond = threading.Condition(self._frame_lock)
        self._pending_frame: np.ndarray | None = None
        self._pending_frame_seq = 0
        self._infer_seq = 0
        self._capture_ema_fps = 0.0
        self._lock_detect_every = max(1, _env_int("VISION_LOCK_DETECT_EVERY", 2))
        _ot = (os.environ.get("VISION_OPENCV_TRACKER") or "on").strip().lower()
        self._opencv_tracker_enabled = _ot not in ("0", "false", "off", "no")
        self._opencv_tracker_kind = (os.environ.get("VISION_OPENCV_TRACKER_KIND") or "CSRT").strip().upper()
        self._opencv_tracker: object | None = None
        self._lock_last_conf = 0.0
        self._lock_last_cls = 0
        self._cached_picked_rect: tuple[float, float, float, float] | None = None
        self._cached_cid_name: str | None = None

        self._source = os.environ.get("VISION_VIDEO_SOURCE", "0")
        self._width = _env_int("VISION_CAPTURE_WIDTH", 640)
        self._preview_max_w = _env_int("VISION_PREVIEW_MAX_WIDTH", 640)
        self._buffer_drain = max(0, _env_int("VISION_BUFFER_DRAIN", 3))
        self._detect_every = max(1, _env_int("VISION_DETECT_EVERY", 1))
        self._yolo_imgsz = _env_int("VISION_YOLO_IMGSZ", 320)
        self._preview_jpeg_quality = max(30, min(95, _env_int("VISION_PREVIEW_JPEG_QUALITY", 55)))
        self._dead = _env_float("VISION_CENTER_DEADBAND", 0.08)
        self._target_class = (os.environ.get("VISION_TARGET_CLASS") or "").strip().lower()
        self._yolo_model = os.environ.get("VISION_YOLO_MODEL", "yolo11n.pt")

        self._model = None
        self._backend = "stub"
        self._try_load_yolo()

        self._cmd_lock = threading.Lock()
        self._pending_lock_center = False
        self._pending_unlock = False
        self._detection_enabled = True
        self._follow_lock = False
        self._lock_cls_id: int | None = None
        self._lock_track_id: int | None = None  # ByteTrack id с YOLO track(), приоритет над классом
        self._lock_prev_xy: tuple[float, float] | None = None
        self._lock_note: str = ""
        self._tracker_cfg = os.environ.get("VISION_TRACKER", "bytetrack.yaml")

        self._monitor_lock = threading.Lock()
        self._monitor_enabled = False
        self._monitor_started_at = 0.0
        self._monitor_stop_at: float | None = None
        self._monitor_events_written = 0
        self._monitor_classes: set[str] = set()
        self._monitor_min_conf = _env_float("VISION_MONITOR_MIN_CONF", 0.35)
        self._monitor_cooldown_s = _env_float("VISION_MONITOR_COOLDOWN_S", 45.0)
        self._monitor_log_path = os.environ.get("VISION_MONITOR_LOG_PATH", "monitor-events.jsonl")
        self._monitor_last_emit: dict[str, float] = {}

        _evp = (os.environ.get("VISION_EVENT_LOG_PATH") or "").strip()
        if _evp.lower() in ("", "0", "false", "off", "none"):
            self._event_log_path = ""
        else:
            self._event_log_path = _evp
        self._event_log_lock = threading.Lock()
        self._event_summary_interval = max(5.0, _env_float("VISION_EVENT_SUMMARY_SEC", 30.0))

        _merge_opencv_ffmpeg_rtsp_options()

    def _vision_event(self, kind: str, data: dict | None = None) -> None:
        """Краткий JSONL-журнал: камера, кадры, цель, lock (без паролей в URL)."""
        if not self._event_log_path:
            return
        rec = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "ts_epoch": time.time(),
            "kind": kind,
            **(data or {}),
        }
        line = json.dumps(rec, ensure_ascii=True)
        try:
            with self._event_log_lock:
                with open(self._event_log_path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
        except OSError as e:
            log.warning("VISION_EVENT_LOG_PATH write failed: %s", e)

    def _try_load_yolo(self) -> None:
        try:
            from ultralytics import YOLO

            self._model = YOLO(self._yolo_model)
            self._backend = "yolo"
            log.info("YOLO загружен: %s", self._yolo_model)
        except Exception as e:
            self._model = None
            self._backend = "stub"
            log.warning("YOLO недоступен (%s), работаем без детекции", e)

    @classmethod
    def from_env(cls) -> VisionPipeline:
        return cls()

    def start(self) -> None:
        if self._capture_thread and self._capture_thread.is_alive():
            return
        self._stop.clear()
        self._infer_seq = 0
        self._pending_frame = None
        self._pending_frame_seq = 0
        self._capture_thread = threading.Thread(target=self._run_capture, name="vision-capture", daemon=True)
        self._inference_thread = threading.Thread(target=self._run_inference, name="vision-infer", daemon=True)
        self._capture_thread.start()
        self._inference_thread.start()
        log.info(
            "Vision: capture + inference потоки (lock_detect_every=%s, opencv_tracker=%s), source=%s",
            self._lock_detect_every,
            self._opencv_tracker_enabled,
            self._source,
        )

    def stop(self) -> None:
        self._stop.set()
        with self._frame_cond:
            self._frame_cond.notify_all()
        for th in (self._capture_thread, self._inference_thread):
            if th:
                th.join(timeout=3.0)
        self._capture_thread = None
        self._inference_thread = None
        self._tracker_reset()
        log.info("Потоки vision остановлены")

    def request_lock_center_target(self) -> None:
        with self._cmd_lock:
            self._pending_lock_center = True

    def request_unlock_target(self) -> None:
        with self._cmd_lock:
            self._pending_unlock = True

    def _consume_pending_cmds(self) -> tuple[bool, bool]:
        """(lock_center, unlock)"""
        with self._cmd_lock:
            lc = self._pending_lock_center
            u = self._pending_unlock
            self._pending_lock_center = False
            self._pending_unlock = False
        return lc, u

    def is_detection_enabled(self) -> bool:
        with self._cmd_lock:
            return self._detection_enabled

    def set_detection_enabled(self, enabled: bool) -> None:
        with self._cmd_lock:
            self._detection_enabled = bool(enabled)
        if not enabled:
            self.request_unlock_target()
            with self._lock:
                self._cached_picked_rect = None
                self._cached_cid_name = None
        log.info("Detection %s", "enabled" if enabled else "disabled (preview only)")

    def snapshot(self) -> TrackerSnapshot:
        with self._lock:
            return TrackerSnapshot(
                track_id=self._snap.track_id,
                cx=self._snap.cx,
                cy=self._snap.cy,
                confidence=self._snap.confidence,
                lost=self._snap.lost,
                backend=self._snap.backend,
                hint=self._snap.hint,
                fps=self._snap.fps,
                frame_w=self._snap.frame_w,
                frame_h=self._snap.frame_h,
                cls_name=self._snap.cls_name,
                camera_status=self._snap.camera_status,
                target_locked=self._snap.target_locked,
                lock_note=self._snap.lock_note,
                detection_enabled=self.is_detection_enabled(),
            )

    def get_preview_jpeg(self) -> bytes | None:
        with self._lock:
            return self._preview_jpeg

    def wait_preview_update(self, last_seq: int, timeout: float = 1.0) -> tuple[bytes | None, int]:
        """Ждёт новый JPEG (для MJPEG без лишней задержки и повторов одного кадра)."""
        deadline = time.monotonic() + max(0.01, timeout)
        with self._preview_ready:
            while self._preview_seq == last_seq and not self._stop.is_set():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._preview_ready.wait(timeout=remaining)
            with self._lock:
                return self._preview_jpeg, self._preview_seq

    def _publish_preview_jpeg(self, data: bytes | None) -> None:
        if not data:
            return
        with self._lock:
            self._preview_jpeg = data
            self._preview_seq += 1
        with self._preview_ready:
            self._preview_ready.notify_all()

    def _read_fresh_frame(self, cap) -> tuple[bool, np.ndarray | None]:
        """Сбрасывает буфер камеры — иначе в браузере секунды задержки."""
        ok = False
        frame: np.ndarray | None = None
        reads = max(1, self._buffer_drain + 1)
        for _ in range(reads):
            ok, frame = cap.read()
            if not ok or frame is None:
                break
        return ok, frame

    def _build_vis(
        self,
        frame: np.ndarray,
        picked_rect: tuple[float, float, float, float] | None,
        *,
        cid_name: str | None,
        w: int,
        h: int,
    ) -> np.ndarray:
        vis = frame
        if picked_rect is not None:
            px1, py1, px2, py2 = picked_rect
            col = (0, 140, 255) if self._follow_lock else (0, 220, 0)
            cv2.rectangle(vis, (int(px1), int(py1)), (int(px2), int(py2)), col, 2)
            if self._follow_lock and cid_name:
                lock_lbl = f"LOCK {cid_name}"
                if self._lock_track_id is not None:
                    lock_lbl += f" id{self._lock_track_id}"
                cv2.putText(
                    vis,
                    lock_lbl,
                    (int(px1), max(16, int(py1) - 6)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.55,
                    col,
                    2,
                    cv2.LINE_AA,
                )
        cv2.line(vis, (w // 2, 0), (w // 2, h), (60, 60, 60), 1)
        cv2.line(vis, (0, h // 2), (w, h // 2), (60, 60, 60), 1)
        if vis.shape[1] > self._preview_max_w > 0:
            sc = self._preview_max_w / float(vis.shape[1])
            vis = cv2.resize(vis, (int(vis.shape[1] * sc), int(vis.shape[0] * sc)))
        return vis

    def _encode_vis_jpeg(self, vis: np.ndarray) -> bytes | None:
        ok_jpg, buf = cv2.imencode(
            ".jpg",
            vis,
            [int(cv2.IMWRITE_JPEG_QUALITY), int(self._preview_jpeg_quality)],
        )
        return buf.tobytes() if ok_jpg else None

    def _yolo_infer_kwargs(self, conf_infer: float) -> dict:
        kw: dict = {"verbose": False, "conf": conf_infer}
        if self._yolo_imgsz > 0:
            kw["imgsz"] = self._yolo_imgsz
        return kw

    def _push_raw_preview(self, frame: np.ndarray, w: int, h: int) -> None:
        vis = frame
        if vis.shape[1] > self._preview_max_w > 0:
            sc = self._preview_max_w / float(vis.shape[1])
            vis = cv2.resize(vis, (int(vis.shape[1] * sc), int(vis.shape[0] * sc)))
        self._publish_preview_jpeg(self._encode_vis_jpeg(vis))

    def _push_fast_preview(self, frame: np.ndarray, w: int, h: int) -> None:
        if not self.is_detection_enabled():
            self._push_raw_preview(frame, w, h)
            return
        vis = self._build_vis(
            frame.copy(),
            self._cached_picked_rect,
            cid_name=self._cached_cid_name,
            w=w,
            h=h,
        )
        self._publish_preview_jpeg(self._encode_vis_jpeg(vis))

    def start_monitor(
        self,
        *,
        classes: list[str] | None = None,
        min_conf: float | None = None,
        cooldown_s: float | None = None,
        duration_s: float | None = None,
    ) -> dict:
        cls_set = {c.strip().lower() for c in (classes or []) if c.strip()}
        with self._monitor_lock:
            self._monitor_enabled = True
            self._monitor_started_at = time.time()
            self._monitor_stop_at = (
                self._monitor_started_at + float(duration_s)
                if duration_s is not None and duration_s > 0
                else None
            )
            self._monitor_events_written = 0
            self._monitor_last_emit.clear()
            self._monitor_classes = cls_set
            if min_conf is not None:
                self._monitor_min_conf = float(max(0.01, min(1.0, min_conf)))
            if cooldown_s is not None:
                self._monitor_cooldown_s = float(max(0.0, cooldown_s))
        log.info(
            "Monitor started: classes=%s conf=%.2f cooldown=%.1fs duration=%s",
            sorted(self._monitor_classes) if self._monitor_classes else ["*"],
            self._monitor_min_conf,
            self._monitor_cooldown_s,
            duration_s,
        )
        return self.monitor_status()

    def stop_monitor(self) -> dict:
        with self._monitor_lock:
            self._monitor_enabled = False
            self._monitor_stop_at = None
            self._monitor_last_emit.clear()
        log.info("Monitor stopped")
        return self.monitor_status()

    def monitor_status(self) -> dict:
        with self._monitor_lock:
            started_at = self._monitor_started_at
            stop_at = self._monitor_stop_at
            enabled = self._monitor_enabled
            classes = sorted(self._monitor_classes)
            events_written = self._monitor_events_written
            min_conf = self._monitor_min_conf
            cooldown_s = self._monitor_cooldown_s
            log_path = self._monitor_log_path
        return {
            "enabled": enabled,
            "started_at": started_at,
            "stop_at": stop_at,
            "classes": classes,
            "min_conf": min_conf,
            "cooldown_s": cooldown_s,
            "events_written": events_written,
            "log_path": log_path,
        }

    def read_monitor_logs(self, *, limit: int = 500) -> list[dict]:
        path = self._monitor_log_path
        if not os.path.exists(path):
            return []
        max_items = max(1, min(int(limit), 5000))
        out: list[dict] = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
        if len(out) > max_items:
            out = out[-max_items:]
        return out

    def monitor_summary(self, *, hours: float | None = None) -> dict:
        rows = self.read_monitor_logs(limit=5000)
        ts_min = None
        if hours is not None and hours > 0:
            ts_min = time.time() - hours * 3600.0
        by_class: dict[str, int] = {}
        total = 0
        for r in rows:
            ts = float(r.get("ts_epoch", 0.0) or 0.0)
            if ts_min is not None and ts < ts_min:
                continue
            cls_name = str(r.get("class", "?"))
            by_class[cls_name] = by_class.get(cls_name, 0) + 1
            total += 1
        return {"total_events": total, "by_class": by_class}

    def _monitor_write_event(
        self,
        *,
        cls_name: str,
        conf: float,
        track_id: int | None,
        bbox: tuple[float, float, float, float],
        frame_w: int,
        frame_h: int,
    ) -> None:
        with self._monitor_lock:
            if not self._monitor_enabled:
                return
            now = time.time()
            if self._monitor_stop_at is not None and now >= self._monitor_stop_at:
                self._monitor_enabled = False
                return

            bx = 0.5 * (bbox[0] + bbox[2])
            by = 0.5 * (bbox[1] + bbox[3])
            qx = int((bx / max(1.0, frame_w)) * 12.0)
            qy = int((by / max(1.0, frame_h)) * 12.0)
            key = f"{cls_name}:t{track_id}" if track_id is not None and track_id >= 0 else f"{cls_name}:z{qx}:{qy}"
            last_ts = self._monitor_last_emit.get(key, 0.0)
            if (now - last_ts) < self._monitor_cooldown_s:
                return
            self._monitor_last_emit[key] = now

            rec = {
                "ts_epoch": now,
                "ts": datetime.now(timezone.utc).isoformat(),
                "class": cls_name,
                "confidence": round(float(conf), 4),
                "track_id": int(track_id) if track_id is not None else None,
                "bbox": [round(float(v), 1) for v in bbox],
                "frame_w": int(frame_w),
                "frame_h": int(frame_h),
            }
            with open(self._monitor_log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=True) + "\n")
            self._monitor_events_written += 1

    def _monitor_collect_detections(
        self,
        *,
        names: dict,
        xyxy: np.ndarray,
        confs: np.ndarray,
        clss: np.ndarray,
        track_ids: np.ndarray,
        frame_w: int,
        frame_h: int,
    ) -> None:
        with self._monitor_lock:
            enabled = self._monitor_enabled
            min_conf = self._monitor_min_conf
            allow_classes = set(self._monitor_classes)
            stop_at = self._monitor_stop_at
        if not enabled:
            return
        if stop_at is not None and time.time() >= stop_at:
            with self._monitor_lock:
                self._monitor_enabled = False
            return

        n = len(xyxy)
        for i in range(n):
            cf = float(confs[i])
            if cf < min_conf:
                continue
            cid = int(clss[i])
            cls_name = (names.get(cid) or str(cid)).lower()
            if allow_classes and cls_name not in allow_classes:
                continue
            row = xyxy[i]
            tid = int(track_ids[i]) if i < len(track_ids) and int(track_ids[i]) >= 0 else None
            self._monitor_write_event(
                cls_name=cls_name,
                conf=cf,
                track_id=tid,
                bbox=(float(row[0]), float(row[1]), float(row[2]), float(row[3])),
                frame_w=frame_w,
                frame_h=frame_h,
            )

    def _hints(self, cx: float, cy: float) -> AimHint:
        h = "center"
        if cx < -self._dead:
            h = "left"
        elif cx > self._dead:
            h = "right"
        v = "center"
        if cy < -self._dead:
            v = "up"
        elif cy > self._dead:
            v = "down"
        return AimHint(horizontal=h, vertical=v)

    def _camera_indices_to_try(self) -> list[int]:
        raw = os.environ.get("VISION_CAMERA_FALLBACK", "0,1,2")
        out: list[int] = []
        for part in raw.split(","):
            part = part.strip()
            if part.isdigit():
                out.append(int(part))
        return out or [0]

    def _open_capture_v4l_path(self, dev_path: str) -> cv2.VideoCapture | None:
        """Linux/Pi: явный путь /dev/video* + V4L2 (надёжнее, чем индекс 0)."""
        if not dev_path.startswith("/dev/video") or not os.path.exists(dev_path):
            return None
        api = int(cv2.CAP_V4L2) if hasattr(cv2, "CAP_V4L2") else int(cv2.CAP_ANY)
        try:
            cap = cv2.VideoCapture(dev_path, api)
        except Exception as e:
            log.debug("VideoCapture(%s): %s", dev_path, e)
            return None
        if not cap.isOpened():
            cap.release()
            return None
        try:
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass
        if self._width > 0:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, float(self._width))
        ok, frame = cap.read()
        if ok and frame is not None and frame.size > 0:
            log.info("Камера %s (V4L2) размер %s×%s", dev_path, frame.shape[1], frame.shape[0])
            self._vision_event(
                "camera_open_ok",
                {"url": dev_path, "backend": "v4l2_path", "w": int(frame.shape[1]), "h": int(frame.shape[0])},
            )
            return cap
        cap.release()
        log.warning("V4L2 %s открылся, но кадр пустой — попробуй /dev/video1", dev_path)
        return None

    def _open_capture_index(self, idx: int) -> cv2.VideoCapture | None:
        """Windows: без CAP_DSHOW часто isOpened()==True, но кадры 0×0 или read() пустой."""
        if sys.platform != "win32":
            dev = f"/dev/video{idx}"
            cap = self._open_capture_v4l_path(dev)
            if cap is not None:
                return cap

        apis: list[int] = []
        if sys.platform == "win32":
            if hasattr(cv2, "CAP_DSHOW"):
                apis.append(int(cv2.CAP_DSHOW))
            if hasattr(cv2, "CAP_MSMF"):
                apis.append(int(cv2.CAP_MSMF))
        apis.append(int(cv2.CAP_ANY))

        for api in apis:
            try:
                cap = cv2.VideoCapture(idx, api)
            except Exception:
                continue
            if not cap.isOpened():
                cap.release()
                continue
            try:
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            except Exception:
                pass
            if self._width > 0:
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, float(self._width))
            ok, frame = cap.read()
            if ok and frame is not None and frame.size > 0:
                log.info("Камера idx=%s api=%s размер %s×%s", idx, api, frame.shape[1], frame.shape[0])
                return cap
            cap.release()

        return None

    def _open_network_capture(
        self, url: str, *, quiet: bool = False
    ) -> cv2.VideoCapture | HttpMjpegFallbackCapture | FfmpegRtspMjpegPipeCapture | None:
        """
        HTTP(S): только CAP_FFMPEG — CAP_ANY на Windows часто даёт CAP_IMAGES и ошибку на URL с %.
        Если FFMPEG не дал кадр — fallback: MJPEG по HTTP через requests.
        RTSP: только FFMPEG. Локальный файл: FFMPEG, затем CAP_ANY.
        quiet: при переборе URL — без лишних vision-событий и с log.debug для неудач.
        """
        safe = _safe_url_for_log(url)
        if not quiet:
            self._vision_event("camera_open_attempt", {"url": safe})
        scheme = _url_scheme(url)

        if scheme in ("http", "https"):
            ffmpeg_ok = self._try_opencv_ffmpeg_only(url, safe, quiet=quiet)
            if ffmpeg_ok is not None:
                return ffmpeg_ok
            if quiet:
                log.debug("FFMPEG не подошёл — MJPEG через requests: %s", safe)
            else:
                log.info("FFMPEG не подошёл для HTTP — пробуем MJPEG через requests")
            fb = HttpMjpegFallbackCapture(url)
            if fb.connect():
                ok, frame = fb.read()
                if ok and frame is not None and frame.size > 0:
                    log.info(
                        "Поток HTTP MJPEG (requests) %s размер %s×%s",
                        safe,
                        frame.shape[1],
                        frame.shape[0],
                    )
                    self._vision_event(
                        "camera_open_ok",
                        {
                            "url": safe,
                            "backend": "http_mjpeg_requests",
                            "w": int(frame.shape[1]),
                            "h": int(frame.shape[0]),
                        },
                    )
                    return fb
                fb.release()
            if quiet:
                log.debug("Нет видео (FFMPEG+requests): %s", safe)
            else:
                log.error("Не удалось получить видео с %s (FFMPEG и HTTP MJPEG)", safe)
                self._vision_event("camera_open_failed", {"url": safe})
            return None

        if scheme in ("rtsp", "rtsps"):
            cap = self._try_opencv_ffmpeg_only(url, safe, quiet=quiet)
            if cap is not None:
                return cap
            cli = self._try_rtsp_via_ffmpeg_cli(url, safe, quiet=quiet)
            if cli is not None:
                return cli
            if quiet:
                log.debug(
                    "RTSP не открылся (OpenCV + ffmpeg CLI): %s — 401=проверь учётку RTSP в камере или установи ffmpeg в PATH",
                    safe,
                )
            else:
                log.error(
                    "Не удалось открыть RTSP %s (OpenCV и ffmpeg CLI) — при 401 проверь пароль для RTSP в веб-интерфейсе "
                    "или VISION_CAMERA_RTSP_*; для обхода Digest установи ffmpeg и повтори",
                    safe,
                )
                self._vision_event("camera_open_failed", {"url": safe})
            return None

        # Локальный файл или нестандартная схема: FFMPEG, затем CAP_ANY
        backends: list[int] = []
        if hasattr(cv2, "CAP_FFMPEG"):
            backends.append(int(cv2.CAP_FFMPEG))
        backends.append(int(cv2.CAP_ANY))
        for backend in backends:
            try:
                cap = cv2.VideoCapture(url, backend)
            except Exception as e:
                log.warning("VideoCapture(%s) backend=%s: %s", safe, backend, e)
                continue
            if not cap.isOpened():
                cap.release()
                continue
            ok, frame = cap.read()
            if ok and frame is not None and frame.size > 0:
                log.info("Поток открыт %s backend=%s размер %s×%s", safe, backend, frame.shape[1], frame.shape[0])
                self._vision_event(
                    "camera_open_ok",
                    {"url": safe, "backend": backend, "w": int(frame.shape[1]), "h": int(frame.shape[0])},
                )
                return cap
            cap.release()

        log.error("Не удалось получить видео с %s", safe)
        if not quiet:
            self._vision_event("camera_open_failed", {"url": safe})
        return None

    def _try_opencv_ffmpeg_only(self, url: str, safe: str, *, quiet: bool = False) -> cv2.VideoCapture | None:
        """Только CAP_FFMPEG (если есть в сборке OpenCV)."""
        if not hasattr(cv2, "CAP_FFMPEG"):
            return None
        backend = int(cv2.CAP_FFMPEG)
        try:
            cap = cv2.VideoCapture(url, backend)
        except Exception as e:
            log.warning("VideoCapture FFMPEG(%s): %s", safe, e)
            if not quiet:
                self._vision_event("camera_open_error", {"url": safe, "backend": "CAP_FFMPEG", "error": str(e)})
            return None
        if not cap.isOpened():
            cap.release()
            if not quiet:
                self._vision_event("camera_not_opened", {"url": safe, "backend": "CAP_FFMPEG"})
            return None
        try:
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass
        if self._width > 0:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, float(self._width))
        ok, frame = cap.read()
        if ok and frame is not None and frame.size > 0:
            log.info("Поток открыт %s backend=CAP_FFMPEG размер %s×%s", safe, frame.shape[1], frame.shape[0])
            self._vision_event(
                "camera_open_ok",
                {"url": safe, "backend": "CAP_FFMPEG", "w": int(frame.shape[1]), "h": int(frame.shape[0])},
            )
            return cap
        cap.release()
        if quiet:
            log.debug("FFMPEG без кадра: %s", safe)
        else:
            log.warning("FFMPEG: поток открылся, но нет кадра: %s", safe)
            self._vision_event("camera_no_first_frame", {"url": safe, "backend": "CAP_FFMPEG"})
        return None

    def _try_rtsp_via_ffmpeg_cli(
        self, url: str, safe: str, *, quiet: bool = False
    ) -> FfmpegRtspMjpegPipeCapture | None:
        """Если OpenCV даёт 401 на RTSP — полноценный ffmpeg в CLI часто проходит Digest."""
        if (os.environ.get("VISION_DISABLE_FFMPEG_CLI") or "").strip().lower() in ("1", "true", "yes", "on"):
            return None
        exe = (os.environ.get("VISION_FFMPEG_BIN") or "").strip() or shutil.which("ffmpeg")
        if not exe:
            log.debug("ffmpeg не в PATH — RTSP только через OpenCV; поставь ffmpeg или VISION_FFMPEG_BIN")
            return None
        log.info("Пробуем RTSP ffmpeg CLI: %s", safe)
        cap = FfmpegRtspMjpegPipeCapture(url, ffmpeg_exe=exe)
        if not cap.start():
            log.warning("ffmpeg CLI: не удалось запустить процесс для %s", safe)
            return None
        try:
            timeout_s = float(os.environ.get("VISION_FFMPEG_CLI_RTSP_TIMEOUT_SEC") or "15")
        except ValueError:
            timeout_s = 15.0
        deadline = time.perf_counter() + max(3.0, timeout_s)
        last_frame: np.ndarray | None = None
        while time.perf_counter() < deadline:
            ok, frame = cap.read()
            if ok and frame is not None:
                last_frame = frame
                break
            time.sleep(0.05)
        if last_frame is None:
            cap.release()
            return None
        log.info(
            "RTSP через ffmpeg CLI (обход OpenCV) %s размер %s×%s",
            safe,
            last_frame.shape[1],
            last_frame.shape[0],
        )
        self._vision_event(
            "camera_open_ok",
            {
                "url": safe,
                "backend": "ffmpeg_cli_rtsp_mjpeg",
                "w": int(last_frame.shape[1]),
                "h": int(last_frame.shape[0]),
            },
        )
        return cap

    def _try_http_snapshot_digest(self, host: str) -> HttpSnapshotPollCapture | None:
        u = _env_camera_user()
        p = _env_camera_password()
        if not u or not p:
            return None
        urls = _collect_snapshot_probe_urls(host, u, p)
        if not urls:
            return None
        cap = HttpSnapshotPollCapture(urls, u, p)
        if cap.connect():
            self._vision_event(
                "camera_open_ok",
                {
                    "backend": "http_snapshot_digest",
                    "host": host,
                    "url": _safe_url_for_log(cap.working_url or ""),
                },
            )
            return cap
        return None

    def _open_picamera2(self) -> Picamera2Capture | None:
        """CSI-камера Raspberry Pi (Pi Camera) через Picamera2 — на Pi 5 нет /dev/video0."""
        try:
            cap = Picamera2Capture(width=self._width)
        except Exception as e:
            log.warning("Picamera2 недоступна: %s", e)
            return None
        if cap.isOpened():
            log.info("Камера: Picamera2 (CSI Raspberry Pi)")
            return cap
        return None

    def _open_capture(self) -> (
        cv2.VideoCapture
        | HttpMjpegFallbackCapture
        | FfmpegRtspMjpegPipeCapture
        | HttpSnapshotPollCapture
        | Picamera2Capture
        | None
    ):
        src = self._source.strip()
        # Опечатка в .env: http://dev/video0 → /dev/video0
        if src.lower().startswith("http://dev/video") or src.lower().startswith("https://dev/video"):
            src = "/" + src.split("://", 1)[-1]
            log.warning("VISION_VIDEO_SOURCE поправлен на %s", src)

        picam_names = {"picamera", "picamera2", "rpi", "csi", "libcamera", "rpicam"}
        if src.lower() in picam_names:
            cap = self._open_picamera2()
            if cap is not None:
                return cap
            log.error("VISION_VIDEO_SOURCE=%s, но Picamera2 не открылась (apt: python3-picamera2)", src)
            return None

        if src.startswith("/dev/video"):
            seen: set[str] = set()
            candidates: list[str] = []
            for p in [src, "/dev/video0", "/dev/video1"] + _discover_v4l_usb_capture_paths():
                if p.startswith("/dev/video") and p not in seen:
                    seen.add(p)
                    candidates.append(p)
            for dev_path in candidates:
                cap = self._open_capture_v4l_path(dev_path)
                if cap is not None:
                    return cap
                log.warning("V4L2 %s — нет кадра, пробуем следующий узел", dev_path)
            log.warning(
                "V4L2 не открылся (%s) — пробуем CSI Picamera2",
                src,
            )
            cap = self._open_picamera2()
            if cap is not None:
                return cap
            log.error(
                "Не удалось открыть камеру (%s). Для CSI: VISION_VIDEO_SOURCE=picamera "
                "и apt python3-picamera2. Для USB: v4l2-ctl --list-devices.",
                src,
            )
            return None

        if not src.isdigit():
            if _http_source_needs_path_probe(src):
                urls = _iter_http_stream_urls(src)
                log.info("HTTP: перебор %s вариантов URL (пути и порты)", len(urls))
                for url in urls:
                    safe = _safe_url_for_log(url)
                    log.info("Пробуем %s", safe)
                    cap = self._open_network_capture(url, quiet=True)
                    if cap is not None:
                        log.info("Подошёл поток: %s", safe)
                        return cap
                log.warning(
                    "HTTP MJPEG не найден на портах из VISION_CAMERA_PROBE_PORTS "
                    "(по умолчанию только 80; добавьте 8080 при необходимости)"
                )
                host_probe = _bare_http_host_from_source(src)
                cu, cp = _env_camera_user(), _env_camera_password()
                snap_urls_n = (
                    len(_collect_snapshot_probe_urls(host_probe, cu, cp))
                    if (host_probe and cu and cp)
                    else 0
                )
                if (
                    host_probe
                    and snap_urls_n
                    and not _env_bool("VISION_DISABLE_HTTP_SNAPSHOT", False)
                ):
                    log.info(
                        "Пробуем HTTP JPEG snapshot (%s URL: Digest/Basic и логин в query) для %s — до перебора RTSP",
                        snap_urls_n,
                        host_probe,
                    )
                    snap = self._try_http_snapshot_digest(host_probe)
                    if snap is not None:
                        log.info(
                            "Кадры через опрос JPEG (VISION_SNAPSHOT_MIN_INTERVAL_SEC задаёт минимальный интервал)"
                        )
                        return snap
                    log.info("HTTP snapshot не подошёл — переходим к RTSP")
                rtsp_urls = _iter_rtsp_stream_urls(src)
                log.info("RTSP: перебор %s вариантов (порты %s)", len(rtsp_urls), _probe_rtsp_ports())
                for url in rtsp_urls:
                    safe = _safe_url_for_log(url)
                    log.info("Пробуем %s", safe)
                    cap = self._open_network_capture(url, quiet=True)
                    if cap is not None:
                        log.info("Подошёл поток: %s", safe)
                        return cap
                log.error(
                    "Не удалось открыть ни HTTP MJPEG, ни HTTP snapshot, ни RTSP (OpenCV + ffmpeg CLI). Проверь: "
                    "учётка RTSP в камере или отдельный пароль потока; полный rtsp:// из веб-морды камеры; "
                    "для snapshot — Digest/Basic и путь (часто /cgi-bin/snapshot.cgi?channel=1&subtype=0); "
                    "VISION_CAMERA_PROBE_PORTS при необходимости; см. README vision-tracker."
                )
                return None
            url = _resolve_stream_url(src)
            if url != src:
                log.info("Источник видео (после настройки URL): %s", _safe_url_for_log(url))
            return self._open_network_capture(url)

        preferred = int(src)
        if sys.platform != "win32":
            seen_paths: set[str] = set()
            path_order: list[str] = []
            for p in [f"/dev/video{preferred}"] + _discover_v4l_usb_capture_paths():
                if p not in seen_paths:
                    seen_paths.add(p)
                    path_order.append(p)
            for dev_path in path_order:
                cap = self._open_capture_v4l_path(dev_path)
                if cap is not None:
                    return cap
                log.warning("Камера %s не дала кадр", dev_path)

        order = [preferred] + [i for i in self._camera_indices_to_try() if i != preferred]
        for idx in order:
            cap = self._open_capture_index(idx)
            if cap is not None:
                return cap
            log.warning("Камера idx=%s не дала кадр", idx)
        return None

    def _pick_box(self, names: dict, xyxy: np.ndarray, conf: np.ndarray, cls: np.ndarray) -> tuple | None:
        """Возвращает (x1,y1,x2,y2,conf,cls_id) лучшего бокса или None."""
        if xyxy is None or len(xyxy) == 0:
            return None
        best = None
        best_area = -1.0
        for i in range(len(xyxy)):
            x1, y1, x2, y2 = xyxy[i].tolist()
            c = float(conf[i])
            cid = int(cls[i])
            name = (names.get(cid) or "").lower()
            if self._target_class and name != self._target_class:
                continue
            area = max(0.0, x2 - x1) * max(0.0, y2 - y1) * c
            if area > best_area:
                best_area = area
                best = (x1, y1, x2, y2, c, cid)
        if best is None and len(xyxy) > 0 and not self._target_class:
            # без фильтра класса — самый большой по площади
            for i in range(len(xyxy)):
                x1, y1, x2, y2 = xyxy[i].tolist()
                c = float(conf[i])
                cid = int(cls[i])
                area = max(0.0, x2 - x1) * max(0.0, y2 - y1) * c
                if area > best_area:
                    best_area = area
                    best = (x1, y1, x2, y2, c, cid)
        return best

    @staticmethod
    def _pick_bbox_at_frame_center_with_index(
        xyxy: np.ndarray,
        confs: np.ndarray,
        clss: np.ndarray,
        w: int,
        h: int,
        min_conf: float = 0.25,
    ) -> tuple[tuple[float, float, float, float, float, int], int] | None:
        """Как `_pick_bbox_at_frame_center`, плюс индекс бокса в массивах."""
        if xyxy is None or len(xyxy) == 0:
            return None
        fx, fy = 0.5 * w, 0.5 * h
        inside_idx: list[tuple[float, int]] = []
        for i in range(len(xyxy)):
            if float(confs[i]) < min_conf:
                continue
            x1, y1, x2, y2 = map(float, xyxy[i])
            if x1 <= fx <= x2 and y1 <= fy <= y2:
                area = max(1.0, (x2 - x1) * (y2 - y1))
                inside_idx.append((area, i))
        if inside_idx:
            idx = min(inside_idx)[1]
        else:
            best_j = None
            best_d = 1e18
            for i in range(len(xyxy)):
                if float(confs[i]) < min_conf:
                    continue
                x1, y1, x2, y2 = map(float, xyxy[i])
                bx = 0.5 * (x1 + x2)
                by = 0.5 * (y1 + y2)
                d = math.hypot(bx - fx, by - fy)
                if d < best_d:
                    best_d = d
                    best_j = i
            if best_j is None:
                return None
            idx = best_j
        row = xyxy[idx]
        t = (
            float(row[0]),
            float(row[1]),
            float(row[2]),
            float(row[3]),
            float(confs[idx]),
            int(clss[idx]),
        )
        return t, idx

    @staticmethod
    def _pick_bbox_at_frame_center(
        xyxy: np.ndarray,
        confs: np.ndarray,
        clss: np.ndarray,
        w: int,
        h: int,
        min_conf: float = 0.25,
    ) -> tuple[float, float, float, float, float, int] | None:
        r = VisionPipeline._pick_bbox_at_frame_center_with_index(
            xyxy, confs, clss, w, h, min_conf=min_conf
        )
        return r[0] if r else None

    @staticmethod
    def _pick_by_track_id(
        xyxy: np.ndarray,
        confs: np.ndarray,
        clss: np.ndarray,
        track_ids: np.ndarray,
        want_tid: int,
        min_conf: float = 0.2,
    ) -> tuple[float, float, float, float, float, int] | None:
        if xyxy is None or len(xyxy) == 0:
            return None
        best_i = None
        best_c = -1.0
        for i in range(len(xyxy)):
            if int(track_ids[i]) != want_tid or float(confs[i]) < min_conf:
                continue
            cf = float(confs[i])
            if cf > best_c:
                best_c = cf
                best_i = i
        if best_i is None:
            return None
        row = xyxy[best_i]
        return (
            float(row[0]),
            float(row[1]),
            float(row[2]),
            float(row[3]),
            float(confs[best_i]),
            int(clss[best_i]),
        )

    @staticmethod
    def _box_row_index(xyxy: np.ndarray, pick: tuple[float, float, float, float, float, int]) -> int | None:
        px1, py1, px2, py2 = pick[0], pick[1], pick[2], pick[3]
        tol = 6.0
        for i in range(len(xyxy)):
            q = xyxy[i]
            if (
                abs(float(q[0]) - px1) < tol
                and abs(float(q[1]) - py1) < tol
                and abs(float(q[2]) - px2) < tol
                and abs(float(q[3]) - py2) < tol
            ):
                return i
        return None

    def _pick_locked_target(
        self,
        xyxy: np.ndarray,
        confs: np.ndarray,
        clss: np.ndarray,
        *,
        cls_id: int,
        prev_xy: tuple[float, float],
        min_conf: float = 0.25,
    ) -> tuple[float, float, float, float, float, int] | None:
        """Среди объектов нужного класса — ближайший центр к предыдущему."""
        if xyxy is None or len(xyxy) == 0 or cls_id < 0:
            return None
        best = None
        best_d = 1e18
        px, py = prev_xy
        for i in range(len(xyxy)):
            if int(clss[i]) != cls_id or float(confs[i]) < min_conf:
                continue
            x1, y1, x2, y2 = map(float, xyxy[i])
            bx = 0.5 * (x1 + x2)
            by = 0.5 * (y1 + y2)
            d = math.hypot(bx - px, by - py)
            if d < best_d:
                best_d = d
                row = xyxy[i]
                best = (float(row[0]), float(row[1]), float(row[2]), float(row[3]), float(confs[i]), cls_id)
        return best

    def _create_cv_tracker(self) -> object | None:
        """OpenCV KCF/CSRT — быстрый трек между кадрами YOLO на Pi."""
        pref = self._opencv_tracker_kind
        candidates: list[tuple[str, str]] = []
        if pref == "KCF":
            candidates = [("KCF", "TrackerKCF_create"), ("CSRT", "TrackerCSRT_create"), ("MOSSE", "TrackerMOSSE_create")]
        else:
            candidates = [("CSRT", "TrackerCSRT_create"), ("KCF", "TrackerKCF_create"), ("MOSSE", "TrackerMOSSE_create")]

        modules = [cv2]
        legacy = getattr(cv2, "legacy", None)
        if legacy is not None:
            modules.append(legacy)

        for mod in modules:
            for _name, factory in candidates:
                fn = getattr(mod, factory, None)
                if fn is None:
                    continue
                try:
                    tr = fn()
                    if tr is not None:
                        return tr
                except Exception:
                    continue
        log.warning("OpenCV tracker недоступен — только YOLO между кадрами")
        return None

    def _tracker_reset(self) -> None:
        self._opencv_tracker = None

    def _tracker_init_from_rect(self, frame: np.ndarray, rect: tuple[float, float, float, float]) -> bool:
        if not self._opencv_tracker_enabled:
            return False
        x1, y1, x2, y2 = rect
        bw = max(4, int(x2 - x1))
        bh = max(4, int(y2 - y1))
        tr = self._create_cv_tracker()
        if tr is None:
            return False
        try:
            ok = tr.init(frame, (int(x1), int(y1), bw, bh))
        except Exception as e:
            log.debug("tracker.init: %s", e)
            return False
        if ok:
            self._opencv_tracker = tr
        return bool(ok)

    def _tracker_step_xyxy(self, frame: np.ndarray) -> tuple[float, float, float, float] | None:
        if self._opencv_tracker is None:
            return None
        try:
            ok, bbox = self._opencv_tracker.update(frame)
        except Exception:
            return None
        if not ok:
            return None
        x, y, bw, bh = bbox
        return (float(x), float(y), float(x + bw), float(y + bh))

    def _row_from_rect(
        self,
        rect: tuple[float, float, float, float],
        *,
        conf: float,
        cls_id: int,
    ) -> tuple[float, float, float, float, float, int]:
        x1, y1, x2, y2 = rect
        return (x1, y1, x2, y2, conf, cls_id)

    def _target_from_row(
        self,
        row: tuple[float, float, float, float, float, int],
        w: int,
        h: int,
    ) -> tuple[float, float, float, tuple[float, float, float, float], int | None, str | None]:
        x1, y1, x2, y2, conf, cid = row
        rect = (x1, y1, x2, y2)
        bx = 0.5 * (x1 + x2)
        by = 0.5 * (y1 + y2)
        cx_n = float(np.clip((bx - 0.5 * w) / (0.5 * w), -1.0, 1.0))
        cy_n = float(np.clip((by - 0.5 * h) / (0.5 * h), -1.0, 1.0))
        tid = self._lock_track_id if self._follow_lock else 1
        return cx_n, cy_n, conf, rect, tid, None

    def _publish_target_snap(
        self,
        *,
        cx_n: float,
        cy_n: float,
        conf: float,
        lost: bool,
        w: int,
        h: int,
        cid_name: str | None,
        fps: float,
        picked_rect: tuple[float, float, float, float] | None,
        infer_mode: str = "yolo",
    ) -> None:
        hint = self._hints(cx_n, cy_n) if not lost else None
        backend = self._backend if infer_mode == "yolo" else f"{self._backend}+tracker"
        with self._lock:
            self._snap = TrackerSnapshot(
                track_id=self._lock_track_id if self._follow_lock else (1 if not lost else None),
                cx=cx_n,
                cy=cy_n,
                confidence=conf,
                lost=lost,
                backend=backend,
                hint=hint,
                fps=float(fps),
                frame_w=w,
                frame_h=h,
                cls_name=cid_name,
                camera_status="ok",
                target_locked=self._follow_lock,
                lock_note=self._lock_note,
            )
        if picked_rect is not None:
            self._cached_picked_rect = picked_rect
            self._cached_cid_name = cid_name
        elif not self._follow_lock:
            self._cached_picked_rect = None
            self._cached_cid_name = None

    def _handoff_frame(self, frame: np.ndarray) -> None:
        with self._frame_cond:
            self._pending_frame = frame
            self._pending_frame_seq += 1
            self._frame_cond.notify()

    def _run_capture(self) -> None:
        cap = self._open_capture()
        if cap is None or not cap.isOpened():
            src_show = _safe_url_for_log(_resolve_stream_url(self._source.strip())) if not self._source.strip().isdigit() else self._source
            log.error("Не удалось открыть источник VISION_VIDEO_SOURCE=%s", src_show)
            self._vision_event("camera_unavailable", {"source": src_show, "vision_backend": self._backend})
            with self._lock:
                self._snap = TrackerSnapshot(
                    track_id=None,
                    cx=0.0,
                    cy=0.0,
                    confidence=0.0,
                    lost=True,
                    backend=self._backend,
                    hint=None,
                    fps=0.0,
                    frame_w=0,
                    frame_h=0,
                    cls_name=None,
                    camera_status="no_device",
                    target_locked=False,
                    lock_note="",
                )
            self._stop.set()
            with self._frame_cond:
                self._frame_cond.notify_all()
            return

        t_cap_prev = time.perf_counter()
        bad_reads = 0
        stall_reported = False
        self._vision_event(
            "pipeline_started",
            {
                "vision_backend": self._backend,
                "source": self._source.strip()[:200],
                "dual_thread": True,
                "lock_detect_every": self._lock_detect_every,
            },
        )

        while not self._stop.is_set():
            ok, frame = self._read_fresh_frame(cap)
            if not ok or frame is None:
                bad_reads += 1
                if bad_reads == 30:
                    self._vision_event("stream_stall", {"failed_reads": bad_reads})
                    stall_reported = True
                if bad_reads % 30 == 0:
                    with self._lock:
                        prev = self._snap
                        self._snap = TrackerSnapshot(
                            track_id=prev.track_id,
                            cx=prev.cx,
                            cy=prev.cy,
                            confidence=prev.confidence,
                            lost=True,
                            backend=prev.backend,
                            hint=prev.hint,
                            fps=prev.fps,
                            frame_w=0,
                            frame_h=0,
                            cls_name=prev.cls_name,
                            camera_status="no_frames",
                            target_locked=prev.target_locked,
                            lock_note=prev.lock_note,
                        )
                time.sleep(0.05)
                continue
            if stall_reported:
                self._vision_event("stream_recovered", {})
                stall_reported = False
            bad_reads = 0

            h, w = frame.shape[:2]
            self._frame_seq += 1
            now = time.perf_counter()
            dt = max(1e-6, now - t_cap_prev)
            t_cap_prev = now
            inst_fps = 1.0 / dt
            self._capture_ema_fps = (
                0.9 * self._capture_ema_fps + 0.1 * inst_fps if self._capture_ema_fps > 0 else inst_fps
            )

            with self._lock:
                prev = self._snap
                if prev.camera_status != "ok" or prev.frame_w != w or prev.frame_h != h:
                    self._snap = TrackerSnapshot(
                        track_id=prev.track_id,
                        cx=prev.cx,
                        cy=prev.cy,
                        confidence=prev.confidence,
                        lost=prev.lost,
                        backend=prev.backend,
                        hint=prev.hint,
                        fps=prev.fps,
                        frame_w=w,
                        frame_h=h,
                        cls_name=prev.cls_name,
                        camera_status="ok",
                        target_locked=prev.target_locked,
                        lock_note=prev.lock_note,
                    )

            self._push_fast_preview(frame, w, h)
            self._handoff_frame(frame.copy())

        cap.release()
        self._stop.set()
        with self._frame_cond:
            self._frame_cond.notify_all()

    def _run_inference(self) -> None:
        t_prev = time.perf_counter()
        ema_fps = 0.0
        was_lost = True
        last_summary = 0.0
        last_seq = 0

        while not self._stop.is_set():
            with self._frame_cond:
                while self._pending_frame_seq <= last_seq and not self._stop.is_set():
                    self._frame_cond.wait(timeout=0.2)
                if self._stop.is_set():
                    break
                frame = self._pending_frame
                seq = self._pending_frame_seq

            if frame is None or seq <= last_seq:
                continue
            last_seq = seq
            h, w = frame.shape[:2]
            self._infer_seq += 1
            t_prev, ema_fps, was_lost, last_summary = self._infer_on_frame(
                frame, w, h, t_prev, ema_fps, was_lost, last_summary
            )

    def _infer_on_frame(
        self,
        frame: np.ndarray,
        w: int,
        h: int,
        t_prev: float,
        ema_fps: float,
        was_lost: bool,
        last_summary: float,
    ) -> tuple[float, float, bool, float]:
        det_n = 0
        cx_n, cy_n, conf = 0.0, 0.0, 0.0
        lost = True
        cid_name: str | None = None
        picked_rect: tuple[float, float, float, float] | None = None
        picked_row: tuple[float, float, float, float, float, int] | None = None
        names_dict: dict = {}
        infer_mode = "yolo"

        lock_center_cmd, unlock_cmd = self._consume_pending_cmds()
        if unlock_cmd:
            self._follow_lock = False
            self._lock_cls_id = None
            self._lock_track_id = None
            self._lock_prev_xy = None
            self._lock_note = ""
            self._cached_picked_rect = None
            self._cached_cid_name = None
            self._tracker_reset()
            self._vision_event("target_unlock", {})

        if not self.is_detection_enabled():
            now = time.perf_counter()
            dt = max(1e-6, now - t_prev)
            ema_fps = 0.9 * ema_fps + 0.1 * (1.0 / dt) if ema_fps > 0 else 1.0 / dt
            with self._lock:
                prev = self._snap
                self._snap = TrackerSnapshot(
                    track_id=None,
                    cx=0.0,
                    cy=0.0,
                    confidence=0.0,
                    lost=True,
                    backend="preview",
                    hint=None,
                    fps=float(ema_fps),
                    frame_w=w,
                    frame_h=h,
                    cls_name=None,
                    camera_status="ok",
                    target_locked=False,
                    lock_note="",
                    detection_enabled=False,
                )
            return now, ema_fps, was_lost, last_summary

        run_yolo = self._model is not None
        run_opencv_only = False

        if self._follow_lock and self._opencv_tracker_enabled and self._opencv_tracker is not None:
            if lock_center_cmd:
                run_yolo = True
            elif self._infer_seq % self._lock_detect_every != 0:
                run_yolo = False
                run_opencv_only = True
        elif run_yolo and not (lock_center_cmd or self._follow_lock):
            if self._detect_every > 1 and (self._infer_seq % self._detect_every != 0):
                run_yolo = False

        if run_opencv_only:
            infer_mode = "tracker"
            rect = self._tracker_step_xyxy(frame)
            if rect is None:
                self._lock_note = "lost_track"
                lost = True
            else:
                picked_rect = rect
                picked_row = self._row_from_rect(
                    rect, conf=self._lock_last_conf, cls_id=self._lock_last_cls
                )
                cx_n, cy_n, conf, picked_rect, _, _ = self._target_from_row(picked_row, w, h)
                x1, y1, x2, y2 = picked_rect
                self._lock_prev_xy = (0.5 * (x1 + x2), 0.5 * (y1 + y2))
                cid_name = self._cached_cid_name
                self._lock_note = "ok"
                lost = False

            now = time.perf_counter()
            dt = max(1e-6, now - t_prev)
            t_prev = now
            ema_fps = 0.9 * ema_fps + 0.1 * (1.0 / dt) if ema_fps > 0 else 1.0 / dt
            self._publish_target_snap(
                cx_n=cx_n,
                cy_n=cy_n,
                conf=conf,
                lost=lost,
                w=w,
                h=h,
                cid_name=cid_name,
                fps=ema_fps,
                picked_rect=picked_rect if not lost else None,
                infer_mode=infer_mode,
            )
            return self._infer_emit_events(
                cx_n, cy_n, conf, lost, cid_name, det_n, ema_fps, w, h, was_lost, last_summary, t_prev
            )

        if not run_yolo:
            now = time.perf_counter()
            dt = max(1e-6, now - t_prev)
            t_prev = now
            ema_fps = 0.9 * ema_fps + 0.1 * (1.0 / dt) if ema_fps > 0 else 1.0 / dt
            with self._lock:
                prev = self._snap
                self._snap = TrackerSnapshot(
                    track_id=prev.track_id,
                    cx=prev.cx,
                    cy=prev.cy,
                    confidence=prev.confidence,
                    lost=prev.lost,
                    backend=prev.backend,
                    hint=prev.hint,
                    fps=float(ema_fps),
                    frame_w=w,
                    frame_h=h,
                    cls_name=prev.cls_name,
                    camera_status="ok",
                    target_locked=prev.target_locked,
                    lock_note=prev.lock_note,
                )
            return t_prev, ema_fps, was_lost, last_summary

        if lock_center_cmd and self._model is None:
            self._follow_lock = False
            self._lock_note = "no_model"
            log.warning("Фиксация цели: нет YOLO")
            self._vision_event("lock_failed", {"reason": "no_model"})
        elif self._model is not None:
            if lock_center_cmd:
                self._vision_event("lock_requested", {})
            conf_infer = 0.26 if self._follow_lock else 0.35
            infer_kw = self._yolo_infer_kwargs(conf_infer)
            res = self._model.predict(frame, **infer_kw)[0]
            if lock_center_cmd and (res.boxes is None or len(res.boxes) == 0):
                res = self._model.predict(frame, **infer_kw)[0]
            rd = res.names if isinstance(res.names, dict) else {i: str(v) for i, v in enumerate(res.names)}
            names_dict = rd

            if res.boxes is None or len(res.boxes) == 0:
                if lock_center_cmd:
                    self._follow_lock = False
                    self._lock_cls_id = None
                    self._lock_prev_xy = None
                    self._lock_track_id = None
                    self._lock_note = "none_in_center"
                    self._tracker_reset()
                    log.info("Фиксация: нет детекций в кадре")
                    self._vision_event("lock_failed", {"reason": "none_in_center", "detections": det_n})
                elif self._follow_lock:
                    self._lock_note = "lost_track"
            else:
                xyxy = res.boxes.xyxy.cpu().numpy()
                confs = res.boxes.conf.cpu().numpy()
                clss = res.boxes.cls.cpu().numpy().astype(int)
                det_n = len(xyxy)
                det_track_ids = np.full(det_n, -1, dtype=np.int64)

                if lock_center_cmd:
                    cen_widx = self._pick_bbox_at_frame_center_with_index(
                        xyxy, confs, clss, w, h, min_conf=0.28
                    )
                    if cen_widx is None:
                        self._follow_lock = False
                        self._lock_cls_id = None
                        self._lock_prev_xy = None
                        self._lock_track_id = None
                        self._lock_note = "none_in_center"
                        self._tracker_reset()
                        log.info("Фиксация: под центром никого подходящего по conf")
                        self._vision_event("lock_failed", {"reason": "none_in_center", "detections": det_n})
                    else:
                        cen, _jcen = cen_widx
                        x1l, y1l, x2l, y2l, cfl, cidl = cen
                        self._follow_lock = True
                        self._lock_cls_id = int(cidl)
                        self._lock_track_id = None
                        self._lock_prev_xy = (0.5 * (x1l + x2l), 0.5 * (y1l + y2l))
                        picked_row = cen
                        self._lock_note = "ok"
                        self._lock_last_conf = float(cfl)
                        self._lock_last_cls = int(cidl)
                        log.info("Цель зафиксирована: %s cls=%s", names_dict.get(self._lock_cls_id, "?"), self._lock_cls_id)
                        self._vision_event(
                            "target_lock_ok",
                            {
                                "class": names_dict.get(self._lock_cls_id, "?"),
                                "cls_id": self._lock_cls_id,
                                "opencv_tracker": self._opencv_tracker_enabled,
                            },
                        )
                elif self._follow_lock and self._lock_cls_id is not None and self._lock_prev_xy is not None:
                    picked_row = self._pick_locked_target(
                        xyxy,
                        confs,
                        clss,
                        cls_id=self._lock_cls_id,
                        prev_xy=self._lock_prev_xy,
                        min_conf=0.25,
                    )
                    if picked_row is None:
                        self._lock_note = "lost_track"
                    else:
                        x1l, y1l, x2l, y2l, cfl, cidl = picked_row
                        self._lock_prev_xy = (0.5 * (x1l + x2l), 0.5 * (y1l + y2l))
                        self._lock_last_conf = float(cfl)
                        self._lock_last_cls = int(cidl)
                        self._lock_note = "ok"
                else:
                    picked_row = self._pick_box(names_dict, xyxy, confs, clss)
                    if not self._follow_lock:
                        self._lock_note = ""

                self._monitor_collect_detections(
                    names=names_dict,
                    xyxy=xyxy,
                    confs=confs,
                    clss=clss,
                    track_ids=det_track_ids,
                    frame_w=w,
                    frame_h=h,
                )

        if picked_row is not None:
            cx_n, cy_n, conf, picked_rect, _, _ = self._target_from_row(picked_row, w, h)
            cid_name = names_dict.get(int(picked_row[5])) if names_dict else self._cached_cid_name
            lost = False
            if self._follow_lock and picked_rect is not None:
                self._tracker_init_from_rect(frame, picked_rect)

        now = time.perf_counter()
        dt = max(1e-6, now - t_prev)
        t_prev = now
        ema_fps = 0.9 * ema_fps + 0.1 * (1.0 / dt) if ema_fps > 0 else 1.0 / dt
        self._publish_target_snap(
            cx_n=cx_n,
            cy_n=cy_n,
            conf=conf,
            lost=lost,
            w=w,
            h=h,
            cid_name=cid_name,
            fps=ema_fps,
            picked_rect=picked_rect,
            infer_mode=infer_mode,
        )
        return self._infer_emit_events(
            cx_n, cy_n, conf, lost, cid_name, det_n, ema_fps, w, h, was_lost, last_summary, t_prev
        )

    def _infer_emit_events(
        self,
        cx_n: float,
        cy_n: float,
        conf: float,
        lost: bool,
        cid_name: str | None,
        det_n: int,
        ema_fps: float,
        w: int,
        h: int,
        was_lost: bool,
        last_summary: float,
        now: float,
    ) -> tuple[float, float, bool, float]:
        if was_lost != lost:
            if lost:
                self._vision_event(
                    "target_lost",
                    {
                        "target_locked": self._follow_lock,
                        "lock_note": self._lock_note,
                        "detections": det_n,
                    },
                )
            else:
                self._vision_event(
                    "target_found",
                    {
                        "class": cid_name,
                        "confidence": round(float(conf), 4),
                        "cx": round(float(cx_n), 4),
                        "cy": round(float(cy_n), 4),
                        "detections": det_n,
                    },
                )
            was_lost = lost

        if now - last_summary >= self._event_summary_interval:
            last_summary = now
            self._vision_event(
                "frame_summary",
                {
                    "fps": round(float(ema_fps), 2),
                    "capture_fps": round(float(self._capture_ema_fps), 2),
                    "lost": lost,
                    "vision_backend": self._snap.backend,
                    "detections": det_n,
                    "class": cid_name,
                    "confidence": round(float(conf), 4) if not lost else None,
                    "target_locked": self._follow_lock,
                    "lock_note": self._lock_note or None,
                    "frame_w": w,
                    "frame_h": h,
                },
            )

        return now, ema_fps, was_lost, last_summary
