"""
Autenticación JWT consciente de la sesión única.
"""
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import AuthenticationFailed


class SessionAwareJWTAuthentication(JWTAuthentication):
    """
    Además de validar el JWT, exige que el claim 'sid' del token coincida con la
    sesión vigente del usuario (ActiveSession). Si no coincide, la sesión fue
    revocada por un login más reciente -> 401.

    Para honrar RN5 (no cerrar sesiones previas al despliegue), solo se exige
    cuando el token trae 'sid' Y el usuario tiene una ActiveSession: los tokens
    legacy (sin 'sid') siguen funcionando hasta su próximo login.
    """

    def get_user(self, validated_token):
        user = super().get_user(validated_token)

        sid = validated_token.get('sid')
        active = getattr(user, 'active_session', None)

        if sid is not None and active is not None and sid != active.session_key:
            raise AuthenticationFailed(
                detail='La sesión fue cerrada porque iniciaste sesión en otro dispositivo.',
                code='session_revoked',
            )

        return user
