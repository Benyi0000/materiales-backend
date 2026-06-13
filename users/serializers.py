from rest_framework import serializers
from django.contrib.auth.models import User
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
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
        import re
        if len(value) < 4 or len(value) > 30:
            raise serializers.ValidationError("El nombre de usuario debe tener entre 4 y 30 caracteres.")
        if not re.match(r'^[a-zA-Z0-9._-]+$', value):
            raise serializers.ValidationError("El nombre de usuario solo puede contener letras, números, puntos y guiones.")
        user = User.objects.filter(username__iexact=value).first()
        if user and user.is_active:
            raise serializers.ValidationError("Ya existe un usuario con este nombre.")
        return value

    def validate_email(self, value):
        user = User.objects.filter(email__iexact=value).first()
        if user and user.is_active:
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
        user = User.objects.filter(email__iexact=validated_data['email']).first()
        if user and not user.is_active:
            user.set_password(validated_data['password'])
            user.username = validated_data['username']
            user.first_name = validated_data.get('first_name', '')
            user.last_name = validated_data.get('last_name', '')
            user.save()
        else:
            user = User.objects.create_user(
                username=validated_data['username'],
                email=validated_data['email'],
                password=validated_data['password'],
                first_name=validated_data.get('first_name', ''),
                last_name=validated_data.get('last_name', ''),
                is_active=False
            )
        return user

class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    def validate(self, attrs):
        username = attrs.get(self.username_field)
        password = attrs.get('password')
        
        user = User.objects.filter(username=username).first()
        if not user:
            user = User.objects.filter(email=username).first()

        if user and user.check_password(password):
            if not user.is_active:
                raise serializers.ValidationError({
                    "error": "unverified",
                    "email": user.email,
                    "detail": "Debe verificar su cuenta para ingresar al sistema."
                })
            
            if hasattr(user, 'force_password_change'):
                raise serializers.ValidationError({
                    "error": "force_password_change",
                    "email": user.email,
                    "username": user.username,
                    "detail": "Debe cambiar su contraseña inicial para poder ingresar."
                })
        
        return super().validate(attrs)

class AdminUserCreateSerializer(serializers.ModelSerializer):
    """
    Serializador para la creación interna de usuarios por un administrador.
    Activa al usuario automáticamente y permite capturar la contraseña para el correo.
    """
    password = serializers.CharField(write_only=True, required=True, style={'input_type': 'password'})

    class Meta:
        model = User
        fields = ('username', 'email', 'password', 'first_name', 'last_name')

    def validate_username(self, value):
        import re
        if len(value) < 4 or len(value) > 30:
            raise serializers.ValidationError("El nombre de usuario debe tener entre 4 y 30 caracteres.")
        if not re.match(r'^[a-zA-Z0-9._-]+$', value):
            raise serializers.ValidationError("El nombre de usuario solo puede contener letras, números, puntos y guiones.")
        if User.objects.filter(username__iexact=value).exists():
            raise serializers.ValidationError("Ya existe un usuario con este nombre.")
        return value

    def validate_email(self, value):
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("Este correo ya está en uso.")
        return value

    def validate_password(self, value):
        if len(value) < 6:
            raise serializers.ValidationError("La contraseña debe tener al menos 6 caracteres.")
        return value

    def create(self, validated_data):
        raw_password = validated_data['password']
        user = User.objects.create_user(
            username=validated_data['username'],
            email=validated_data['email'],
            password=raw_password,
            first_name=validated_data.get('first_name', ''),
            last_name=validated_data.get('last_name', ''),
            is_active=True
        )
        user._raw_password = raw_password
        
        # Registrar que este usuario interno debe cambiar su contraseña
        from .models import ForcePasswordChange
        ForcePasswordChange.objects.create(user=user)
        
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
