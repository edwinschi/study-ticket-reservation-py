from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.exceptions import AppError, ValidationAppError
from app.core.request_context import REQUEST_ID_HEADER, get_request_id


def _request_id(request: Request) -> str:
    state_request_id = getattr(request.state, "request_id", None)
    return str(state_request_id or get_request_id() or "unknown")


def _error_response(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
) -> JSONResponse:
    request_id = _request_id(request)
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "request_id": request_id,
            }
        },
        headers={REQUEST_ID_HEADER: request_id},
    )


async def app_error_handler(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, AppError):
        raise exc

    return _error_response(
        request,
        status_code=exc.status_code,
        code=exc.code,
        message=exc.message,
    )


async def validation_error_handler(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    if not isinstance(exc, RequestValidationError):
        raise exc

    error = ValidationAppError()
    return _error_response(
        request,
        status_code=error.status_code,
        code=error.code,
        message=error.message,
    )


async def http_exception_handler(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    if not isinstance(exc, StarletteHTTPException):
        raise exc

    status_code = exc.status_code
    try:
        status = HTTPStatus(status_code)
    except ValueError:
        status = None
    code_by_status = {
        HTTPStatus.NOT_FOUND: "NOT_FOUND",
        HTTPStatus.METHOD_NOT_ALLOWED: "METHOD_NOT_ALLOWED",
    }
    message_by_status = {
        HTTPStatus.NOT_FOUND: "Resource not found",
        HTTPStatus.METHOD_NOT_ALLOWED: "Method not allowed",
    }
    fallback_message = message_by_status.get(status) if status is not None else "Request failed"
    code = (
        code_by_status.get(status, f"HTTP_{status_code}")
        if status is not None
        else f"HTTP_{status_code}"
    )
    message = exc.detail if isinstance(exc.detail, str) else fallback_message
    return _error_response(
        request,
        status_code=status_code,
        code=code,
        message=message,
    )


def register_exception_handlers(app: FastAPI) -> None:
    handlers: dict[type[Exception], Any] = {
        AppError: app_error_handler,
        RequestValidationError: validation_error_handler,
        StarletteHTTPException: http_exception_handler,
    }
    for exception_type, handler in handlers.items():
        app.add_exception_handler(exception_type, handler)
