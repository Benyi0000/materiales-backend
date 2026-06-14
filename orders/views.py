from rest_framework import viewsets, status, generics
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.pagination import PageNumberPagination
from django.db import transaction
from django.utils import timezone
from .models import Order, Subscription, OrderItem, Coupon, Cart, CartItem, Plan, Payment
from .serializers import (
    OrderSerializer, OrderCreateSerializer, SubscriptionSerializer,
    CouponSerializer, CartSerializer, PlanSerializer, PaymentSerializer
)
from catalog.models import Product
from users.permissions import HasDynamicPermission, has_custom_permission
from .tasks import send_order_status_change_email
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
            queryset = Order.objects.filter(user=user)
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

