from fastapi import APIRouter, HTTPException

from services.firebase_service import FirebaseNotConfiguredError, get_stats

router = APIRouter(prefix="/stats", tags=["stats"])


@router.get("")
async def statistics():
    try:
        return get_stats()
    except FirebaseNotConfiguredError as e:
        raise HTTPException(status_code=503, detail=str(e))
