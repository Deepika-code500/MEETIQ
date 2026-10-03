# =========================================================
# MeetIQ - Asynchronous Celery & Redis Task Queue
# =========================================================

import os
from celery import Celery

def make_celery(app_name=__name__):
    broker_url = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
    result_backend = os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/0")
    
    celery_instance = Celery(
        app_name,
        broker=broker_url,
        backend=result_backend
    )
    
    celery_instance.conf.update(
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        timezone="UTC",
        enable_utc=True,
        task_track_started=True
    )
    
    return celery_instance

celery = make_celery("meetiq")

@celery.task(name="tasks.async_generate_summary_task")
def async_generate_summary_task(job_id, meeting_id):
    """
    Background Celery task to execute Ollama meeting summarization.
    Updates job status in MongoDB ai_jobs collection.
    """
    from database import db
    from datetime import datetime, timezone
    
    def get_now():
        return datetime.now(timezone.utc).replace(tzinfo=None)

    ai_jobs = db["ai_jobs"]
    ai_jobs.update_one(
        {"job_id": job_id},
        {"$set": {"status": "PROCESSING", "started_at": get_now()}}
    )
    
    try:
        from app import generate_meeting_ai_summary
        res = generate_meeting_ai_summary(meeting_id)
        
        if res.get("success"):
            ai_jobs.update_one(
                {"job_id": job_id},
                {
                    "$set": {
                        "status": "COMPLETED",
                        "result": res.get("summary"),
                        "completed_at": get_now()
                    }
                }
            )
            return {"status": "COMPLETED", "summary": res.get("summary")}
        else:
            error_msg = res.get("message", "Summary generation failed")
            ai_jobs.update_one(
                {"job_id": job_id},
                {
                    "$set": {
                        "status": "FAILED",
                        "error": error_msg,
                        "completed_at": get_now()
                    }
                }
            )
            return {"status": "FAILED", "error": error_msg}
            
    except Exception as exc:
        err_str = str(exc)
        ai_jobs.update_one(
            {"job_id": job_id},
            {
                "$set": {
                    "status": "FAILED",
                    "error": err_str,
                    "completed_at": get_now()
                }
            }
        )
        return {"status": "FAILED", "error": err_str}


@celery.task(name="tasks.async_transcribe_audio_task")
def async_transcribe_audio_task(job_id, meeting_id, participant_id, chunk_path_str, participant_name="Participant"):
    """
    Background Celery task to execute Whisper audio transcription & toxicity detection.
    Updates job status in MongoDB ai_jobs collection.
    """
    from database import db
    from datetime import datetime, timezone
    from pathlib import Path

    def get_now():
        return datetime.now(timezone.utc).replace(tzinfo=None)

    ai_jobs = db["ai_jobs"]
    ai_jobs.update_one(
        {"job_id": job_id},
        {"$set": {"status": "PROCESSING", "started_at": get_now()}}
    )

    chunk_path = Path(chunk_path_str)

    try:
        if not chunk_path.exists():
            raise FileNotFoundError(f"Audio recording file not found: {chunk_path_str}")

        from app import process_audio_chunk_inference
        result_data = process_audio_chunk_inference(
            meeting_id,
            participant_id,
            chunk_path,
            participant_name
        )

        ai_jobs.update_one(
            {"job_id": job_id},
            {
                "$set": {
                    "status": "COMPLETED",
                    "result": result_data,
                    "completed_at": get_now()
                }
            }
        )
        return {"status": "COMPLETED", "result": result_data}

    except Exception as exc:
        err_str = str(exc)
        if chunk_path.exists():
            try:
                chunk_path.unlink()
            except Exception:
                pass

        ai_jobs.update_one(
            {"job_id": job_id},
            {
                "$set": {
                    "status": "FAILED",
                    "error": err_str,
                    "completed_at": get_now()
                }
            }
        )
        return {"status": "FAILED", "error": err_str}

