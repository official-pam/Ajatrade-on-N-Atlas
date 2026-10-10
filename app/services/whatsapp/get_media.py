"""
WhatsApp Cloud API — media (voice note) retrieval.
 
Meta's flow for downloading inbound media is two calls:
  1. GET  https://graph.facebook.com/{version}/{media_id}
     -> returns a short-lived, signed media URL + metadata (mime_type, sha256, file_size)
  2. GET  <that url>  with your access token as a Bearer header
     -> returns the raw binary bytes
 
The media URL from step 1 expires quickly (minutes), so always fetch it fresh —
never cache or reuse it.
"""
 
import httpx
from fastapi import HTTPException
from dataclasses import dataclass
import base64
 
from app.config import get_settings  # WHATSAPP_ACCESS_TOKEN, WHATSAPP_API_VERSION
import os
 
 
GRAPH_BASE_URL = f"https://graph.facebook.com/{get_settings().whatsapp_api_version}"
 
 
async def get_media_url(media_id: str) -> dict:
    """Step 1: resolve a media_id to a temporary download URL + metadata."""
    headers = {"Authorization": f"Bearer {os.getenv('WHATSAPP_ACCESS_TOKEN', '')}"}
 
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(f"{GRAPH_BASE_URL}/{media_id}", headers=headers)
 
    if response.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to resolve WhatsApp media_id={media_id}: {response.text}",
        )
 
    return response.json()  # {"url": ..., "mime_type": ..., "sha256": ..., "file_size": ..., "id": ...}
 
 
async def download_media(media_url: str) -> bytes:
    """Step 2: download the actual binary from the signed media URL."""
    headers = {"Authorization": f"Bearer {os.getenv('WHATSAPP_ACCESS_TOKEN', '')}"}
 
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(media_url, headers=headers)
 
    if response.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to download WhatsApp media binary: {response.status_code}",
        )
 
    return response.content
 
 
async def fetch_voice_note(media_id: str) -> tuple[bytes, str]:
    """
    Convenience wrapper: media_id -> (audio_bytes, mime_type).
    This is what your webhook handler / STT stage should call.
    """
    meta = await get_media_url(media_id)
    audio_bytes = await download_media(meta["url"])
    return audio_bytes, meta.get("mime_type", "audio/ogg")


@dataclass
class WhatsAppImage:
    media_id: str
    mime_type: str
    data: bytes
 
    @property
    def base64(self) -> str:
        return base64.b64encode(self.data).decode("utf-8")
 
    @property
    def data_url(self) -> str:
        return f"data:{self.mime_type};base64,{self.base64}"
 

async def fetch_whatsapp_image(media_id: str) -> WhatsAppImage:
    """
    Two-step retrieval:
      1. GET /{media_id}  -> returns a temporary download URL + mime type
      2. GET that URL (with the same bearer token) -> raw image bytes
    The download URL expires after ~5 minutes, so fetch immediately.
    """
    headers = {"Authorization": f"Bearer {os.getenv('WHATSAPP_ACCESS_TOKEN', '')}"}
    async with httpx.AsyncClient(timeout=30.0) as client:
        # Step 1: resolve the media ID to a download URL
        meta_resp = await client.get(f"{GRAPH_BASE_URL}/{media_id}", headers=headers)
        if meta_resp.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail=f"Could not resolve media {media_id}: {meta_resp.text}",
            )
        meta = meta_resp.json()
        media_url = meta["url"]
        print("media_url link: ", media_url)
        mime_type = meta.get("mime_type", "image/jpeg")
 
        # Step 2: download the binary (the Authorization header is required here too)
        file_resp = await client.get(media_url, headers=headers)
        if file_resp.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail=f"Could not download media {media_id}: {file_resp.status_code}",
            )

        # print("Fetched image data: ", file_resp.content)
        image_bytes = file_resp.content
 
    # return WhatsAppImage(media_id=media_id, mime_type=mime_type, data=file_resp.content)
    return  mime_type, image_bytes
 