from pydantic import BaseModel, Field, EmailStr
from typing import List, Optional, Dict, Any
from datetime import datetime
from bson import ObjectId
from enum import Enum
from backend.config import settings

class PyObjectId(ObjectId):
    @classmethod
    def __get_validators__(cls):
        yield cls.validate

    @classmethod
    def validate(cls, v):
        if not ObjectId.is_valid(v):
            raise ValueError("Invalid objectid")
        return ObjectId(v)

    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema, handler):
        return {"type": "string"}

class DesignBase(BaseModel):
    user_id: str
    prompt: Optional[str] = None
    name: str = "Untitled Project"
    description: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    is_deleted: bool = False
    parent_id: Optional[str] = None  # Tracking versions/duplications
    layout_data: Optional[Dict[str, Any]] = None
    image_url: Optional[str] = None
    model_url: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

    model_config = {
        "populate_by_name": True,
        "json_encoders": {ObjectId: str},
        "protected_namespaces": ()
    }

class DesignCreate(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=settings.MAX_PROMPT_LENGTH, description="Design prompt (max 5000 characters)")

class DesignResponse(DesignBase):
    id: str = Field(alias="_id")

class UserRole(str, Enum):
    free = "free"
    pro = "pro"
    admin = "admin"

class UsageStats(BaseModel):
    generations_this_month: int = 0
    total_generations: int = 0
    storage_used_bytes: int = 0

class UserBase(BaseModel):
    email: EmailStr
    full_name: Optional[str] = None
    photo_url: Optional[str] = None
    role: UserRole = UserRole.free
    joined_date: datetime = Field(default_factory=datetime.utcnow)
    usage_stats: UsageStats = Field(default_factory=UsageStats)

class UserCreate(UserBase):
    firebase_uid: str
