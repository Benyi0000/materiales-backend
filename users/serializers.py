from rest_framework import serializers
from django.contrib.auth.models import User
from .models import PermissionAtom, Profile, ProfilePermission, UserProfileAssignment, PermissionAuditLog
from .permissions import get_user_active_permissions

class RegisterSerializer(serializers.ModelSerializer):
    """
    Serializador para el registro de nuevos usuarios.
    """
    password = serializers.CharField(write_only=True, required=True, style={'input_type': 'password'})

    class Meta:
        model = User
        fields = ('username', 'email', 'password', 'first_name', 'last_name')

    def validate_username(self, value):
        if User.objects.filter(username__iexact=value).exists():
            raise serializers.ValidationError("Ya existe un usuario con este nombre.")
        return value

    def validate_email(self, value):
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("Este correo ya está en uso.")
        return value

    def validate_password(self, value):
        import re
        if len(value) < 8:
            raise serializers.ValidationError("La contraseña debe tener al menos 8 caracteres.")
        if not re.search(r'[A-Z]', value):
            raise serializers.ValidationError("La contraseña debe contener al menos una letra mayúscula.")
        if not re.search(r'[a-z]', value):
            raise serializers.ValidationError("La contraseña debe contener al menos una letra minúscula.")
        if not re.search(r'[0-9]', value):
            raise serializers.ValidationError("La contraseña debe contener al menos un número.")
        return value

    def create(self, validated_data):
        user = User.objects.create_user(
            username=validated_data['username'],
            email=validated_data['email'],
            password=validated_data['password'],
            first_name=validated_data.get('first_name', ''),
            last_name=validated_data.get('last_name', ''),
            is_active=False
        )
        return user


class PermissionAtomSerializer(serializers.ModelSerializer):
    class Meta:
        model = PermissionAtom
        fields = '__all__'


class ProfilePermissionSerializer(serializers.ModelSerializer):
    permission_code = serializers.CharField(source='permission.code', read_only=True)
    permission_desc = serializers.CharField(source='permission.description', read_only=True)
    module = serializers.CharField(source='permission.module', read_only=True)

    class Meta:
        model = ProfilePermission
        fields = ('permission', 'permission_code', 'permission_desc', 'module', 'scope')


class ProfileSerializer(serializers.ModelSerializer):
    permissions_detail = ProfilePermissionSerializer(source='profilepermission_set', many=True, read_only=True)
    
    class Meta:
        model = Profile
        fields = ('id', 'name', 'description', 'created_at', 'updated_at', 'permissions_detail')


class UserProfileAssignmentSerializer(serializers.ModelSerializer):
    profile_name = serializers.CharField(source='profile.name', read_only=True)
    assigned_by_name = serializers.CharField(source='assigned_by.username', read_only=True)
    has_expired = serializers.BooleanField(read_only=True)

    class Meta:
        model = UserProfileAssignment
        fields = ('id', 'profile', 'profile_name', 'assigned_at', 'assigned_by', 'assigned_by_name', 'expires_at', 'is_active', 'has_expired')


class UserSerializer(serializers.ModelSerializer):
    """
    Serializador para detalles de usuario, incluyendo sus permisos consolidados activos.
    """
    active_permissions = serializers.SerializerMethodField()
    assignments = UserProfileAssignmentSerializer(many=True, read_only=True)

    class Meta:
        model = User
        fields = ('id', 'username', 'email', 'first_name', 'last_name', 'is_superuser', 'active_permissions', 'assignments')

    def get_active_permissions(self, obj):
        return get_user_active_permissions(obj)


class PermissionAuditLogSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source='user.username', read_only=True)
    profile_name = serializers.CharField(source='profile.name', read_only=True)
    performed_by_name = serializers.CharField(source='performed_by.username', read_only=True)

    class Meta:
        model = PermissionAuditLog
        fields = ('id', 'timestamp', 'username', 'profile_name', 'action', 'performed_by_name', 'notes')
