"""
Pydantic 请求/响应模型
"""

from typing import List, Optional
from pydantic import BaseModel


# === 请求模型 ===

class SubmitBranchRequest(BaseModel):
    user_id: str
    title: str
    description: str
    tone: str
    preview: str


class VoteRequest(BaseModel):
    user_id: str


# === 响应模型 ===

class ComicPanel(BaseModel):
    panel_number: int
    image_url: str
    description: str


class BranchInfo(BaseModel):
    id: int
    title: str
    description: str
    tone: str
    preview: str
    vote_count: int
    source: str
    user_has_voted: bool
    comics: List[ComicPanel]


class BranchListResponse(BaseModel):
    episode_id: int
    branches: List[BranchInfo]
    has_branches: bool
    is_generating: bool


class SubmitBranchResponse(BaseModel):
    branch_id: int
    status: str
    rejection_reason: Optional[str] = None


class VoteResponse(BaseModel):
    branch_id: int
    vote_count: int
    already_voted: bool


class ComicListResponse(BaseModel):
    branch_id: int
    comics: List[ComicPanel]


class GenerateResponse(BaseModel):
    status: str
    episode_id: int
    message: str = ""
