import logging
from rest_framework import viewsets, generics, filters, status
from rest_framework.decorators import action
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from django.db.models import F
from .models import Category, Product, StockMovement, Banner
from .serializers import (
    CategorySerializer, ProductSerializer, StockMovementSerializer,
    LowStockProductSerializer, BannerSerializer,
)
from users.permissions import HasDynamicPermission, has_custom_permission

logger = logging.getLogger('catalog.audit')


class LowStockReportView(APIView):
    """Productos con stock por debajo del umbral. Requiere 'gestion.ver_stock_bajo'."""
    permission_classes = [HasDynamicPermission]
    required_permission = 'gestion.ver_stock_bajo'
    required_scope = 'todos'

    def get(self, request):
        qs = Product.objects.filter(stock__lt=F('min_stock')).select_related('category').order_by('stock')
        return Response(LowStockProductSerializer(qs, many=True).data)


class StockMovementListView(generics.ListAPIView):
    """Historial de movimientos de stock (filtrable por producto). 'gestion.ver_stock_bajo'."""
    serializer_class = StockMovementSerializer
    permission_classes = [HasDynamicPermission]
    required_permission = 'gestion.ver_stock_bajo'
    required_scope = 'todos'

    def get_queryset(self):
        qs = StockMovement.objects.select_related('product', 'user').all()
        product_id = self.request.query_params.get('product')
        if product_id:
            qs = qs.filter(product_id=product_id)
        return qs


class BannerViewSet(viewsets.ModelViewSet):
    """ABM de banners del home. Requiere 'gestion.gestionar_banners'."""
    queryset = Banner.objects.all()
    serializer_class = BannerSerializer
    permission_classes = [HasDynamicPermission]
    required_permission = 'gestion.gestionar_banners'
    required_scope = 'todos'


class PublicBannerListView(generics.ListAPIView):
    """Banners activos para el home (público)."""
    serializer_class = BannerSerializer
    permission_classes = [AllowAny]
    queryset = Banner.objects.filter(is_active=True)

class StandardResultsSetPagination(PageNumberPagination):
    page_size = 12
    page_size_query_param = 'page_size'
    max_page_size = 100


class CategoryListView(generics.ListAPIView):
    """
    API pública para listar categorías jerárquicas del catálogo.
    """
    queryset = Category.objects.filter(parent__isnull=True).order_by('name')
    serializer_class = CategorySerializer
    permission_classes = [AllowAny]


