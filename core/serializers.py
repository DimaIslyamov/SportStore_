from typing import Mapping

from rest_framework import serializers


class StrictInputMixin:
    """
    Добавляет строгую проверку входящих полей сериализатора DRF.

    По умолчанию DRF игнорирует неизвестные поля и поля с read_only=True.
    Этот Mixin запрещает передавать такие поля и возвращает ValidationError.

    Используется совместно с Serializer или ModelSerializer.
    Должен указываться перед ними в списке наследования.
    """

    def to_internal_value(self, data):
        """
        Проверяет входящие данные перед стандартной валидацией DRF.
        """
        if isinstance(data, Mapping):
            writable = {
                name for name, field in self.fields.items() if not field.read_only
            }

            forbidden = set(data) - writable

            if forbidden:
                raise serializers.ValidationError(
                    {
                        name: ["This field cannot be submitted."]
                        for name in sorted(forbidden)
                    }
                )

        return super().to_internal_value(data)
