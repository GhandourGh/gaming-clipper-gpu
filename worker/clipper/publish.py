"""Publishing through one multi-network API, behind a small adapter interface. Nothing is ever automatic:
  draft (local) --owner Approves--> approved --owner "Send as draft"--> held at the provider (requiresApproval)
  --owner Publish (only if publishing is enabled in Settings)--> published.
Ayrshare is implemented (docs: ayrshare.com/docs). PostLake and Publora have no verified public API docs yet:
they are listed but refuse to run until an adapter is written against their real docs."""
import os
from pathlib import Path

import httpx

AYRSHARE = "https://api.ayrshare.com/api"
PLATFORM = {"youtube": "youtube", "instagram": "instagram", "tiktok": "tiktok"}


class PublishError(RuntimeError):
    pass


class Publisher:
    name = "base"

    def configured(self) -> bool:
        raise NotImplementedError

    def create_draft(self, post: dict, video: Path) -> dict:
        """Upload the clip and create a post that is held (not live). Returns {"external_id", "status", "media_url"}."""
        raise NotImplementedError

    def publish(self, external_id: str) -> dict:
        raise NotImplementedError


class Ayrshare(Publisher):
    name = "ayrshare"

    def __init__(self, key: str | None = None):
        self.key = key or os.environ.get("AYRSHARE_API_KEY")

    def configured(self) -> bool:
        return bool(self.key)

    def _h(self, **extra):
        if not self.key:
            raise PublishError("AYRSHARE_API_KEY is not set")
        return {"Authorization": f"Bearer {self.key}", **extra}

    def _check(self, r: httpx.Response) -> dict:
        try:
            d = r.json()
        except ValueError:
            d = {"raw": r.text[:300]}
        if r.status_code >= 400 or (isinstance(d, dict) and d.get("status") == "error"):
            raise PublishError(f"Ayrshare HTTP {r.status_code}: {str(d.get('message') or d)[:300]}")
        return d

    def upload(self, video: Path) -> str:
        """Small files: /media/upload (<=30 MB). Larger: presigned URL (up to 5 GB), as in Ayrshare's docs."""
        size = video.stat().st_size
        if size <= 10 * 1024 * 1024:
            with video.open("rb") as f:
                d = self._check(httpx.post(f"{AYRSHARE}/media/upload", headers=self._h(), timeout=300,
                                           files={"file": (video.name, f, "video/mp4")}, data={"fileName": video.name}))
            return d["url"]
        d = self._check(httpx.get(f"{AYRSHARE}/media/uploadUrl", headers=self._h(), timeout=60,
                                  params={"fileName": video.name, "contentType": "mp4"}))
        with video.open("rb") as f:
            put = httpx.put(d["uploadUrl"], content=f.read(), headers={"Content-Type": d.get("contentType", "video/mp4")}, timeout=600)
        if put.status_code != 200:
            raise PublishError(f"Ayrshare upload failed: HTTP {put.status_code}")
        return d["accessUrl"]

    def create_draft(self, post: dict, video: Path) -> dict:
        url = self.upload(video)
        text = (post.get("caption") or "").strip()
        if post.get("hashtags"):
            text = f"{text}\n\n{post['hashtags'].strip()}".strip()
        body = {"post": text, "platforms": [PLATFORM[post["platform"]]], "mediaUrls": [url], "isVideo": True,
                "requiresApproval": True, "notes": "Created by gaming-clipper; approve in the clipper dashboard.",
                "idempotencyKey": f"gclip-{post['id']}"}
        if post["platform"] == "youtube":
            body["youTubeOptions"] = {"title": (post.get("title") or text[:90] or "Clip")[:100], "shorts": True, "visibility": "public"}
        d = self._check(httpx.post(f"{AYRSHARE}/post", headers=self._h(), json=body, timeout=120))
        return {"external_id": d.get("id") or (d.get("posts") or [{}])[0].get("id"), "status": d.get("status") or "awaiting approval", "media_url": url}

    def publish(self, external_id: str) -> dict:
        d = self._check(httpx.patch(f"{AYRSHARE}/post", headers=self._h(), json={"id": external_id, "approved": True}, timeout=60))
        return {"status": d.get("status") or "approved"}


class NotIntegrated(Publisher):
    def __init__(self, name: str):
        self.name = name

    def configured(self) -> bool:
        return False

    def create_draft(self, post, video):
        raise PublishError(f"{self.name} is not integrated yet: its API docs could not be verified. Send the docs URL to add it.")

    publish = create_draft


def provider(name: str) -> Publisher:
    return Ayrshare() if name == "ayrshare" else NotIntegrated(name)


def gate(action: str, post: dict, publishing_enabled: bool, pub: Publisher) -> str | None:
    """Why an action is not allowed right now (None = allowed). Every step is a manual owner action."""
    if action == "send":
        if post["status"] != "approved":
            return "Approve the post first."
        if not pub.configured():
            return f"{pub.name} is not configured (add its API key to .env)."
        return None
    if action == "publish":
        if not publishing_enabled:
            return "Publishing is locked. Enable it in Settings first."
        if post["status"] != "sent" or not post.get("external_id"):
            return "Send the post as a draft first."
        return None
    return f"unknown action {action}"
