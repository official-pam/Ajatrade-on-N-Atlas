"""Pydantic models describing the WhatsApp Cloud API webhook payload."""

from pydantic import BaseModel, ConfigDict, Field


class Profile(BaseModel):
    name: str


class Contact(BaseModel):
    wa_id: str
    profile: Profile


class TextContent(BaseModel):
    body: str


class MediaContent(BaseModel):
    id: str
    mime_type: str | None = None
    caption: str | None = None


class LocationContent(BaseModel):
    latitude: float
    longitude: float
    name: str | None = None
    address: str | None = None


class InteractiveReply(BaseModel):
    id: str
    title: str | None = None
    description: str | None = None


class InteractiveContent(BaseModel):
    type: str | None = None
    button_reply: InteractiveReply | None = None
    list_reply: InteractiveReply | None = None


class Message(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str | None = None
    from_: str = Field(alias="from")
    timestamp: str | None = None
    type: str
    text: TextContent | None = None
    audio: MediaContent | None = None
    image: MediaContent | None = None
    location: LocationContent | None = None
    interactive: InteractiveContent | None = None


class ChangeValue(BaseModel):
    contacts: list[Contact] = Field(default_factory=list)
    messages: list[Message] = Field(default_factory=list)


class Change(BaseModel):
    field: str | None = None
    value: ChangeValue = Field(default_factory=ChangeValue)


class Entry(BaseModel):
    id: str | None = None
    changes: list[Change] = Field(default_factory=list)


class WebhookPayload(BaseModel):
    object: str | None = None
    entry: list[Entry] = Field(default_factory=list)
    
