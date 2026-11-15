import copy
import json
import re

import pytest
import yaml
from django.contrib.auth.models import AnonymousUser
from django.core.management import call_command
from django.urls import URLResolver, get_resolver, reverse
from drf_spectacular.generators import SchemaGenerator
from drf_spectacular.validation import validate_schema

from accounts.views import AddressViewSet
from cart.views import CartItemDetailView
from core.schema import require_command_fields
from orders.views import OrderDetailView, OrderListView

from tests.stage20.conftest import request_schema, resolve, response_schema


def business_routes(patterns, prefix=""):
    """Read Django routes independently of the schema generator's endpoint discovery."""
    routes = {}
    for entry in patterns:
        route = prefix + str(entry.pattern).removeprefix("^").removesuffix("$")
        if isinstance(entry, URLResolver):
            if not route.startswith("admin/"):
                routes.update(business_routes(entry.url_patterns, route))
            continue
        if not route.startswith("api/v1/"):
            continue
        route = re.sub(r"<\w+:(\w+)>", r"{\1}", route)
        route = re.sub(r"\(\?P<(\w+)>[^)]+\)", r"{\1}", route)
        route = route.replace("{pk}", "{id}")
        view = entry.callback.cls
        actions = getattr(entry.callback, "actions", None)
        routes["/" + route] = {
            method
            for method in view.http_method_names
            if method not in {"head", "options"}
            and (method in actions if actions is not None else hasattr(view, method))
        }
    return routes


def test_schema_matches_every_real_business_route(openapi):
    actual = business_routes(get_resolver().url_patterns)
    assert len(actual) == 28
    assert sum(map(len, actual.values())) == 41
    assert {path: set(item) for path, item in openapi["paths"].items()} == actual
    ids = [
        op["operationId"] for item in openapi["paths"].values() for op in item.values()
    ]
    assert len(ids) == len(set(ids)) == 41
    assert not any("put" in methods for methods in actual.values())
    assert not any(
        word in path
        for path in openapi["paths"]
        for word in ("reviews", "wishlist", "promotions", "webhook", "refund")
    )
    assert openapi["openapi"] == "3.0.3"
    validate_schema(openapi)


@pytest.mark.parametrize("output_format", ["openapi", "openapi-json"])
def test_cli_validates_without_warnings(output_format, tmp_path, capsys, openapi):
    output = tmp_path / "schema"
    call_command(
        "spectacular",
        file=str(output),
        format=output_format,
        validate=True,
        fail_on_warn=True,
    )
    document = yaml.safe_load(output.read_text())
    assert document == openapi
    assert capsys.readouterr().err == ""


def test_anonymous_http_schema_and_swagger(api_client, openapi, settings):
    yaml_response = api_client.get(reverse("schema"))
    assert yaml_response.status_code == 200
    assert yaml.safe_load(yaml_response.content) == openapi
    json_response = api_client.get(
        reverse("schema"), HTTP_ACCEPT="application/vnd.oai.openapi+json"
    )
    assert json_response.status_code == 200
    assert json.loads(json_response.content) == openapi
    validate_schema(json.loads(json_response.content))
    docs = api_client.get(reverse("swagger-ui"))
    assert docs.status_code == 200
    assert docs["Content-Type"].startswith("text/html")
    assert re.search(r'\burl:\s*"/api/schema/"', docs.content.decode())
    assert "SwaggerUIBundle" in docs.content.decode()
    assert settings.REST_FRAMEWORK["DEFAULT_RENDERER_CLASSES"] == [
        "rest_framework.renderers.JSONRenderer"
    ]


@pytest.mark.parametrize(
    "view_class", [AddressViewSet, CartItemDetailView, OrderListView, OrderDetailView]
)
def test_schema_guards_allow_anonymous_introspection_without_db(view_class):
    view = view_class()
    view.swagger_fake_view = True
    view.request = type("Request", (), {"user": AnonymousUser()})()
    assert not view.get_queryset().exists()


def test_security_matches_public_and_protected_operations(openapi, operations):
    assert openapi["components"]["securitySchemes"] == {
        "jwtAuth": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}
    }
    for name, operation in operations.items():
        security = operation.get("security", [])
        if name in {
            "auth_register",
            "auth_token_create",
            "auth_token_refresh",
            "health_retrieve",
        }:
            assert not any("jwtAuth" in item for item in security)
        elif name in {
            "products_list",
            "products_retrieve",
            "categories_list",
            "categories_retrieve",
            "brands_list",
            "brands_retrieve",
        }:
            assert {"jwtAuth": []} in security and {} in security
        else:
            assert security == [{"jwtAuth": []}], name


