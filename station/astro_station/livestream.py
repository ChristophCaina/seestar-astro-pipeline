"""MJPEG-Strom von seestar_alp lesen (ohne GUI, damit testbar).

seestar_alp schickt multipart/x-mixed-replace mit Grenze "--frame" und ohne Content-Length:
  \r\n--frame\r\nContent-Type: image/jpeg\r\n\r\n<JPEG>\r\n--frame\r\n ...
"Loading" kommt als GIF, ein ruhender Seestar liefert das letzte Bild und "Idle" – danach endet der Strom.
"""
from __future__ import annotations

import urllib.request

BOUNDARY = b"--frame"


class MjpegParser:
    def __init__(self):
        self.buf = b""

    def _part(self, chunk: bytes):
        chunk = chunk.strip(b"\r\n")
        head, sep, body = chunk.partition(b"\r\n\r\n")
        if not sep:
            return None
        ctype = ""
        for line in head.split(b"\r\n"):
            k, _, v = line.partition(b":")
            if k.strip().lower() == b"content-type":
                ctype = v.strip().decode("ascii", "replace").lower()
        return ctype, body.rstrip(b"\r\n")

    def feed(self, data: bytes) -> list[tuple[str, bytes]]:
        """Neue Bytes -> fertige Teile [(content_type, daten)]. Ein Teil ist fertig, wenn die naechste Grenze da ist."""
        self.buf += data
        out = []
        while True:
            i = self.buf.find(BOUNDARY)
            if i < 0:
                break
            j = self.buf.find(BOUNDARY, i + len(BOUNDARY))
            if j < 0:
                self.buf = self.buf[i:]
                break
            p = self._part(self.buf[i + len(BOUNDARY):j])
            if p and p[1]:
                out.append(p)
            self.buf = self.buf[j:]
        return out

    def finish(self) -> list[tuple[str, bytes]]:
        """Strom zu Ende: letzten Teil noch auswerten."""
        rest, self.buf = self.buf, b""
        if rest.startswith(BOUNDARY):
            p = self._part(rest[len(BOUNDARY):])
            if p and p[1]:
                return [p]
        return []


def frames(url: str, stop, timeout: float = 120):
    """Generator: JPEG-Bilder aus dem Strom, bis er endet oder stop() wahr ist.

    Jedes Bild wird erst weitergegeben, wenn das naechste da ist: seestar_alp schickt Einzelbilder doppelt
    (also ohne Verzoegerung), und das letzte Bild vor dem Ende ist die graue "Idle"-Tafel – die faellt so weg.
    """
    parser = MjpegParser()
    held = None
    with urllib.request.urlopen(url, timeout=timeout) as r:
        while not stop():
            data = r.read1(65536) if hasattr(r, "read1") else r.read(65536)
            if not data:
                break
            for ctype, body in parser.feed(data):
                if "jpeg" not in ctype:
                    continue
                if held is not None:
                    yield held
                held = body
