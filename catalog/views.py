from rest_framework import viewsets, generics, filters
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import AllowAny, IsAuthenticatedOrReadOnly
from django_filters.rest_framework import DjangoFilterBackend
from .models import Category, Product
from .serializers import CategorySerializer, ProductSerializer
from users.permissions import HasDynamicPermission

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
    queryset = Product.objects.all().order_by('name')
    serializer_class = ProductSerializer
    pagination_class = StandardResultsSetPagination
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['category', 'category__slug', 'category__name']
    search_fields = ['name', 'description', 'sku']
    ordering_fields = ['price', 'name']

    def get_permissions(self):
        # Permitir listado y detalle público para que Next.js pueda indexar vía SSR (RNF 7)
        if self.action in ['list', 'retrieve']:
            return [AllowAny()]
        
        # Determinar permiso requerido según la acción
        if self.action == 'create':
            self.required_permission = 'catalogo.crear_producto'
        elif self.action in ['update', 'partial_update']:
            # Si se actualiza sólo el stock, requerir catalogo.gestionar_stock, de lo contrario catalogo.editar_producto
            if 'stock' in self.request.data and len(self.request.data) <= 2:
                self.required_permission = 'catalogo.gestionar_stock'
            else:
                self.required_permission = 'catalogo.editar_producto'
        elif self.action == 'destroy':
            self.required_permission = 'catalogo.eliminar_producto'
        else:
            self.required_permission = 'catalogo.ver_catalogo'

        self.required_scope = 'propios'
        return [HasDynamicPermission()]

    def perform_create(self, serializer):
        # Guardar asociando el producto al usuario actual
        serializer.save(created_by=self.request.user)
