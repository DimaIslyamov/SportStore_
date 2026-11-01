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

## Stage 9: ORM-сценарий и тесты

Сценарий `scripts/stage9_orm_scenario.py` запускается вручную после применения
миграций в dev-базе. Он запросит ID существующего активного Customer, принадлежащего
ему Address и двух разных активных Variant с ценами **19.90** и **12.50 PLN**
соответственно. Их Product, Category и Brand также должны быть активны.

```bash
.venv/bin/python manage.py shell -c 'from scripts.stage9_orm_scenario import run; run()'
```

Сценарий сохраняет один демонстрационный заказ с двумя позициями, subtotal=77.30,
discount=7.30, shipping_cost=10.00 и total=80.00 PLN. Затем меняет источники,
проверяет сохранность снимков и откатывает изменения источников. Сам заказ остаётся
в dev-базе; каждый повторный запуск создаёт новый. Корзина и stock не изменяются;
checkout здесь не выполняется.

Автоматические тесты Stage 9 используют отдельную тестовую PostgreSQL-базу:

```bash
.venv/bin/python -m pytest -q tests/stage9
```

## Остановка

```bash
docker compose down
```
