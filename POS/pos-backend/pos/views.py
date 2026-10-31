import datetime
import logging

from django.contrib.postgres.search import (
    TrigramSimilarity,
    TrigramWordSimilarity,
)
from django.db import IntegrityError
from django.db.models import Q
from django.db.models.functions import Greatest
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import status
from rest_framework.exceptions import AuthenticationFailed, ValidationError
from rest_framework.generics import ListAPIView
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .pagination import ItemPagination, TransactionPagination
from .item_search import search_items
from .payment_proof_service import (
    InvalidPaymentProofFile,
    PaymentProofConflict,
    PaymentProofNotFound,
    PaymentProofTooLarge,
    store_payment_proof,
)
from .pin_throttle import PinThrottled, check_pin_allowed, clear_pin_attempts
from .serializers import (
    ItemSerializer,
    PaymentProofUploadSerializer,
    RefundCreateSerializer,
    TransactionCreateSerializer,
    TransactionHistoryItemSerializer,
    TransactionHistorySerializer,
)
from .transaction_request import (
    TransactionBatchSyncSerializer,
    TransactionRequestSerializer,
    _verify_pin,
    pin_throttle_identifier,
)
from .refund_service import (
    RefundConflict,
    RefundForbidden,
    RefundNotFound,
    RefundValidationError,
    device_code_from_transaction_id,
    persist_refund,
    refund_status_map,
)
from .transaction_service import (
    TransactionValidationError,
    persist_transaction,
)
from .transaction_sync import TransactionSyncConflict, TransactionSyncStatus
from .models import (
    Device,
    DeviceTransactionSequence,
    Item,
    RefundEvent,
    TransactionDetail,
    Transactions,
    Worker,
)
from .device_authentication import (
    DEVICE_TOKEN_RENEWAL_MAX_AGE_SECONDS,
    DeviceTokenAuthentication,
    catat_pendaftaran,
    create_device_token,
    get_device_from_token,
)
from .device_registration import (
    DeviceRegistrationSerializer,
    InvalidDeviceRegistrationToken,
    NoDeviceCodeAvailable,
    register_device,
    validate_device_registration_token,
)
from .device_sequence import (
    DeviceSequenceError,
    DeviceSequenceNotFound,
    allocate_transaction_sequence,
)
from .worker_verification import daftar_offline, describe_pin_format
from .nota_tinjau import jawaban as jawaban_tahan
from .nota_tinjau import sudah_ditahan, sync_id_dari
from .kunci_pin import ambil_pin_penyetuju, kunci_publik
from .nota_tinjau import tahan as tahan_nota
from .transaction_request import verifikasi_penyetuju
from .models import NotaPersetujuan


def catat_persetujuan(sync_id, approver_id):
    """Simpan siapa yang menyetujui harga di bawah floor untuk nota ini."""
    if sync_id and approver_id:
        NotaPersetujuan.objects.get_or_create(sync_id=sync_id, defaults={"approver_worker_id": approver_id})
from . import data_master, pengaturan
from rest_framework import serializers as drf_serializers


logger = logging.getLogger(__name__)


def _flatten_error_message(errors):
    """Turn DRF validation errors into one readable line."""
    if isinstance(errors, dict):
        for field_name, nested in errors.items():
            return f"{field_name}: {_flatten_error_message(nested)}"

        return "Isi nota tidak valid."

    if isinstance(errors, list):
        if not errors:
            return "Isi nota tidak valid."

        return _flatten_error_message(errors[0])

    return str(errors)


def _log_sync_rejection(device_code, sync_id, reason):
    """Structured server-side trace of a rejected sync.

    Nota yang ditolak hanya hidup di antrean device; log ini memberi sisi server
    sebuah jejak tanpa menambah tabel (keputusan 2026-09-26).
    """
    logger.warning(
        "Batch sync rejection device=%s sync_id=%s reason=%s",
        device_code,
        sync_id,
        reason,
    )


def _sync_queued_transaction(queued_transaction, worker_id, device_code, lewati_floor=False):
    """Persist one queued transaction and report its own outcome.

    Satu batch tidak boleh gagal seluruhnya karena satu nota ditolak: antrean
    offline perlu hasil per transaksi agar setiap nota bisa ditandai tersinkron,
    ditolak (dengan alasan), atau konflik.
    """
    sync_id = queued_transaction.get("sync_id")
    transaction_id = queued_transaction.get("transaction_id")

    transaction_serializer = TransactionCreateSerializer(
        data=queued_transaction
    )

    if not transaction_serializer.is_valid():
        reason = _flatten_error_message(transaction_serializer.errors)
        _log_sync_rejection(device_code, sync_id, reason)

        return {
            "sync_id": sync_id,
            "transaction_id": transaction_id,
            "status": "rejected",
            "detail": reason,
        }

    try:
        if lewati_floor:
            sync_status, transaction_record = persist_transaction(
                transaction_serializer.validated_data,
                worker_id,
                device_code,
                lewati_floor=True,
            )
        else:
            sync_status, transaction_record = persist_transaction(
                transaction_serializer.validated_data,
                worker_id,
                device_code,
            )
    except TransactionSyncConflict as exc:
        _log_sync_rejection(device_code, sync_id, exc)

        return {
            "sync_id": sync_id,
            "transaction_id": transaction_id,
            "status": "conflict",
            "detail": str(exc),
        }
    except TransactionValidationError as exc:
        _log_sync_rejection(device_code, sync_id, exc)

        return {
            "sync_id": sync_id,
            "transaction_id": transaction_id,
            "status": "rejected",
            "detail": str(exc),
        }
    except IntegrityError as exc:
        # Kegagalan DB yang tak terduga tidak boleh menggagalkan seluruh batch
        # ataupun membocorkan detail internal ke klien.
        _log_sync_rejection(device_code, sync_id, exc)

        return {
            "sync_id": sync_id,
            "transaction_id": transaction_id,
            "status": "rejected",
            "detail": "Nota tidak dapat disimpan.",
        }

    return {
        "sync_id": str(transaction_record.sync_id),
        "transaction_id": transaction_record.transaction_id,
        "status": sync_status.value,
        "detail": None,
    }


