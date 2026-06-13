from rest_framework import serializers
from .models import ChatbotMessage, ChatbotSession

class ChatbotMessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChatbotMessage
        fields = ["id", "session", "role", "content", "created_at"]

class ChatbotSessionSerializer(serializers.ModelSerializer):
    messages = ChatbotMessageSerializer(many=True, read_only=True)

    class Meta:
        model = ChatbotSession
        fields = ["id", "created_at", "messages"]
