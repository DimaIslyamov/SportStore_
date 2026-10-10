# SportStore API

Backend дипломного интернет-магазина спортивных товаров.

## Первый запуск

1. Создать `.env` на основе `.env.example` и указать локальный секретный ключ.
2. Убедиться, что в исходниках задан `AUTH_USER_MODEL = "accounts.User"`.
3. Запустить PostgreSQL и собрать Django image:

```bash
docker compose up -d --wait postgres
docker compose build django
```

4. Если `accounts/migrations/0001_initial.py` ещё отсутствует, создать его:

```bash
docker compose run --rm django python manage.py makemigrations accounts
```

5. Применить миграции и запустить Django:

```bash
docker compose run --rm django python manage.py migrate
docker compose up -d --wait django
```

## Проверка

```bash
curl -i http://127.0.0.1:8000/api/v1/health/
docker compose exec django python manage.py check
docker compose exec django python -m pytest -q
```

Health: `http://127.0.0.1:8000/api/v1/health/`.
Admin: `http://127.0.0.1:8000/admin/`.

## Остановка

```bash
docker compose down
```