class HealthAPIView(APIView):
    """Bukti server kasir hidup, tanpa data apa pun (dipakai tablet untuk
    memeriksa sambungan, termasuk sebelum tablet terdaftar)."""

    authentication_classes = []
    permission_classes = []

    def get(self, request):
        # server_time: tablet membandingkannya dengan jamnya sendiri dan
        # memperingatkan kasir kalau jam tablet salah (nota bertanggal salah).
        # pembulatan_diskon: kelipatan pembulatan diskon persen di keranjang (bukan rahasia).
        return Response({"status": "ok", "server_time": timezone.now().isoformat(),
                         "pembulatan_diskon": pengaturan.angka("pembulatan_diskon", 100)})


class ItemListAPIView(ListAPIView):
    """Daftar barang untuk kasir. Wajib tablet terdaftar (17 Okt 2026):
    isinya harga, floor, dan stok, jadi tidak boleh terbaca perangkat asing."""

    authentication_classes = [DeviceTokenAuthentication]
    permission_classes = [IsAuthenticated]
    serializer_class = ItemSerializer
    pagination_class = ItemPagination

    def get_serializer(self, *args, **kwargs):
        # Alias hanya untuk item di halaman ini: satu query kecil per halaman.
        if args and kwargs.get("many"):
            # Stok barang yang sedang dihitung (tugas hitung buta) tidak dikirim.
            items = data_master.tutup_stok(list(args[0]))
            args = (items,) + args[1:]
            kwargs["context"] = {**self.get_serializer_context(),
                                 "alias": data_master.alias_untuk({i.item_name for i in items})}
        return super().get_serializer(*args, **kwargs)

    def get_queryset(self):
        queryset = data_master.saring(
            Item.objects
            .only(
                "item_name",
                "variant",
                "max_price",
                "min_price",
                "in_stock",
                "stock_alert",
            )
        )

        search = self.request.query_params.get("search", "").strip()

        if search:
            queryset = (
                queryset
                .annotate(
                    name_similarity=TrigramSimilarity(
                        "item_name",
                        search,
                    ),
                    variant_similarity=TrigramSimilarity(
                        "variant",
                        search,
                    ),
                    name_word_similarity=TrigramWordSimilarity(
                        search,
                        "item_name",
                    ),
                    variant_word_similarity=TrigramWordSimilarity(
                        search,
                        "variant",
                    ),
                )
                .annotate(
                    similarity=Greatest(
                        "name_similarity",
                        "variant_similarity",
                        "name_word_similarity",
                        "variant_word_similarity",
                    )
                )
                .filter(
                    Q(item_name__icontains=search)
                    | Q(variant__icontains=search)
                    | Q(similarity__gte=0.3)
                )
                .order_by(
                    "-similarity",
                    "item_name",
                    "variant",
                )
            )

            # Pass the fully filtered legacy queryset so SQL failures fall back
            # to the same search rather than returning the unfiltered catalog.
            alias_items = search_items(search, queryset)
            if alias_items is not None:
                return alias_items
        else:
            # Urutan alami (17 Okt 2026): varian "2 MM" sebelum "10 MM", sama
            # dengan master. Katalog utuh hanya ±3.000 baris, jadi diurutkan
            # di Python lalu dipotong per halaman oleh paginator.
            return data_master.urut_alami(queryset)

        return queryset

