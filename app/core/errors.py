"""One error shape for the whole API, so the frontend handles every failure the same way:

    {"error": {"code": "QUESTION_NOT_FOUND", "message": "...", "fields": {"stem": "required"}}}
"""
from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, fields: dict | None = None):
        self.status, self.code, self.message, self.fields = status, code, message, fields or {}


def not_found(what: str = "Resource") -> ApiError:
    return ApiError(404, f"{what.upper().replace(' ', '_')}_NOT_FOUND", f"{what} not found")


def forbidden(msg: str = "You do not have permission for this action") -> ApiError:
    return ApiError(403, "FORBIDDEN", msg)


def bad_request(code: str, msg: str, fields: dict | None = None) -> ApiError:
    return ApiError(400, code, msg, fields)


def _body(code, message, fields=None):
    return {"error": {"code": code, "message": message, "fields": fields or {}}}


def install(app):
    @app.exception_handler(ApiError)
    async def _api(_: Request, e: ApiError):
        return JSONResponse(_body(e.code, e.message, e.fields), status_code=e.status)

    @app.exception_handler(RequestValidationError)
    async def _val(_: Request, e: RequestValidationError):
        fields = {}
        for err in e.errors():
            loc = [str(x) for x in err.get("loc", []) if x not in ("body", "query", "path")]
            fields[".".join(loc) or "_"] = err.get("msg", "invalid")
        return JSONResponse(_body("VALIDATION_ERROR", "Some fields are invalid", fields), status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, e: StarletteHTTPException):
        code = {401: "UNAUTHORIZED", 403: "FORBIDDEN", 404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED",
                429: "RATE_LIMITED"}.get(e.status_code, "HTTP_ERROR")
        return JSONResponse(_body(code, str(e.detail)), status_code=e.status_code, headers=getattr(e, "headers", None))
