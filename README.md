# SportStore API

## Project Overview

SportStore is a diploma backend project for a sporting goods store, built as a
modular monolith with Django REST Framework and PostgreSQL. Customers can browse
products, manage a cart, place orders, simulate payments, and track their orders.
Managers maintain the shared catalog, stock levels, and order fulfillment.

The main workflow is registration → catalog → cart → checkout → fake payment →
order fulfillment. All prices and totals are calculated by the backend in PLN;
monetary values are returned as decimal strings.

## Technology Stack

| Area | Technologies |
| --- | --- |
| Language & Framework | Python 3.13 in Docker, Django 5.2 |
| API & Authentication | Django REST Framework, SimpleJWT |
| Database | PostgreSQL 17, psycopg |
| Infrastructure | Docker, Docker Compose; Django development server |
| File Storage | Amazon S3 via boto3; Pillow for image validation |
| API Documentation | drf-spectacular, OpenAPI 3.0.3, Swagger UI |
| Testing & Code Quality | pytest, pytest-django, Ruff |

Dependency constraints are defined in [requirements.txt](requirements.txt) and
[requirements-dev.txt](requirements-dev.txt).

## Features

### Accounts & Authentication

- Custom user model with email-based login and CUSTOMER / MANAGER roles.
- JWT access tokens, rotating refresh tokens, and refresh-token blacklisting on
  logout.
- Customer registration, profile updates, and owner-scoped addresses with a
  default-address option. Public registration always creates a CUSTOMER.

### Product Catalog

- Hierarchical categories, brands, products, variants, and product/variant attributes.
- Public catalog with category, brand, price, and stock filters; text search;
  ordering by name or price; pagination with 12 products per page.
- Manager operations for products, variants, inventory, and product images.
- Variant-level prices and stock quantities; validated multipart image uploads to
  S3, metadata updates, and primary-image selection.

### Shopping Cart

- Customer-owned cart with item addition, quantity updates, and removal.
- Adding the same variant increases the existing item's quantity.
- Server-calculated line totals and subtotal. Cart contents do not reserve stock;
  availability is checked again at checkout.

### Orders & Checkout

- Atomic checkout with PostgreSQL row locks, stock validation, stock decrement,
  and cart clearing in one transaction.
- Historical item, price, currency, and shipping-address snapshots.
- Idempotent checkout with stored responses and conflicts for reused keys with
  different payloads.
- Role-aware order visibility and controlled status transitions from PENDING
  through PAID, PROCESSING, SHIPPED, and DELIVERED.
- Customer cancellation of PENDING or PAID orders with stock restored once.

### Payments

- A synchronous fake provider simulates successful and failed payment attempts;
  it does not process real monetary transactions.
- Payment idempotency, owner checks, and server-controlled amount/currency from
  the order.
- Successful payment moves the order to PAID. Manual status changes cannot mark
  an order paid without a matching successful payment.

### API Reliability

- A unified `error` envelope containing `code`, `message`, `details`, and `request_id`.
- A server-generated `X-Request-ID` on responses and structured request/business
  event logging.
- Role permissions, queryset-level ownership checks, and strict input validation
  for business payloads.
- Automated API, model, service, query-count, transaction, and PostgreSQL
  concurrency tests.

## Project Structure

```text
SportStore_/
├── accounts/             # Users, JWT, roles, addresses
├── catalog/              # Catalog, variants, inventory, images, S3 storage
├── cart/                 # Customer carts and item operations
├── orders/               # Checkout, snapshots, idempotency, order lifecycle
├── payments/             # Payment attempts and fake provider
├── core/                 # Health, error handling, request IDs, logging, schema helpers
├── config/               # Django settings and root URL configuration
├── tests/                # Automated tests organized by domain/stage
├── scripts/              # Additional development and learning scenarios
├── manage.py
├── compose.yaml
├── Dockerfile
├── Makefile
├── .env.example
├── requirements.txt
└── requirements-dev.txt
```

Views coordinate HTTP handling, serializers validate and represent data, services
implement business operations, and queryset helpers prepare optimized reads.
PostgreSQL constraints and transactions enforce data integrity.

