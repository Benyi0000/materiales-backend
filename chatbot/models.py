import uuid
from django.db import models
from django.contrib.auth.models import User

class ChatbotSession(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="chatbot_sessions",
        null=True,
        blank=True,
        help_text="Dueño de la sesión; cada usuario solo ve sus propios chats",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    objects = models.Manager()

    class Meta:
        db_table = "chatbot_sessions"
        ordering = ["-created_at"]

class ChatbotMessage(models.Model):
    ROLE_CHOICES = (
        ("user", "User"),
        ("assistant", "Assistant"),
    )
    session = models.ForeignKey(
        ChatbotSession, related_name="messages", on_delete=models.CASCADE
    )
    role = models.CharField(max_length=10, choices=ROLE_CHOICES)
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    objects = models.Manager()

    class Meta:
        db_table = "chatbot_messages"
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.role} - {self.session_id}"
