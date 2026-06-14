from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone

class PermissionAtom(models.Model):
    """
    Representa un permiso individual del sistema.
    Ejemplo: 'gestion.gestionar_banners', 'gestion.gestionar_promociones'.
    """
    code = models.CharField(max_length=100, unique=True, help_text="Formato 'modulo.accion'")
    module = models.CharField(max_length=50, help_text="Módulo del sistema al que pertenece")
    description = models.CharField(max_length=255)
    scope_aplica = models.BooleanField(
        default=True,
        help_text=(
            "Si es False, el alcance (Propios/Todos) no tiene sentido para este permiso "
            "(recurso per-usuario o función global) y se fija en 'todos'. El panel oculta el selector."
        ),
    )

    def __str__(self):
        return f"{self.module} | {self.code}"


class Profile(models.Model):
    """
    Agrupación de permisos configurables por el administrador.
    Ejemplo: 'Pasante de Marketing', 'Comprar en la tienda', 'Tutor Visual IA'.
    """
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    permissions = models.ManyToManyField(
        PermissionAtom, 
        through='ProfilePermission', 
        related_name='profiles'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name


class ProfilePermission(models.Model):
    """
    Asociación de un permiso a un perfil, definiendo su alcance.
    """
    SCOPE_CHOICES = (
        ('propios', 'Propios (Solo datos del creador/usuario)'),
        ('todos', 'Todos (Todo el sistema)'),
    )
    profile = models.ForeignKey(Profile, on_delete=models.CASCADE)
    permission = models.ForeignKey(PermissionAtom, on_delete=models.CASCADE)
    scope = models.CharField(max_length=10, choices=SCOPE_CHOICES, default='propios')

    class Meta:
        unique_together = ('profile', 'permission')

    def __str__(self):
        return f"{self.profile.name} -> {self.permission.code} ({self.scope})"


class UserProfileAssignment(models.Model):
    """
    Asignación de perfiles a usuarios con fecha de vencimiento opcional.
    """
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='assignments')
    profile = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name='user_assignments')
    assigned_at = models.DateTimeField(auto_now_add=True)
    assigned_by = models.ForeignKey(
        User, 
        on_delete=models.SET_NULL, 
        null=True, 
        related_name='assignments_made'
    )
    expires_at = models.DateTimeField(
        null=True, 
        blank=True, 
        help_text="Fecha opcional en la que el perfil expira automáticamente"
    )
    is_active = models.BooleanField(default=True)

    @property
    def has_expired(self):
        if self.expires_at and timezone.now() > self.expires_at:
            return True
        return False

    def __str__(self):
        status = "Expirado" if self.has_expired else "Activo"
        expires = f" (Vence: {self.expires_at})" if self.expires_at else " (Permanente)"
        return f"{self.user.username} - {self.profile.name} [{status}]{expires}"


class PermissionAuditLog(models.Model):
    """
    Historial de auditoría para el ABM de perfiles de usuario.
    """
    ACTION_CHOICES = (
        ('assign', 'Asignación'),
        ('revoke', 'Revocación'),
        ('auto_expire', 'Expiración Automática'),
        ('subs_activate', 'Suscripción Premium Activada'),
        ('subs_deactivate', 'Suscripción Premium Vencida'),
    )
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='audit_logs')
    profile = models.ForeignKey(Profile, on_delete=models.CASCADE)
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    performed_by = models.ForeignKey(
        User, 
        on_delete=models.SET_NULL, 
        null=True, 
        related_name='audit_actions'
    )
    timestamp = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True)

    def __str__(self):
        actor = self.performed_by.username if self.performed_by else "Sistema"
        return f"{self.timestamp} - {actor} realizó {self.get_action_display()} de {self.profile.name} a {self.user.username}"


class ActiveSession(models.Model):
    """
    Sesión vigente por usuario (sesión única). Guarda el identificador de la
    sesión activa; cualquier token cuyo claim 'sid' no coincida está revocado.
    """
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='active_session')
    session_key = models.CharField(max_length=64)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Sesión activa de {self.user.username} ({self.session_key[:8]}…)"


class ForcePasswordChange(models.Model):
    """
    Indica que el usuario fue creado por un administrador y debe cambiar su contraseña al iniciar sesión por primera vez.
    """
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='force_password_change')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Debe cambiar contraseña: {self.user.username}"
