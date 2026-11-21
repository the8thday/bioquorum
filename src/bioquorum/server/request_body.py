"""Bounded JSON bodies for workbench commands."""

from pydantic import BaseModel, ValidationError
from starlette.requests import Request


async def read_body[T: BaseModel](request: Request, model: type[T], *, limit: int = 32_768) -> T:
    if request.headers.get("content-type", "").split(";")[0] != "application/json":
        raise ValueError("Expected application/json")
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > limit:
            raise ValueError(f"Request body exceeds {limit // 1024} KiB")
    try:
        return model.model_validate_json(data)
    except ValidationError:
        raise ValueError("Invalid request fields") from None


class Empty(BaseModel):
    pass