class TransactionCreateAPIView(APIView):
    """Penjualan online: klien wajib teridentifikasi per device/tab.

    Keputusan 2026-09-26: tanpa device token tidak ada dasar membatasi
    "transaksi kasir lain/tab lain" per device, jadi device auth diwajibkan
    di sini — bukan hanya di batch sync.
    """

    authentication_classes = [DeviceTokenAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        # Nota final di tablet (`final_lokal`) yang gagal diperiksa tidak
        # ditolak, tetapi ditahan untuk ACC pemilik (pos/nota_tinjau.py).
        data = request.data if isinstance(request.data, dict) else {}
        # final_lokal: nota sudah final di tablet. minta_acc: kasir memilih
        # "tidak ada penyetuju, kirim untuk ACC pemilik" untuk harga di bawah floor.
        final_lokal = data.get("final_lokal") is True or data.get("minta_acc") is True
        mentah = data.get("transaction") if isinstance(data.get("transaction"), dict) else None
        perangkat = getattr(request.user, "device", None)
        kode_perangkat = perangkat.device_code if perangkat else None

        ditahan = sudah_ditahan(sync_id_dari(mentah))
        if ditahan is not None:
            return Response(jawaban_tahan(ditahan), status=status.HTTP_201_CREATED)

        # PIN penyetuju yang dikunci tablet (pos/kunci_pin.py) dibuka di sini.
        if isinstance(request.data, dict) and request.data.get("approver_pin_sandi"):
            request.data["approver_pin"] = ambil_pin_penyetuju(request.data)

        serializer = TransactionRequestSerializer(
            data=request.data,
            context={
                "pin_throttle_identifier": pin_throttle_identifier(request),
            },
        )

        try:
            serializer.is_valid(raise_exception=True)
        except drf_serializers.ValidationError as exc:
            if final_lokal and mentah is not None:
                nota = tahan_nota(mentah, _flatten_error_message(exc.detail), kode_perangkat)
                if nota is not None:
                    return Response(jawaban_tahan(nota), status=status.HTTP_201_CREATED)
            raise
        except PinThrottled as exc:
            # Throttle terjadi saat validasi PIN; klien dapat 429 + Retry-After
            # agar UI bisa menampilkan "tunggu X detik" (keputusan 2026-09-26).
            response = Response(
                {"detail": str(exc)},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )
            response["Retry-After"] = exc.retry_after_seconds
            return response

        transaction_data = serializer.validated_data["transaction"]
        worker_id = serializer.validated_data["worker_id"]
        approver_id = serializer.validated_data.get("approver_id")
        # Produksi selalu terautentikasi (device auth wajib); getattr hanya
        # mengakomodasi jalur uji yang menonaktifkan authentication_classes.
        device_code = getattr(request.user, "device", None)
        device_code = device_code.device_code if device_code else None

        try:
            if approver_id:
                sync_status, transaction_record = persist_transaction(
                    transaction_data,
                    worker_id,
                    device_code,
                    lewati_floor=True,
                )
                catat_persetujuan(transaction_record.sync_id, approver_id)
            else:
                sync_status, transaction_record = persist_transaction(
                    transaction_data,
                    worker_id,
                    device_code,
                )
        except TransactionSyncConflict as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_409_CONFLICT,
            )
        except TransactionValidationError as exc:
            if final_lokal and mentah is not None:
                nota = tahan_nota(mentah, str(exc), device_code, worker_id)
                if nota is not None:
                    return Response(jawaban_tahan(nota), status=status.HTTP_201_CREATED)
            # Domain rejections are client-fixable, so they map to 400
            # with the rejection reason instead of an opaque 500.
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        response_status = (
            status.HTTP_201_CREATED
            if sync_status is TransactionSyncStatus.NEW
            else status.HTTP_200_OK
        )

        return Response(
            {
                "status": sync_status.value,
                "sync_id": str(transaction_record.sync_id),
                "transaction_id": transaction_record.transaction_id,
            },
            status=response_status,
        )

def _parse_history_date(value):
    """Ubah `?date=YYYY-MM-DD` menjadi satu tanggal lokal, atau None.

    Riwayat nota dipakai per hari (permintaan pemilik, 2026-09-30: tampilkan
    hari ini dulu, hari lain lewat pencarian kalender), dan hari yang dimaksud
    kasir adalah hari **lokal toko**, bukan UTC. `transaction_time` disimpan
    UTC (USE_TZ), jadi batas harinya harus dikonversi lebih dulu — tanpa itu
    penjualan pukul 07.00 WIB terhitung sebagai hari sebelumnya.

    Format yang salah dibalas 400, bukan diabaikan: filter tanggal yang diam-diam
    tidak jalan membuat kasir melihat nota hari lain dan mengira riwayatnya
    bocor.
    """
    if value is None:
        return None

    text = value.strip()

    if not text:
        return None

    try:
        return datetime.date.fromisoformat(text)
    except ValueError:
        raise ValidationError(
            {"detail": "Parameter date harus berformat YYYY-MM-DD."}
        )


def _person_throttle_identifier(request):
    """Bucket throttle PIN orang (kasir), terpisah dari PIN checkout."""
    return f"{pin_throttle_identifier(request)}:person"


def _verify_person_pin(request):
    """PIN kasir dari header `X-Approver-PIN` -> worker_id.

    Dipakai gerbang `scope=all` dan aksi refund (keputusan pemilik 2026-10-05:
    verifikasi PIN; refund hanya oleh kasir nota asal). Device token
    membuktikan perangkat, PIN ini membuktikan orangnya. PIN salah dihitung
    throttle; PIN yang lolos membersihkan hitungan.
    """
    pin = (request.headers.get("X-Approver-PIN") or "").strip()

    if not pin:
        raise ValidationError(
            {"detail": "PIN kasir (X-Approver-PIN) wajib untuk aksi ini."}
        )

    try:
        return _verify_pin(pin, _person_throttle_identifier(request))
    except PinThrottled:
        raise
    except drf_serializers.ValidationError as exc:
        raise ValidationError({"detail": "PIN kasir tidak cocok."}) from exc


