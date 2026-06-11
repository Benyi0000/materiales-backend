import os

path = r'E:\Biblioteca\Escritorio\Sistemas\Craftiar-main\Craftiar\materiales-backend\users\views.py'
with open(path, 'r', encoding='utf-8') as f:
    c = f.read()

# Patch RegisterView
old_reg = '''class RegisterView(generics.CreateAPIView):
    \"\"\"
    API para registrar nuevos usuarios.
    Asigna automáticamente el perfil base "Comprar en la tienda".
    \"\"\"
    serializer_class = RegisterSerializer
    permission_classes = [AllowAny]'''
new_reg = '''class RegisterView(generics.CreateAPIView):
    \"\"\"
    API para registrar nuevos usuarios.
    \"\"\"
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
        send_verification_email.delay(user.id, token, uidb64)'''
c = c.replace(old_reg, new_reg)

# Patch PasswordResetConfirmView
old_pass = '''        # Establecer la nueva contraseña
        user.set_password(new_password)
        user.save()

        # Loguear la acción en la auditoría'''
new_pass = '''        # Establecer la nueva contraseña
        user.set_password(new_password)
        user.save()
        
        from rest_framework_simplejwt.token_blacklist.models import OutstandingToken, BlacklistedToken
        for tk in OutstandingToken.objects.filter(user=user):
            BlacklistedToken.objects.get_or_create(token=tk)

        # Loguear la acción en la auditoría'''
c = c.replace(old_pass, new_pass)

# Add VerifyEmailView
verify_view = '''

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
'''
c += verify_view

with open(path, 'w', encoding='utf-8') as f:
    f.write(c)

print("views.py patched.")
