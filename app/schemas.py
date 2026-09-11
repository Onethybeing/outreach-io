import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ResumeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str
    status: str
    uploaded_at: datetime


class HealthOut(BaseModel):
    status: str
    db: str
