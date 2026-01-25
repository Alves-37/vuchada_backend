from datetime import datetime, timezone
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import List, Dict, Any, Optional

router = APIRouter(prefix="/api", tags=["Sync"])

# Placeholder for a dependency that would get the current user from a JWT token
async def get_current_user():
    # In a real app, this would decode the token and return the user model
    return {"username": "testuser", "id": "123"}


@router.get("/sync/ping")
async def sync_ping():
    return {"ok": True}

class SyncPushEvent(BaseModel):
    outbox_id: int
    entity: str
    operation: str
    payload: Dict[str, Any] = {}


class SyncPushRequest(BaseModel):
    events: List[SyncPushEvent] = []


@router.post("/sync/push")
async def push_changes(req: SyncPushRequest, current_user: dict = Depends(get_current_user)):
    """Receives a batch of offline changes from a client."""
    # TODO: Implement the logic to process changes:
    # 1. Iterate through changes in a transaction.
    # 2. Use a repository/service layer to apply upserts.
    # 3. Use LWW (Last-Write-Wins) for conflict resolution based on updated_at.
    # 4. Collect temp_id -> id mappings.
    # 5. Return the mappings and status for each change.
    events = req.events or []
    try:
        print(f"Received {len(events)} events from user {current_user['username']}")
    except Exception:
        pass

    # Placeholder: marcar todos como OK para destravar o cliente.
    results = [
        {"outbox_id": int(ev.outbox_id), "ok": True, "error": None}
        for ev in events
    ]
    return {"results": results}

@router.get("/sync/pull")
async def pull_changes(
    since: Optional[str] = None,
    limit: int = 500,
    current_user: dict = Depends(get_current_user),
):
    """Compatível com o NEOPDV2: retorna pedidos incrementais.

    Por enquanto é placeholder (não aplica/retorna dados reais), mas evita 405 Method Not Allowed.
    """
    server_now = datetime.now(timezone.utc).isoformat()
    return {"server_now": server_now, "pedidos": [], "next_since": None, "since": since, "limit": int(limit)}
