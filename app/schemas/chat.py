from typing import Literal

from pydantic import BaseModel

AgentName = Literal["gurobot", "explainer", "modeler"]


class FilePayload(BaseModel):
    filename: str
    content_b64: str


class ChatRequest(BaseModel):
    conversation_id: str
    agent: AgentName
    prompt: str
    input_files: list[FilePayload] | None = None


class ChatResponse(BaseModel):
    conversation_id: str
    agent: str
    response: str
    output_files: list[FilePayload] = []
    recovered: bool = False
