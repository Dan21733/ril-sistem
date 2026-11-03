from django.urls import path

from .views import (
    HealthAPIView,
    ItemListAPIView,
    PaymentProofUploadAPIView,
    RefundCreateAPIView,
    TransactionBatchSyncAPIView,
    TransactionCreateAPIView,
    TransactionHistoryDetailAPIView,
    TransactionHistoryListAPIView,
    DeviceRegistrationAPIView,
    DeviceTransactionSequenceAPIView,
    WorkerOfflineListAPIView,
    PiutangAksiAPIView,
    PiutangAturanAPIView,
    PiutangDaftarAPIView,
    PiutangDetailAPIView,
    KasBukaAPIView,
    KasGerakAPIView,
    KasStatusAPIView,
    KasTutupAPIView,
)


urlpatterns = [
    path("kas/", KasStatusAPIView.as_view(), name="kas-status"),
    path("kas/buka/", KasBukaAPIView.as_view(), name="kas-buka"),
    path("kas/tutup/", KasTutupAPIView.as_view(), name="kas-tutup"),
    path("kas/gerak/", KasGerakAPIView.as_view(), name="kas-gerak"),
    path("piutang/aturan/", PiutangAturanAPIView.as_view(), name="piutang-aturan"),
    path("piutang/", PiutangDaftarAPIView.as_view(), name="piutang-daftar"),
    path("piutang/aksi/", PiutangAksiAPIView.as_view(), name="piutang-aksi"),
    path("piutang/<str:kunci>/", PiutangDetailAPIView.as_view(), name="piutang-detail"),
    path(
        "health/",
        HealthAPIView.as_view(),
        name="health",
    ),
    path(
        "items/",
        ItemListAPIView.as_view(),
        name="item-list",
    ),
    path(
        "transactions/",
        TransactionCreateAPIView.as_view(),
        name="transaction-create",
    ),
    path(
        "transactions/sync/",
        TransactionBatchSyncAPIView.as_view(),
        name="transaction-batch-sync",
    ),
    path(
        "transactions/history/",
        TransactionHistoryListAPIView.as_view(),
        name="transaction-history-list",
    ),
    path(
        "transactions/history/<str:transaction_id>/",
        TransactionHistoryDetailAPIView.as_view(),
        name="transaction-history-detail",
    ),
    path(
        "refunds/",
        RefundCreateAPIView.as_view(),
        name="refund-create",
    ),
    path(
        "payment-proofs/",
        PaymentProofUploadAPIView.as_view(),
        name="payment-proof-upload",
    ),
    path(
        "devices/register/",
        DeviceRegistrationAPIView.as_view(),
        name="device-register",
    ),
    path(
        "devices/<uuid:device_id>/transaction-sequence/",
        DeviceTransactionSequenceAPIView.as_view(),
        name="device-transaction-sequence",
    ),
    path(
        "workers/offline-list/",
        WorkerOfflineListAPIView.as_view(),
        name="worker-offline-list",
    ),
]