## Getting Started

### 1. Clone the repository

Prerequisites: Git and Docker with Docker Compose v2.

```bash
git clone https://github.com/DimaIslyamov/SportStore_.git
cd SportStore_
cp .env.example .env
```

### 2. Configure the environment

Edit `.env` before starting the services:

| Variables | Configuration |
| --- | --- |
| `DJANGO_SECRET_KEY`, `JWT_SIGNING_KEY` | Replace the placeholders with two independent randomly generated keys |
| `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` | Set the development database name, user, and a URL-safe password |
| `DATABASE_URL` | For host-based Python commands, match the database credentials and `POSTGRES_HOST_PORT` |
| `DJANGO_ALLOWED_HOSTS` | Keep the supplied localhost values for the default local setup |
| `DJANGO_HOST_PORT`, `POSTGRES_HOST_PORT` | Default host ports: 8000 for Django, 5433 for PostgreSQL |

Compose overrides `DATABASE_URL` inside the Django container to connect to
`postgres:5432`. The example host connection uses `127.0.0.1:5433`. Keep `.env`
private; it is excluded from Git.

**Image storage:** S3 is optional for starting the API and using the core shopping
workflow. Uploading or replacing images requires `CATALOG_S3_BUCKET`,
`CATALOG_S3_REGION`, and valid credentials resolved through Boto3's standard
credential provider chain. For environment-based credentials, set
`AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY`; temporary credentials also require
`AWS_SESSION_TOKEN`. The backend needs `s3:PutObject` and `s3:DeleteObject` access
to the `sportstore/catalog/` prefix. Compose does not provision a bucket.
Unavailable or unconfigured storage can return HTTP 503 for uploads.

For uploaded images, the backend stores an unsigned regional S3 object URL in
`image_url`, which the client fetches directly from S3. These URLs have no
application-set expiry; access depends on the object existing and S3 read
permissions. The application does not generate presigned URLs or proxy private
images, and a SportStore JWT does not authorize S3 downloads.

The Stage 7 delivery model assumes public read access to the dedicated catalog
media prefix. Bucket policies and Block Public Access settings are managed
outside the application; the repository cannot confirm a deployed bucket's
privacy. A private object will not load anonymously through its unsigned URL.
Preserve existing AWS access controls and use an approved image-delivery
configuration.

### 3. Start PostgreSQL and build Django

```bash
docker compose up -d --wait postgres
docker compose build django
```

### 4. Apply the existing migrations and start the API

```bash
docker compose run --rm django python manage.py migrate
docker compose up -d --wait django
```

Migrations are included in the repository. A first-time setup uses `migrate`;
generating initial migrations is not part of installation.

### 5. Verify the application

```bash
docker compose ps
curl -i http://127.0.0.1:8000/api/v1/health/
docker compose exec django python manage.py check
```

The health endpoint should return HTTP 200 with
`{"status":"ok","database":"ok"}`. The database starts without demo users or
products. Register a customer through Swagger. For manager operations, register a
separate account and promote that existing active user:

```bash
docker compose exec django python manage.py promote_manager manager@example.com
```

