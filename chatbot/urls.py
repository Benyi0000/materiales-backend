from django.urls import path
from . import views

urlpatterns = [
    # GET /api/chatbot/chat/ - Listar sesiones
    # POST /api/chatbot/chat/ - Crear un nuevo chat
    path("chat/", views.ChatSessionListCreateView.as_view(), name="chat-session-list-create"),

    # GET /api/chatbot/chat/<uuid>/ - Para ver historial
    # DELETE /api/chatbot/chat/<uuid>/ - Borrar sesion
    path("chat/<uuid:pk>/", views.ChatSessionDetailView.as_view(), name="chat-session-detail"),

    # POST /api/chatbot/chat/<uuid>/stream/ - Para enviar un mensaje y recibir respuesta en streaming (SSE)
    path("chat/<uuid:pk>/stream/", views.ChatMessageStreamView.as_view(), name="chat-message-stream"),
]
