from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    OrderViewSet, SubscriptionView, CartView, CartItemView, CartCouponView, CouponViewSet,
    PlanViewSet, SubscriptionCheckoutView, CancelMySubscriptionView, PaymentHistoryView,
    AdminSubscriptionListView, AdminSubscriptionActionView, DashboardView, OrderReportView,
    MercadoPagoPreferenceView, MercadoPagoWebhookView,
)

router = DefaultRouter()
router.register(r'orders', OrderViewSet, basename='orders')
router.register(r'coupons', CouponViewSet, basename='coupons')
router.register(r'plans', PlanViewSet, basename='plans')

urlpatterns = [
    path('subscription/', SubscriptionView.as_view(), name='subscription'),
    path('subscription/checkout/', SubscriptionCheckoutView.as_view(), name='subscription-checkout'),
    path('subscription/cancel/', CancelMySubscriptionView.as_view(), name='subscription-cancel'),
    path('payments/', PaymentHistoryView.as_view(), name='payments'),
    path('admin/subscriptions/', AdminSubscriptionListView.as_view(), name='admin-subscriptions'),
    path('admin/subscriptions/<int:user_id>/action/', AdminSubscriptionActionView.as_view(), name='admin-subscription-action'),
    path('gestion/dashboard/', DashboardView.as_view(), name='gestion-dashboard'),
    path('gestion/reportes/pedidos/', OrderReportView.as_view(), name='gestion-reporte-pedidos'),
    path('cart/', CartView.as_view(), name='cart-detail'),
    path('cart/items/', CartItemView.as_view(), name='cart-item-add'),
    path('cart/items/<int:product_id>/', CartItemView.as_view(), name='cart-item-detail'),
    path('cart/coupon/', CartCouponView.as_view(), name='cart-coupon'),
    path('mp/create-preference/', MercadoPagoPreferenceView.as_view(), name='mp-create-preference'),
    path('mp/webhook/', MercadoPagoWebhookView.as_view(), name='mp-webhook'),
    path('', include(router.urls)),
]
