from rest_framework import serializers
from django.db import transaction
from .models import Subscription, Order, OrderItem, Coupon, CouponRedemption, Cart, CartItem, Plan, Payment
from catalog.models import Product
from users.models import Profile
from .tasks import send_order_confirmation_email

class PlanSerializer(serializers.ModelSerializer):
    profiles = serializers.PrimaryKeyRelatedField(many=True, queryset=Profile.objects.all(), required=False)
    profile_names = serializers.SerializerMethodField()

    class Meta:
        model = Plan
        fields = ('id', 'name', 'description', 'price', 'duration_days', 'trial_days',
                  'auto_renew', 'profiles', 'profile_names', 'is_active', 'created_at')
        read_only_fields = ('created_at',)

    def get_profile_names(self, obj):
        return [p.name for p in obj.profiles.all()]


class PaymentSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source='user.username', read_only=True)
    plan_name = serializers.CharField(source='plan.name', read_only=True, default=None)

    class Meta:
        model = Payment
        fields = ('id', 'username', 'plan_name', 'amount', 'status', 'created_at')


class SubscriptionSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source='user.username', read_only=True)
    current_plan_name = serializers.CharField(source='current_plan.name', read_only=True, default=None)

    class Meta:
        model = Subscription
        fields = ('id', 'username', 'plan', 'current_plan', 'current_plan_name', 'status',
                  'cancel_at_period_end', 'start_date', 'end_date')


class OrderItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source='product.name', read_only=True)
    product_sku = serializers.CharField(source='product.sku', read_only=True)

    class Meta:
        model = OrderItem
        fields = ('id', 'product', 'product_name', 'product_sku', 'quantity', 'price_at_purchase')


class OrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True, read_only=True)
    username = serializers.CharField(source='user.username', read_only=True)
    coupon_code = serializers.CharField(source='coupon.code', read_only=True, default=None)

    class Meta:
        model = Order
        fields = ('id', 'username', 'status', 'total', 'coupon_code', 'discount_amount', 'created_at', 'updated_at', 'items')


class CouponSerializer(serializers.ModelSerializer):
    """ABM de cupones (permiso 'marketing.gestionar_cupones')."""
    created_by_username = serializers.CharField(source='created_by.username', read_only=True, default=None)

    class Meta:
        model = Coupon
        fields = ('id', 'code', 'description', 'discount_type', 'value', 'valid_from',
                  'valid_until', 'max_uses', 'times_used', 'is_active', 'created_by_username', 'created_at')
        read_only_fields = ('times_used', 'created_at')

    def validate(self, data):
        discount_type = data.get('discount_type', getattr(self.instance, 'discount_type', None))
        value = data.get('value', getattr(self.instance, 'value', None))
        valid_from = data.get('valid_from', getattr(self.instance, 'valid_from', None))
        valid_until = data.get('valid_until', getattr(self.instance, 'valid_until', None))

        if value is not None and value <= 0:
            raise serializers.ValidationError({"value": "El valor del descuento debe ser mayor a cero."})
        if discount_type == 'percent' and value is not None and value > 100:
            raise serializers.ValidationError({"value": "Un descuento porcentual no puede superar el 100%."})
        if valid_from and valid_until and valid_from >= valid_until:
            raise serializers.ValidationError({"valid_until": "La fecha de fin debe ser posterior a la de inicio."})
        return data


class CartItemSerializer(serializers.ModelSerializer):
    product_id = serializers.IntegerField(source='product.id', read_only=True)
    sku = serializers.CharField(source='product.sku', read_only=True)
    name = serializers.CharField(source='product.name', read_only=True)
    image_url = serializers.CharField(source='product.image_url', read_only=True)
    price = serializers.DecimalField(source='product.price', max_digits=10, decimal_places=2, read_only=True)
    stock = serializers.IntegerField(source='product.stock', read_only=True)
    weight_kg = serializers.DecimalField(source='product.weight_kg', max_digits=6, decimal_places=2, read_only=True)
    price_changed = serializers.SerializerMethodField()
    item_total = serializers.SerializerMethodField()

    class Meta:
        model = CartItem
        fields = ('id', 'product_id', 'sku', 'name', 'image_url', 'price', 'price_at_add',
                  'price_changed', 'stock', 'weight_kg', 'quantity', 'item_total')

    def get_price_changed(self, obj):
        # RN-12: informar si el precio cambió desde que se agregó al carrito
        return obj.product.price != obj.price_at_add

    def get_item_total(self, obj):
        return float(obj.product.price * obj.quantity)


