from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from catalog.models import Product

class Subscription(models.Model):
    """
    Control de suscripción para habilitar el Tutor Visual IA de forma dinámica.
    """
    PLAN_CHOICES = (
        ('free', 'Gratuito'),
        ('premium', 'Premium (Acceso Tutor Visual IA)'),
    )
    STATUS_CHOICES = (
        ('active', 'Activa'),
        ('cancelled', 'Cancelada'),
        ('expired', 'Expirada'),
    )
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='subscription')
    plan = models.CharField(max_length=20, choices=PLAN_CHOICES, default='free')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active')
    start_date = models.DateTimeField(auto_now_add=True)
    end_date = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.username} - {self.get_plan_display()} ({self.status})"


class Coupon(models.Model):
    """
    Cupón de descuento aplicable al carrito (RN-06 a RN-10 de la spec Carrito y Pedidos).
    """
    TYPE_CHOICES = (
        ('percent', 'Porcentaje sobre el subtotal'),
        ('fixed', 'Monto fijo'),
    )
    code = models.CharField(max_length=50, unique=True, help_text="Código que ingresa el cliente (se normaliza a mayúsculas)")
    description = models.CharField(max_length=255, blank=True)
    discount_type = models.CharField(max_length=10, choices=TYPE_CHOICES)
    value = models.DecimalField(max_digits=10, decimal_places=2, help_text="Porcentaje (0-100) o monto fijo en pesos según el tipo")
    valid_from = models.DateTimeField()
    valid_until = models.DateTimeField()
    max_uses = models.PositiveIntegerField(null=True, blank=True, help_text="Límite de usos totales; vacío = ilimitado")
    times_used = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='created_coupons')
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        self.code = self.code.strip().upper()
        super().save(*args, **kwargs)

    def validate_for_user(self, user):
        """
        Valida vigencia y usos (RN-08). Retorna None si es válido,
        o el mensaje de error específico (RN-10).
        """
        now = timezone.now()
        if not self.is_active:
            return "El cupón no existe o no está disponible."
        if now < self.valid_from:
            return "El cupón aún no está vigente."
        if now > self.valid_until:
            return "El cupón ha vencido."
        if self.max_uses is not None and self.times_used >= self.max_uses:
            return "El cupón se ha agotado."
        if self.redemptions.filter(user=user).exists():
            return "Ya utilizaste este cupón en una compra anterior."
        return None

    def compute_discount(self, subtotal):
        """RN-07: porcentaje o monto fijo; nunca deja el total por debajo de $0."""
        if self.discount_type == 'percent':
            discount = subtotal * self.value / 100
        else:
            discount = self.value
        return min(discount, subtotal)

    def __str__(self):
        return f"{self.code} ({self.get_discount_type_display()}: {self.value})"


class Order(models.Model):
    """
    Pedido realizado en la tienda
    """
    STATUS_CHOICES = (
        ('pending', 'Pendiente'),
        ('preparing', 'En preparación'),
        ('shipped', 'Enviado'),
        ('delivered', 'Entregado'),
        ('cancelled', 'Cancelado'),
    )

    # Secuencia lineal de avance de estado (spec pedidos/ventas, RN4).
    # Las transiciones solo avanzan al estado inmediatamente siguiente; no hay
    # saltos ni retrocesos. 'cancelled' queda fuera de la secuencia (RN6).
    STATUS_SEQUENCE = ['pending', 'preparing', 'shipped', 'delivered']

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='orders')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    total = models.DecimalField(max_digits=12, decimal_places=2)
    coupon = models.ForeignKey(Coupon, on_delete=models.SET_NULL, null=True, blank=True, related_name='orders')
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Pedido #{self.id} - {self.user.username} ({self.status})"

    def next_status(self):
        """
        Retorna el estado inmediatamente siguiente en la secuencia, o None si el
        pedido ya está en el último estado o fuera de la secuencia (ej. cancelado).
        """
        seq = self.STATUS_SEQUENCE
        if self.status not in seq:
            return None
        idx = seq.index(self.status)
        return seq[idx + 1] if idx + 1 < len(seq) else None


class OrderItem(models.Model):
    """
    Ítems específicos de un pedido
    """
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField(default=1)
    price_at_purchase = models.DecimalField(max_digits=10, decimal_places=2)

    def __str__(self):
        return f"{self.quantity}x {self.product.name} (Pedido #{self.order.id})"


class CouponRedemption(models.Model):
    """
    Registro de uso de un cupón por un usuario (RN-08: máximo 1 uso por usuario).
    """
    coupon = models.ForeignKey(Coupon, on_delete=models.CASCADE, related_name='redemptions')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='coupon_redemptions')
    order = models.ForeignKey(Order, on_delete=models.SET_NULL, null=True, related_name='coupon_redemptions')
    redeemed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('coupon', 'user')

    def __str__(self):
        return f"{self.user.username} usó {self.coupon.code} (Pedido #{self.order_id})"


class Cart(models.Model):
    """
    Carrito persistente en base de datos, único por usuario (RN-01).
    El carrito anónimo del navegador se descarta al iniciar sesión (RN-02).
    """
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='cart')
    coupon = models.ForeignKey(Coupon, on_delete=models.SET_NULL, null=True, blank=True, related_name='carts')
    updated_at = models.DateTimeField(auto_now=True)

    def subtotal(self):
        return sum((item.product.price * item.quantity for item in self.items.all()), 0)

    def __str__(self):
        return f"Carrito de {self.user.username}"


class CartItem(models.Model):
    """
    Ítem del carrito persistente. Guarda el precio al momento de agregarlo
    para poder informar cambios de precio antes del checkout (RN-12).
    """
    cart = models.ForeignKey(Cart, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField(default=1)
    price_at_add = models.DecimalField(max_digits=10, decimal_places=2)
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('cart', 'product')

    def __str__(self):
        return f"{self.quantity}x {self.product.name} (Carrito de {self.cart.user.username})"
