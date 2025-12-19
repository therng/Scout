#!/usr/bin/env ./venv/bin/python
from dotenv import load_dotenv
load_dotenv()

import os
from datetime import datetime, timezone
from typing import List, Optional
from contextlib import asynccontextmanager
from zoneinfo import ZoneInfo

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Path
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorCollection
from bson.objectid import ObjectId
from pyngrok import ngrok

from scraper import search_tracks_async

# -----------------------------------------------------------------------------
# Configuration from environment (.env)
# -----------------------------------------------------------------------------
MONGO_URL = os.getenv("MONGO_URL")
DB_NAME = os.getenv("MONGO_DB", "music")
COL_NAME = os.getenv("MONGO_COL", "history")

NGROK_AUTH = os.getenv("NGROK_AUTH", "")
PORT = int(os.getenv("PORT", "8111"))
APP_TIMEZONE = ZoneInfo("Asia/Bangkok")


def now_in_app_tz() -> datetime:
    return datetime.now(APP_TIMEZONE)


def to_app_tz(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(APP_TIMEZONE)


# -----------------------------------------------------------------------------
# Global state
# -----------------------------------------------------------------------------
class State:
    client: Optional[AsyncIOMotorClient] = None
    collection: Optional[AsyncIOMotorCollection] = None


# -----------------------------------------------------------------------------
# Pydantic models (aligned with Track.swift)
# -----------------------------------------------------------------------------
class Track(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int
    artist: str
    title: str
    duration: int
    download: str
    stream: str


class SearchResponse(BaseModel):
    search_id: str
    results: List[Track]
    query: str
    count: int


class HistoryItem(BaseModel):
    timestamp: datetime
    search_id: str
    query: str


# -----------------------------------------------------------------------------
# FastAPI app
# -----------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    if not MONGO_URL:
        print("MONGO_URL not set. Running without MongoDB.")
        State.client = None
        State.collection = None
    else:
        try:
            State.client = AsyncIOMotorClient(
                MONGO_URL,
                tz_aware=True,
                tzinfo=APP_TIMEZONE,
            )
            await State.client.admin.command("ping")
            State.collection = State.client[DB_NAME][COL_NAME]
            print("MongoDB connected.")
        except Exception as e:
            print("MongoDB connection error:", e)
            State.client = None
            State.collection = None

    # App is ready
    yield

    # Teardown
    if State.client:
        State.client.close()
        print("MongoDB closed.")


app = FastAPI(title="Music Search API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# -----------------------------------------------------------------------------
# Endpoints
# -----------------------------------------------------------------------------
@app.get("/")
async def root():
    return {"status": "ok", "service": "music-search"}


@app.get("/health")
async def health():
    if not State.client:
        return {"status": "degraded", "db": "disconnected"}
    try:
        await State.client.admin.command("ping")
        return {"status": "ok", "db": "connected"}
    except Exception:
        return {"status": "degraded", "db": "disconnected"}


@app.get("/search", response_model=SearchResponse)
async def search(track: str = Query(..., min_length=1)):
    query = track.strip()
    if not query:
        raise HTTPException(status_code=400, detail="Empty query")

    # Call Playwright scraper
    try:
        raw_items = await search_tracks_async(query)
    except Exception as e:
        # Important: bubble errors instead of “นิ่ง”
        raise HTTPException(status_code=500, detail=f"Scraper error: {e}")

    tracks = [Track.model_validate(x) for x in (raw_items or [])]
    count = len(tracks)

    search_id = "no-db"
    if State.collection is not None:
        doc = {
            "query": query,
            "results": [t.model_dump() for t in tracks],
            "count": count,
            "timestamp": now_in_app_tz(),
        }
        result = await State.collection.insert_one(doc)
        search_id = str(result.inserted_id)

    return SearchResponse(
        search_id=search_id,
        results=tracks,
        query=query,
        count=count,
    )


@app.get("/history", response_model=List[HistoryItem])
async def history():
    if State.collection is None:
        raise HTTPException(status_code=503, detail="Database not available")

    cursor = (
        State.collection.find({}, {"results": 0})
        .sort("timestamp", -1)
    )

    items: List[HistoryItem] = []
    async for d in cursor:
        timestamp = to_app_tz(d.get("timestamp") or now_in_app_tz())
        items.append(
            HistoryItem(
                search_id=str(d.get("_id")),
                query=d.get("query", ""),
                timestamp=timestamp,
            )
        )
    return items


@app.get("/history/{search_id}", response_model=SearchResponse)
async def history_by_id(search_id: str = Path(...)):
    if State.collection is None:
        raise HTTPException(status_code=503, detail="Database not available")

    try:
        oid = ObjectId(search_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid search_id")

    d = await State.collection.find_one({"_id": oid})
    if not d:
        raise HTTPException(status_code=404, detail="Not found")

    results = [Track.model_validate(x) for x in d.get("results", [])]
    count = int(d.get("count", len(results)))

    return SearchResponse(
        search_id=str(d.get("_id")),
        results=results,
        query=d.get("query", ""),
        count=count,
    )


@app.api_route("/delete", methods=["DELETE", "GET"])
async def delete_all_history():
    if State.collection is None:
        raise HTTPException(status_code=503, detail="Database not available")

    result = await State.collection.delete_many({})
    return {"deleted_count": result.deleted_count}


@app.api_route("/delete/{search_id}", methods=["DELETE", "GET"])
async def delete_history_by_id(search_id: str = Path(...)):
    if State.collection is None:
        raise HTTPException(status_code=503, detail="Database not available")

    try:
        oid = ObjectId(search_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid search_id")

    result = await State.collection.delete_one({"_id": oid})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Not found")

    return {"deleted": True, "search_id": search_id}


# -----------------------------------------------------------------------------
# Entry point (for ./main.py)
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    if NGROK_AUTH:
        ngrok.set_auth_token(NGROK_AUTH)
        tunnel = ngrok.connect(PORT)
        url = tunnel.public_url
        print(f'\033[1;92m{url}/search?track=Xijaro%20Pitch%20Extended%20Mix\033[0m')
        print()
        print(f'\033[1;92m{url}/search?track=Armin%20Van%20Extended%20Mix\033[0m')
        print()
        print(f'\033[1;92m{url}/search?track=Aly%20Fila%20Extended%20Mix\033[0m')
    uvicorn.run("main:app", host="0.0.0.0", port=PORT, reload=True)
