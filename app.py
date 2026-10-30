import os
import re
import hmac
from urllib.parse import urljoin, urlsplit, quote

import requests
from flask import Flask, Response, request, abort, stream_with_context

app = Flask(__name__)

ORIGIN = "https://live-tr-next.spacetoongo.com"
PLAYLIST = "/ST_TR_NEXT/hls/h7qefeiwfbjn1.m3u8"
SECRET = os.environ["ACCESS_KEY"]


@app.get("/")
def home():
    return "Yayın proxy hazır."


@app.get("/watch.m3u8")
@app.get("/stream/<path:resource>")
def proxy(resource=None):
    key = request.args.get("key", "")
    if not hmac.compare_digest(key, SECRET):
        abort(403)

    path = "/" + resource if resource else PLAYLIST
    upstream = ORIGIN + path
    query = request.args.get("q", "")
    if query:
        upstream += "?" + query

    try:
        result = requests.get(
            upstream,
            stream=True,
            timeout=(10, 30),
            allow_redirects=False,
        )
    except requests.RequestException:
        return "Yayın sunucusuna ulaşılamadı.", 502

    if result.status_code != 200:
        status = result.status_code
        result.close()
        return f"Yayın sunucusu yanıtı: {status}", 502

    content_type = result.headers.get("Content-Type", "")
    is_playlist = (
        path.lower().endswith(".m3u8")
        or "mpegurl" in content_type.lower()
    )

    if is_playlist:
        def local_url(value):
            absolute = urlsplit(urljoin(upstream, value))
            if (
                absolute.scheme != "https"
                or absolute.netloc != urlsplit(ORIGIN).netloc
            ):
                raise ValueError("Farklı yayın sunucusu")

            target = (
                "/stream"
                + quote(absolute.path, safe="/%")
                + "?key="
                + quote(SECRET, safe="")
            )
            if absolute.query:
                target += "&q=" + quote(absolute.query, safe="")
            return target

        try:
            playlist = result.content.decode("utf-8-sig")
            if not playlist.lstrip().startswith("#EXTM3U"):
                return "Geçerli yayın listesi alınamadı.", 502

            lines = []
            for line in playlist.splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    line = local_url(line)
                elif line.startswith("#"):
                    line = re.sub(
                        r'URI="([^"]+)"',
                        lambda match: 'URI="' + local_url(match[1]) + '"',
                        line,
                    )
                lines.append(line)

            return Response(
                "\n".join(lines) + "\n",
                content_type="application/vnd.apple.mpegurl",
                headers={"Cache-Control": "no-store"},
            )
        except (ValueError, requests.RequestException):
            return "Yayın listesi aktarılamadı; kaynak kontrol edilmeli.", 502
        finally:
            result.close()

    def chunks():
        try:
            yield from result.iter_content(chunk_size=65536)
        finally:
            result.close()

    return Response(
        stream_with_context(chunks()),
        content_type=content_type or "application/octet-stream",
        headers={"Cache-Control": "no-store"},
    )
