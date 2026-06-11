from rest_framework import status, viewsets, generics
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework_simplejwt.tokens import RefreshToken
from django.contrib.auth.models import User
from django.utils import timezone
from django.db.models import Q
from django.contrib.auth.tokens import default_token_generator
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from django.utils.encoding import force_bytes, force_str
from .models import PermissionAtom, Profile, ProfilePermission, UserProfileAssignment, PermissionAuditLog
from .serializers import (
    UserSerializer, RegisterSerializer, PermissionAtomSerializer, 
    ProfileSerializer, UserProfileAssignmentSerializer, PermissionAuditLogSerializer
)
from .permissions import HasDynamicPermission
from .tasks import send_password_reset_email

class RegisterView(generics.CreateAPIView):
    """
    API para registrar nuevos usuarios.
    """
    serializer_class = RegisterSerializer
    permission_classes = [AllowAny]

    def perform_create(self, serializer):
        user = serializer.save()
        from django.contrib.auth.tokens import default_token_generator
        from django.utils.http import urlsafe_base64_encode
        from django.utils.encoding import force_bytes
        from .tasks import send_verification_email
        token = default_token_generator.make_token(user)
        uidb64 = urlsafe_base64_encode(force_bytes(user.pk))
        send_verification_email.delay(user.id, token, uidb64)


class GoogleOAuthView(APIView):
    """
    Simulación o validación de Google OAuth 2.0.
    Recibe un token de Google, verifica el correo y retorna tokens JWT del sistema.
    """
    permission_classes = [AllowAny]

    def post(self, request):
        email = request.data.get('email')
        first_name = request.data.get('first_name', '')
        last_name = request.data.get('last_name', '')
        google_id = request.data.get('google_id') # ID de Google retornado por OAuth

        if not email:
            return Response({"error": "El email es requerido"}, status=status.HTTP_400_BAD_REQUEST)

        # En una implementación real, aquí validaríamos el token con Google API:
        # idinfo = id_token.verify_oauth2_token(token, requests.Request(), CLIENT_ID)
        # email = idinfo['email']

        # Generar un username único case-insensitive
        base_username = email.split('@')[0]
        username = base_username
        counter = 1
        while User.objects.filter(username__iexact=username).exclude(email__iexact=email).exists():
            username = f"{base_username}{counter}"
            counter += 1

        # Buscar o crear usuario
        user, created = User.objects.get_or_create(
            email=email,
            defaults={
                'username': username, # Nombre de usuario único
                'first_name': first_name,
                'last_name': last_name
            }
        )

        if created:
            # Dado que se autentica por Google, no tiene contraseña local
            user.set_unusable_password()
            user.save()
            # El post_save signal ya le habrá asignado el perfil base "Comprar en la tienda"

        # Generar tokens JWT del backend
        refresh = RefreshToken.for_user(user)
        
        return Response({
            'access': str(refresh.access_token),
            'refresh': str(refresh),
            'user': UserSerializer(user).data,
            'created': created
        }, status=status.HTTP_200_OK)


