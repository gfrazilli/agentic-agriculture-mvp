import json

from django.conf import settings
from django.core.exceptions import RequestDataTooBig
from django.http import HttpRequest
from pydantic import BaseModel, ValidationError

from agriculture.api.errors import APIError


def parse_json[ModelT: BaseModel](request: HttpRequest, model: type[ModelT]) -> ModelT:
    if request.content_type != "application/json":
        raise APIError(
            code="unsupported_media_type",
            message="Content-Type must be application/json.",
            status=415,
        )

    content_length = request.headers.get("Content-Length")
    if content_length:
        try:
            if int(content_length) > settings.API_MAX_REQUEST_BYTES:
                raise APIError(
                    code="request_too_large",
                    message="The request body exceeds the configured limit.",
                    status=413,
                )
        except ValueError:
            raise APIError(
                code="invalid_content_length",
                message="Content-Length must be an integer.",
                status=400,
            ) from None

    try:
        body = request.body
    except RequestDataTooBig:
        raise APIError(
            code="request_too_large",
            message="The request body exceeds the configured limit.",
            status=413,
        ) from None

    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise APIError(
            code="invalid_json",
            message="The request body is not valid JSON.",
            status=400,
        ) from None

    if not isinstance(payload, dict):
        raise APIError(
            code="invalid_json_object",
            message="The request body must be a JSON object.",
            status=400,
        )
    try:
        # JSON mode preserves strict contracts while allowing JSON-native string
        # encodings for UUIDs, dates and datetimes.
        return model.model_validate_json(body)
    except ValidationError as exc:
        raise APIError(
            code="validation_error",
            message="The request body failed validation.",
            status=422,
            details=exc.errors(include_url=False, include_context=False),
        ) from None
