from dtos.progress_tracker_dto import ProgressTrackerGetDTO
from fastapi import APIRouter
from repositories.progress_tracker_repo import get_progress
from services.decorators import standard_response

router = APIRouter(prefix="/progress-tracker")

@router.get("/{progress_tracker_id}")
@standard_response()
def get(progress_tracker_id: int) -> ProgressTrackerGetDTO:
  response = get_progress(progress_tracker_id)

  response = ProgressTrackerGetDTO.model_validate(response)

  return response