Replace the example email with the account you registered. Managers can then
create catalog entries and set stock through the API. Django Admin is available
at [http://127.0.0.1:8000/admin/](http://127.0.0.1:8000/admin/); create a technical
administrator with `docker compose exec django python manage.py createsuperuser`
if needed.

Use `docker compose logs -f django` to inspect application logs. Stop the services
with `docker compose down`; the PostgreSQL named volume is retained.

## API Documentation

- [Swagger UI](http://127.0.0.1:8000/api/docs/) — interactive API documentation.
- [OpenAPI Schema](http://127.0.0.1:8000/api/schema/) — OpenAPI 3.0.3, YAML by
  default; JSON via `Accept: application/vnd.oai.openapi+json`.
- Business API prefix: `/api/v1/`.

These URLs use the default `DJANGO_HOST_PORT=8000`; adjust the port if configured
differently. Swagger UI loads its assets from a CDN.

### Authenticate in Swagger

1. Create a customer with `POST /api/v1/auth/register/`, including `email`,
   `password`, and `password_confirm`.
2. Submit email/password to `POST /api/v1/auth/token/` to obtain `access` and
   `refresh` tokens.
3. Click **Authorize** for `jwtAuth` and paste the **access token only**. Swagger
   adds the `Bearer` prefix to the Authorization header.
4. Use `POST /api/v1/auth/token/refresh/` with the refresh token to obtain a new
   pair. Refresh tokens rotate; save the new pair.

Catalog GET endpoints allow anonymous access. Address, cart, checkout, cancel,
and payment operations require CUSTOMER; catalog writes and order status updates
require MANAGER or a superuser. Customers see their own orders; managers can read
all orders. JWT authentication and role/ownership checks are separate concerns.

Representative endpoints:

| Method | Endpoint | Purpose |
| --- | --- | --- |
| POST | `/api/v1/auth/register/` | Register a customer |
| POST | `/api/v1/auth/token/` | Obtain JWT tokens |
| GET | `/api/v1/products/` | Browse, filter, search, and sort products |
| GET | `/api/v1/products/{slug}/` | Read product details and variants |
| GET | `/api/v1/cart/` | Read the customer's cart |
| POST | `/api/v1/cart/items/` | Add a variant with a quantity |
| POST | `/api/v1/orders/checkout/` | Create an order using an owned `address_id` |
| GET | `/api/v1/orders/{id}/` | Read order snapshots and current status |
| POST | `/api/v1/orders/{id}/payments/` | Simulate a payment attempt |

Checkout and payment require an `Idempotency-Key` UUID header, for example
`Idempotency-Key: 7df51125-94b2-4cb8-918b-580220ab413e`. Reuse the same key and
payload when retrying the same operation; generate a **new UUID for every new
attempt**. Checkout replay returns the original HTTP 201 and saved JSON, while
payment replay returns HTTP 200 with the existing payment. Each HTTP request gets
a new Request ID. Payment accepts `outcome: "success"` or `"failed"`, defaulting
to success; the order supplies the amount and currency.

## Testing & Code Quality

With the Docker services running, execute the checks from the repository root:

```bash
# Django checks and migration drift check
docker compose exec django python manage.py check
docker compose exec django python manage.py makemigrations --check --dry-run

# Full pytest suite
docker compose exec django python -m pytest -q

# Lint and formatting checks
docker compose exec django python -m ruff check .
docker compose exec django python -m ruff format --check .

# Dependency consistency
docker compose exec django python -m pip check

# Generate and validate OpenAPI; fail on generation warnings
docker compose exec django python manage.py spectacular \
  --file /tmp/sportstore-openapi.yaml --validate --fail-on-warn
```

The generated schema file is inside the container. Tests use a separate
PostgreSQL test database; the configured database user must be able to create it.
The suite covers business workflows, permissions, ownership, snapshots,
transaction rollback, idempotency, query counts, and concurrent operations on
real PostgreSQL. Image tests use fake storage instead of live AWS calls.

With Make installed, `make docker-check` and `make docker-test` are shortcuts for
the corresponding checks and test command above.

For host-based development, install Python 3.13 and Make, keep PostgreSQL running, and
configure the host `DATABASE_URL` in `.env`:

```bash
python3.13 -m venv .venv
make install
make check
make test
make lint
```

The Makefile uses `.venv/bin/python` by default. `make check` runs Django system
checks and `makemigrations --check --dry-run`; `make test` runs pytest;
`make lint` runs Ruff lint and formatting checks.

## Project Status & Scope

The current implementation provides a functional Diploma MVP backend shopping
workflow with API documentation completed through Stage 20. Later core milestones
cover the full-project quality gate, security review, and Docker MVP validation;
completion of Stage 20 does not mark the entire diploma project complete.

The current scope excludes Redis/Celery, background jobs, caching, reviews,
wishlists, promotions, real payment integrations, webhooks, refunds, CI/CD, and
deployment. The included Compose stack is for local development.
