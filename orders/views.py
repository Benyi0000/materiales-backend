from rest_framework import viewsets, status, generics
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.pagination import PageNumberPagination
from django.db import transaction
from django.utils import timezone
from .models import Order, Subscription, OrderItem, Coupon, Cart, CartItem
from .serializers import (
    OrderSerializer, OrderCreateSerializer, SubscriptionSerializer,
    CouponSerializer, CartSerializer
)
from catalog.models import Product
from users.permissions import HasDynamicPermission, has_custom_permission
from .tasks import send_order_status_change_email


class OrderPagination(PageNumberPagination):
    """CA-10: historial paginado de a 10."""
    page_size = 10
    page_size_query_param = 'page_size'
    max_page_size = 50

class OrderViewSet(viewsets.ModelViewSet):
    """
    Gestión de pedidos:
    - Autenticado: Listar sus propios pedidos o ventas (alcance 'propios') (RF 1.4, 4.3).
    - Vendedor/Admin (permiso 'pedidos.ver_todos'): Listar todos los pedidos (alcance 'todos').
    - Checkout (POST): Crear pedidos desde el carrito persistente, con permiso 'carrito.checkout'.
    - Cancelación (POST /orders/{id}/cancel/): el comprador cancela su pedido Pendiente (RN-15).
    """
    pagination_class = OrderPagination

    def get_serializer_class(self):
        if self.action == 'create':
            return OrderCreateSerializer
        return OrderSerializer

    def get_permissions(self):
        if self.action == 'create':
            self.required_permission = 'carrito.checkout'
            self.required_scope = 'propios'
            return [HasDynamicPermission()]
        elif self.action == 'update_status':
            self.required_permission = 'pedidos.cambiar_estado'
            self.required_scope = 'propios'
            return [HasDynamicPermission()]
        elif self.action in ['list', 'retrieve']:
            # El usuario debe poseer al menos pedidos.ver_propios o pedidos.ver_todos
            if has_custom_permission(self.request.user, 'pedidos.ver_todos', 'propios'):
                self.required_permission = 'pedidos.ver_todos'
            else:
                self.required_permission = 'pedidos.ver_propios'
            self.required_scope = 'propios'
            return [HasDynamicPermission()]
        
        return [IsAuthenticated()]

    def get_queryset(self):
        user = self.request.user
        if not user.is_authenticated:
            return Order.objects.none()

        # Verificar si el usuario tiene permiso para ver todos los pedidos
        if has_custom_permission(user, 'pedidos.ver_todos', required_scope='todos'):
            queryset = Order.objects.all().order_by('-created_at')
        # Si tiene permisos para ver propios (o ver todos con alcance propio)
        elif has_custom_permission(user, 'pedidos.ver_propios', required_scope='propios') or has_custom_permission(user, 'pedidos.ver_todos', required_scope='propios'):
            from django.db.models import Q
            # Pedidos creados por él o pedidos con productos creados por él
            queryset = Order.objects.filter(
                Q(user=user) | Q(items__product__created_by=user)
            ).distinct().order_by('-created_at')
        else:
            # Por defecto, el comprador sólo ve sus propias compras
            queryset = Order.objects.filter(user=user).order_by('-created_at')

        # CA-10: filtro opcional por estado (?status=pending|paid|shipped|delivered|cancelled)
        status_filter = self.request.query_params.get('status')
        if status_filter:
            queryset = queryset.filter(status=status_filter)

        return queryset

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
        RF 4.2 y RF 7.3: Permite cambiar el estado de un pedido.
        Requiere el permiso dinámico 'pedidos.cambiar_estado'.
        """
        self.required_permission = 'pedidos.cambiar_estado'
        self.required_scope = 'propios'
        
        order = self.get_object()
        new_status = request.data.get('status')
        
        valid_statuses = [choice[0] for choice in Order.STATUS_CHOICES]
        if new_status not in valid_statuses:
            return Response(
                {"error": f"Estado inválido. Los estados válidos son: {', '.join(valid_statuses)}"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        old_status = order.status
        order.status = new_status
        order.save()
        
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
    ABM de cupones de descuento. Requiere permiso 'marketing.gestionar_cupones'.
    """
    queryset = Coupon.objects.all().order_by('-created_at')
    serializer_class = CouponSerializer
    permission_classes = [HasDynamicPermission]
    required_permission = 'marketing.gestionar_cupones'
    required_scope = 'todos'

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