class CartSerializer(serializers.ModelSerializer):
    items = CartItemSerializer(many=True, read_only=True)
    subtotal = serializers.SerializerMethodField()
    discount_amount = serializers.SerializerMethodField()
    total = serializers.SerializerMethodField()
    coupon_code = serializers.CharField(source='coupon.code', read_only=True, default=None)

    class Meta:
        model = Cart
        fields = ('id', 'items', 'subtotal', 'coupon_code', 'discount_amount', 'total', 'updated_at')

    def get_subtotal(self, obj):
        return float(obj.subtotal())

    def get_discount_amount(self, obj):
        if not obj.coupon:
            return 0.0
        # Solo mostramos descuento si el cupón sigue siendo válido para el usuario (RN-09)
        if obj.coupon.validate_for_user(obj.user) is not None:
            return 0.0
        return float(obj.coupon.compute_discount(obj.subtotal()))

    def get_total(self, obj):
        return float(obj.subtotal()) - self.get_discount_amount(obj)


class OrderCreateSerializer(serializers.Serializer):
    """
    Checkout desde el carrito persistente del usuario (spec Carrito y Pedidos).
    - RN-11: validación de stock todo-o-nada.
    - RN-09: revalidación del cupón al confirmar.
    - RN-12: se cobra el precio vigente al momento del checkout.
    - RN-13: el pedido nace 'Pendiente de Pago' (pago simulado, sin pasarela).
    - RN-14: el email de confirmación es asíncrono y no bloquea la creación.
    """

    def validate(self, data):
        user = self.context['request'].user
        cart = Cart.objects.filter(user=user).prefetch_related('items__product').first()

        if not cart or not cart.items.exists():
            raise serializers.ValidationError("El carrito está vacío. Agrega productos antes de confirmar la compra.")

        # RN-11: validar stock de TODOS los ítems; si alguno falla, se rechaza el pedido completo
        stock_errors = []
        for item in cart.items.all():
            product = item.product
            if not product.is_active:
                stock_errors.append(f"{product.name} (SKU: {product.sku}) ya no está disponible en el catálogo.")
            elif product.stock < item.quantity:
                stock_errors.append(
                    f"Stock insuficiente para {product.name} (SKU: {product.sku}). Disponible: {product.stock}, solicitado: {item.quantity}."
                )
        if stock_errors:
            raise serializers.ValidationError({"stock": stock_errors})

        # RN-09: revalidar el cupón aplicado; si dejó de ser válido se rechaza con mensaje claro
        if cart.coupon:
            error = cart.coupon.validate_for_user(user)
            if error:
                raise serializers.ValidationError({
                    "coupon": f"{error} Quita el cupón del carrito para continuar la compra sin descuento."
                })

        data['cart'] = cart
        return data

    def create(self, validated_data):
        user = self.context['request'].user
        cart = validated_data['cart']

        with transaction.atomic():
            # Bloquear los productos involucrados para evitar sobreventa concurrente
            product_ids = list(cart.items.values_list('product_id', flat=True))
            products = {p.id: p for p in Product.objects.select_for_update().filter(id__in=product_ids)}

            # Revalidación de stock dentro de la transacción (todo-o-nada)
            for item in cart.items.all():
                product = products[item.product_id]
                if not product.is_active or product.stock < item.quantity:
                    raise serializers.ValidationError({
                        "stock": [f"Stock insuficiente para {product.name} (SKU: {product.sku}). Disponible: {product.stock}, solicitado: {item.quantity}."]
                    })

            order = Order.objects.create(user=user, total=0, status='pending')
            subtotal = 0

            from catalog.models import StockMovement
            for item in cart.items.all():
                product = products[item.product_id]
                product.stock -= item.quantity
                product.save(update_fields=['stock'])
                StockMovement.objects.create(
                    product=product, change=-item.quantity, reason='sale',
                    resulting_stock=product.stock, user=user,
                )

                price = product.price  # RN-12: precio vigente al checkout
                subtotal += price * item.quantity

                OrderItem.objects.create(
                    order=order,
                    product=product,
                    quantity=item.quantity,
                    price_at_purchase=price
                )

            # Aplicar cupón (ya revalidado) y registrar la redención
            discount = 0
            if cart.coupon:
                coupon = Coupon.objects.select_for_update().get(id=cart.coupon_id)
                error = coupon.validate_for_user(user)
                if error:
                    raise serializers.ValidationError({"coupon": error})
                discount = coupon.compute_discount(subtotal)
                coupon.times_used += 1
                coupon.save(update_fields=['times_used'])
                CouponRedemption.objects.create(coupon=coupon, user=user, order=order)
                order.coupon = coupon

            order.total = subtotal - discount
            order.discount_amount = discount
            order.save()

            # Vaciar el carrito una vez confirmado el pedido
            cart.items.all().delete()
            cart.coupon = None
            cart.save(update_fields=['coupon'])

        # RN-14: email asíncrono; su falla no bloquea la creación del pedido
        send_order_confirmation_email.delay(order.id)

        return order
