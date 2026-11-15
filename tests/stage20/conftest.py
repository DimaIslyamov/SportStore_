import pytest
from drf_spectacular.generators import SchemaGenerator
from jsonschema import Draft4Validator, FormatChecker

from tests.stage12.conftest import (
    order_factory as order_factory,
    order_variants as order_variants,
    other_customer as other_customer,
    prepare_order_cart as prepare_order_cart,
)


@pytest.fixture(scope="session")
def openapi():
    # No DB fixture: schema introspection must also work without database access.
    return SchemaGenerator().get_schema(request=None, public=True)


@pytest.fixture(scope="session")
def operations(openapi):
    return {
        operation["operationId"]: operation
        for item in openapi["paths"].values()
        for operation in item.values()
    }


def resolve(document, value):
    if "$ref" in value:
        return resolve(
            document, document["components"]["schemas"][value["$ref"].split("/")[-1]]
        )
    if "allOf" in value:
        result = {}
        for part in value["allOf"]:
            result.update(resolve(document, part))
        result.update({key: item for key, item in value.items() if key != "allOf"})
        return result
    return value


def request_schema(document, operation, media="application/json"):
    return resolve(document, operation["requestBody"]["content"][media]["schema"])


def response_schema(document, operation, status="200"):
    return resolve(
        document,
        operation["responses"][status]["content"]["application/json"]["schema"],
    )


def json_schema(value):
    """Translate OpenAPI 3.0 nullable for the already installed JSON Schema validator."""
    if isinstance(value, list):
        return [json_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {
        key: json_schema(item) for key, item in value.items() if key != "nullable"
    }
    return {"anyOf": [result, {"type": "null"}]} if value.get("nullable") else result


@pytest.fixture
def check_response(openapi, operations):
    def check(operation_id, response, status):
        assert response.status_code == status, response.content
        operation = operations[operation_id]
        documented = operation["responses"][str(status)]
        assert "X-Request-ID" in documented["headers"]
        FormatChecker().check(response["X-Request-ID"], "uuid")
        if status == 204:
            assert "content" not in documented
            assert response.content == b""
            return None
        body = response.json()
        schema = {
            **documented["content"]["application/json"]["schema"],
            "components": openapi["components"],
        }
        Draft4Validator(json_schema(schema), format_checker=FormatChecker()).validate(
            body
        )
        if status >= 400:
            assert body["error"]["request_id"] == response["X-Request-ID"]
        return body

    return check
