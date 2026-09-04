"""Parse Hotmart cf-embed URLs into export env vars."""

from __future__ import annotations

import base64
import json
import re
from dataclasses import asdict, dataclass
from urllib.parse import parse_qs, unquote, urlparse


@dataclass
class EmbedVars:
    app: str
    user_code: str
    user_id: str
    media: str
    jwt: str
    ref: str
    embed: str
    play_drm: bool | None = None
    title: str | None = None
    description: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    def export_script(self) -> str:
        def q(s: str) -> str:
            return "'" + s.replace("'", "'\"'\"'") + "'"

        lines = [
            "#!/usr/bin/env bash",
            "# generated — contains secrets; do not commit",
            "unset EMBED embed",
            f"export APP={q(self.app)}",
            f"export USER_CODE={q(self.user_code)}",
            f"export USER_ID={q(self.user_id)}",
            f"export MEDIA={q(self.media)}",
            f"export JWT={q(self.jwt)}",
            f"export REF={q(self.ref)}",
            f"export EMBED={q(self.embed)}",
            "",
            f'echo "JWT length: ${{#JWT}} MEDIA=$MEDIA playDrm={self.play_drm}"',
        ]
        return "\n".join(lines) + "\n"


def _extract_url(text: str) -> str:
    text = text.strip().strip("\"'")
    # allow pasting full iframe HTML
    m = re.search(r'src=["\']([^"\']+)["\']', text, flags=re.I)
    if m:
        text = m.group(1)
    m = re.search(r"https://cf-embed\.play\.hotmart\.com/embed/[^\s\"'<>]+", text)
    if m:
        return m.group(0)
    if text.startswith("http"):
        return text.split()[0]
    raise ValueError("No Hotmart embed URL found in pasted text")


def parse_embed(text: str) -> EmbedVars:
    url = _extract_url(text)
    u = urlparse(url)
    q = {k: v[0] for k, v in parse_qs(u.query, keep_blank_values=True).items()}

    parts = [p for p in u.path.split("/") if p]
    media = parts[-1] if parts else ""

    jwt = q.get("jwtToken") or q.get("jwt") or ""
    app = q.get("applicationCode") or ""
    user_code = q.get("userCode") or ""
    user_id = q.get("user") or q.get("userId") or ""
    play_drm = None
    title = None
    description = None

    if jwt.count(".") >= 2:
        payload = jwt.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload))
        media = data.get("mediaCode") or media
        user_id = str(data.get("userId") or user_id)
        user_code = data.get("userCode") or user_code
        play_drm = data.get("playDrm")
        title = data.get("title")
        description = data.get("description")

    if len(jwt) < 100:
        raise ValueError(f"JWT looks invalid (len={len(jwt)})")

    slug = "direito-cacd"
    product_id = "2039476"
    page_hash = ""
    meta_raw = q.get("metadata")
    if meta_raw:
        try:
            meta = json.loads(unquote(meta_raw))
            md = {m.get("key"): m.get("value") for m in meta if isinstance(m, dict)}
            slug = str(md.get("slug") or slug)
            product_id = str(md.get("productId") or product_id)
            page_hash = str(md.get("pageHash") or "")
        except Exception:
            pass

    if page_hash:
        locale = q.get("locale") or "es"
        if locale in ("en",) or locale not in ("es", "pt", "pt-br"):
            locale = "es"
        ref = f"https://hotmart.com/{locale}/club/{slug}/products/{product_id}/content/{page_hash}"
    else:
        ref = "https://hotmart.com/es/club/direito-cacd/products/2039476/content/PAGE_HASH"

    return EmbedVars(
        app=app,
        user_code=user_code,
        user_id=str(user_id),
        media=media,
        jwt=jwt,
        ref=ref,
        embed=url,
        play_drm=play_drm,
        title=title,
        description=description,
    )
