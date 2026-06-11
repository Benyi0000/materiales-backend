from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from .views import (
    RegisterView, UserProfileView, GoogleOAuthView,
    AdminUserListView, AdminUserProfilesView, AdminProfileViewSet,
    PermissionAtomListView, PermissionAuditLogListView,
    PasswordResetRequestView, PasswordResetConfirmView, VerifyEmailView
)

router = DefaultRouter()
router.register(r'admin/profiles', AdminProfileViewSet, basename='admin-profiles')

urlpatterns = [
    # Autenticación estándar
    path('auth/register/', RegisterView.as_view(), name='auth-register'),
    path('auth/login/', TokenObtainPairView.as_view(), name='auth-login'),
    path('auth/token/refresh/', TokenRefreshView.as_view(), name='auth-token-refresh'),
    path('auth/verify-email/', VerifyEmailView.as_view(), name='auth-verify-email'),
    path('auth/password-reset/', PasswordResetRequestView.as_view(), name='password-reset-request'),
    path('auth/password-reset-confirm/', PasswordResetConfirmView.as_view(), name='password-reset-confirm'),
    
    # Autenticación Google OAuth 2.0
    path('auth/google/', GoogleOAuthView.as_view(), name='auth-google'),
    
    # Perfil del usuario actual
    path('auth/profile/', UserProfileView.as_view(), name='auth-profile'),
    
    # ABM Usuarios e asignación de roles
    path('admin/users/', AdminUserListView.as_view(), name='admin-users-list'),
    path('admin/users/<int:user_id>/profiles/', AdminUserProfilesView.as_view(), name='admin-user-profiles'),
    
    # Permisos atómicos y logs de auditoría
    path('admin/permissions/', PermissionAtomListView.as_view(), name='admin-permissions'),
    path('admin/audit-logs/', PermissionAuditLogListView.as_view(), name='admin-audit-logs'),
    
    # Incluir rutas del router para perfiles dinámicos
    path('', include(router.urls)),
]
