"""
Utilidades para la sesión única: canal Redis pub/sub y publicación de revocación.
"""
import logging
import redis
from django.conf import settings

logger = logging.getLogger(__name__)


def channel_for(user_id):
    return f"session_revoke:{user_id}"


def get_redis():
    return redis.from_url(settings.REDIS_URL)


def publish_session_revoke(user_id, new_sid):
    """
    Avisa a las conexiones SSE del usuario que la sesión vigente cambió.
    Las conexiones cuyo 'sid' no sea new_sid deben cerrarse. No rompe el login
    si Redis no está disponible (el fallback por 401 sigue funcionando).
    """
    try:
        get_redis().publish(channel_for(user_id), new_sid)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"No se pudo publicar revocación de sesión para user {user_id}: {exc}")