class UserProfileView(APIView):
    """
    Retorna los detalles del usuario autenticado y sus permisos activos.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        serializer = UserSerializer(request.user)
        data = serializer.data
        
        # Verificar si la clave de Google API está configurada en settings o el entorno
        from django.conf import settings
        import os
        api_key = getattr(settings, "GOOGLE_API_KEY", "") or os.environ.get("GOOGLE_API_KEY", "")
        data["google_api_key_configured"] = bool(api_key)
        
        return Response(data)


class AdminUserListView(generics.ListCreateAPIView):
    """
    ABM Usuarios: Listado y creación de usuarios del sistema.
    Requiere permiso 'admin.gestionar_usuarios'.
    """
    queryset = User.objects.all().order_by('id')
    permission_classes = [HasDynamicPermission]
    required_permission = 'admin.gestionar_usuarios'
    required_scope = 'todos'

    def get_serializer_class(self):
        if self.request.method == 'POST':
            return RegisterSerializer
        return UserSerializer

    def perform_create(self, serializer):
        user = serializer.save()
        # Asignar perfiles seleccionados en la creación (RF 6.5)
        profiles_data = self.request.data.get('profiles', [])
        for item in profiles_data:
            p_id = None
            expires_at = None
            if isinstance(item, dict):
                p_id = item.get('id')
                expires_at = item.get('expires_at')
            else:
                p_id = item
            
            if not p_id:
                continue

            try:
                profile = Profile.objects.get(id=p_id)
                assignment, created = UserProfileAssignment.objects.get_or_create(
                    user=user,
                    profile=profile,
                    defaults={'assigned_by': self.request.user}
                )
                if not created:
                    assignment.is_active = True
                if expires_at:
                    assignment.expires_at = expires_at
                assignment.save()
                
                # Registrar Auditoría
                PermissionAuditLog.objects.create(
                    user=user,
                    profile=profile,
                    action='assign',
                    performed_by=self.request.user,
                    notes=f"Perfil asignado durante la creación de usuario interno. Vence: {expires_at or 'Permanente'}"
                )
            except Profile.DoesNotExist:
                continue


class AdminUserProfilesView(APIView):
    """
    Administración de perfiles por usuario.
    Permite ver y tildar/destildar perfiles desde el panel visual, registrando auditoría.
    Requiere permiso 'admin.asignar_perfiles'.
    """
    permission_classes = [HasDynamicPermission]
    required_permission = 'admin.asignar_perfiles'

    def get(self, request, user_id):
        try:
            user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            return Response({"error": "Usuario no encontrado"}, status=status.HTTP_404_NOT_FOUND)

        assignments = UserProfileAssignment.objects.filter(user=user)
        serializer = UserProfileAssignmentSerializer(assignments, many=True)
        return Response(serializer.data)

    def post(self, request, user_id):
        try:
            user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            return Response({"error": "Usuario no encontrado"}, status=status.HTTP_444_NOT_FOUND)

        profile_id = request.data.get('profile')
        action = request.data.get('action') # 'assign' o 'revoke'
        expires_at = request.data.get('expires_at') # Opcional: formato ISO date-time

        try:
            profile = Profile.objects.get(id=profile_id)
        except Profile.DoesNotExist:
            return Response({"error": "Perfil no encontrado"}, status=status.HTTP_404_NOT_FOUND)

        if action == 'assign':
            assignment, created = UserProfileAssignment.objects.get_or_create(
                user=user,
                profile=profile,
                defaults={'assigned_by': request.user}
            )
            
            assignment.is_active = True
            if expires_at:
                assignment.expires_at = expires_at
            else:
                assignment.expires_at = None
            assignment.assigned_by = request.user
            assignment.save()

            # Registrar Auditoría
            PermissionAuditLog.objects.create(
                user=user,
                profile=profile,
                action='assign',
                performed_by=request.user,
                notes=f"Perfil asignado manualmente. Vence: {expires_at or 'Permanente'}"
            )
            return Response({"status": "Perfil asignado correctamente"})

        elif action == 'revoke':
            UserProfileAssignment.objects.filter(user=user, profile=profile).update(is_active=False)
            
            # Registrar Auditoría
            PermissionAuditLog.objects.create(
                user=user,
                profile=profile,
                action='revoke',
                performed_by=request.user,
                notes="Perfil revocado manualmente"
            )
            return Response({"status": "Perfil revocado correctamente"})

        return Response({"error": "Acción inválida. Use 'assign' o 'revoke'"}, status=status.HTTP_400_BAD_REQUEST)


class AdminProfileViewSet(viewsets.ModelViewSet):
    """
    ABM Perfiles: Creación y configuración dinámica de perfiles agrupando permisos (RF 1.2).
    Permite habilitar/deshabilitar permisos atómicos mediante checkboxes en tiempo real.
    Requiere permiso 'admin.configurar_perfiles'.
    """
    queryset = Profile.objects.all().order_by('name')
    serializer_class = ProfileSerializer
    permission_classes = [HasDynamicPermission]
    required_permission = 'admin.configurar_perfiles'
    required_scope = 'todos'

    def perform_create(self, serializer):
        profile = serializer.save()
        self._update_permissions(profile, self.request.data.get('permissions', []))

    def perform_update(self, serializer):
        profile = serializer.save()
        self._update_permissions(profile, self.request.data.get('permissions', []))

    def update(self, request, *args, **kwargs):
        profile = self.get_object()
        # RN 3: El perfil "Administrador del Sistema" es inmutable
        if profile.name == "Administrador del Sistema":
            return Response(
                {"error": "El perfil 'Administrador del Sistema' es del sistema y no se puede editar."},
                status=status.HTTP_400_BAD_REQUEST
            )
        return super().update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        profile = self.get_object()
        # RN 3: El perfil "Administrador del Sistema" es inmutable
        if profile.name == "Administrador del Sistema":
            return Response(
                {"error": "El perfil 'Administrador del Sistema' es del sistema y no se puede eliminar."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # RN 1: No se permite eliminar perfil asignado activamente a usuarios
        now = timezone.now()
        active_assignments = UserProfileAssignment.objects.filter(
            profile=profile,
            is_active=True
        ).filter(
            Q(expires_at__isnull=True) | Q(expires_at__gt=now)
        )
        if active_assignments.exists():
            return Response(
                {"error": "No se puede eliminar el perfil porque está asignado a usuarios activos."},
                status=status.HTTP_400_BAD_REQUEST
            )

        return super().destroy(request, *args, **kwargs)

    def _update_permissions(self, profile, permissions_data):
        # Limpiar permisos previos del perfil
        ProfilePermission.objects.filter(profile=profile).delete()

        # Configurar los nuevos permisos recibidos (agrupando checkboxes con su respectivo scope)
        for perm_data in permissions_data:
            # perm_data = {"permission_id": 1, "scope": "propios"/"todos"}
            perm_id = perm_data.get('permission_id')
            scope = perm_data.get('scope', 'propios')
            try:
                perm_atom = PermissionAtom.objects.get(id=perm_id)
                ProfilePermission.objects.create(
                    profile=profile,
                    permission=perm_atom,
                    scope=scope
                )
            except PermissionAtom.DoesNotExist:
                continue


class PermissionAtomListView(generics.ListAPIView):
    """
    Lista de permisos atómicos disponibles en el sistema.
    """
    queryset = PermissionAtom.objects.all().order_by('module', 'code')
    serializer_class = PermissionAtomSerializer
    permission_classes = [IsAuthenticated]


class PermissionAuditLogListView(generics.ListAPIView):
    """
    Ver el log de auditoría (RF 6.3). Requiere permisos de analista/administrador.
    """
    queryset = PermissionAuditLog.objects.all().order_by('-timestamp')
    serializer_class = PermissionAuditLogSerializer
    permission_classes = [HasDynamicPermission]
    required_permission = 'admin.ver_auditoria'
    required_scope = 'todos'


class PasswordResetRequestView(APIView):
    """
    Solicitar enlace de recuperación de contraseña.
    Genera un token de un solo uso de 15 minutos y envía un correo mediante Celery.
    """
    permission_classes = [AllowAny]

    def post(self, request):
        email = request.data.get('email')
        if not email:
            return Response({"error": "El email es requerido"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            user = User.objects.get(email=email)
            # Si el usuario no tiene contraseña (registrado por Google OAuth), rechazamos
            if not user.has_usable_password():
                return Response(
                    {"error": "Esta cuenta está registrada a través de Google. Inicia sesión directamente usando Google."},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Generar token y uid
            token = default_token_generator.make_token(user)
            uidb64 = urlsafe_base64_encode(force_bytes(user.pk))

            # Disparar tarea asíncrona de Celery
            send_password_reset_email.delay(user.id, token, uidb64)

        except User.DoesNotExist:
            # Por seguridad, no informamos si el correo no existe en la base de datos (evita enumeración de usuarios)
            pass

        return Response({"status": "Se ha enviado un enlace a tu correo en caso de estar registrado."}, status=status.HTTP_200_OK)


class PasswordResetConfirmView(APIView):
    """
    Restablecer contraseña utilizando un token válido.
    """
    permission_classes = [AllowAny]

    def post(self, request):
        token = request.data.get('token')
        uidb64 = request.data.get('uid')
        new_password = request.data.get('new_password')

        if not token or not uidb64 or not new_password:
            return Response({"error": "Token, uid y nueva contraseña son obligatorios."}, status=status.HTTP_400_BAD_REQUEST)

        if len(new_password) < 8:
            return Response({"error": "La contraseña debe tener al menos 8 caracteres."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            uid = force_str(urlsafe_base64_decode(uidb64))
            user = User.objects.get(pk=uid)
        except (TypeError, ValueError, OverflowError, User.DoesNotExist):
            return Response({"error": "Enlace de recuperación inválido o vencido."}, status=status.HTTP_400_BAD_REQUEST)

        # Verificar si el token es válido
        if not default_token_generator.check_token(user, token):
            return Response({"error": "El enlace de recuperación es inválido o ha expirado (límite 15 minutos)."}, status=status.HTTP_400_BAD_REQUEST)

        # Establecer la nueva contraseña
        user.set_password(new_password)
        user.save()
        
        from rest_framework_simplejwt.token_blacklist.models import OutstandingToken, BlacklistedToken
        for tk in OutstandingToken.objects.filter(user=user):
            BlacklistedToken.objects.get_or_create(token=tk)

        # Loguear la acción en la auditoría
        try:
            base_profile = Profile.objects.get(name="Comprar en la tienda")
        except Profile.DoesNotExist:
            base_profile = Profile.objects.first()

        PermissionAuditLog.objects.create(
            user=user,
            profile=base_profile,
            action='assign',
            performed_by=None,
            notes="Cambio de contraseña exitoso mediante enlace de recuperación de email."
        )

        return Response({"status": "Contraseña restablecida con éxito. Ya puedes iniciar sesión."}, status=status.HTTP_200_OK)



class VerifyEmailView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        token = request.data.get('token')
        uidb64 = request.data.get('uid')
        if not token or not uidb64:
            return Response({"error": "Faltan parámetros."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            uid = force_str(urlsafe_base64_decode(uidb64))
            user = User.objects.get(pk=uid)
        except (TypeError, ValueError, OverflowError, User.DoesNotExist):
            return Response({"error": "Enlace inválido."}, status=status.HTTP_400_BAD_REQUEST)
        if not default_token_generator.check_token(user, token):
            return Response({"error": "El enlace ha expirado o es inválido."}, status=status.HTTP_400_BAD_REQUEST)
        user.is_active = True
        user.save()
        return Response({"status": "Cuenta activada con éxito."}, status=status.HTTP_200_OK)

class CheckUsernameView(APIView):
    permission_classes = [AllowAny]
    def get(self, request):
        username = request.query_params.get('username', '').strip()
        if not username:
            return Response({'error': 'Se requiere el parámetro username'}, status=status.HTTP_400_BAD_REQUEST)
        exists = User.objects.filter(username__iexact=username).exists()
        return Response({'available': not exists}, status=status.HTTP_200_OK)

class CheckEmailView(APIView):
    permission_classes = [AllowAny]
    def get(self, request):
        email = request.query_params.get('email', '').strip()
        if not email:
            return Response({'error': 'Se requiere el parámetro email'}, status=status.HTTP_400_BAD_REQUEST)
        exists = User.objects.filter(email__iexact=email).exists()
        return Response({'available': not exists}, status=status.HTTP_200_OK)