def _transaction_history_queryset(
    device_code,
    on_date=None,
    scope_all=False,
    refund_status=None,
):
    """Nota milik device/tab ini saja (keputusan visibilitas 2026-09-26).

    Scoping memakai prefix nomor nota `Txx-` milik device pemanggil. Prefix
    itu bukan nilai klien yang dipercaya mentah-mentah: saat penulisan,
    _validate_transaction_id_device memastikan hanya device pemilik prefix
    yang bisa menyimpan nota berprefix tersebut, sehingga filter baca ini
    tidak bisa dipalsukan oleh device lain.

    Nota tanpa nomor (offline, kehabisan jatah nomor) sengaja tidak
    ditampilkan: tanpa kolom device pada transactions, kepemilikannya belum
    bisa diverifikasi (lihat OFFLINE_SYNC_DESIGN.md KEPUTUSAN-4).

    `on_date` membatasi ke satu hari lokal; None berarti tidak dibatasi.
    `refund_status` menyaring nota yang status refund agregatnya persis
    tertentu (lihat refund_status_map) supaya pemilik bisa meninjau semua
    void/retur/refund satu hari tanpa membuka nota satu per satu.
    """
    queryset = Transactions.objects.select_related("worker")

    if not scope_all:
        queryset = queryset.filter(
            transaction_id__startswith=f"{device_code}-"
        )

    if on_date is not None:
        start = timezone.make_aware(
            datetime.datetime.combine(on_date, datetime.time.min),
            timezone.get_current_timezone(),
        )

        queryset = queryset.filter(
            transaction_time__gte=start,
            transaction_time__lt=start + datetime.timedelta(days=1),
        )

    if refund_status is not None:
        voided = RefundEvent.objects.filter(
            event_type="void",
        ).values("sync_id")
        money_back = RefundEvent.objects.filter(
            event_type="refund",
        ).values("sync_id")
        goods_back = RefundEvent.objects.filter(
            event_type="return",
        ).values("sync_id")

        if refund_status == "none":
            queryset = queryset.exclude(
                sync_id__in=RefundEvent.objects.values("sync_id")
            )
        elif refund_status == "void":
            queryset = queryset.filter(sync_id__in=voided)
        elif refund_status == "refunded":
            queryset = (
                queryset.filter(sync_id__in=money_back)
                .exclude(sync_id__in=voided)
            )
        else:  # returned: barang kembali, belum ada uang keluar
            queryset = (
                queryset.filter(sync_id__in=goods_back)
                .exclude(sync_id__in=voided)
                .exclude(sync_id__in=money_back)
            )

    return queryset.order_by("-transaction_time", "-transaction_id")


def _parse_history_scope(value):
    """`?scope=self|all`; selain itu dibalas 400 (filter diam-diam tidak jalan
    membuat kasir melihat nota yang bukan haknya atau sebaliknya)."""
    text = (value or "self").strip().lower()

    if text in ("self", "all"):
        return text == "all"

    raise ValidationError(
        {"detail": "Parameter scope harus self atau all."}
    )


REFUND_STATUS_FILTERS = ("none", "void", "returned", "refunded")


def _parse_history_refund_status(value):
    """`?refund_status=none|void|returned|refunded`; kosong = tanpa filter."""
    text = (value or "").strip().lower()

    if not text:
        return None

    if text in REFUND_STATUS_FILTERS:
        return text

    raise ValidationError(
        {
            "detail": (
                "Parameter refund_status harus none, void, returned, "
                "atau refunded."
            )
        }
    )


class TransactionHistoryListAPIView(ListAPIView):
    """Riwayat nota: daftar ringkas, terbaru dulu.

    Param opsional `date` (YYYY-MM-DD) membatasi ke satu hari lokal.
    Param `scope=self` (bawaan) hanya nota device pemanggil; `scope=all`
    membuka semua device untuk kemudahan refund, tetapi wajib header
    `X-Approver-PIN` berisi PIN kasir yang valid (keputusan pemilik
    2026-10-05). Tiap baris membawa `device_code` (turunan prefix nomor
    nota; null untuk nota tanpa nomor) dan `refund_status`.
    """

    authentication_classes = [DeviceTokenAuthentication]
    permission_classes = [IsAuthenticated]
    serializer_class = TransactionHistorySerializer
    pagination_class = TransactionPagination

    def get_queryset(self):
        scope_all = _parse_history_scope(
            self.request.query_params.get("scope")
        )

        if scope_all:
            _verify_person_pin(self.request)

        return _transaction_history_queryset(
            self.request.user.device.device_code,
            on_date=_parse_history_date(self.request.query_params.get("date")),
            scope_all=scope_all,
            refund_status=_parse_history_refund_status(
                self.request.query_params.get("refund_status")
            ),
        )

    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        results = response.data.get("results", [])
        statuses = refund_status_map(
            [row["sync_id"] for row in results]
        )

        for row in results:
            row["device_code"] = device_code_from_transaction_id(
                row.get("transaction_id")
            )
            row["refund_status"] = statuses.get(str(row["sync_id"]), "none")

        return response


