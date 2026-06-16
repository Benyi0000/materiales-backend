import re
import phonenumbers
from phonenumbers import PhoneNumberType
from rest_framework import serializers
from django.db import transaction
from .models import Subscription, Order, OrderItem, Coupon, CouponRedemption, Cart, CartItem, Plan, Payment
from catalog.models import Product
from users.models import Profile
from .tasks import send_order_confirmation_email


def normalizar_celular_ar(raw: str) -> str | None:
    """
    Normaliza un número de celular argentino a exactamente 10 dígitos sin prefijos.

    Acepta:
      "3704123456"          → "3704123456"
      "370 4123456"         → "3704123456"
      "370-4123456"         → "3704123456"
      "370 15 4123456"      → "3704123456"  (quita el 15)
      "+54 9 370 4123456"   → "3704123456"
      "03704123456"         → "3704123456"

    Retorna None si el número no es válido o no tiene exactamente 10 dígitos
    después de normalizar.
    """
    if not raw:
        return None
    try:
        parsed = phonenumbers.parse(raw.strip(), "AR")
    except phonenumbers.NumberParseException:
        return None

    if not phonenumbers.is_valid_number(parsed):
        return None

    # Solo aceptamos números argentinos (código de país +54)
    if parsed.country_code != 54:
        return None

    num_type = phonenumbers.number_type(parsed)
    # Aceptamos MOBILE, FIXED_LINE y FIXED_LINE_OR_MOBILE.
    # phonenumbers clasifica como FIXED_LINE los números de 10 dígitos sin
    # prefijo +54 9 o 15; con esos prefijos los marca como MOBILE.
    # Para un campo de contacto se acepta cualquier número argentino válido.
    _VALID_TYPES = (
        PhoneNumberType.MOBILE,
        PhoneNumberType.FIXED_LINE,
        PhoneNumberType.FIXED_LINE_OR_MOBILE,
    )
    if num_type not in _VALID_TYPES:
        return None

    national = str(parsed.national_number)

    # phonenumbers devuelve 11 dígitos (con el 9 de móvil) para números en
    # formato internacional (+54 9 …) o con prefijo "15".  Se saca ese 9.
    if len(national) == 11 and national.startswith("9"):
        national = national[1:]

    return national if len(national) == 10 else None


def validate_shipping_fields(shipping: dict) -> dict:
    """
    Valida y limpia los campos de envío. Retorna el dict limpio o lanza
    serializers.ValidationError con los errores por campo.
    """
    errors = {}

    name = (shipping.get('name') or '').strip()
    address = (shipping.get('address') or '').strip()
    city = (shipping.get('city') or '').strip()
    zip_code = (shipping.get('zip') or '').strip()
    phone_raw = (shipping.get('phone') or '').strip()

    if not name:
        errors['name'] = "El nombre es obligatorio."
    elif len(name) < 3:
        errors['name'] = "El nombre debe tener al menos 3 caracteres."
    elif not re.match(r"^[a-zA-ZáéíóúÁÉÍÓÚüÜñÑ\s'\-]+$", name):
        errors['name'] = "El nombre solo puede contener letras y espacios."

    if not address:
        errors['address'] = "La dirección es obligatoria."
    elif len(address) < 5:
        errors['address'] = "Ingresá una dirección válida (al menos 5 caracteres)."

    if not city:
        errors['city'] = "La ciudad es obligatoria."
    elif len(city) < 2:
        errors['city'] = "Ingresá la ciudad."
    elif not re.match(r"^[a-zA-Z0-9áéíóúÁÉÍÓÚüÜñÑ\s'\-]+$", city):
        errors['city'] = "La ciudad contiene caracteres no válidos."

    if zip_code:
        if not re.match(r"^\d{4}$", zip_code):
            errors['zip'] = "El código postal debe tener 4 dígitos numéricos."

    normalized_phone = ""
    if phone_raw:
        normalized_phone = normalizar_celular_ar(phone_raw)
        if normalized_phone is None:
            errors['phone'] = (
                "Ingresá un celular argentino válido de 10 dígitos. "
                "Ej: 1145678901, +54 9 11 4567 8901 o 011 15 4567-8901."
            )

    if errors:
        raise serializers.ValidationError({"shipping": errors})

    return {
        'name': name,
        'address': address,
        'city': city,
        'zip': zip_code,
        'phone': normalized_phone,
    }

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
    product_image = serializers.CharField(source='product.image_url', read_only=True, default=None)

    class Meta:
        model = OrderItem
        fields = ('id', 'product', 'product_name', 'product_sku', 'product_image', 'quantity', 'price_at_purchase')


class OrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True, read_only=True)
    username = serializers.CharField(source='user.username', read_only=True)
    coupon_code = serializers.CharField(source='coupon.code', read_only=True, default=None)

    class Meta:
        model = Order
        fields = ('id', 'username', 'status', 'total', 'coupon_code', 'discount_amount',
                  'checkout_payment_method', 'shipping_cost',
                  'mp_preference_id', 'mp_payment_id', 'mp_payment_data', 'mp_paid_at', 'shipping_data',
                  'created_at', 'updated_at', 'items')


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
    Checkout desde el carrito persistente del usuario (flujos tarjeta/efectivo).
    - Valida y persiste datos de envío, tipo de entrega y método de pago.
    - RN-11: validación de stock todo-o-nada.
    - RN-09: revalidación del cupón al confirmar.
    - RN-12: se cobra el precio vigente al momento del checkout.
    - RN-14: el email de confirmación es asíncrono y no bloquea la creación.
    """
    shipping = serializers.DictField(required=True)
    delivery_type = serializers.ChoiceField(
        choices=['standard', 'express'],
        default='standard',
    )
    payment_method = serializers.ChoiceField(
        choices=['card', 'cash'],
        default='cash',
    )

    def validate_shipping(self, value):
        return validate_shipping_fields(value)

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

        # RN-09: revalidar el cupón aplicado
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
        shipping = validated_data['shipping']
        delivery_type = validated_data['delivery_type']
        payment_method = validated_data['payment_method']
        shipping_cost = 4000 if delivery_type == 'express' else 0

        with transaction.atomic():
            product_ids = list(cart.items.values_list('product_id', flat=True))
            products = {p.id: p for p in Product.objects.select_for_update().filter(id__in=product_ids)}

            # Revalidación de stock dentro de la transacción (todo-o-nada)
            for item in cart.items.all():
                product = products[item.product_id]
                if not product.is_active or product.stock < item.quantity:
                    raise serializers.ValidationError({
                        "stock": [f"Stock insuficiente para {product.name} (SKU: {product.sku}). Disponible: {product.stock}, solicitado: {item.quantity}."]
                    })

            order = Order.objects.create(
                user=user,
                total=0,
                status='pending',
                checkout_payment_method=payment_method,
                shipping_cost=shipping_cost,
                shipping_data={
                    **shipping,
                    'delivery_type': delivery_type,
                    'payment_method': payment_method,
                },
            )
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

            order.total = subtotal - discount + shipping_cost
            order.discount_amount = discount
            order.save()

            cart.items.all().delete()
            cart.coupon = None
            cart.save(update_fields=['coupon'])

        # RN-14: email asíncrono
        send_order_confirmation_email.delay(order.id)

        return order
