from fastapi import APIRouter, HTTPException

from app.services import grader

router = APIRouter(prefix='/api', tags=['grader'])


@router.get('/grader')
def grades(client_id: int):
    return grader.grade_client(client_id)


@router.post('/grader/queue')
def queue(payload: dict):
    client_id = payload.get('client_id')
    if not client_id:
        raise HTTPException(400, 'client_id required')
    return grader.queue_recommendations(client_id)


@router.get('/grader/actions')
def actions(client_id: int | None = None):
    return grader.pending_actions(client_id)


@router.post('/grader/actions/{action_id}/apply')
def apply_action(action_id: int):
    try:
        return grader.apply_action(action_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc))


@router.post('/grader/actions/{action_id}/dismiss')
def dismiss_action(action_id: int):
    grader.dismiss_action(action_id)
    return {'ok': True}
