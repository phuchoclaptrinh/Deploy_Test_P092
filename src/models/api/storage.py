"""Storage API schemas."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class SignedUploadRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "original_filename": "water-leak.jpg",
                "mime_type": "image/jpeg",
                "file_size": 245760,
            }
        },
    )

    original_filename: str = Field(
        min_length=1,
        max_length=255,
        description="Original client filename. Path traversal and unsupported extensions are rejected.",
        examples=["water-leak.jpg"],
    )
    mime_type: str = Field(
        min_length=1,
        max_length=255,
        description="Client-declared MIME type. The MVP accepts configured image MIME types only.",
        examples=["image/jpeg"],
    )
    file_size: int = Field(
        gt=0,
        description="Expected upload size in bytes. It must not exceed the configured ticket image size limit.",
        examples=[245760],
    )


class SignedUploadResponse(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "upload_id": "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
                "signed_upload_url": "https://storage.example.invalid/upload/sign/fictional",
                "signed_upload_token": None,
                "expires_in": 7200,
                "required_headers": {"content-type": "image/jpeg"},
            }
        },
    )

    upload_id: UUID = Field(description="Upload session UUID to pass in ticket creation after the object is uploaded.")
    signed_upload_url: str | None = Field(
        default=None,
        description="Short-lived Supabase signed upload URL, when returned by Storage.",
    )
    signed_upload_token: str | None = Field(
        default=None,
        description="Short-lived Supabase signed upload token, when returned instead of a URL.",
    )
    expires_in: int = Field(description="Signed upload target lifetime in seconds.", examples=[7200])
    required_headers: dict[str, str] = Field(
        default_factory=dict,
        description="Headers the client must send when uploading the object to Supabase Storage.",
    )
