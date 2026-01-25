from fastapi import APIRouter, Depends, HTTPException
from typing import List, Dict, Any

router = APIRouter(prefix="/api", tags=["Sync"])

# Placeholder for a dependency that would get the current user from a JWT token
async def get_current_user():
    # In a real app, this would decode the token and return the user model
    return {"username": "testuser", "id": "123"}


@router.get("/sync/ping")
async def sync_ping():
    return {"ok": True}

@router.post("/sync/push")
async def push_changes(changes: List[Dict[str, Any]], current_user: dict = Depends(get_current_user)):
    """Receives a batch of offline changes from a client."""
    # TODO: Implement the logic to process changes:
    # 1. Iterate through changes in a transaction.
    # 2. Use a repository/service layer to apply upserts.
    # 3. Use LWW (Last-Write-Wins) for conflict resolution based on updated_at.
    # 4. Collect temp_id -> id mappings.
    # 5. Return the mappings and status for each change.
    print(f"Received {len(changes)} changes from user {current_user['username']}")
    return {"status": "received", "processed_changes": len(changes), "mappings": []}

@router.post("/sync/pull")
async def pull_changes(last_sync_at: str, current_user: dict = Depends(get_current_user)):
    """Provides changes from the server since the client's last sync time."""
    # TODO: Implement the logic to fetch new data:
    # 1. Parse last_sync_at string to a datetime object.
    # 2. Query all relevant tables for records where updated_at > last_sync_at.
    # 3. Serialize and return the records.
    return {"status": "ok", "since": last_sync_at, "changes": []}
