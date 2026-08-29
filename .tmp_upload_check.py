import httpx

from src.config import get_settings


settings = get_settings()
with httpx.Client(timeout=20) as client:
    auth = client.post(
        f"{settings.supabase_url}/auth/v1/token?grant_type=password",
        headers={"apikey": settings.supabase_publishable_key},
        json={"email": "minhan@homecare.vn", "password": "homecare-demo"},
    )
    auth.raise_for_status()
    response = client.post(
        "http://127.0.0.1:8001/api/v1/storage/completion-evidence/upload-url",
        headers={"Authorization": f"Bearer {auth.json()['access_token']}"},
        json={"original_filename": "check.png", "mime_type": "image/png", "file_size": 68},
    )
    response.raise_for_status()
    data = response.json()["data"]
    print({"status": response.status_code, "signed_upload_url": bool(data["signed_upload_url"])})
