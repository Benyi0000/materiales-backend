from rest_framework import viewsets, status, generics
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.pagination import PageNumberPagination
from django.db import models, transaction
from django.utils import timezone
from .models import Order, Subscription, OrderItem, Coupon, Cart, CartItem, Plan, Payment
from .serializers import (
    OrderSerializer, OrderCreateSerializer, SubscriptionSerializer,
    CouponSerializer, CartSerializer, PlanSerializer, PaymentSerializer,
    validate_shipping_fields,
)
from catalog.models import Product
from users.permissions import HasDynamicPermission, has_custom_permission
from .tasks import send_order_status_change_email, send_order_confirmation_email
from . import subscriptions as subs_service


class OrderPagination(PageNumberPagination):
    """CA-10: historial paginado de a 10."""
    page_size = 10
    page_size_query_param = 'page_size'
    max_page_size = 50

class OrderViewSet(viewsets.ModelViewSet):
    """
    Gestión de pedidos (spec pedidos/ventas por permisos atómicos):

    - Vista COMPRAS (?view=compras, por defecto): el cliente ve sus propias compras.
      Requiere el permiso 'pedidos.ver'.
    - Vista VENTAS (?view=ventas): ver las ventas del ecommerce. Requiere
      'pedidosventas.ver'. Alcance 'propios' = ventas con sus productos;
      alcance 'todos' = todas las ventas, con filtros por estado y rango de fechas.
    - Cambio de estado (POST /orders/{id}/update_status/): requiere
      'pedidosventas.cambiar_estado'; respeta la secuencia lineal (RN4).
    - Checkout (POST): crear pedidos desde el carrito persistente ('carrito.checkout').
    - Cancelación (POST /orders/{id}/cancel/): el comprador cancela su pedido Pendiente (RN6).
    """
    pagination_class = OrderPagination

    # Acciones de detalle: el alcance/propiedad lo resuelve has_object_permission.
    DETAIL_ACTIONS = ('retrieve', 'update_status', 'cancel')

    def get_serializer_class(self):
        if self.action == 'create':
            return OrderCreateSerializer
        return OrderSerializer

    def _is_sales_view(self):
        return self.request.query_params.get('view', 'compras') == 'ventas'

    def get_permissions(self):
        if self.action == 'create':
            self.required_permission = 'carrito.checkout'
            self.required_scope = 'propios'
            return [HasDynamicPermission()]
        elif self.action == 'update_status':
            self.required_permission = 'pedidosventas.cambiar_estado'
            self.required_scope = 'propios'
            return [HasDynamicPermission()]
        elif self.action == 'list':
            # La vista (compras/ventas) determina el permiso requerido (RN3).
            self.required_permission = 'pedidosventas.ver' if self._is_sales_view() else 'pedidos.ver'
            self.required_scope = 'propios'
            return [HasDynamicPermission()]
        elif self.action == 'retrieve':
            # Un pedido puede consultarse como compra o como venta; basta con uno.
            self.required_permission = ['pedidos.ver', 'pedidosventas.ver']
            self.required_scope = 'propios'
            return [HasDynamicPermission()]

        return [IsAuthenticated()]

    def _apply_sales_filters(self, queryset):
        """Filtros del listado de ventas: estado y rango de fechas (RN8)."""
        params = self.request.query_params
        status_filter = params.get('status')
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        date_from = params.get('date_from')
        if date_from:
            queryset = queryset.filter(created_at__date__gte=date_from)
        date_to = params.get('date_to')
        if date_to:
            queryset = queryset.filter(created_at__date__lte=date_to)
        return queryset

    def get_queryset(self):
        user = self.request.user
        if not user.is_authenticated:
            return Order.objects.none()

        # Acciones de detalle: devolver el universo y dejar que has_object_permission
        # (o la verificación manual en cancel) controle el acceso por propiedad/alcance.
        if self.action in self.DETAIL_ACTIONS:
            return Order.objects.all()

        if self._is_sales_view():
            # Vista de VENTAS (pedidosventas.ver)
            if has_custom_permission(user, 'pedidosventas.ver', required_scope='todos'):
                queryset = Order.objects.all()
            else:
                # Alcance 'propios': solo ventas que contienen productos del usuario (RN2).
                queryset = Order.objects.filter(items__product__created_by=user).distinct()
            queryset = self._apply_sales_filters(queryset)
        else:
            # Vista de COMPRAS (pedidos.ver): solo lo que el usuario compró (RN3).
            # Se excluyen los pending_payment y los cancelled de MP sin pago
            # confirmado: son checkouts abandonados que el comprador no puede
            # retomar y que nunca representaron una compra real.
            queryset = (
                Order.objects.filter(user=user)
                .exclude(status='pending_payment')
                .exclude(status='cancelled', checkout_payment_method='mercadopago', mp_paid_at__isnull=True)
            )
            status_filter = self.request.query_params.get('status')
            if status_filter:
                queryset = queryset.filter(status=status_filter)

        return queryset.order_by('-created_at')

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        order = serializer.save()
        
        # Serializar la respuesta final con el OrderSerializer detallado
        response_serializer = OrderSerializer(order)
        return Response(response_serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], permission_classes=[HasDynamicPermission])
    def update_status(self, request, pk=None):
        """
        RF 4.2 y RF 7.3: Avanza el estado de un pedido respetando la secuencia
        lineal Pendiente → En preparación → Enviado → Entregado (RN4).
        Requiere el permiso dinámico 'pedidosventas.cambiar_estado'.
        """
        self.required_permission = 'pedidosventas.cambiar_estado'
        self.required_scope = 'propios'

        order = self.get_object()
        new_status = request.data.get('status')

        valid_statuses = [choice[0] for choice in Order.STATUS_CHOICES]
        if new_status not in valid_statuses:
            return Response(
                {"error": f"Estado inválido. Los estados válidos son: {', '.join(valid_statuses)}"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # RN4: solo se permite avanzar al estado inmediatamente siguiente.
        expected_next = order.next_status()
        if expected_next is None:
            return Response(
                {"error": f"El pedido en estado '{order.get_status_display()}' no admite más cambios de estado."},
                status=status.HTTP_400_BAD_REQUEST
            )
        if new_status != expected_next:
            expected_label = dict(Order.STATUS_CHOICES)[expected_next]
            return Response(
                {"error": f"Transición inválida. Desde '{order.get_status_display()}' solo se puede avanzar a '{expected_label}'."},
                status=status.HTTP_400_BAD_REQUEST
            )

        old_status = order.status
        order.status = new_status
        order.save(update_fields=['status', 'updated_at'])

        # Disparar tarea Celery asíncrona para notificar cambio de estado por email (RF 7.3)
        send_order_status_change_email.delay(order.id, old_status, new_status)

        return Response({"status": f"Estado del pedido actualizado a {new_status} con éxito."})

    @action(detail=True, methods=['post'], permission_classes=[IsAuthenticated])
    def cancel(self, request, pk=None):
        """
        RN-15: El comprador puede cancelar su propio pedido solo mientras esté
        Pendiente de Pago. La cancelación repone el stock descontado.
        """
        order = self.get_object()

        if order.user != request.user:
            return Response(
                {"error": "Solo el comprador puede cancelar su propio pedido."},
                status=status.HTTP_403_FORBIDDEN
            )

        if order.status != 'pending':
            return Response(
                {"error": f"Solo se pueden cancelar pedidos en estado 'Pendiente de Pago'. Estado actual: {order.get_status_display()}."},
                status=status.HTTP_400_BAD_REQUEST
            )

        with transaction.atomic():
            # Reponer el stock de cada ítem del pedido
            for item in order.items.select_related('product'):
                product = Product.objects.select_for_update().get(id=item.product_id)
                product.stock += item.quantity
                product.save(update_fields=['stock'])

            old_status = order.status
            order.status = 'cancelled'
            order.save(update_fields=['status', 'updated_at'])

        send_order_status_change_email.delay(order.id, old_status, 'cancelled')

        return Response({"status": "Pedido cancelado con éxito. El stock fue repuesto."})


class SubscriptionView(generics.RetrieveUpdateDestroyAPIView):
    """
    RF 4.4: Gestión de Suscripciones (Activación y Cancelación simuladas para el MVP).
    - GET: Retorna la suscripción actual.
    - POST: Activa el plan Premium (asigna automáticamente el perfil 'Tutor Visual IA').
    - DELETE: Cancela/vence el plan Premium (revoca automáticamente el perfil).
    """
    permission_classes = [IsAuthenticated]
    serializer_class = SubscriptionSerializer

    def get_object(self):
        subscription, created = Subscription.objects.get_or_create(
            user=self.request.user,
            defaults={'plan': 'free', 'status': 'active'}
        )
        # Self-heal: si el plan otorgaba perfiles y al usuario ya no le queda
        # ninguno asignado (ej. se lo revocaron desde Gestión de Perfiles),
        # entonces ya no está suscripto.
        if subscription.current_plan_id:
            from users.models import UserProfileAssignment
            profile_ids = list(subscription.current_plan.profiles.values_list('id', flat=True))
            if profile_ids:
                still = UserProfileAssignment.objects.filter(
                    user=self.request.user, profile_id__in=profile_ids, is_active=True
                ).exists()
                if not still:
                    subscription.current_plan = None
                    subscription.status = 'expired'
                    subscription.cancel_at_period_end = True
                    subscription.plan = 'free'
                    subscription.save(update_fields=['current_plan', 'status', 'cancel_at_period_end', 'plan'])
        return subscription

    def post(self, request):
        subscription = self.get_object()
        
        # Simulación de pago y activación del plan Premium
        subscription.plan = 'premium'
        subscription.status = 'active'
        subscription.start_date = timezone.now()
        # Vence en 1 mes por defecto
        subscription.end_date = timezone.now() + timezone.timedelta(days=30)
        subscription.save()

        # El signal en Django (handle_subscription_change) se encargará 
        # de crear el UserProfileAssignment del perfil "Tutor Visual IA"
        serializer = self.get_serializer(subscription)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def delete(self, request, *args, **kwargs):
        subscription = self.get_object()
        
        # Simulación de cancelación/baja de suscripción
        subscription.plan = 'free'
        subscription.status = 'expired'
        subscription.end_date = timezone.now()
        subscription.save()

        # El signal revoca el perfil "Tutor Visual IA" automáticamente
        serializer = self.get_serializer(subscription)
        return Response(serializer.data, status=status.HTTP_200_OK)


def _get_or_create_cart(user):
    cart, _ = Cart.objects.get_or_create(user=user)
    return cart


def _clean_unavailable_items(cart):
    """
    RN-04: quita del carrito los productos desactivados o eliminados del catálogo
    y retorna los nombres removidos para notificar al usuario.
    """
    removed = []
    for item in cart.items.select_related('product'):
        if item.product is None or not item.product.is_active:
            removed.append(item.product.name if item.product else "Producto eliminado")
            item.delete()
    return removed


def _cart_response(cart, extra=None):
    data = CartSerializer(cart).data
    if extra:
        data.update(extra)
    return data


class CartView(APIView):
    """
    Carrito persistente en base de datos (RN-01, RN-02).
    - GET: retorna el carrito de la cuenta (el carrito anónimo local se descarta en el frontend).
    - DELETE: vacía el carrito.
    Requiere permiso 'carrito.gestionar'.
    """
    permission_classes = [HasDynamicPermission]
    required_permission = 'carrito.gestionar'

    def get(self, request):
        cart = _get_or_create_cart(request.user)
        removed = _clean_unavailable_items(cart)
        extra = {"removed_items": removed} if removed else None
        return Response(_cart_response(cart, extra))

    def delete(self, request):
        cart = _get_or_create_cart(request.user)
        cart.items.all().delete()
        cart.coupon = None
        cart.save(update_fields=['coupon'])
        return Response(_cart_response(cart))


class CartItemView(APIView):
    """
    Gestión de ítems del carrito persistente (permiso 'carrito.gestionar').
    - POST /cart/items/: agrega un producto {product_id, quantity} (suma si ya existe).
    - PATCH /cart/items/{product_id}/: fija la cantidad {quantity}.
    - DELETE /cart/items/{product_id}/: elimina el ítem.
    RN-03: la cantidad se limita al stock disponible y se informa el ajuste.
    """
    permission_classes = [HasDynamicPermission]
    required_permission = 'carrito.gestionar'

    def post(self, request):
        product_id = request.data.get('product_id')
        try:
            quantity = int(request.data.get('quantity', 1))
        except (TypeError, ValueError):
            return Response({"error": "La cantidad debe ser un número entero."}, status=status.HTTP_400_BAD_REQUEST)

        if quantity < 1:
            return Response({"error": "La cantidad debe ser al menos 1."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            product = Product.objects.get(id=product_id, is_active=True)
        except Product.DoesNotExist:
            return Response({"error": "El producto no existe o no está disponible."}, status=status.HTTP_404_NOT_FOUND)

        cart = _get_or_create_cart(request.user)
        item, created = CartItem.objects.get_or_create(
            cart=cart,
            product=product,
            defaults={'quantity': 0, 'price_at_add': product.price}
        )

        requested = item.quantity + quantity
        # RN-03: limitar al stock disponible e informar
        allocated = min(requested, product.stock)
        adjusted = allocated < requested

        if allocated <= 0:
            if created:
                item.delete()
            return Response({"error": f"No hay stock disponible de {product.name}."}, status=status.HTTP_400_BAD_REQUEST)

        item.quantity = allocated
        item.save()

        extra = {"adjusted": f"La cantidad de {product.name} se ajustó a {allocated} por límite de stock."} if adjusted else None
        return Response(_cart_response(cart, extra), status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)

    def patch(self, request, product_id):
        try:
            quantity = int(request.data.get('quantity'))
        except (TypeError, ValueError):
            return Response({"error": "La cantidad debe ser un número entero."}, status=status.HTTP_400_BAD_REQUEST)

        cart = _get_or_create_cart(request.user)
        try:
            item = cart.items.select_related('product').get(product_id=product_id)
        except CartItem.DoesNotExist:
            return Response({"error": "El producto no está en el carrito."}, status=status.HTTP_404_NOT_FOUND)

        if quantity < 1:
            item.delete()
            return Response(_cart_response(cart))

        # RN-03: limitar al stock disponible e informar
        allocated = min(quantity, item.product.stock)
        adjusted = allocated < quantity
        item.quantity = allocated
        item.save(update_fields=['quantity'])

        extra = {"adjusted": f"La cantidad de {item.product.name} se ajustó a {allocated} por límite de stock."} if adjusted else None
        return Response(_cart_response(cart, extra))

    def delete(self, request, product_id):
        cart = _get_or_create_cart(request.user)
        cart.items.filter(product_id=product_id).delete()
        return Response(_cart_response(cart))


class CartCouponView(APIView):
    """
    Aplicación de cupones al carrito (permiso 'carrito.gestionar').
    - POST: aplica un cupón {code}. RN-06: un solo cupón; el nuevo reemplaza al anterior.
    - DELETE: quita el cupón aplicado.
    RN-10: errores específicos (inexistente, vencido, agotado, ya utilizado).
    """
    permission_classes = [HasDynamicPermission]
    required_permission = 'carrito.gestionar'

    def post(self, request):
        code = (request.data.get('code') or '').strip().upper()
        if not code:
            return Response({"error": "Debe ingresar un código de cupón."}, status=status.HTTP_400_BAD_REQUEST)

        cart = _get_or_create_cart(request.user)

        try:
            coupon = Coupon.objects.get(code=code)
        except Coupon.DoesNotExist:
            return Response({"error": "El cupón no existe o no está disponible."}, status=status.HTTP_404_NOT_FOUND)

        error = coupon.validate_for_user(request.user)
        if error:
            return Response({"error": error}, status=status.HTTP_400_BAD_REQUEST)

        cart.coupon = coupon
        cart.save(update_fields=['coupon'])
        return Response(_cart_response(cart))

    def delete(self, request):
        cart = _get_or_create_cart(request.user)
        cart.coupon = None
        cart.save(update_fields=['coupon'])
        return Response(_cart_response(cart))


class CouponViewSet(viewsets.ModelViewSet):
    """
    ABM de cupones de descuento. Requiere permiso 'gestion.gestionar_promociones'
    (antes 'marketing.gestionar_cupones', deprecado).
    """
    queryset = Coupon.objects.all().order_by('-created_at')
    serializer_class = CouponSerializer
    permission_classes = [HasDynamicPermission]
    required_permission = 'gestion.gestionar_promociones'
    required_scope = 'todos'

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)


# ============================================================
# Planes, suscripciones y pago simulado (Gestión Interna)
# ============================================================

class PlanViewSet(viewsets.ModelViewSet):
    """
    ABM de planes. Crear/editar/eliminar requiere 'gestion.gestionar_planes';
    listar/ver está disponible para cualquier usuario autenticado (para suscribirse),
    pero quienes no gestionan planes solo ven los activos.
    """
    serializer_class = PlanSerializer
    required_permission = 'gestion.gestionar_planes'
    required_scope = 'todos'

    def get_permissions(self):
        if self.action in ('list', 'retrieve'):
            return [IsAuthenticated()]
        return [HasDynamicPermission()]

    def get_queryset(self):
        qs = Plan.objects.all().prefetch_related('profiles').order_by('-created_at')
        if not has_custom_permission(self.request.user, 'gestion.gestionar_planes', 'todos'):
            qs = qs.filter(is_active=True)
        return qs


class SubscriptionCheckoutView(APIView):
    """
    Checkout SIMULADO self-service. Recibe el plan (y datos de tarjeta que se
    ignoran); activa el plan, asigna sus perfiles y registra el pago simulado.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        if not has_custom_permission(request.user, 'suscripciones.suscribirse', 'todos'):
            return Response({"error": "No tenés permiso para suscribirte."}, status=status.HTTP_403_FORBIDDEN)
        plan_id = request.data.get('plan_id') or request.data.get('plan')
        plan = Plan.objects.filter(id=plan_id, is_active=True).first()
        if not plan:
            return Response({"error": "Plan inválido o no disponible."}, status=status.HTTP_400_BAD_REQUEST)
        # Pago simulado: no se valida ni guarda ningún dato de tarjeta.
        sub = subs_service.activate_plan(request.user, plan, by=request.user)
        return Response(SubscriptionSerializer(sub).data, status=status.HTTP_200_OK)


class CancelMySubscriptionView(APIView):
    """Cancela la suscripción propia (se revoca al fin del período)."""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        if not has_custom_permission(request.user, 'suscripciones.suscribirse', 'todos'):
            return Response({"error": "No tenés permiso para gestionar tu suscripción."}, status=status.HTTP_403_FORBIDDEN)
        sub = subs_service.cancel_plan(request.user, by=request.user)
        if not sub:
            return Response({"error": "No tenés una suscripción activa."}, status=status.HTTP_400_BAD_REQUEST)
        return Response(SubscriptionSerializer(sub).data, status=status.HTTP_200_OK)


class PaymentHistoryView(generics.ListAPIView):
    """Historial de pagos simulados. Propio por defecto; admin ve todos."""
    serializer_class = PaymentSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = Payment.objects.select_related('user', 'plan').order_by('-created_at')
        if has_custom_permission(self.request.user, 'gestion.gestionar_suscripciones', 'todos'):
            return qs
        return qs.filter(user=self.request.user)


class AdminSubscriptionListView(generics.ListAPIView):
    """Listado de todas las suscripciones. Requiere 'gestion.gestionar_suscripciones'."""
    serializer_class = SubscriptionSerializer
    permission_classes = [HasDynamicPermission]
    required_permission = 'gestion.gestionar_suscripciones'
    required_scope = 'todos'
    queryset = Subscription.objects.select_related('user', 'current_plan').order_by('-updated_at')


class AdminSubscriptionActionView(APIView):
    """
    Administrar la suscripción de un usuario (activar un plan o cancelar).
    Requiere 'gestion.gestionar_suscripciones'.
    """
    permission_classes = [HasDynamicPermission]
    required_permission = 'gestion.gestionar_suscripciones'
    required_scope = 'todos'

    def post(self, request, user_id):
        from django.contrib.auth.models import User
        target = User.objects.filter(id=user_id).first()
        if not target:
            return Response({"error": "Usuario no encontrado."}, status=status.HTTP_404_NOT_FOUND)
        action_name = request.data.get('action')
        if action_name == 'activate':
            plan = Plan.objects.filter(id=request.data.get('plan_id')).first()
            if not plan:
                return Response({"error": "Plan inválido."}, status=status.HTTP_400_BAD_REQUEST)
            sub = subs_service.activate_plan(target, plan, by=request.user, register_payment=False)
        elif action_name == 'cancel':
            sub = subs_service.cancel_plan(target, by=request.user, immediate=True)
            if not sub:
                return Response({"error": "El usuario no tiene suscripción."}, status=status.HTTP_400_BAD_REQUEST)
        else:
            return Response({"error": "action debe ser 'activate' o 'cancel'."}, status=status.HTTP_400_BAD_REQUEST)
        return Response(SubscriptionSerializer(sub).data, status=status.HTTP_200_OK)


# ============================================================
# MercadoPago Checkout Pro
# ============================================================

class MercadoPagoPreferenceView(APIView):
    """
    Crea el pedido y la preferencia de MercadoPago en un solo paso.
    - Valida stock y cupón (igual que OrderCreateSerializer).
    - Descuenta stock y crea el Order con status='pending_payment'.
    - Llama a MP SDK para crear la preferencia.
    - Devuelve { order_id, init_point, sandbox_init_point }.
    El frontend redirige al init_point (o sandbox_init_point en testing).
    """
    permission_classes = [HasDynamicPermission]
    required_permission = 'carrito.checkout'
    required_scope = 'propios'

    def post(self, request):
        from django.conf import settings as django_settings
        import mercadopago

        user = request.user
        raw_shipping = request.data.get('shipping', {})
        delivery_type = request.data.get('delivery_type', 'standard')

        if delivery_type not in ('standard', 'express'):
            return Response({"error": "Tipo de entrega inválido."}, status=status.HTTP_400_BAD_REQUEST)

        from rest_framework import serializers as drf_serializers
        try:
            shipping_data = validate_shipping_fields(raw_shipping)
        except drf_serializers.ValidationError as exc:
            field_errors = exc.detail.get('shipping', exc.detail)
            return Response({"error": "Datos de envío inválidos.", "shipping": field_errors}, status=status.HTTP_400_BAD_REQUEST)

        shipping_cost = 4000 if delivery_type == 'express' else 0

        cart = Cart.objects.filter(user=user).prefetch_related('items__product').first()
        if not cart or not cart.items.exists():
            return Response({"error": "El carrito está vacío."}, status=status.HTTP_400_BAD_REQUEST)

        # Validación de stock
        stock_errors = []
        for item in cart.items.all():
            p = item.product
            if not p.is_active:
                stock_errors.append(f"{p.name} ya no está disponible.")
            elif p.stock < item.quantity:
                stock_errors.append(f"Stock insuficiente para {p.name}. Disponible: {p.stock}.")
        if stock_errors:
            return Response({"error": " ".join(stock_errors)}, status=status.HTTP_400_BAD_REQUEST)

        # Validación del cupón
        if cart.coupon:
            coupon_error = cart.coupon.validate_for_user(user)
            if coupon_error:
                return Response({"error": coupon_error}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            product_ids = list(cart.items.values_list('product_id', flat=True))
            products = {p.id: p for p in Product.objects.select_for_update().filter(id__in=product_ids)}

            # Revalidar stock con lock
            for item in cart.items.all():
                p = products[item.product_id]
                if not p.is_active or p.stock < item.quantity:
                    return Response(
                        {"error": f"Stock insuficiente para {p.name}. Disponible: {p.stock}."},
                        status=status.HTTP_400_BAD_REQUEST
                    )

            order = Order.objects.create(
                user=user,
                total=0,
                status='pending_payment',
                checkout_payment_method='mercadopago',
                shipping_cost=shipping_cost,
                shipping_data={**shipping_data, 'delivery_type': delivery_type},
            )
            subtotal = 0

            from catalog.models import StockMovement
            mp_items = []
            for item in cart.items.all():
                p = products[item.product_id]
                p.stock -= item.quantity
                p.save(update_fields=['stock'])
                StockMovement.objects.create(
                    product=p, change=-item.quantity, reason='sale',
                    resulting_stock=p.stock, user=user,
                )
                price = float(p.price)
                subtotal += price * item.quantity
                OrderItem.objects.create(order=order, product=p, quantity=item.quantity, price_at_purchase=p.price)
                mp_items.append({
                    "id": str(p.id),
                    "title": p.name[:256],
                    "quantity": item.quantity,
                    "unit_price": price,
                    "currency_id": "ARS",
                })

            # Ítem de envío express
            if shipping_cost:
                mp_items.append({
                    "id": "shipping_express",
                    "title": "Envío Express",
                    "quantity": 1,
                    "unit_price": float(shipping_cost),
                    "currency_id": "ARS",
                })

            # Aplicar cupón
            discount = 0
            if cart.coupon:
                coupon = Coupon.objects.select_for_update().get(id=cart.coupon_id)
                err = coupon.validate_for_user(user)
                if err:
                    raise Exception(err)
                discount = float(coupon.compute_discount(subtotal))
                coupon.times_used += 1
                coupon.save(update_fields=['times_used'])
                CouponRedemption.objects.create(coupon=coupon, user=user, order=order)
                order.coupon = coupon

            order.total = subtotal - discount + shipping_cost
            order.discount_amount = discount
            order.save()

            # Vaciar carrito
            cart.items.all().delete()
            cart.coupon = None
            cart.save(update_fields=['coupon'])

        # Crear preferencia en MercadoPago
        import os as _os
        frontend_url = _os.environ.get('FRONTEND_URL', 'https://craftiar.me').rstrip('/')
        sdk = mercadopago.SDK(_os.environ.get('MP_ACCESS_TOKEN', ''))
        use_auto_return = frontend_url.startswith("https://")
        preference_data = {
            "items": mp_items,
            "payer": {"email": user.email or f"{user.username}@craftiar.me"},
            "external_reference": str(order.id),
            "back_urls": {
                "success": f"{frontend_url}/checkout/result?status=approved&order_id={order.id}",
                "failure": f"{frontend_url}/checkout/result?status=failure&order_id={order.id}",
                "pending": f"{frontend_url}/checkout/result?status=pending&order_id={order.id}",
            },
            "notification_url": "https://craftiar.me/api/orders/mp/webhook/",
            "statement_descriptor": "Craftiar",
        }
        if use_auto_return:
            preference_data["auto_return"] = "approved"
        mp_response = sdk.preference().create(preference_data)
        pref = mp_response.get("response", {})

        if mp_response.get("status") not in (200, 201) or "id" not in pref:
            # Si MP falla, revertir el pedido y restaurar stock
            with transaction.atomic():
                for item in order.items.select_related('product'):
                    Product.objects.filter(id=item.product_id).update(
                        stock=models.F('stock') + item.quantity
                    )
                order.status = 'cancelled'
                order.save(update_fields=['status'])
            return Response(
                {"error": "No se pudo crear la preferencia de pago en MercadoPago. Intentá de nuevo."},
                status=status.HTTP_502_BAD_GATEWAY
            )

        order.mp_preference_id = pref["id"]
        order.save(update_fields=['mp_preference_id'])

        return Response({
            "order_id": order.id,
            "preference_id": pref["id"],
            "init_point": pref.get("init_point"),
            "sandbox_init_point": pref.get("sandbox_init_point"),
        }, status=status.HTTP_201_CREATED)


class MercadoPagoWebhookView(APIView):
    """
    Webhook que recibe las notificaciones de MP (IPN / webhooks).
    Verifica el pago con la API de MP y actualiza el estado del pedido.
    No requiere autenticación JWT (MP llama sin token).
    """
    permission_classes = []
    authentication_classes = []

    def post(self, request):
        from django.conf import settings as django_settings
        import mercadopago

        data = request.data
        topic = data.get('type') or request.query_params.get('topic')
        resource_id = data.get('data', {}).get('id') or request.query_params.get('id')

        if topic not in ('payment', 'merchant_order') or not resource_id:
            return Response({"status": "ignored"})

        import os as _os
        sdk = mercadopago.SDK(_os.environ.get('MP_ACCESS_TOKEN', ''))

        if topic == 'payment':
            mp_resp = sdk.payment().get(resource_id)
            payment_data = mp_resp.get("response", {})
            mp_status = payment_data.get("status")
            order_id = payment_data.get("external_reference")
            mp_payment_id = str(resource_id)
        else:
            mo_resp = sdk.merchant_order().get(resource_id)
            mo = mo_resp.get("response", {})
            order_id = mo.get("external_reference")
            payments = mo.get("payments", [])
            approved = [p for p in payments if p.get("status") == "approved"]
            if not approved:
                return Response({"status": "no_approved_payment"})
            mp_status = "approved"
            mp_payment_id = str(approved[0].get("id", ""))
            # Obtener detalle completo del pago aprobado
            mp_resp = sdk.payment().get(mp_payment_id)
            payment_data = mp_resp.get("response", {})

        if not order_id:
            return Response({"status": "no_order_ref"})

        try:
            order = Order.objects.get(id=int(order_id))
        except (Order.DoesNotExist, ValueError):
            return Response({"status": "order_not_found"}, status=status.HTTP_404_NOT_FOUND)

        # Extraer datos relevantes del pago para guardar en DB
        mp_snapshot = {
            "payment_id": mp_payment_id,
            "status": payment_data.get("status"),
            "status_detail": payment_data.get("status_detail"),
            "payment_type": payment_data.get("payment_type_id"),
            "payment_method": payment_data.get("payment_method_id"),
            "installments": payment_data.get("installments"),
            "transaction_amount": str(payment_data.get("transaction_amount", "")),
            "net_received_amount": str(payment_data.get("net_received_amount", "")),
            "fee_details": payment_data.get("fee_details", []),
            "currency_id": payment_data.get("currency_id"),
            "payer_email": payment_data.get("payer", {}).get("email"),
            "payer_id": str(payment_data.get("payer", {}).get("id", "")),
            "card_last_four": payment_data.get("card", {}).get("last_four_digits"),
            "card_holder": payment_data.get("card", {}).get("cardholder", {}).get("name"),
            "date_approved": str(payment_data.get("date_approved", "")),
            "date_created": str(payment_data.get("date_created", "")),
        }

        order.mp_payment_id = mp_payment_id
        order.mp_payment_data = mp_snapshot

        if mp_status == "approved" and order.status == "pending_payment":
            order.status = "pending"
            order.mp_paid_at = timezone.now()
            order.save(update_fields=['status', 'mp_payment_id', 'mp_payment_data', 'mp_paid_at'])
            send_order_confirmation_email.delay(order.id)
        elif mp_status in ("rejected", "cancelled") and order.status == "pending_payment":
            with transaction.atomic():
                for item in order.items.select_related('product'):
                    Product.objects.filter(id=item.product_id).update(
                        stock=models.F('stock') + item.quantity
                    )
            order.status = "cancelled"
            order.save(update_fields=['status', 'mp_payment_id', 'mp_payment_data'])
        else:
            order.save(update_fields=['mp_payment_id', 'mp_payment_data'])

        return Response({"status": "ok"})


# ============================================================
# Dashboard y reportes de pedidos (Gestión Interna)
# ============================================================

def _parse_date_range(request, default_days=30):
    from datetime import timedelta, datetime
    today = timezone.now().date()
    def _parse(s, fallback):
        try:
            return datetime.strptime(s, "%Y-%m-%d").date()
        except (TypeError, ValueError):
            return fallback
    date_from = _parse(request.query_params.get('date_from'), today - timedelta(days=default_days))
    date_to = _parse(request.query_params.get('date_to'), today)
    return date_from, date_to


class DashboardView(APIView):
    """
    Métricas de ventas. Requiere 'gestion.ver_dashboard'.
    Ingresos = pedidos no cancelados. Top 10 por cantidad. Evolución diaria.
    """
    permission_classes = [HasDynamicPermission]
    required_permission = 'gestion.ver_dashboard'
    required_scope = 'todos'

    def get(self, request):
        from django.db.models import Sum, Count
        from django.db.models.functions import TruncDate
        date_from, date_to = _parse_date_range(request)

        base = Order.objects.filter(created_at__date__gte=date_from, created_at__date__lte=date_to)
        non_cancelled = base.exclude(status='cancelled')

        ingresos = non_cancelled.aggregate(s=Sum('total'))['s'] or 0
        por_estado = list(base.values('status').annotate(cantidad=Count('id')).order_by('status'))
        top = list(
            OrderItem.objects.filter(order__in=non_cancelled)
            .values('product__name')
            .annotate(cantidad=Sum('quantity'))
            .order_by('-cantidad')[:10]
        )
        evolucion = list(
            non_cancelled.annotate(dia=TruncDate('created_at'))
            .values('dia').annotate(total=Sum('total'), pedidos=Count('id')).order_by('dia')
        )
        return Response({
            'date_from': date_from, 'date_to': date_to,
            'ingresos_totales': ingresos,
            'pedidos_por_estado': por_estado,
            'productos_mas_vendidos': top,
            'evolucion_ventas': evolucion,
        })


class OrderReportView(APIView):
    """
    Reporte filtrable de pedidos (estado, rango de fechas, vendedor) con
    exportación CSV (?export=csv). Requiere 'gestion.exportar_reportes'.
    El 'vendedor' es el creador (created_by) de los productos del pedido.
    """
    permission_classes = [HasDynamicPermission]
    required_permission = 'gestion.exportar_reportes'
    required_scope = 'todos'

    def _filtered_qs(self, request):
        date_from, date_to = _parse_date_range(request, default_days=90)
        qs = Order.objects.select_related('user').filter(
            created_at__date__gte=date_from, created_at__date__lte=date_to
        )
        estado = request.query_params.get('status')
        if estado:
            qs = qs.filter(status=estado)
        vendedor = request.query_params.get('vendedor')
        if vendedor:
            qs = qs.filter(items__product__created_by_id=vendedor).distinct()
        return qs.order_by('-created_at')

    def get(self, request):
        qs = self._filtered_qs(request)
        if request.query_params.get('export') == 'csv':
            import csv
            from django.http import HttpResponse
            resp = HttpResponse(content_type='text/csv')
            resp['Content-Disposition'] = 'attachment; filename="reporte_pedidos.csv"'
            writer = csv.writer(resp)
            writer.writerow(['ID', 'Cliente', 'Estado', 'Total', 'Descuento', 'Cupon', 'Fecha'])
            for o in qs:
                writer.writerow([o.id, o.user.username, o.get_status_display(), o.total,
                                 o.discount_amount, o.coupon.code if o.coupon_id else '', o.created_at.strftime('%Y-%m-%d %H:%M')])
            return resp
        return Response(OrderSerializer(qs, many=True).data)