class ProductViewSet(viewsets.ModelViewSet):
    """
    ABM de Productos y Catálogo.
    - Lectura pública (GET) para permitir Server-Side Rendering (SSR) en Next.js.
    - Escritura protegida por permisos dinámicos y alcances (RF 1.4).
    """
    serializer_class = ProductSerializer
    pagination_class = StandardResultsSetPagination
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['category', 'category__slug', 'category__name', 'subcategories', 'subcategories__slug', 'is_active']
    search_fields = ['name', 'description', 'sku']
    ordering_fields = ['price', 'name']

    def get_queryset(self):
        user = self.request.user
        # Si el usuario es administrador/gestor, ve todos los productos
        if user.is_authenticated and (user.is_superuser or has_custom_permission(user, 'catalogo.editar_producto')):
            return Product.objects.all().order_by('name')
        # De lo contrario, solo ve productos activos
        return Product.objects.filter(is_active=True).order_by('name')

    def get_permissions(self):
        # Permitir listado y detalle público para que Next.js pueda indexar vía SSR
        if self.action in ['list', 'retrieve']:
            return [AllowAny()]
        
        # Determinar permiso requerido según la acción
        if self.action == 'create':
            self.required_permission = 'catalogo.crear_producto'
        elif self.action in ['update', 'partial_update']:
            self.required_permission = 'catalogo.editar_producto'
        elif self.action == 'stock':
            self.required_permission = 'catalogo.gestionar_stock'
        elif self.action == 'destroy':
            self.required_permission = 'catalogo.eliminar_producto'
        elif self.action == 'semantic_search':
            self.required_permission = 'catalogo.busqueda_semantica'
        else:
            self.required_permission = 'catalogo.ver_catalogo'

        self.required_scope = 'propios'
        return [HasDynamicPermission()]

    def perform_create(self, serializer):
        # Guardar asociando el producto al usuario actual
        product = serializer.save(created_by=self.request.user)
        # Por defecto, agregamos la categoría principal a la lista de subcategorías
        if product.category:
            product.subcategories.add(product.category)
        logger.info(f"AUDIT [Creación]: El usuario {self.request.user.username} creó el producto SKU: {product.sku} ({product.name}).")

    def perform_update(self, serializer):
        old_instance = self.get_object()
        old_price = old_instance.price
        old_stock = old_instance.stock
        
        product = serializer.save()
        
        # Verificar cambios significativos para auditoría
        changes = []
        if old_price != product.price:
            changes.append(f"Precio modificado de ${old_price} a ${product.price}")
        if old_stock != product.stock:
            changes.append(f"Stock ajustado de {old_stock} a {product.stock}")
            # Registrar el movimiento de stock por ajuste manual
            StockMovement.objects.create(
                product=product, change=product.stock - old_stock, reason='adjust',
                resulting_stock=product.stock, user=self.request.user,
            )

        changes_str = ", ".join(changes) if changes else "Datos modificados generales"
        logger.info(f"AUDIT [Modificación]: El usuario {self.request.user.username} editó el producto SKU: {product.sku} ({product.name}). Detalle: {changes_str}.")

    def destroy(self, request, *args, **kwargs):
        product = self.get_object()
        # Verificar si tiene pedidos históricos usando orderitem_set
        if product.orderitem_set.exists():
            product.is_active = False
            product.save()
            logger.info(f"AUDIT [Desactivación Lógica]: El usuario {request.user.username} desactivó lógicamente el producto SKU: {product.sku} porque tiene ventas asociadas.")
            return Response(
                {"status": "Producto con ventas asociadas: se ha desactivado lógicamente para preservar históricos."},
                status=status.HTTP_200_OK
            )
        else:
            sku = product.sku
            name = product.name
            product.delete()
            logger.info(f"AUDIT [Eliminación Física]: El usuario {request.user.username} eliminó físicamente el producto SKU: {sku} ({name}).")
            return Response(
                {"status": "Producto eliminado físicamente con éxito."},
                status=status.HTTP_204_NO_CONTENT
            )

    @action(detail=True, methods=['patch'])
    def stock(self, request, pk=None):
        """
        Ajuste rápido de stock. Requiere el permiso 'catalogo.gestionar_stock'.
        Solo acepta el campo 'stock' (y un 'reason' opcional para auditoría);
        cualquier otro campo del producto exige 'catalogo.editar_producto' vía PATCH estándar.
        Ejemplo: PATCH /api/catalog/products/5/stock/  {"stock": 20, "reason": "Reposición"}
        """
        product = self.get_object()

        if 'stock' not in request.data:
            return Response({"error": "Debe proporcionar el campo 'stock'."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            new_stock = int(request.data['stock'])
        except (TypeError, ValueError):
            return Response({"error": "El campo 'stock' debe ser un número entero."}, status=status.HTTP_400_BAD_REQUEST)

        if new_stock < 0:
            return Response({"error": "El stock no puede ser negativo."}, status=status.HTTP_400_BAD_REQUEST)

        old_stock = product.stock
        product.stock = new_stock
        product.save(update_fields=['stock'])

        reason = request.data.get('reason', 'Ajuste manual de inventario')
        logger.info(f"AUDIT [Ajuste de Stock]: El usuario {request.user.username} ajustó el stock del producto SKU: {product.sku} ({product.name}) de {old_stock} a {new_stock}. Motivo: {reason}.")

        serializer = self.get_serializer(product)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=False, methods=['get'])
    def semantic_search(self, request):
        """
        Realiza una búsqueda vectorial RAG en base a similitud de significados.
        Ejemplo: GET /api/catalog/products/semantic_search/?q=pegamento fuerte
        """
        query = request.query_params.get('q', '').strip()
        if not query:
            return Response({"error": "Debe proporcionar un parámetro de búsqueda 'q'."}, status=status.HTTP_400_BAD_REQUEST)

        # 1. Vectorizar la consulta usando Google Gemini
        import os
        from django.conf import settings
        import google.generativeai as genai
        
        api_key = getattr(settings, "GOOGLE_API_KEY", "")
        if not api_key:
            api_key = os.environ.get("GOOGLE_API_KEY", "")
            
        if not api_key:
            return self._fallback_text_search(query)
            
        try:
            genai.configure(api_key=api_key)
            result = genai.embed_content(
                model="models/gemini-embedding-2",
                content=query,
                task_type="retrieval_query"
            )
            
            if not result or 'embedding' not in result:
                return self._fallback_text_search(query)
                
            query_vector = result['embedding']
            
            # 2. Búsqueda Vectorial (Si pgvector está disponible)
            from django.conf import settings
            import numpy as np
            
            db_engine = settings.DATABASES['default']['ENGINE']
            
            if 'sqlite' in db_engine:
                # Calcular distancia en memoria usando numpy (para entorno de desarrollo con SQLite)
                query_np = np.array(query_vector)
                products_list = list(Product.objects.filter(is_active=True, embedding__isnull=False))
                
                for p in products_list:
                    p_emb = np.array(p.embedding)
                    p.distance = np.linalg.norm(p_emb - query_np)
                
                # Ordenar por menor distancia
                products_list.sort(key=lambda x: x.distance)
                top_products = products_list[:10]
                
                serializer = self.get_serializer(top_products, many=True)
                return Response({
                    "results": serializer.data,
                    "search_type": "semantic",
                    "message": "Resultados basados en búsqueda de IA (Gemini / In-memory SQLite)."
                })
            else:
                try:
                    from pgvector.django import L2Distance
                    products = Product.objects.filter(is_active=True, embedding__isnull=False).order_by(L2Distance('embedding', query_vector))[:10]
                    
                    serializer = self.get_serializer(products, many=True)
                    return Response({
                        "results": serializer.data,
                        "search_type": "semantic",
                        "message": "Resultados basados en búsqueda de inteligencia artificial (Gemini)."
                    })
                except ImportError:
                    return self._fallback_text_search(query)
                
        except Exception as e:
            logger.error(f"Excepción en búsqueda semántica (Gemini): {str(e)}")
            return self._fallback_text_search(query)
            
    def _fallback_text_search(self, query):
        """Si la API de vectores falla o no hay pgvector, hacemos búsqueda tradicional de texto."""
        from django.db.models import Q
        products = Product.objects.filter(
            Q(is_active=True) & (Q(name__icontains=query) | Q(description__icontains=query))
        )[:10]
        serializer = self.get_serializer(products, many=True)
        return Response({
            "results": serializer.data,
            "search_type": "text_fallback",
            "message": "Mostrando resultados clásicos (búsqueda semántica no disponible temporalmente)."
        })


from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
from rest_framework.views import APIView
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.permissions import IsAuthenticated
from django.conf import settings

class ProductImageUploadView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request, *args, **kwargs):
        file_obj = request.FILES.get('image')
        if not file_obj:
            return Response({"error": "No se proporcionó ningún archivo de imagen."}, status=status.HTTP_400_BAD_REQUEST)
        
        # Validar formato de archivo (PNG o JPG)
        if not file_obj.name.lower().endswith(('.png', '.jpg', '.jpeg')):
            return Response({"error": "Solo se permiten imágenes PNG o JPG."}, status=status.HTTP_400_BAD_REQUEST)
        
        # Validar tamaño máximo (5 MB)
        if file_obj.size > 5 * 1024 * 1024:
            return Response({"error": "El archivo excede el tamaño máximo permitido de 5 MB."}, status=status.HTTP_400_BAD_REQUEST)
        
        # Guardar en media/products/
        path = default_storage.save(f'products/{file_obj.name}', ContentFile(file_obj.read()))
        # Construir la URL completa
        file_url = request.build_absolute_uri(settings.MEDIA_URL + path)
        return Response({"image_url": file_url}, status=status.HTTP_201_CREATED)