class TransactionHistoryDetailAPIView(APIView):
    """Satu nota beserta daftar barangnya untuk tampilan riwayat."""

    authentication_classes = [DeviceTokenAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request, transaction_id):
        device_code = request.user.device.device_code
        scope_all = False

        # Nota device lain dibalas 404 — bukan 403 — supaya keberadaan nota
        # tab lain tidak bocor dari endpoint baca ini. Pengecualian: PIN
        # kasir lewat header membuka satu nota lintas device untuk refund.
        if not transaction_id.startswith(f"{device_code}-"):
            _verify_person_pin(request)
            scope_all = True

        transaction_record = (
            _transaction_history_queryset(device_code, scope_all=scope_all)
            .filter(transaction_id=transaction_id)
            .first()
        )

        if transaction_record is None:
            return Response(
                {"detail": "Nota tidak ditemukan."},
                status=status.HTTP_404_NOT_FOUND,
            )

        response_data = dict(
            TransactionHistorySerializer(transaction_record).data
        )
        response_data["device_code"] = device_code_from_transaction_id(
            transaction_record.transaction_id
        )
        response_data["refund_status"] = refund_status_map(
            [transaction_record.sync_id]
        ).get(str(transaction_record.sync_id), "none")
        response_data["items"] = TransactionHistoryItemSerializer(
            TransactionDetail.objects
            .filter(sync_id=transaction_record.sync_id)
            .order_by("item_name", "variant"),
            many=True,
        ).data
        response_data["refunds"] = _refund_events_payload(
            transaction_record.sync_id,
            note_worker_id=transaction_record.worker_id,
            note_transaction_id=transaction_record.transaction_id,
        )

        return Response(response_data)


def _refund_events_payload(
    sync_id,
    note_worker_id=None,
    note_transaction_id=None,
):
    """Daftar event refund nota untuk tampilan detail riwayat.

    `self_approval` menandai approver yang sama dengan kasir nota asal, dan
    `cross_device` menandai event yang dicatat dari device lain. Keduanya
    turunan dari data yang sudah tersimpan (tanpa kolom/DDL baru) supaya
    pemilik bisa menelusuri siapa menyetujui apa dan dari mana.
    """
    from .models import RefundEventDetail

    payload = []
    note_device_code = device_code_from_transaction_id(note_transaction_id)

    events = (
        RefundEvent.objects
        .filter(sync_id=sync_id)
        .order_by("created_at", "refund_id")
    )

    approver_names = {
        worker.id: worker.name
        for worker in Worker.objects.filter(
            id__in={event.approver_worker_id for event in events}
        ).only("id", "name")
    }

    for event in events:
        payload.append({
            "refund_id": str(event.refund_id),
            "event_type": event.event_type,
            "reason": event.reason,
            "refund_method": event.refund_method,
            "refund_amount": event.refund_amount,
            "approver_worker_id": event.approver_worker_id,
            "approver_name": approver_names.get(
                event.approver_worker_id, ""
            ),
            "self_approval": (
                note_worker_id is not None
                and event.approver_worker_id == note_worker_id
            ),
            "cross_device": bool(
                note_device_code
                and event.device_code
                and note_device_code != event.device_code
            ),
            "device_code": event.device_code,
            "created_at": event.created_at.isoformat(),
            "items": [
                {
                    "item_name": detail.item_name,
                    "variant": detail.variant,
                    "quantity": detail.quantity,
                    "unit_price": detail.unit_price,
                    "subtotal": detail.subtotal,
                }
                for detail in RefundEventDetail.objects.filter(
                    refund_id=event.refund_id
                ).order_by("item_name", "variant")
            ],
        })

    return payload


