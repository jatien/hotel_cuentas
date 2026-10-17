"""Errores de servicio con código y estado HTTP, compartidos por la API y las pantallas."""


class ServiceError(Exception):
    status = 400

    def __init__(self, code: str, message: str, status: int | None = None, details=None):
        super().__init__(message)
        self.code, self.message, self.details = code, message, details
        if status is not None:
            self.status = status


class Conflict(ServiceError):
    status = 409


class NotFound(ServiceError):
    status = 404


class Forbidden(ServiceError):
    status = 403
