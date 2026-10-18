from typing import Dict, Any, Optional, List
import time
import base64
import re
from .client import MovieBoxClient

DUMMY_MARKERS = (
    "/other/", "b164fbfb", "please-update", "please_update",
    "update-your-app", "macdn.aoneroom.com/other"
)


def _decode_urlprefix(sc: str) -> Optional[str]:
    """Decode real CDN base from signCookie urlprefix= field (anti 21s dummy)."""
    if not sc or "urlprefix=" not in sc:
        return None
    m = re.search(r"urlprefix=([A-Za-z0-9+/=_-]+)", sc)
    if not m:
        return None
    b64 = m.group(1)
    b64 += "=" * ((4 - len(b64) % 4) % 4)
    try:
        prefix = base64.b64decode(b64).decode("utf-8", errors="ignore").strip()
        if prefix.startswith("http"):
            if not prefix.endswith((".mpd", ".m3u8")):
                prefix = prefix.rstrip("/") + "/index.mpd"
            return prefix
    except Exception:
        pass
    return None


def _is_dummy(url: str) -> bool:
    if not url:
        return True
    u = url.lower()
    return any(m in u for m in DUMMY_MARKERS)


class MovieBoxStream:
    def __init__(self, client: MovieBoxClient):
        self.client = client
        self.sign_cookie_cache = {}  # subject_id: {cookie, expiry}

    def get_play_info(
        self,
        subject_id: str,
        season: int = 1,
        episode: int = 1,
        resource_id: Optional[str] = None,
    ) -> Dict:
        """Calls the play-info API endpoint."""
        params = {
            "subjectId": subject_id,
            "se": season,
            "ep": episode,
        }
        if resource_id:
            params["resourceId"] = resource_id

        return self.client.request(
            "GET",
            "/wefeed-mobile-bff/subject-api/play-info",
            params=params,
            headers={
                "User-Agent": "MovieBox/4.0.02 (Android 14; Pixel 6)",
                "X-M-Version": "4.0.02",
            },
        )

    def get_stream(self, subject_id: str, auto_refresh: bool = True) -> Dict:
        """
        Resolves the stream URL and the signed cookie for media access.
        Applies anti-piracy bypass so the 21-second dummy video is never returned.
        """
        play_info_res = self.get_play_info(subject_id)

        if not play_info_res or "data" not in play_info_res:
            raise ValueError(
                f"Failed to get play info for {subject_id}: {play_info_res.get('msg', 'Unknown Error')}"
            )

        data = play_info_res["data"]
        video_streams = data.get("streamList") or data.get("streams") or []
        if not video_streams:
            raise ValueError("No streams found in play info response.")

        best = None
        for st in video_streams:
            sc = st.get("signCookie") or data.get("signCookie") or ""
            url = st.get("url") or ""
            real = _decode_urlprefix(sc)
            if real:
                url = real
            if _is_dummy(url):
                continue
            best = {**st, "url": url, "signCookie": sc}
            break

        if not best:
            st = video_streams[0]
            sc = st.get("signCookie") or data.get("signCookie") or ""
            real = _decode_urlprefix(sc)
            url = real or st.get("url")
            if _is_dummy(url):
                raise ValueError(
                    "Only dummy 21s anti-piracy stream available for this title."
                )
            best = {**st, "url": url, "signCookie": sc}

        stream_url = best.get("url")
        sign_cookie = best.get("signCookie") or data.get("signCookie", "")

        self.sign_cookie_cache[subject_id] = {
            "cookie": sign_cookie,
            "timestamp": time.time(),
        }

        subtitle_list = data.get("subTitleList", [])

        return {
            "url": stream_url,
            "headers": {
                "Cookie": sign_cookie if sign_cookie else "",
                "User-Agent": "ExoPlayerLib/2.18.7",
            },
            "subtitles": subtitle_list,
        }

    def get_subtitles(self, subject_id: str, lang: str = "en") -> List[Dict]:
        """Convenience method to get subtitles."""
        res = self.get_stream(subject_id)
        return [
            s
            for s in res["subtitles"]
            if s.get("language") == lang or not lang
        ]