class RefundCreateAPIView(APIView):
    """Catat satu event void/return/refund atas nota yang sudah tersimpan.

    Aturan 2026-10-06 yang direvisi keputusan pemilik (lihat REFUND_DESIGN.md
    §6): approver wajib kasir nota asal — PIN sah milik kasir lain ditolak
    403, termasuk saat kasir asal sudah pulang (butuh peran master di
    sistem). `approver_pin` write-only dan langsung dibuang. Device token
    wajib seperti endpoint tulis lain. `refund_id` ganda berisi sama =
    idempotent 200; isi beda = 409.
    """

    authentication_classes = [DeviceTokenAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = RefundCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            worker_id = _verify_pin(
                serializer.validated_data["approver_pin"],
                f"{pin_throttle_identifier(request)}:refund",
            )
        except PinThrottled as exc:
            response = Response(
                {"detail": str(exc)},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )
            response["Retry-After"] = exc.retry_after_seconds
            return response
        except drf_serializers.ValidationError:
            return Response(
                {"detail": "PIN kasir tidak cocok."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        device = getattr(request.user, "device", None)
        device_code = device.device_code if device else None

        note_worker_id = (
            Transactions.objects
            .filter(sync_id=serializer.validated_data["sync_id"])
            .values_list("worker_id", flat=True)
            .first()
        )

        try:
            event, created = persist_refund(
                serializer.validated_data,
                worker_id,
                device_code,
            )
        except RefundNotFound as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_404_NOT_FOUND,
            )
        except RefundForbidden as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_403_FORBIDDEN,
            )
        except RefundConflict as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_409_CONFLICT,
            )
        except RefundValidationError as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        note_device_code = device_code_from_transaction_id(
            event.transaction_id
        )

        return Response(
            {
                "refund_id": str(event.refund_id),
                "sync_id": str(event.sync_id),
                "transaction_id": event.transaction_id,
                "event_type": event.event_type,
                "status": "recorded" if created else "idempotent",
                # Dua penanda audit tanpa kolom baru: approver sendiri vs
                # nota dari device lain. Frontend memperingatkan kasir.
                "self_approval": event.approver_worker_id == note_worker_id,
                "cross_device": bool(
                    note_device_code
                    and event.device_code
                    and note_device_code != event.device_code
                ),
            },
            status=(
                status.HTTP_201_CREATED if created else status.HTTP_200_OK
            ),
        )


def _pin_throttled_response(exc):
    """Seluruh request ditolak 429 bila throttle sudah terkunci di awal."""
    response = Response(
        {"detail": str(exc)},
        status=status.HTTP_429_TOO_MANY_REQUESTS,
    )
    response["Retry-After"] = exc.retry_after_seconds
    return response


def _waktu_nota(nilai):
    """transaction_time mentah dari antrean -> datetime aware, atau None."""
    if not isinstance(nilai, str):
        return None

    try:
        waktu = parse_datetime(nilai.strip())
    except ValueError:
        return None

    if waktu is not None and timezone.is_naive(waktu):
        waktu = timezone.make_aware(waktu)

    return waktu


def _sync_per_nota_transactions(members, throttle_identifier, device_code):
    """Sinkronkan tiap nota dengan worker dari PIN nota itu sendiri.

    Bentuk baru envelope batch (slice 2 PIN-per-nota, 2026-10-09): PIN salah
    hanya menggagalkan nota itu (`rejected` + alasan), nota lain tetap
    diproses. PIN yang sama diulang dalam satu request diverifikasi sekali
    (verifikasi memindai seluruh tabel worker). Kunci `pin` dilepas sebelum
    payload diteruskan agar serializer transaksi tidak menolak field asing.
    Hitungan kegagalan throttle baru dihapus bila seluruh verifikasi dalam
    request berhasil.
    """
    results = []
    pin_cache = {}
    # Hasil pencocokan bcrypt per PIN, dipakai ulang untuk semua nota dengan
    # PIN yang sama; pemilihan worker tetap per nota (berdasarkan waktunya).
    match_cache = {}
    pin_locked = False
    lock_retry_after = 0
    any_pin_failure = False

    for member in members:
        queued = dict(member) if isinstance(member, dict) else {}
        sync_id = queued.get("sync_id")
        transaction_id = queued.get("transaction_id")

        if pin_locked:
            results.append({
                "sync_id": sync_id,
                "transaction_id": transaction_id,
                "status": "rejected",
                "detail": (
                    "Terlalu banyak percobaan PIN. Coba lagi dalam "
                    f"{lock_retry_after} detik."
                ),
                "retry_after_seconds": lock_retry_after,
            })
            continue

        pin = queued.pop("pin", None)
        approver_pin = ambil_pin_penyetuju(queued)
        queued.pop("approver_pin", None)
        minta_acc = queued.pop("minta_acc", False) is True
        final_lokal = queued.pop("final_lokal", False) is True or minta_acc

        ditahan = sudah_ditahan(sync_id_dari(queued))
        if ditahan is not None:
            results.append(jawaban_tahan(ditahan))
            continue

        def tahan_atau_tolak(alasan, worker=None):
            """Nota final: tahan untuk ACC pemilik. Selain itu: tolak seperti biasa."""
            if final_lokal:
                nota = tahan_nota(queued, alasan, device_code, worker)
                if nota is not None:
                    return jawaban_tahan(nota)
            _log_sync_rejection(device_code, sync_id, alasan)
            return {
                "sync_id": sync_id,
                "transaction_id": transaction_id,
                "status": "rejected",
                "detail": alasan,
            }

        if pin is None:
            any_pin_failure = True
            results.append(tahan_atau_tolak("PIN wajib untuk setiap nota."))
            continue

        waktu_nota = _waktu_nota(queued.get("transaction_time"))
        # PIN yang sama sekali tidak dikenal cukup dihitung gagal sekali per
        # request, apa pun waktu notanya (sama seperti sebelum ada riwayat PIN).
        cache_key = (pin, None) if match_cache.get(pin) == [] else (pin, waktu_nota)

        if cache_key not in pin_cache:
            try:
                pin_cache[cache_key] = ("ok", _verify_pin(
                    pin,
                    throttle_identifier,
                    clear_on_success=False,
                    waktu=waktu_nota,
                    cache=match_cache,
                ))
            except PinThrottled as exc:
                any_pin_failure = True
                pin_locked = True
                lock_retry_after = exc.retry_after_seconds
                _log_sync_rejection(device_code, sync_id, exc)
                results.append({
                    "sync_id": sync_id,
                    "transaction_id": transaction_id,
                    "status": "rejected",
                    "detail": str(exc),
                    "retry_after_seconds": exc.retry_after_seconds,
                })
                continue
            except drf_serializers.ValidationError as exc:
                any_pin_failure = True
                reason = _flatten_error_message(exc.detail)
                pin_cache[cache_key] = ("error", reason)
                if match_cache.get(pin) == []:
                    pin_cache[(pin, None)] = ("error", reason)

        kind, value = pin_cache[cache_key]

        if kind == "error":
            results.append(tahan_atau_tolak(value))
            continue

        approver_id = None
        if approver_pin:
            try:
                approver_id = verifikasi_penyetuju(
                    approver_pin, throttle_identifier, waktu=waktu_nota, cache=match_cache,
                )
            except PinThrottled as exc:
                results.append({
                    "sync_id": sync_id,
                    "transaction_id": transaction_id,
                    "status": "rejected",
                    "detail": str(exc),
                    "retry_after_seconds": exc.retry_after_seconds,
                })
                continue
            except drf_serializers.ValidationError as exc:
                any_pin_failure = True
                results.append(tahan_atau_tolak(_flatten_error_message(exc.detail), value))
                continue

        hasil = _sync_queued_transaction(queued, value, device_code, lewati_floor=bool(approver_id))
        if approver_id and hasil.get("status") == "new":
            catat_persetujuan(hasil.get("sync_id"), approver_id)
        if hasil.get("status") == "rejected" and final_lokal:
            hasil = tahan_atau_tolak(hasil.get("detail") or "Ditolak server.", value)
        results.append(hasil)

    if not any_pin_failure:
        clear_pin_attempts(throttle_identifier)

    return results


