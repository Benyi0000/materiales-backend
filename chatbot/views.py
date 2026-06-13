from django.http import StreamingHttpResponse
from rest_framework import generics, status
from rest_framework.renderers import BaseRenderer
from rest_framework.response import Response
from rest_framework.views import APIView
from django.core.exceptions import ObjectDoesNotExist
from django.shortcuts import get_object_or_404

from .models import ChatbotSession
from .serializers import ChatbotSessionSerializer
from .services import RAGQueryService
from users.permissions import HasDynamicPermission

class ChatSessionListCreateView(generics.ListCreateAPIView):
    serializer_class = ChatbotSessionSerializer
    permission_classes = [HasDynamicPermission]
    required_permission = 'tutor.acceder'

    def get_queryset(self):
        # Cada usuario solo ve sus propias sesiones.
        return ChatbotSession.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

class ChatSessionDetailView(generics.RetrieveDestroyAPIView):
    serializer_class = ChatbotSessionSerializer
    permission_classes = [HasDynamicPermission]
    required_permission = 'tutor.acceder'

    def get_queryset(self):
        # Solo puede ver/borrar sesiones propias (404 para ajenas).
        return ChatbotSession.objects.filter(user=self.request.user)

class ChatMessageStreamView(APIView):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.rag_service = RAGQueryService()

    permission_classes = [HasDynamicPermission]
    required_permission = 'tutor.acceder'

    class ServerSentEventsRenderer(BaseRenderer):
        media_type = "text/event-stream"
        format = "sse"
        charset = "utf-8"

        def render(self, data, media_type=None, renderer_context=None):
            if isinstance(data, (dict, list)):
                import json
                return json.dumps(data).encode(self.charset)
            if isinstance(data, str):
                return data.encode(self.charset)
            return data

    renderer_classes = [ServerSentEventsRenderer]

    def post(self, request, pk):
        user_text = request.data.get("message")

        if not user_text:
            return Response(
                {"error": "El contenido del mensaje es requerido."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Aislamiento por usuario: solo se puede escribir en sesiones propias (404 si es ajena).
        get_object_or_404(ChatbotSession, pk=pk, user=request.user)

        try:
            stream = self.rag_service.query(session_id=pk, user_text=user_text)

            response = StreamingHttpResponse(stream, content_type="text/event-stream")
            response['Cache-Control'] = 'no-cache'
            response['X-Accel-Buffering'] = 'no'  # Deshabilitar el buffering en Nginx

            return response
        except ObjectDoesNotExist:
            return Response(
                {"error": "Sesión de chat no encontrada."}, status=status.HTTP_404_NOT_FOUND
            )
        except ValueError as ve:
            return Response(
                {"error": str(ve)}, status=status.HTTP_400_BAD_REQUEST
            )
