import os

import httpx
from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import agent

app = FastAPI(title="Yamaha BD Voice Agent (prototype)")


class Msg(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(max_length=2000)


class ChatIn(BaseModel):
    messages: list[Msg] = Field(min_length=1, max_length=40)


class TtsIn(BaseModel):
    text: str = Field(max_length=800)


@app.post("/api/chat")
async def chat(body: ChatIn):
    if body.messages[-1].role != "user":
        raise HTTPException(400, "last message must be from user")
    return await agent.respond([m.model_dump() for m in body.messages])


@app.get("/api/config")
def config():
    return {"server_tts": bool(os.environ.get("AZURE_SPEECH_KEY"))}


@app.post("/api/tts")
async def tts(body: TtsIn):
    """Optional paid upgrade: Azure Bangla neural voice. Free path uses browser voice."""
    key = os.environ.get("AZURE_SPEECH_KEY")
    if not key:
        raise HTTPException(404, "server TTS not configured")
    region = os.environ.get("AZURE_SPEECH_REGION", "southeastasia")
    voice = os.environ.get("AZURE_VOICE", "bn-BD-NabanitaNeural")
    text = body.text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    ssml = f"<speak version='1.0' xml:lang='bn-BD'><voice name='{voice}'>{text}</voice></speak>"
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(
            f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1",
            headers={
                "Ocp-Apim-Subscription-Key": key,
                "Content-Type": "application/ssml+xml",
                "X-Microsoft-OutputFormat": "audio-24khz-48kbitrate-mono-mp3",
            },
            content=ssml.encode("utf-8"),
        )
    if r.status_code != 200:
        raise HTTPException(502, "tts upstream error")
    return Response(r.content, media_type="audio/mpeg")


@app.get("/")
def index():
    return FileResponse("static/index.html")


app.mount("/static", StaticFiles(directory="static"), name="static")