class TransactionBatchSyncAPIView(APIView):
    """Sinkronkan antrean nota offline.

    Dua envelope selama transisi (slice 2 PIN-per-nota, 2026-10-09):
    - Lama (keputusan 2026-09-26): satu PIN top-level untuk seluruh batch;
      respons memuat `worker_id` untuk kompatibilitas.
    - Baru: tiap anggota membawa `pin`-nya sendiri; worker tiap nota berasal
      dari PIN nota itu; respons hanya `{ results: [...] }` tanpa worker_id.

    Klien tidak pernah mengirim worker_id, device diidentifikasi lewat device
    token, dan nota yang ditolak tetap ada di antrean device dengan alasannya.
    """

    authentication_classes = [DeviceTokenAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        throttle_identifier = pin_throttle_identifier(request)
        serializer = TransactionBatchSyncSerializer(
            data=request.data,
            context={
                "pin_throttle_identifier": throttle_identifier,
            },
        )

        try:
            serializer.is_valid(raise_exception=True)
        except PinThrottled as exc:
            return _pin_throttled_response(exc)

        device_code = request.user.device.device_code

        if serializer.validated_data.get("batch_mode") == "per_nota":
            try:
                check_pin_allowed(throttle_identifier)
            except PinThrottled as exc:
                return _pin_throttled_response(exc)

            results = _sync_per_nota_transactions(
                serializer.validated_data["transactions"],
                throttle_identifier,
                device_code,
            )

            return Response(
                {"results": results},
                status=status.HTTP_200_OK,
            )

        worker_id = serializer.validated_data["worker_id"]

        results = [
            _sync_queued_transaction(
                queued_transaction,
                worker_id,
                device_code,
            )
            for queued_transaction in serializer.validated_data["transactions"]
        ]

        return Response(
            {
                "worker_id": worker_id,
                "results": results,
            },
            status=status.HTTP_200_OK,
        )


class DeviceRegistrationAPIView(APIView):
    def post(self, request):
        registration_token = request.headers.get(
            "X-Device-Registration-Token"
        )

        try:
            validate_device_registration_token(registration_token)
        except InvalidDeviceRegistrationToken as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        serializer = DeviceRegistrationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        if serializer.is_renewal:
            try:
                device = get_device_from_token(
                    serializer.renewal_token,
                    max_age=DEVICE_TOKEN_RENEWAL_MAX_AGE_SECONDS,
                )
            except AuthenticationFailed as exc:
                return Response(
                    {"detail": str(exc)},
                    status=status.HTTP_401_UNAUTHORIZED,
                )

            if str(device.id) != str(serializer.validated_data["device_id"]):
                return Response(
                    {"detail": "Token device tidak cocok dengan device_id."},
                    status=status.HTTP_401_UNAUTHORIZED,
                )

            response_status = status.HTTP_200_OK
        else:
            if pengaturan.baca("pendaftaran_tablet") == "tutup":
                return Response(
                    {
                        "detail": (
                            "Pendaftaran tablet baru sedang ditutup pemilik. "
                            "Minta pemilik membukanya di master (Kasir > Tablet kasir)."
                        )
                    },
                    status=status.HTTP_403_FORBIDDEN,
                )

            try:
                device = register_device()
            except NoDeviceCodeAvailable as exc:
                return Response(
                    {"detail": str(exc)},
                    status=status.HTTP_409_CONFLICT,
                )

            catat_pendaftaran(device)
            response_status = status.HTTP_201_CREATED

        return Response(
            {
                "device_id": str(device.id),
                "device_code": device.device_code,
                "device_token": create_device_token(device),
            },
            status=response_status,
        )

class DeviceTransactionSequenceAPIView(APIView):
    """Jatah nomor nota baru untuk tablet itu sendiri.

    Wajib token perangkat, dan tablet hanya boleh meminta jatah untuk dirinya
    (QA 18 Okt 2026): sebelumnya siapa pun yang tahu id tablet bisa
    menghabiskan jatah nomornya tanpa token.
    """

    authentication_classes = [DeviceTokenAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request, device_id):
        milik = getattr(request.user, "device", None)
        if milik is None or str(milik.id) != str(device_id):
            return Response(
                {"detail": "Tablet hanya boleh meminta jatah nomor nota untuk dirinya sendiri."},
                status=status.HTTP_403_FORBIDDEN,
            )
        device = get_object_or_404(Device, id=device_id)

        try:
            start_sequence, end_sequence = allocate_transaction_sequence(device)
        except DeviceSequenceNotFound as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_404_NOT_FOUND,
            )
        except DeviceSequenceError as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {
                "device_id": str(device.id),
                "device_code": device.device_code,
                "start_sequence": start_sequence,
                "end_sequence": end_sequence,
            },
            status=status.HTTP_200_OK,
        )