def test_query_path_and_idempotency_parameters(openapi, operations):
    query = {
        p["name"]: p
        for p in operations["products_list"]["parameters"]
        if p["in"] == "query"
    }
    assert set(query) == {
        "category",
        "brand",
        "min_price",
        "max_price",
        "in_stock",
        "search",
        "ordering",
        "page",
    }
    assert query["ordering"]["schema"]["enum"] == ["name", "price", "-price"]
    assert query["ordering"]["schema"]["default"] == "name"
    assert query["in_stock"]["schema"]["type"] == "boolean"
    for path, item in openapi["paths"].items():
        for operation in item.values():
            name = operation["operationId"]
            parameters = operation.get("parameters", [])
            assert len({(p["name"], p["in"]) for p in parameters}) == len(parameters)
            if name != "products_list":
                assert not any(p["in"] == "query" for p in parameters), name
            assert {p["name"] for p in parameters if p["in"] == "path"} == set(
                re.findall(r"{(\w+)}", path)
            )
            for parameter in parameters:
                if parameter["in"] == "path":
                    assert parameter["required"] is True
                    if parameter["name"] not in {"slug", "lookup"}:
                        assert parameter["schema"]["type"] == "integer"
            headers = [p for p in parameters if p["in"] == "header"]
            if name in {"orders_checkout", "payments_create"}:
                assert len(headers) == 1
                assert headers[0]["name"] == "Idempotency-Key"
                assert headers[0]["required"] is True
                assert headers[0]["schema"]["format"] == "uuid"
            else:
                assert not headers
    for entity in ("products", "categories", "brands"):
        get_parameter = operations[f"{entity}_retrieve"]["parameters"][0]
        patch_parameter = operations[f"{entity}_update"]["parameters"][0]
        assert (
            get_parameter["schema"]["type"]
            == patch_parameter["schema"]["type"]
            == "string"
        )
        assert patch_parameter["schema"]["pattern"] == "^[0-9]+$"


def test_request_response_separation_and_pagination(openapi, operations):
    register = request_schema(openapi, operations["auth_register"])
    assert set(register["required"]) == {"email", "password", "password_confirm"}
    assert register["properties"]["password"]["writeOnly"] is True
    assert "role" not in register["properties"]
    assert (
        "password"
        not in response_schema(openapi, operations["auth_register"], "201")[
            "properties"
        ]
    )
    for name in ("auth_token_create", "auth_token_refresh"):
        assert set(response_schema(openapi, operations[name])["properties"]) == {
            "access",
            "refresh",
        }
    assert set(
        request_schema(openapi, operations["users_me_update"])["properties"]
    ) == {"first_name", "last_name", "phone"}
    product_list = response_schema(openapi, operations["products_list"])
    assert set(product_list["properties"]) == {"count", "next", "previous", "results"}
    for name in ("addresses_list", "categories_list", "brands_list", "orders_list"):
        assert response_schema(openapi, operations[name])["type"] == "array"
    assert (
        response_schema(openapi, operations["products_create"], "201")["properties"][
            "category"
        ]["type"]
        == "integer"
    )
    assert (
        resolve(
            openapi,
            openapi["components"]["schemas"]["ProductList"]["properties"]["category"],
        )["type"]
        == "object"
    )
    for name, statuses in {
        "orders_checkout": {"201"},
        "payments_create": {"200", "201"},
        "cart_items_create": {"200", "201"},
    }.items():
        assert {
            s for s in operations[name]["responses"] if s.startswith("2")
        } == statuses
    for name in ("orders_cancel", "orders_status_update"):
        assert operations[name]["responses"]["200"]["content"]["application/json"][
            "schema"
        ]["$ref"].endswith("/OrderSummary")


@pytest.mark.parametrize(
    "name,field",
    [
        ("cart_items_update", "quantity"),
        ("inventory_update", "quantity"),
        ("orders_status_update", "status"),
    ],
)
def test_command_patch_body_and_field_are_required(openapi, operations, name, field):
    operation = operations[name]
    assert operation["requestBody"]["required"] is True
    for media in operation["requestBody"]["content"]:
        assert request_schema(openapi, operation, media)["required"] == [field]


