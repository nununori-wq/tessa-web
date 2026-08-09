from pydantic import BaseModel
from typing import List, Optional, Dict

class ChatMessage(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    message: str
    history: Optional[List[ChatMessage]] = None

class ChatResponse(BaseModel):
    reply: str

class HealthResponse(BaseModel):
    status: str