class PaymentProofUploadAPIView(APIView):
    """Unggah bukti transfer untuk satu nota yang sudah tersimpan.

    Sengaja endpoint terpisah, bukan field di payload transaksi: payload nota
    adalah JSON dengan kontrak idempotency `sync_id`, sementara nota bisa lahir
    saat offline dan berkasnya baru bisa dikirim setelah koneksi kembali
    (PAYMENT_PROOF_DESIGN.md §4 dan §6). Karena syarat sahnya nota bukan bukti,
    urutan yang benar adalah nota dulu, bukti menyusul.
    """

    authentication_classes = [DeviceTokenAuthentication]
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        serializer = PaymentProofUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            proof, created = store_payment_proof(
                proof_id=serializer.validated_data["proof_id"],
                sync_id=serializer.validated_data["sync_id"],
                uploaded_file=serializer.validated_data["file"],
                device_code=request.user.device.device_code,
                captured_at=serializer.validated_data.get("captured_at"),
            )
        except PaymentProofNotFound as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_404_NOT_FOUND,
            )
        except PaymentProofTooLarge as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            )
        except InvalidPaymentProofFile as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except PaymentProofConflict as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_409_CONFLICT,
            )

        return Response(
            {
                "proof_id": str(proof.proof_id),
                "sync_id": str(proof.sync_id),
                "content_type": proof.content_type,
                "byte_size": proof.byte_size,
                "sha256": proof.sha256,
                "uploaded_at": proof.uploaded_at,
            },
            status=(
                status.HTTP_201_CREATED if created else status.HTTP_200_OK
            ),
        )


class WorkerOfflineListAPIView(APIView):
    """Daftar worker untuk pencocokan PIN di perangkat (OFFLINE_SYNC_DESIGN.md §7).

    Tablet menyimpan daftar ini supaya PIN bisa dicocokkan tanpa server, dan yang
    dikirim hanya `name` + nilai PIN tersimpan apa adanya. `worker_id` tidak
    pernah keluar ke klien: identitas resmi tetap ditentukan server saat nota
    dikirim atau disinkronkan, dan `worker_id` itu juga yang dicatat di nota.

    Device token wajib. Isi daftar ini adalah bahan mentah untuk menyapu PIN 6
    digit, jadi ia tidak boleh terbaca perangkat asing di LAN yang sama.

    Isinya = SEMUA worker. Skema `worker` hanya punya `id`, `name`, `pin`, dan
    peran approver belum ada, jadi tidak ada yang bisa dikecualikan hari ini;
    desain approval nanti wajib mengecualikan approver dari daftar ini.
    """

    authentication_classes = [DeviceTokenAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        # Riwayat PIN (17 Okt 2026): satu karyawan bisa muncul lebih dari sekali
        # di sekitar pergantian PIN (PIN lama yang baru berakhir dan PIN
        # berikutnya). Siapa pemilik nota tetap diputuskan server dari waktu nota.
        workers = daftar_offline()

        return Response(
            {
                "workers": [
                    {
                        "name": name,
                        "pin_stored": pin_stored,
                        "pin_format": describe_pin_format(pin_stored),
                    }
                    for name, pin_stored in workers
                ],
                "generated_at": timezone.now().isoformat(),
                # Kunci publik untuk mengunci PIN penyetuju di antrean tablet.
                "kunci_pin": kunci_publik(),
            }
        )