import os
import httpx
from app.services.whatsapp.helper import to_ogg_opus

WHATSAPP_TOKEN = os.environ["WHATSAPP_ACCESS_TOKEN"]
PHONE_NUMBER_ID = os.environ["WHATSAPP_PHONE_NUMBER_ID"]
GRAPH_VERSION = "v21.0"   # use the version shown in your Meta app dashboard
GRAPH_URL = f"https://graph.facebook.com/{GRAPH_VERSION}/{PHONE_NUMBER_ID}"
HEADERS = {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}


async def send_voice_note(to: str, audio_bytes: bytes) -> bool:
    async with httpx.AsyncClient(timeout=15.0) as http:   
        media_id = await upload_media(audio_bytes, http)
        if not media_id:
            return False

        media_sent = await send_uploaded_media(media_id, to, http)
        if not media_sent:
            return False
        return True

        


async def upload_media(audio_bytes: bytes, http: httpx.AsyncClient) -> str | bool:
    # Step 1: upload the audio, get a media id back
    upload = await http.post(
        f"{GRAPH_URL}/media",
        headers=HEADERS,
        data={"messaging_product": "whatsapp", "type": "audio/mpeg"},
        files={"file": ("reply.mp3", audio_bytes, "audio/mpeg")},
    )
    if upload.status_code != 200:
        print(f"Media upload failed: {upload.status_code} {upload.text}")
        return False
    media_id = upload.json()["id"]
    return media_id

async def send_uploaded_media(media_id: str, to: str, http: httpx.AsyncClient) -> bool: 
# Step 2: send the message that references that media id
    send = await http.post(
        f"{GRAPH_URL}/messages",
        headers=HEADERS,
        json={
            "messaging_product": "whatsapp",
            "to": to,
            "type": "audio",
            "audio": {"id": media_id},
        },
    )
    if send.status_code != 200:
        print(f"Send failed: {send.status_code} {send.text}")
        return False
    return True




async def send_text(to: str, body: str) -> bool:
    async with httpx.AsyncClient(timeout=15.0) as http:
        r = await http.post(
            f"{GRAPH_URL}/messages",
            headers=HEADERS,
            json={
                "messaging_product": "whatsapp",
                "to": to,
                "type": "text",
                "text": {"body": body},
            },
        )
    if r.status_code != 200:
        print(f"Text send failed: {r.status_code} {r.text}")
    return r.status_code == 200