def test_hook_only_changes_the_three_commands(settings):
    settings.SPECTACULAR_SETTINGS = {
        **settings.SPECTACULAR_SETTINGS,
        "POSTPROCESSING_HOOKS": ["drf_spectacular.hooks.postprocess_schema_enums"],
    }
    before = SchemaGenerator().get_schema(public=True)
    expected = copy.deepcopy(before)
    fields = {
        "cart_items_update": "quantity",
        "inventory_update": "quantity",
        "orders_status_update": "status",
    }
    for item in expected["paths"].values():
        operation = item.get("patch", {})
        if operation.get("operationId") in fields:
            operation["requestBody"]["required"] = True
            for media in operation["requestBody"]["content"]:
                request_schema(expected, operation, media)["required"] = [
                    fields[operation["operationId"]]
                ]
    assert require_command_fields(before, None, None, True) == expected
    assert expected["components"]["schemas"]["OrderStatusEnum"]["enum"] == [
        "PENDING",
        "PAID",
        "PROCESSING",
        "SHIPPED",
        "DELIVERED",
        "CANCELLED",
    ]
    assert expected["components"]["schemas"]["PaymentStatusEnum"]["enum"] == [
        "PENDING",
        "SUCCESS",
        "FAILED",
        "REFUNDED",
    ]


@pytest.mark.parametrize(
    "name",
    [
        "users_me_update",
        "addresses_update",
        "categories_update",
        "brands_update",
        "products_update",
        "variants_update",
        "images_update",
    ],
)
def test_normal_patch_keeps_all_fields_optional(openapi, operations, name):
    operation = operations[name]
    assert not operation["requestBody"].get("required", False)
    for media in operation["requestBody"]["content"]:
        assert not request_schema(openapi, operation, media).get("required")


def test_computed_nullable_and_decimal_types(openapi):
    schemas = openapi["components"]["schemas"]
    for name in ("ProductList", "ProductDetail"):
        assert schemas[name]["properties"]["primary_image"]["nullable"] is True
        assert (
            resolve(openapi, schemas[name]["properties"]["primary_image"])["type"]
            == "object"
        )
    for field in ("id", "created_at", "updated_at"):
        assert schemas["CartOutput"]["properties"][field]["nullable"] is True
    assert schemas["VariantOutput"]["properties"]["barcode"]["nullable"] is True
    assert schemas["Category"]["properties"]["parent"]["nullable"] is True
    expected = {
        "CartOutput": {
            "items_count": "integer",
            "total_quantity": "integer",
            "subtotal": "string",
            "currency": "string",
        },
        "CartItemOutput": {
            "line_total": "string",
            "is_available": "boolean",
            "has_sufficient_stock": "boolean",
            "unit_price": "string",
        },
        "ProductList": {
            "in_stock": "boolean",
            "from_price": "string",
            "currency": "string",
        },
        "ProductVariant": {"in_stock": "boolean", "price": "string"},
        "VariantOutput": {"price": "string"},
        "CheckoutOrderOutput": dict.fromkeys(
            ("subtotal", "discount", "shipping_cost", "total"), "string"
        ),
        "CheckoutItemOutput": {"unit_price": "string", "total_price": "string"},
        "OrderSummary": {"total": "string"},
        "PaymentOutput": {"amount": "string"},
    }
    for name, fields in expected.items():
        for field, expected_type in fields.items():
            assert schemas[name]["properties"][field]["type"] == expected_type, (
                name,
                field,
            )


def test_images_only_offer_binary_file_in_multipart(openapi, operations):
    create = operations["images_create"]
    assert set(create["requestBody"]["content"]) == {"multipart/form-data"}
    upload = request_schema(openapi, create, "multipart/form-data")
    assert upload["properties"]["file"]["format"] == "binary"
    assert "file" in upload["required"]
    update = operations["images_update"]
    assert (
        request_schema(openapi, update, "multipart/form-data")["properties"]["file"][
            "format"
        ]
        == "binary"
    )
    for media in ("application/json", "application/x-www-form-urlencoded"):
        metadata = request_schema(openapi, update, media)
        assert set(metadata["properties"]) == {"alt_text", "position", "is_primary"}
        assert metadata["additionalProperties"] is False
    assert set(response_schema(openapi, create, "201")["properties"]) == {
        "id",
        "image_url",
        "alt_text",
        "position",
        "is_primary",
    }


def test_all_errors_and_response_headers_follow_stage16(openapi, operations):
    for operation in operations.values():
        for status, response in operation["responses"].items():
            assert response["headers"]["X-Request-ID"]["schema"]["format"] == "uuid"
            if status == "204":
                assert "content" not in response
            elif int(status) >= 400:
                assert response["content"]["application/json"]["schema"][
                    "$ref"
                ].endswith("/ApiError")
        assert "429" not in operation["responses"]
    error = openapi["components"]["schemas"]["ApiErrorDetail"]
    assert (
        set(error["properties"])
        == set(error["required"])
        == {"code", "message", "details", "request_id"}
    )
    assert error["properties"]["details"]["type"] == "object"
    assert error["properties"]["details"]["additionalProperties"] == {}
    assert error["properties"]["request_id"]["format"] == "uuid"
