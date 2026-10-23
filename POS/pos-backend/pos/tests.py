from datetime import datetime, timezone
import unittest
from unittest.mock import ANY, MagicMock, patch
from uuid import uuid4

import bcrypt

from django.test import override_settings
from django.contrib.auth.hashers import make_password
from django.test import SimpleTestCase, TestCase, TransactionTestCase
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, connection

from rest_framework.exceptions import AuthenticationFailed
from rest_framework.test import APIClient, APIRequestFactory, APITestCase

from .device_authentication import (
    AuthenticatedDevice,
    DEVICE_TOKEN_RENEWAL_MAX_AGE_SECONDS,
    DeviceTokenAuthentication,
    _decode_device_token,
    create_device_token,
    get_device_from_token,
)
from .pin_throttle import (
    LOCKOUT_SECONDS,
    MAX_PIN_ATTEMPTS,
    PinThrottled,
    check_pin_allowed,
    clear_pin_attempts,
    register_pin_failure,
)

from .device_registration import (
    DeviceRegistrationSerializer,
    NoDeviceCodeAvailable,
    register_device,
)
from .item_search import reset_alias_table_cache
from .models import (
    Device,
    PaymentProof,
    Worker,
    Item,
    Transactions,
    TransactionDetail,
)
from .payment_proof_service import (
    InvalidPaymentProofFile,
    PaymentProofConflict,
    PaymentProofNotFound,
    PaymentProofTooLarge,
    store_payment_proof,
)
from .payment_proof_storage import (
    MAX_PROOF_BYTES,
    build_object_key,
    detect_content_type,
    sha256_hexdigest,
)
from .serializers import (
    ItemSerializer,
    PaymentProofUploadSerializer,
    TransactionCreateSerializer,
)
from .transaction_request import (
    MAX_BATCH_SYNC_TRANSACTIONS,
    TransactionRequestSerializer,
)
from .views import (
    DeviceRegistrationAPIView,
    DeviceTransactionSequenceAPIView,
    PaymentProofUploadAPIView,
    TransactionCreateAPIView,
    WorkerOfflineListAPIView,
)
from .worker_verification import (
    InvalidWorkerCredentials,
    InvalidWorkerPinFormat,
    describe_pin_format,
    verify_worker_pin,
)
from .device_sequence import (
    DeviceSequenceError,
    DeviceSequenceNotFound,
    allocate_transaction_sequence,
)
from .transaction_id import (
    TransactionIDError,
    build_transaction_id,
)
from .transaction_sync import (
    TransactionSyncConflict,
    TransactionSyncStatus,
    resolve_transaction_request,
)

from .transaction_service import (
    TransactionValidationError,
    persist_transaction,
)

class WorkerPinVerificationTests(TestCase):
    def setUp(self):
        self.valid_pin = "".join(str(digit) for digit in range(1, 7))

    @patch("pos.worker_verification.Worker.objects")
    def test_valid_worker_and_legacy_text_pin_returns_worker_id(
        self,
        worker_objects,
    ):
        worker = Worker(
            id="W0001",
            name="Test Worker",
            pin=self.valid_pin,
        )

        worker_objects.only.return_value.iterator.return_value = [worker]

        verified_worker_id = verify_worker_pin(self.valid_pin)

        self.assertEqual(verified_worker_id, worker.id)
        self.assertIsInstance(verified_worker_id, str)
        self.assertNotEqual(verified_worker_id, self.valid_pin)
        self.assertNotIn("pin", verified_worker_id.lower())

    @patch("pos.worker_verification.Worker.objects")
    def test_valid_worker_and_encoded_pin_returns_worker_id(
        self,
        worker_objects,
    ):
        worker = Worker(
            id="W0001",
            name="Test Worker",
            pin=make_password(self.valid_pin),
        )

        worker_objects.only.return_value.iterator.return_value = [worker]

        verified_worker_id = verify_worker_pin(self.valid_pin)

        self.assertEqual(verified_worker_id, worker.id)
        self.assertIsInstance(verified_worker_id, str)
        self.assertNotEqual(verified_worker_id, self.valid_pin)
        self.assertNotIn("pin", verified_worker_id.lower())

    @patch("pos.worker_verification.Worker.objects")
    def test_invalid_pin_is_rejected_with_generic_credentials_error(
        self,
        worker_objects,
    ):
        worker = Worker(
            id="W0001",
            name="Test Worker",
            pin=make_password(self.valid_pin),
        )

        worker_objects.only.return_value.iterator.return_value = [worker]

        with self.assertRaises(InvalidWorkerCredentials) as context:
            verify_worker_pin("0" * 6)

        self.assertEqual(
            str(context.exception),
            "PIN kasir tidak cocok.",
        )

    @patch("pos.worker_verification.Worker.objects")
    def test_unknown_pin_is_rejected_with_same_generic_error(
        self,
        worker_objects,
    ):
        worker_objects.only.return_value.iterator.return_value = []

        with self.assertRaises(InvalidWorkerCredentials) as context:
            verify_worker_pin(self.valid_pin)

        self.assertEqual(
            str(context.exception),
            "PIN kasir tidak cocok.",
        )

    @patch("pos.worker_verification.Worker.objects")
    def test_valid_worker_and_raw_bcrypt_pin_returns_worker_id(
        self,
        worker_objects,
    ):
        """Isi tabel worker saat ini: hash bcrypt mentah dari sistem lama."""
        worker = Worker(
            id="ID001",
            name="Rusdi",
            pin=bcrypt.hashpw(self.valid_pin.encode("utf-8"), bcrypt.gensalt(rounds=6)).decode(
                "utf-8"
            ),
        )

        worker_objects.only.return_value.iterator.return_value = [worker]

        verified_worker_id = verify_worker_pin(self.valid_pin)

        self.assertEqual(verified_worker_id, worker.id)
        self.assertNotEqual(verified_worker_id, self.valid_pin)

    @patch("pos.worker_verification.Worker.objects")
    def test_raw_bcrypt_pin_with_wrong_pin_is_rejected(
        self,
        worker_objects,
    ):
        worker = Worker(
            id="ID001",
            name="Rusdi",
            pin=bcrypt.hashpw(self.valid_pin.encode("utf-8"), bcrypt.gensalt(rounds=6)).decode(
                "utf-8"
            ),
        )

        worker_objects.only.return_value.iterator.return_value = [worker]

        with self.assertRaises(InvalidWorkerCredentials):
            verify_worker_pin("0" * 6)

    @patch("pos.worker_verification.Worker.objects")
    def test_malformed_bcrypt_pin_is_treated_as_no_match(
        self,
        worker_objects,
    ):
        """Nilai bcrypt yang rusak tidak boleh membuat server gagal 500."""
        worker = Worker(
            id="ID001",
            name="Rusdi",
            pin="$2a$06$rusak",
        )

        worker_objects.only.return_value.iterator.return_value = [worker]

        with self.assertRaises(InvalidWorkerCredentials):
            verify_worker_pin(self.valid_pin)

    def test_invalid_pin_format_is_rejected(self):
        with self.assertRaises(InvalidWorkerPinFormat):
            verify_worker_pin(("0" * 5) + "x")


class TransactionCreateSerializerTests(SimpleTestCase):
    def setUp(self):
        self.valid_payload = {
            "sync_id": uuid4(),
            "transaction_id": "T01-000001",
            "transaction_time": datetime.now(timezone.utc),
            "item_amount": 95000,
            "shipping_amount": 10000,
            "total_amount": 105000,
            "payment_method": "cash",
            "payment_amount": 110000,
            "change_amount": 5000,
            "items": [
                {
                    "item_name": "Test Item",
                    "variant": "Default",
                    "quantity": 2,
                    "unit_price": 47500,
                    # Toko tidak memakai diskon per baris (QA 18 Okt 2026):
                    # harga nego langsung di unit_price.
                    "discount": 0,
                    "subtotal": 95000,
                }
            ],
        }

    def test_valid_transaction_is_accepted(self):
        serializer = TransactionCreateSerializer(
            data=self.valid_payload
        )

        self.assertTrue(
            serializer.is_valid(),
            serializer.errors,
        )

    def test_null_transaction_id_is_accepted(self):
        # Nota offline yang kehabisan jatah nomor device dikirim tanpa nomor.
        payload = self.valid_payload.copy()
        payload["transaction_id"] = None

        serializer = TransactionCreateSerializer(data=payload)

        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_customer_fields_are_accepted_and_normalized(self):
        payload = self.valid_payload.copy()
        payload["cust_name"] = "  Budi Santoso  "
        payload["phone_no"] = "0812-3456-7890"

        serializer = TransactionCreateSerializer(data=payload)

        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(
            serializer.validated_data["cust_name"],
            "Budi Santoso",
        )
        self.assertEqual(
            serializer.validated_data["phone_no"],
            "0812-3456-7890",
        )

    def test_missing_or_blank_customer_is_legacy_null(self):
        # Antrean offline yang disegel sebelum fitur pelanggan dan klien lama
        # tidak membawa nama; NULL berarti warisan, bukan penolakan
        # (CUSTOMER_DESIGN.md). Penjualan baru selalu membawa nama via UI.
        for payload in (
            self.valid_payload.copy(),
            {**self.valid_payload, "cust_name": "   ", "phone_no": ""},
        ):
            with self.subTest(payload=payload.get("cust_name")):
                serializer = TransactionCreateSerializer(data=payload)

                self.assertTrue(serializer.is_valid(), serializer.errors)
                self.assertIsNone(serializer.validated_data.get("cust_name"))
                self.assertIsNone(serializer.validated_data.get("phone_no"))

    def test_overlong_customer_name_is_rejected(self):
        payload = self.valid_payload.copy()
        payload["cust_name"] = "A" * 121

        serializer = TransactionCreateSerializer(data=payload)

        self.assertFalse(serializer.is_valid())
        self.assertIn("cust_name", serializer.errors)

    def test_invalid_phone_number_is_rejected(self):
        for phone_no in ("08AB12", "nota bene", "+" * 21, "1" * 21):
            with self.subTest(phone_no=phone_no):
                payload = self.valid_payload.copy()
                payload["phone_no"] = phone_no

                serializer = TransactionCreateSerializer(data=payload)

                self.assertFalse(serializer.is_valid())
                self.assertIn("phone_no", serializer.errors)

    def test_malformed_transaction_id_is_rejected(self):
        for transaction_id in (
            "T1-000001",
            "T01-00001",
            "T01-0000001",
            "X01-000001",
            "T01-00000A",
            "T01-TMP-0007",
        ):
            with self.subTest(transaction_id=transaction_id):
                payload = self.valid_payload.copy()
                payload["transaction_id"] = transaction_id

                serializer = TransactionCreateSerializer(data=payload)

                self.assertFalse(serializer.is_valid())
                self.assertIn("transaction_id", serializer.errors)

    def test_invalid_item_amount_is_rejected(self):
        payload = self.valid_payload.copy()
        payload["item_amount"] = 99999

        serializer = TransactionCreateSerializer(data=payload)

        self.assertFalse(serializer.is_valid())
        self.assertIn("item_amount", serializer.errors)

    def test_invalid_total_amount_is_rejected(self):
        payload = self.valid_payload.copy()
        payload["total_amount"] = 999999

        serializer = TransactionCreateSerializer(data=payload)

        self.assertFalse(serializer.is_valid())
        self.assertIn("total_amount", serializer.errors)

    def test_payment_less_than_total_is_rejected(self):
        payload = self.valid_payload.copy()
        payload["payment_amount"] = 100000

        serializer = TransactionCreateSerializer(data=payload)

        self.assertFalse(serializer.is_valid())
        self.assertIn("payment_amount", serializer.errors)

    def test_invalid_change_amount_is_rejected(self):
        payload = self.valid_payload.copy()
        payload["change_amount"] = 9999

        serializer = TransactionCreateSerializer(data=payload)

        self.assertFalse(serializer.is_valid())
        self.assertIn("change_amount", serializer.errors)

    def test_discount_cannot_make_subtotal_negative(self):
        payload = self.valid_payload.copy()
        payload["items"] = [
            {
                "item_name": "Test Item",
                "variant": "Default",
                "quantity": 1,
                "unit_price": 10000,
                "discount": 15000,
                "subtotal": 0,
            }
        ]

        serializer = TransactionCreateSerializer(data=payload)

        self.assertFalse(serializer.is_valid())
        self.assertIn("items", serializer.errors)

    def test_invalid_item_subtotal_is_rejected(self):
        payload = self.valid_payload.copy()
        payload["items"] = [
            {
                "item_name": "Test Item",
                "variant": "Default",
                "quantity": 2,
                "unit_price": 50000,
                "discount": 5000,
                "subtotal": 90000,
            }
        ]

        serializer = TransactionCreateSerializer(data=payload)

        self.assertFalse(serializer.is_valid())
        self.assertIn("items", serializer.errors)


class TransactionRequestSerializerTests(TestCase):
    def setUp(self):
        self.valid_transaction = {
            "sync_id": uuid4(),
            "transaction_id": "T01-000001",
            "transaction_time": datetime.now(timezone.utc),
            "item_amount": 95000,
            "shipping_amount": 10000,
            "total_amount": 105000,
            "payment_method": "cash",
            "payment_amount": 110000,
            "change_amount": 5000,
            "items": [
                {
                    "item_name": "Test Item",
                    "variant": "Default",
                    "quantity": 2,
                    "unit_price": 47500,
                    # Toko tidak memakai diskon per baris (QA 18 Okt 2026):
                    # harga nego langsung di unit_price.
                    "discount": 0,
                    "subtotal": 95000,
                }
            ],
        }

    @patch(
        "pos.transaction_request.verify_worker_pin",
        return_value="W001",
    )
    def test_valid_request_resolves_worker_id(self, mock_verify):
        payload = {
            "pin": "123456",
            "transaction": self.valid_transaction,
        }

        serializer = TransactionRequestSerializer(data=payload)

        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(
            serializer.validated_data["worker_id"],
            "W001",
        )
        mock_verify.assert_called_once()
        self.assertEqual(mock_verify.call_args.args[0], "123456")

    @patch(
        "pos.transaction_request.verify_worker_pin",
        side_effect=InvalidWorkerCredentials(
            "PIN kasir tidak cocok."
        ),
    )
    def test_invalid_pin_is_rejected(self, mock_verify):
        payload = {
            "pin": "123456",
            "transaction": self.valid_transaction,
        }

        serializer = TransactionRequestSerializer(data=payload)

        self.assertFalse(serializer.is_valid())
        self.assertIn("pin", serializer.errors)
        mock_verify.assert_called_once()
        self.assertEqual(mock_verify.call_args.args[0], "123456")

    @patch(
        "pos.transaction_request.verify_worker_pin",
        side_effect=InvalidWorkerPinFormat(
            "PIN harus tepat 6 digit."
        ),
    )
    def test_invalid_pin_format_is_rejected(self, mock_verify):
        payload = {
            "pin": "12345",
            "transaction": self.valid_transaction,
        }

        serializer = TransactionRequestSerializer(data=payload)

        self.assertFalse(serializer.is_valid())
        self.assertIn("pin", serializer.errors)

    @patch(
        "pos.transaction_request.verify_worker_pin",
        return_value="W001",
    )
    def test_worker_id_is_not_taken_from_client(self, mock_verify):
        payload = {
            "pin": "123456",
            "worker_id": "MALICIOUS-ID",
            "transaction": self.valid_transaction,
        }

        serializer = TransactionRequestSerializer(data=payload)

        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(
            serializer.validated_data["worker_id"],
            "W001",
        )

    @patch(
        "pos.transaction_request.verify_worker_pin",
        return_value="W001",
    )
    def test_pin_is_not_returned_in_serialized_output(self, mock_verify):
        payload = {
            "pin": "123456",
            "transaction": self.valid_transaction,
        }

        serializer = TransactionRequestSerializer(data=payload)

        self.assertTrue(serializer.is_valid(), serializer.errors)

        output = serializer.data

        self.assertNotIn("pin", output)


class DeviceRegistrationViewTests(TestCase):
    def _request(self, body):
        return APIRequestFactory().post(
            "/api/devices/register/",
            body,
            format="json",
            HTTP_X_DEVICE_REGISTRATION_TOKEN="bootstrap-test-token",
        )

    @patch("pos.views.validate_device_registration_token")
    @patch("pos.views.create_device_token", return_value="fresh-token")
    @patch("pos.views.get_device_from_token")
    def test_renewal_returns_same_device_without_allocating_a_new_one(
        self,
        mock_get_device,
        mock_create_token,
        mock_validate_registration_token,
    ):
        device_id = uuid4()
        device = MagicMock(id=device_id, device_code="T01")
        mock_get_device.return_value = device

        response = DeviceRegistrationAPIView.as_view()(
            self._request(
                {
                    "device_id": str(device_id),
                    "device_token": "signed-old-token",
                }
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data,
            {
                "device_id": str(device_id),
                "device_code": "T01",
                "device_token": "fresh-token",
            },
        )
        mock_validate_registration_token.assert_called_once_with(
            "bootstrap-test-token",
        )
        mock_get_device.assert_called_once_with(
            "signed-old-token",
            max_age=DEVICE_TOKEN_RENEWAL_MAX_AGE_SECONDS,
        )
        mock_create_token.assert_called_once_with(device)

    @patch("pos.views.validate_device_registration_token")
    @patch("pos.views.register_device")
    @patch("pos.views.create_device_token", return_value="fresh-token")
    def test_initial_registration_still_creates_a_device(
        self,
        mock_create_token,
        mock_register_device,
        mock_validate_registration_token,
    ):
        device_id = uuid4()
        device = MagicMock(id=device_id, device_code="T02")
        mock_register_device.return_value = device

        response = DeviceRegistrationAPIView.as_view()(self._request({}))

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["device_id"], str(device_id))
        self.assertEqual(response.data["device_code"], "T02")
        mock_validate_registration_token.assert_called_once_with(
            "bootstrap-test-token",
        )
        mock_register_device.assert_called_once_with()
        mock_create_token.assert_called_once_with(device)


class DeviceRegistrationSerializerTests(SimpleTestCase):
    def test_empty_request_is_accepted(self):
        serializer = DeviceRegistrationSerializer(data={})

        self.assertTrue(
            serializer.is_valid(),
            serializer.errors,
        )

    def test_device_code_input_is_rejected(self):
        serializer = DeviceRegistrationSerializer(
            data={"device_code": "T01"}
        )

        self.assertFalse(serializer.is_valid())
        self.assertIn("non_field_errors", serializer.errors)

    def test_renewal_requires_device_id_and_device_token(self):
        for payload in (
            {"device_id": str(uuid4())},
            {"device_token": "signed-token"},
        ):
            with self.subTest(payload=payload):
                serializer = DeviceRegistrationSerializer(data=payload)

                self.assertFalse(serializer.is_valid())
                self.assertIn("non_field_errors", serializer.errors)

    def test_renewal_accepts_device_id_and_device_token(self):
        serializer = DeviceRegistrationSerializer(
            data={
                "device_id": str(uuid4()),
                "device_token": "signed-token",
            }
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertTrue(serializer.is_renewal)
        self.assertEqual(serializer.renewal_token, "signed-token")

@override_settings(
    DEVICE_REGISTRATION_TOKEN="test-registration-token"
)
class DeviceRegistrationTests(APITestCase):
    registration_token = "test-registration-token"

    def _registration_headers(self):
        return {
            "HTTP_X_DEVICE_REGISTRATION_TOKEN": self.registration_token,
        }

    @patch("pos.views.register_device")
    def test_device_registration_endpoint(self, mock_register_device):
        device = MagicMock()
        device.id = uuid4()
        device.device_code = "T01"
        mock_register_device.return_value = device

        response = self.client.post(
            "/api/devices/register/",
            {},
            format="json",
            **self._registration_headers(),
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["device_code"], "T01")
        self.assertEqual(response.data["device_id"], str(device.id))

        self.assertIn("device_token", response.data)
        self.assertTrue(response.data["device_token"])

        token = response.data["device_token"]

        device_id, device_code = _decode_device_token(token)

        self.assertEqual(device_id, str(device.id))
        self.assertEqual(device_code, "T01")

        mock_register_device.assert_called_once_with()


    @patch("pos.views.create_device_token", return_value="renewed-token")
    @patch("pos.views.get_device_from_token")
    def test_registration_renews_the_same_device(self, mock_get_device, mock_create_token):
        device_id = uuid4()
        device = MagicMock()
        device.id = device_id
        device.device_code = "T01"
        mock_get_device.return_value = device

        response = self.client.post(
            "/api/devices/register/",
            {
                "device_id": str(device_id),
                "device_token": "expired-signed-token",
            },
            format="json",
            **self._registration_headers(),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["device_id"], str(device_id))
        self.assertEqual(response.data["device_code"], "T01")
        self.assertEqual(response.data["device_token"], "renewed-token")
        mock_get_device.assert_called_once_with(
            "expired-signed-token",
            max_age=60 * 60 * 24 * 60,
        )
        mock_create_token.assert_called_once_with(device)

    @patch("pos.views.get_device_from_token")
    def test_registration_rejects_device_token_outside_renewal_window(
        self,
        mock_get_device,
    ):
        mock_get_device.side_effect = AuthenticationFailed(
            "Token device di luar masa perpanjangan."
        )

        response = self.client.post(
            "/api/devices/register/",
            {
                "device_id": str(uuid4()),
                "device_token": "too-old-signed-token",
            },
            format="json",
            **self._registration_headers(),
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(
            response.data["detail"],
            "Token device di luar masa perpanjangan.",
        )

    @patch("pos.views.get_device_from_token")
    def test_registration_rejects_token_for_different_device(self, mock_get_device):
        mock_get_device.return_value = MagicMock(id=uuid4(), device_code="T01")

        response = self.client.post(
            "/api/devices/register/",
            {
                "device_id": str(uuid4()),
                "device_token": "signed-token-for-another-device",
            },
            format="json",
            **self._registration_headers(),
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(
            response.data["detail"],
            "Token device tidak cocok dengan device_id.",
        )

    @patch("pos.views.register_device")
    def test_no_device_code_is_required(
        self,
        mock_register_device,
    ):
        device = MagicMock()
        device.id = uuid4()
        device.device_code = "T02"

        mock_register_device.return_value = device

        response = self.client.post(
            "/api/devices/register/",
            {},
            format="json",
            **self._registration_headers(),
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["device_code"], "T02")

    @patch("pos.views.register_device")
    def test_no_device_code_available_returns_409(
        self,
        mock_register_device,
    ):
        mock_register_device.side_effect = NoDeviceCodeAvailable(
            "Kode device habis. Seluruh kode T01-T99 sudah dipakai."
        )

        response = self.client.post(
            "/api/devices/register/",
            {},
            format="json",
            **self._registration_headers(),
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.data["detail"],
            "Kode device habis. Seluruh kode T01-T99 sudah dipakai.",
        )

    @patch("pos.views.register_device")
    def test_missing_registration_token_returns_401(
        self,
        mock_register_device,
    ):
        response = self.client.post(
            "/api/devices/register/",
            {},
            format="json",
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(
            response.data["detail"],
            "Token registrasi device tidak valid.",
        )

        mock_register_device.assert_not_called()

    @patch("pos.views.register_device")
    def test_invalid_registration_token_returns_401(
        self,
        mock_register_device,
    ):
        response = self.client.post(
            "/api/devices/register/",
            {},
            format="json",
            HTTP_X_DEVICE_REGISTRATION_TOKEN="wrong-token",
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(
            response.data["detail"],
            "Token registrasi device tidak valid.",
        )

        mock_register_device.assert_not_called()

class DeviceSequenceAllocationTests(APITestCase):
    @patch("pos.device_sequence.DeviceTransactionSequence.objects")
    def test_first_range_is_1_to_1000(self, sequence_objects):
        sequence = MagicMock()
        sequence.next_sequence = 1

        (
            sequence_objects
            .select_for_update.return_value
            .filter.return_value
            .first.return_value
        ) = sequence

        device = MagicMock()

        start_sequence, end_sequence = allocate_transaction_sequence(
            device
        )

        self.assertEqual(start_sequence, 1)
        self.assertEqual(end_sequence, 1000)
        self.assertEqual(sequence.next_sequence, 1001)

        # Tambahkan di sini
        self.assertIsNotNone(sequence.updated_at)

        sequence.save.assert_called_once_with(
            update_fields=[
                "next_sequence",
                "updated_at",
            ]
        )

    @patch("pos.device_sequence.DeviceTransactionSequence.objects")
    def test_second_range_starts_after_first_range(
        self,
        sequence_objects,
    ):
        sequence = MagicMock()
        sequence.next_sequence = 1001

        (
            sequence_objects
            .select_for_update.return_value
            .filter.return_value
            .first.return_value
        ) = sequence

        device = MagicMock()

        start_sequence, end_sequence = allocate_transaction_sequence(
            device
        )

        self.assertEqual(start_sequence, 1001)
        self.assertEqual(end_sequence, 2000)
        self.assertEqual(sequence.next_sequence, 2001)

    @patch("pos.device_sequence.DeviceTransactionSequence.objects")
    def test_missing_sequence_row_is_rejected(
        self,
        sequence_objects,
    ):
        (
            sequence_objects
            .select_for_update.return_value
            .filter.return_value
            .first.return_value
        ) = None

        device = MagicMock()

        with self.assertRaises(DeviceSequenceNotFound):
            allocate_transaction_sequence(device)

    @patch("pos.device_sequence.DeviceTransactionSequence.objects")
    def test_sequence_limit_is_rejected(
        self,
        sequence_objects,
    ):
        sequence = MagicMock()
        sequence.next_sequence = 999001

        (
            sequence_objects
            .select_for_update.return_value
            .filter.return_value
            .first.return_value
        ) = sequence

        device = MagicMock()

        with self.assertRaises(DeviceSequenceError):
            allocate_transaction_sequence(device)

        sequence.save.assert_not_called()

class DeviceTransactionSequenceAPITests(APITestCase):
    @patch("pos.views.allocate_transaction_sequence")
    @patch("pos.views.get_object_or_404")
    def test_sequence_allocation_returns_range(
        self,
        mock_get_object,
        mock_allocate,
    ):
        device_id = uuid4()

        device = MagicMock()
        device.id = device_id
        device.device_code = "T01"
        self.client.force_authenticate(user=AuthenticatedDevice(device=device))

        mock_get_object.return_value = device
        mock_allocate.return_value = (1, 1000)

        response = self.client.post(
            f"/api/devices/{device_id}/transaction-sequence/",
            {},
            format="json",
        )

        self.assertEqual(response.status_code, 200)

        self.assertEqual(
            response.data,
            {
                "device_id": str(device_id),
                "device_code": "T01",
                "start_sequence": 1,
                "end_sequence": 1000,
            },
        )

        mock_get_object.assert_called_once_with(
            Device,
            id=device_id,
        )

        mock_allocate.assert_called_once_with(device)

    @patch("pos.views.allocate_transaction_sequence")
    @patch("pos.views.get_object_or_404")
    def test_missing_sequence_row_returns_404(
        self,
        mock_get_object,
        mock_allocate,
    ):
        device_id = uuid4()

        device = MagicMock()
        device.id = device_id
        device.device_code = "T01"
        self.client.force_authenticate(user=AuthenticatedDevice(device=device))

        mock_get_object.return_value = device

        mock_allocate.side_effect = DeviceSequenceNotFound(
            "Jatah nomor nota untuk device ini belum ada."
        )

        response = self.client.post(
            f"/api/devices/{device_id}/transaction-sequence/",
            {},
            format="json",
        )

        self.assertEqual(response.status_code, 404)

        self.assertEqual(
            response.data["detail"],
            "Jatah nomor nota untuk device ini belum ada.",
        )

        mock_get_object.assert_called_once_with(
            Device,
            id=device_id,
        )

        mock_allocate.assert_called_once_with(device)

    @patch("pos.views.allocate_transaction_sequence")
    @patch("pos.views.get_object_or_404")
    def test_sequence_limit_error_returns_400(
        self,
        mock_get_object,
        mock_allocate,
    ):
        device_id = uuid4()

        device = MagicMock()
        device.id = device_id
        device.device_code = "T01"
        self.client.force_authenticate(user=AuthenticatedDevice(device=device))

        mock_get_object.return_value = device

        mock_allocate.side_effect = DeviceSequenceError(
            "Jatah nomor nota device ini sudah habis."
        )

        response = self.client.post(
            f"/api/devices/{device_id}/transaction-sequence/",
            {},
            format="json",
        )

        self.assertEqual(response.status_code, 400)

        self.assertEqual(
            response.data["detail"],
            "Jatah nomor nota device ini sudah habis.",
        )

        mock_get_object.assert_called_once_with(
            Device,
            id=device_id,
        )

        mock_allocate.assert_called_once_with(device)

    def test_tanpa_token_ditolak(self):
        response = self.client.post(f"/api/devices/{uuid4()}/transaction-sequence/", {}, format="json")
        self.assertIn(response.status_code, (401, 403))

    @patch("pos.views.allocate_transaction_sequence")
    def test_tablet_lain_ditolak(self, mock_allocate):
        device = MagicMock()
        device.id = uuid4()
        device.device_code = "T01"
        self.client.force_authenticate(user=AuthenticatedDevice(device=device))
        response = self.client.post(f"/api/devices/{uuid4()}/transaction-sequence/", {}, format="json")
        self.assertEqual(response.status_code, 403)
        mock_allocate.assert_not_called()


class TransactionIDTests(SimpleTestCase):
    def test_first_sequence_is_formatted_correctly(self):
        transaction_id = build_transaction_id("T01", 1)

        self.assertEqual(transaction_id, "T01-000001")

    def test_sequence_is_zero_padded_to_six_digits(self):
        transaction_id = build_transaction_id("T01", 42)

        self.assertEqual(transaction_id, "T01-000042")

    def test_last_sequence_is_formatted_correctly(self):
        transaction_id = build_transaction_id("T99", 999999)

        self.assertEqual(transaction_id, "T99-999999")

    def test_device_code_is_preserved(self):
        transaction_id = build_transaction_id("T05", 123)

        self.assertEqual(transaction_id, "T05-000123")

    def test_sequence_zero_is_rejected(self):
        with self.assertRaises(TransactionIDError):
            build_transaction_id("T01", 0)

    def test_sequence_above_limit_is_rejected(self):
        with self.assertRaises(TransactionIDError):
            build_transaction_id("T01", 1000000)

    def test_non_integer_sequence_is_rejected(self):
        with self.assertRaises(TransactionIDError):
            build_transaction_id("T01", "1")

    def test_invalid_device_code_is_rejected(self):
        with self.assertRaises(TransactionIDError):
            build_transaction_id("T1", 1)

    def test_device_code_without_t_is_rejected(self):
        with self.assertRaises(TransactionIDError):
            build_transaction_id("X01", 1)

    def test_device_code_with_non_digit_suffix_is_rejected(self):
        with self.assertRaises(TransactionIDError):
            build_transaction_id("T0A", 1)

class DeviceRegistrationAllocationTests(APITestCase):
    @patch("pos.device_registration.DeviceTransactionSequence.objects.create")
    @patch("pos.device_registration.Device.objects.create")
    @patch("pos.device_registration.Device.objects.values_list")
    @patch("pos.device_registration._lock_device_registration")
    def test_first_available_code_is_t01(
        self,
        mock_lock,
        mock_values_list,
        mock_device_create,
        mock_sequence_create,
    ):
        mock_values_list.return_value = []

        device = MagicMock()
        device.id = uuid4()
        device.device_code = "T01"

        mock_device_create.return_value = device

        result = register_device()

        self.assertEqual(result, device)

        mock_lock.assert_called_once_with()
        mock_device_create.assert_called_once_with(
            device_code="T01",
        )
        mock_sequence_create.assert_called_once_with(
            device=device,
            next_sequence=1,
            updated_at=ANY,
        )

    @patch("pos.device_registration.DeviceTransactionSequence.objects.create")
    @patch("pos.device_registration.Device.objects.create")
    @patch("pos.device_registration.Device.objects.values_list")
    @patch("pos.device_registration._lock_device_registration")
    def test_next_available_code_skips_existing_codes(
        self,
        mock_lock,
        mock_values_list,
        mock_device_create,
        mock_sequence_create,
    ):
        mock_values_list.return_value = [
            "T01",
            "T02",
            "T04",
        ]

        device = MagicMock()
        device.id = uuid4()
        device.device_code = "T03"

        mock_device_create.return_value = device

        result = register_device()

        self.assertEqual(result.device_code, "T03")

        mock_device_create.assert_called_once_with(
            device_code="T03",
        )

        mock_sequence_create.assert_called_once_with(
            device=device,
            next_sequence=1,
            updated_at=ANY,
        )

    @patch("pos.device_registration.Device.objects.values_list")
    @patch("pos.device_registration._lock_device_registration")
    def test_all_device_codes_used_is_rejected(
        self,
        mock_lock,
        mock_values_list,
    ):
        mock_values_list.return_value = [
            f"T{number:02d}"
            for number in range(1, 100)
        ]

        with self.assertRaises(NoDeviceCodeAvailable):
            register_device()

        mock_lock.assert_called_once_with()

class DeviceRegistrationSequenceTimestampTests(APITestCase):
    """Regresi: baris device_transaction_sequence wajib punya updated_at.

    Model `device_transaction_sequence` managed = False dan field
    `updated_at` tidak punya default Python, jadi Django mengirim NULL secara
    eksplisit walau kolom DB-nya punya default CURRENT_TIMESTAMP. Dulu ini
    bikin POST /api/devices/register/ membalas 500 (NOT NULL violation),
    sehingga modal "Registrasi Device" muncul di POS.
    """

    @patch("pos.device_registration.DeviceTransactionSequence.objects.create")
    @patch("pos.device_registration.Device.objects.create")
    @patch("pos.device_registration.Device.objects.values_list")
    @patch("pos.device_registration._lock_device_registration")
    def test_sequence_row_is_created_with_a_timestamp(
        self,
        mock_lock,
        mock_values_list,
        mock_device_create,
        mock_sequence_create,
    ):
        mock_values_list.return_value = []
        mock_device_create.return_value = MagicMock(
            id=uuid4(),
            device_code="T01",
        )

        register_device()

        mock_sequence_create.assert_called_once()
        created = mock_sequence_create.call_args.kwargs

        self.assertEqual(created["next_sequence"], 1)
        self.assertIsNotNone(created["updated_at"])
        self.assertIsNotNone(created["updated_at"].tzinfo)


class DeviceRegistrationRollbackTests(APITestCase):
    @patch("pos.device_registration.DeviceTransactionSequence.objects.create")
    @patch("pos.device_registration.Device.objects.create")
    @patch("pos.device_registration.Device.objects.values_list")
    @patch("pos.device_registration._lock_device_registration")
    def test_sequence_creation_failure_propagates(
        self,
        mock_lock,
        mock_values_list,
        mock_device_create,
        mock_sequence_create,
    ):
        mock_values_list.return_value = []

        device = MagicMock()
        device.id = uuid4()
        device.device_code = "T01"

        mock_device_create.return_value = device
        mock_sequence_create.side_effect = IntegrityError(
            "sequence creation failed"
        )

        with self.assertRaises(IntegrityError):
            register_device()

        mock_device_create.assert_called_once_with(
            device_code="T01",
        )

class TransactionSyncResolutionTests(APITestCase):
    @patch("pos.transaction_sync.Transactions.objects")
    def test_new_transaction(
        self,
        transaction_objects,
    ):
        transaction_objects.filter.return_value.first.side_effect = [
            None,
            None,
        ]

        sync_id = uuid4()

        status, transaction = resolve_transaction_request(
            sync_id,
            "T01-000001",
        )

        self.assertEqual(
            status,
            TransactionSyncStatus.NEW,
        )
        self.assertIsNone(transaction)

    @patch("pos.transaction_sync.Transactions.objects")
    def test_same_sync_id_and_transaction_id_is_idempotent(
        self,
        transaction_objects,
    ):
        sync_id = uuid4()

        existing_transaction = MagicMock()
        existing_transaction.sync_id = sync_id
        existing_transaction.transaction_id = "T01-000001"

        filter_manager = transaction_objects.filter
        filter_manager.return_value.first.side_effect = [
            existing_transaction,
            existing_transaction,
        ]

        status, transaction = resolve_transaction_request(
            sync_id,
            "T01-000001",
        )

        self.assertEqual(
            status,
            TransactionSyncStatus.IDEMPOTENT,
        )
        self.assertEqual(
            transaction,
            existing_transaction,
        )

    @patch("pos.transaction_sync.Transactions.objects")
    def test_same_sync_id_with_different_transaction_id_is_conflict(
        self,
        transaction_objects,
    ):
        sync_id = uuid4()

        existing_transaction = MagicMock()
        existing_transaction.sync_id = sync_id
        existing_transaction.transaction_id = "T01-000001"

        filter_manager = transaction_objects.filter
        filter_manager.return_value.first.side_effect = [
            existing_transaction,
            None,
        ]

        with self.assertRaises(TransactionSyncConflict) as context:
            resolve_transaction_request(
                sync_id,
                "T01-000002",
            )

        self.assertEqual(
            str(context.exception),
            "sync_id is already associated with a different transaction_id.",
        )

    @patch("pos.transaction_sync.Transactions.objects")
    def test_same_transaction_id_with_different_sync_id_is_conflict(
        self,
        transaction_objects,
    ):
        sync_id = uuid4()

        existing_transaction = MagicMock()
        existing_transaction.sync_id = uuid4()
        existing_transaction.transaction_id = "T01-000001"

        filter_manager = transaction_objects.filter
        filter_manager.return_value.first.side_effect = [
            None,
            existing_transaction,
        ]

        with self.assertRaises(TransactionSyncConflict) as context:
            resolve_transaction_request(
                sync_id,
                "T01-000001",
            )

        self.assertEqual(
            str(context.exception),
            "transaction_id is already associated with a different sync_id.",
        )

    @patch("pos.transaction_sync.Transactions.objects")
    def test_same_sync_id_and_null_transaction_id_is_idempotent(
        self,
        transaction_objects,
    ):
        sync_id = uuid4()

        existing_transaction = MagicMock()
        existing_transaction.sync_id = sync_id
        existing_transaction.transaction_id = None

        filter_manager = transaction_objects.filter
        filter_manager.return_value.first.side_effect = [
            existing_transaction,
        ]

        status, transaction = resolve_transaction_request(
            sync_id,
            None,
        )

        self.assertEqual(
            status,
            TransactionSyncStatus.IDEMPOTENT,
        )
        self.assertEqual(
            transaction,
            existing_transaction,
        )

class TransactionPersistenceServiceTests(APITestCase):
    def setUp(self):
        self.sync_id = uuid4()
        self.transaction_id = "T01-000001"
        self.transaction_data = {
            "sync_id": self.sync_id,
            "transaction_id": self.transaction_id,
            "transaction_time": datetime.now(timezone.utc),
            "cust_name": "Budi Santoso",
            "phone_no": "081234567890",
            "item_amount": 95000,
            "shipping_amount": 10000,
            "total_amount": 105000,
            "payment_method": "cash",
            "payment_amount": 110000,
            "change_amount": 5000,
            "items": [
                {
                    "item_name": "Test Item",
                    "variant": "Default",
                    "quantity": 2,
                    "unit_price": 47500,
                    # Toko tidak memakai diskon per baris (QA 18 Okt 2026):
                    # harga nego langsung di unit_price.
                    "discount": 0,
                    "subtotal": 95000,
                }
            ],
        }

    @patch("pos.transaction_service.transaction.atomic")
    @patch("pos.transaction_service.resolve_transaction_request")
    @patch("pos.transaction_service.TransactionDetail.objects")
    @patch("pos.transaction_service.Transactions.objects")
    @patch("pos.transaction_service._process_inventory")
    def test_new_transaction_persists_header_and_details(
        self,
        mock_process_inventory,
        mock_transactions,
        mock_details,
        mock_resolve,
        mock_atomic,
    ):
        atomic_context = MagicMock()
        mock_atomic.return_value = atomic_context
        mock_resolve.return_value = (TransactionSyncStatus.NEW, None)

        transaction_record = MagicMock()
        transaction_record.sync_id = self.sync_id
        transaction_record.transaction_id = self.transaction_id
        mock_transactions.create.return_value = transaction_record

        status, result = persist_transaction(
            self.transaction_data,
            "W001",
        )

        self.assertIs(status, TransactionSyncStatus.NEW)
        self.assertIs(result, transaction_record)
        mock_transactions.create.assert_called_once_with(
            sync_id=self.sync_id,
            transaction_id=self.transaction_id,
            worker_id="W001",
            transaction_time=self.transaction_data["transaction_time"],
            item_amount=95000,
            shipping_amount=10000,
            total_amount=105000,
            payment_method="cash",
            payment_amount=110000,
            change_amount=5000,
            cust_name="Budi Santoso",
            phone_no="081234567890",
        )
        mock_details.create.assert_called_once_with(
            sync_id=self.sync_id,
            item_name="Test Item",
            variant="Default",
            discount=0,
            quantity=2,
            subtotal=95000,
            unit_price=47500,
        )

    @patch("pos.transaction_service.resolve_transaction_request")
    @patch("pos.transaction_service.TransactionDetail.objects")
    @patch("pos.transaction_service.Transactions.objects")
    def test_idempotent_transaction_is_not_created_again(
        self,
        mock_transactions,
        mock_details,
        mock_resolve,
    ):
        existing = MagicMock()
        existing.sync_id = self.sync_id
        existing.transaction_id = self.transaction_id
        existing.worker_id = "W001"
        for field, value in self.transaction_data.items():
            if field != "items":
                setattr(existing, field, value)
        mock_details.filter.return_value.values.return_value = self.transaction_data["items"]
        mock_resolve.return_value = (
            TransactionSyncStatus.IDEMPOTENT,
            existing,
        )

        status, result = persist_transaction(
            self.transaction_data,
            "W001",
        )

        self.assertIs(status, TransactionSyncStatus.IDEMPOTENT)
        self.assertIs(result, existing)
        mock_transactions.create.assert_not_called()
        mock_details.create.assert_not_called()

    @patch("pos.transaction_service.resolve_transaction_request")
    @patch("pos.transaction_service.TransactionDetail.objects")
    @patch("pos.transaction_service.Transactions.objects")
    def test_conflict_is_rejected(
        self,
        mock_transactions,
        mock_details,
        mock_resolve,
    ):
        mock_resolve.return_value = (
            TransactionSyncStatus.CONFLICT,
            None,
        )

        with self.assertRaises(TransactionSyncConflict):
            persist_transaction(self.transaction_data, "W001")

        mock_transactions.create.assert_not_called()
        mock_details.create.assert_not_called()

class TransactionRetryIntegrityTests(SimpleTestCase):
    """Lost-response retry must not accept new facts or a different cashier."""

    def setUp(self):
        self.data = {
            "sync_id": uuid4(),
            "transaction_id": "T01-000001",
            "transaction_time": datetime.now(timezone.utc),
            "item_amount": 100,
            "shipping_amount": 0,
            "total_amount": 100,
            "payment_method": "cash",
            "payment_amount": 100,
            "change_amount": 0,
            "items": [{
                "item_name": "Test",
                "variant": "Default",
                "quantity": 1,
                "unit_price": 100,
                "discount": 0,
                "subtotal": 100,
            }],
        }
        self.existing = Transactions(worker_id="W001", **{
            key: value for key, value in self.data.items() if key != "items"
        })

    def run_retry(self, data=None, worker_id="W001", race=False):
        with (
            patch("pos.transaction_service.transaction.atomic"),
            patch("pos.transaction_service.transaction.set_rollback") as rollback,
            patch("pos.transaction_service.resolve_transaction_request") as resolve,
            patch("pos.transaction_service.TransactionDetail.objects") as details,
            patch("pos.transaction_service._process_inventory") as inventory,
            patch("pos.transaction_service._create_transaction_header") as create,
        ):
            resolve.return_value = (TransactionSyncStatus.IDEMPOTENT, self.existing)
            details.filter.return_value.values.return_value = self.data["items"]
            if race:
                resolve.side_effect = [
                    (TransactionSyncStatus.NEW, None),
                    resolve.return_value,
                ]
                create.side_effect = IntegrityError()
            result = persist_transaction(data or self.data, worker_id, "T01")
            if race:
                rollback.assert_called_once_with(True)
            else:
                inventory.assert_not_called()
                create.assert_not_called()
            details.create.assert_not_called()
            return result

    def test_lost_response_retry_returns_original_without_stock_write(self):
        status, record = self.run_retry()
        self.assertIs(status, TransactionSyncStatus.IDEMPOTENT)
        self.assertIs(record, self.existing)

    def test_every_changed_header_fact_conflicts(self):
        changes = {
            "transaction_time": datetime(2020, 1, 1, tzinfo=timezone.utc),
            "item_amount": 200, "shipping_amount": 1, "total_amount": 200,
            "payment_method": "edc", "payment_amount": 200, "change_amount": 1,
            "cust_name": "Orang Lain", "phone_no": "089999999999",
        }
        for field, value in changes.items():
            with self.subTest(field=field), self.assertRaises(TransactionSyncConflict):
                self.run_retry({**self.data, field: value})

    def test_different_cashier_conflicts_instead_of_silent_success(self):
        with self.assertRaises(TransactionSyncConflict):
            self.run_retry(worker_id="W002")

    def test_changed_or_duplicated_item_rows_conflict(self):
        changes = {
            "item_name": "Other",
            "variant": "Other",
            "quantity": 2,
            "unit_price": 200,
            "discount": 1,
            "subtotal": 200,
        }
        for field, value in changes.items():
            with self.subTest(field=field), self.assertRaises(TransactionSyncConflict):
                self.run_retry({**self.data, "items": [{**self.data["items"][0], field: value}]})
        with self.assertRaises(TransactionSyncConflict):
            self.run_retry({**self.data, "items": self.data["items"] * 2})

    def test_null_shipping_and_zero_are_equivalent(self):
        self.run_retry({**self.data, "shipping_amount": None})

    def test_race_retry_compares_facts_and_rolls_back_stock(self):
        self.run_retry(race=True)

    def test_race_with_different_cashier_conflicts(self):
        with self.assertRaises(TransactionSyncConflict):
            self.run_retry(worker_id="W002", race=True)


class TransactionCreateAPITests(APITestCase):
    def setUp(self):
        self.url = "/api/transactions/"
        self.sync_id = uuid4()

        # Kelas ini menguji perilaku selain device auth; kewajiban device
        # token diuji tersendiri di TransactionCreateDeviceAuthTests.
        auth_patcher = patch.object(
            TransactionCreateAPIView,
            "authentication_classes",
            [],
        )
        auth_patcher.start()
        self.addCleanup(auth_patcher.stop)

        permission_patcher = patch.object(
            TransactionCreateAPIView,
            "permission_classes",
            [],
        )
        permission_patcher.start()
        self.addCleanup(permission_patcher.stop)

        self.transaction = {
            "sync_id": self.sync_id,
            "transaction_id": "T01-000001",
            "transaction_time": datetime.now(timezone.utc),
            "item_amount": 95000,
            "shipping_amount": 10000,
            "total_amount": 105000,
            "payment_method": "cash",
            "payment_amount": 110000,
            "change_amount": 5000,
            "items": [
                {
                    "item_name": "Test Item",
                    "variant": "Default",
                    "quantity": 2,
                    "unit_price": 47500,
                    # Toko tidak memakai diskon per baris (QA 18 Okt 2026):
                    # harga nego langsung di unit_price.
                    "discount": 0,
                    "subtotal": 95000,
                }
            ],
        }

        self.payload = {
            "pin": "123456",
            "transaction": self.transaction,
        }

    @patch(
        "pos.views.persist_transaction"
    )
    @patch(
        "pos.transaction_request.verify_worker_pin",
        return_value="W001",
    )
    def test_new_transaction_returns_201(
        self,
        mock_verify_pin,
        mock_persist,
    ):
        transaction_record = MagicMock()
        transaction_record.sync_id = self.sync_id
        transaction_record.transaction_id = "T01-000001"

        mock_persist.return_value = (
            TransactionSyncStatus.NEW,
            transaction_record,
        )

        response = self.client.post(
            self.url,
            self.payload,
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["status"], "new")
        self.assertEqual(
            response.data["sync_id"],
            str(self.sync_id),
        )
        self.assertEqual(
            response.data["transaction_id"],
            "T01-000001",
        )

        mock_verify_pin.assert_called_once()
        self.assertEqual(mock_verify_pin.call_args.args[0], "123456")
        mock_persist.assert_called_once()

        transaction_data, worker_id, _device_code = (
            mock_persist.call_args.args
        )

        self.assertEqual(worker_id, "W001")
        self.assertEqual(
            transaction_data["sync_id"],
            self.sync_id,
        )

    @patch(
        "pos.views.persist_transaction"
    )
    @patch(
        "pos.transaction_request.verify_worker_pin",
        return_value="W001",
    )
    def test_idempotent_retry_returns_200(
        self,
        mock_verify_pin,
        mock_persist,
    ):
        transaction_record = MagicMock()
        transaction_record.sync_id = self.sync_id
        transaction_record.transaction_id = "T01-000001"

        mock_persist.return_value = (
            TransactionSyncStatus.IDEMPOTENT,
            transaction_record,
        )

        response = self.client.post(
            self.url,
            self.payload,
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "idempotent")
        self.assertEqual(
            response.data["sync_id"],
            str(self.sync_id),
        )
        self.assertEqual(
            response.data["transaction_id"],
            "T01-000001",
        )

        mock_persist.assert_called_once()

    @patch(
        "pos.views.persist_transaction",
        side_effect=TransactionSyncConflict(
            "Identitas nota bentrok."
        ),
    )
    @patch(
        "pos.transaction_request.verify_worker_pin",
        return_value="W001",
    )
    def test_identity_conflict_returns_409(
        self,
        mock_verify_pin,
        mock_persist,
    ):
        response = self.client.post(
            self.url,
            self.payload,
            format="json",
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.data["detail"],
            "Identitas nota bentrok.",
        )

        mock_persist.assert_called_once()

    @patch(
        "pos.views.persist_transaction"
    )
    @patch(
        "pos.transaction_request.verify_worker_pin",
        side_effect=InvalidWorkerCredentials(
            "PIN kasir tidak cocok."
        ),
    )
    def test_invalid_pin_returns_400(
        self,
        mock_verify_pin,
        mock_persist,
    ):
        response = self.client.post(
            self.url,
            self.payload,
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("pin", response.data)

        mock_verify_pin.assert_called_once()
        self.assertEqual(mock_verify_pin.call_args.args[0], "123456")
        mock_persist.assert_not_called()

    @patch(
        "pos.views.persist_transaction"
    )
    @patch(
        "pos.transaction_request.verify_worker_pin",
        return_value="W001",
    )
    def test_invalid_transaction_payload_returns_400(
        self,
        mock_verify_pin,
        mock_persist,
    ):
        payload = {
            **self.payload,
            "transaction": {
                **self.transaction,
                "payment_amount": 100000,
            },
        }

        response = self.client.post(
            self.url,
            payload,
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn(
            "payment_amount",
            response.data["transaction"],
        )

        mock_persist.assert_not_called()

    @patch(
        "pos.views.persist_transaction"
    )
    @patch(
        "pos.transaction_request.verify_worker_pin",
        return_value="W001",
    )
    def test_worker_id_is_resolved_from_pin(
        self,
        mock_verify_pin,
        mock_persist,
    ):
        transaction_record = MagicMock()
        transaction_record.sync_id = self.sync_id
        transaction_record.transaction_id = "T01-000001"

        mock_persist.return_value = (
            TransactionSyncStatus.NEW,
            transaction_record,
        )

        response = self.client.post(
            self.url,
            self.payload,
            format="json",
        )

        self.assertEqual(response.status_code, 201)

        transaction_data, worker_id, _device_code = (
            mock_persist.call_args.args
        )

        self.assertEqual(worker_id, "W001")
        self.assertNotIn("pin", transaction_data)


class TransactionDomainRejectionTests(APITestCase):
    """Domain rejections from the sale flow must return 400 with a
    usable reason, not an opaque 500."""

    def setUp(self):
        self.url = "/api/transactions/"
        self.sync_id = uuid4()

        # Device auth diuji terpisah; fokus kelas ini adalah mapping 400.
        auth_patcher = patch.object(
            TransactionCreateAPIView,
            "authentication_classes",
            [],
        )
        auth_patcher.start()
        self.addCleanup(auth_patcher.stop)

        permission_patcher = patch.object(
            TransactionCreateAPIView,
            "permission_classes",
            [],
        )
        permission_patcher.start()
        self.addCleanup(permission_patcher.stop)

        self.transaction_data = {
            "sync_id": self.sync_id,
            "transaction_id": "T01-000001",
            "transaction_time": datetime.now(timezone.utc),
            "item_amount": 95000,
            "shipping_amount": 10000,
            "total_amount": 105000,
            "payment_method": "cash",
            "payment_amount": 110000,
            "change_amount": 5000,
            "items": [
                {
                    "item_name": "Test Item",
                    "variant": "Default",
                    "quantity": 2,
                    "unit_price": 47500,
                    # Toko tidak memakai diskon per baris (QA 18 Okt 2026):
                    # harga nego langsung di unit_price.
                    "discount": 0,
                    "subtotal": 95000,
                }
            ],
        }

        self.payload = {
            "pin": "123456",
            "transaction": self.transaction_data,
        }

    @patch(
        "pos.views.persist_transaction",
        side_effect=TransactionValidationError("Stok barang habis."),
    )
    @patch(
        "pos.transaction_request.verify_worker_pin",
        return_value="W001",
    )
    def test_out_of_stock_returns_400_with_reason(
        self,
        mock_verify_pin,
        mock_persist,
    ):
        response = self.client.post(self.url, self.payload, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["detail"], "Stok barang habis.")

    @patch(
        "pos.views.persist_transaction",
        side_effect=TransactionValidationError("Barang tidak ada di master."),
    )
    @patch(
        "pos.transaction_request.verify_worker_pin",
        return_value="W001",
    )
    def test_unknown_item_returns_400_with_reason(
        self,
        mock_verify_pin,
        mock_persist,
    ):
        response = self.client.post(self.url, self.payload, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["detail"], "Barang tidak ada di master.")

    @patch(
        "pos.views.persist_transaction",
        side_effect=TransactionValidationError(
            "Barang yang sama muncul dua kali di nota."
        ),
    )
    @patch(
        "pos.transaction_request.verify_worker_pin",
        return_value="W001",
    )
    def test_duplicate_item_returns_400_with_reason(
        self,
        mock_verify_pin,
        mock_persist,
    ):
        response = self.client.post(self.url, self.payload, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data["detail"],
            "Barang yang sama muncul dua kali di nota.",
        )

    @patch("pos.transaction_service.resolve_transaction_request")
    @patch("pos.transaction_service.TransactionDetail.objects")
    @patch("pos.transaction_service.Transactions.objects")
    @patch("pos.transaction_service.Item.objects")
    def test_unknown_item_fails_at_service_with_domain_error(
        self,
        mock_items,
        mock_transactions,
        mock_details,
        mock_resolve,
    ):
        mock_resolve.return_value = (
            TransactionSyncStatus.NEW,
            None,
        )

        (
            mock_items
            .select_for_update.return_value
            .get.side_effect
        ) = Item.DoesNotExist

        with self.assertRaises(TransactionValidationError) as context:
            persist_transaction(self.transaction_data, "W001")

        self.assertEqual(str(context.exception), "Barang tidak ada di master.")
        mock_transactions.create.assert_not_called()


class TransactionInventoryTests(APITestCase):
    def setUp(self):
        self.sync_id = uuid4()
        self.transaction_id = "T01-000001"

        self.transaction_data = {
            "sync_id": self.sync_id,
            "transaction_id": self.transaction_id,
            "transaction_time": datetime.now(timezone.utc),
            "item_amount": 95000,
            "shipping_amount": 10000,
            "total_amount": 105000,
            "payment_method": "cash",
            "payment_amount": 110000,
            "change_amount": 5000,
            "items": [
                {
                    "item_name": "Test Item",
                    "variant": "Default",
                    "quantity": 2,
                    "unit_price": 47500,
                    # Toko tidak memakai diskon per baris (QA 18 Okt 2026):
                    # harga nego langsung di unit_price.
                    "discount": 0,
                    "subtotal": 95000,
                }
            ],
        }

    @patch("pos.transaction_service.resolve_transaction_request")
    @patch("pos.transaction_service.TransactionDetail.objects")
    @patch("pos.transaction_service.Transactions.objects")
    @patch("pos.transaction_service.Item.objects")
    def test_stock_is_reduced_when_transaction_is_new(
        self,
        mock_items,
        mock_transactions,
        mock_details,
        mock_resolve,
    ):
        mock_resolve.return_value = (
            TransactionSyncStatus.NEW,
            None,
        )

        item = MagicMock()
        item.item_name = "Test Item"
        item.variant = "Default"
        item.in_stock = 5
        item.min_price = 0
        item.max_price = 100000

        (
            mock_items
            .select_for_update.return_value
            .get.return_value
        ) = item

        transaction_record = MagicMock()
        transaction_record.sync_id = self.sync_id
        transaction_record.transaction_id = self.transaction_id

        mock_transactions.create.return_value = transaction_record

        status, result = persist_transaction(
            self.transaction_data,
            "W001",
        )

        self.assertIs(status, TransactionSyncStatus.NEW)
        self.assertIs(result, transaction_record)
        self.assertEqual(item.in_stock, 3)

        mock_items.select_for_update.assert_called_once()
        item.save.assert_called_once()

    @patch("pos.transaction_service.resolve_transaction_request")
    @patch("pos.transaction_service.TransactionDetail.objects")
    @patch("pos.transaction_service.Transactions.objects")
    @patch("pos.transaction_service.Item.objects")
    def test_quantity_can_exceed_stock_when_stock_is_positive(
        self,
        mock_items,
        mock_transactions,
        mock_details,
        mock_resolve,
    ):
        mock_resolve.return_value = (
            TransactionSyncStatus.NEW,
            None,
        )

        item = MagicMock()
        item.item_name = "Test Item"
        item.variant = "Default"
        item.in_stock = 2
        item.min_price = 0
        item.max_price = 100000

        (
            mock_items
            .select_for_update.return_value
            .get.return_value
        ) = item

        transaction_record = MagicMock()
        transaction_record.sync_id = self.sync_id
        transaction_record.transaction_id = self.transaction_id

        mock_transactions.create.return_value = transaction_record

        self.transaction_data["items"][0]["quantity"] = 5

        status, _ = persist_transaction(
            self.transaction_data,
            "W001",
        )

        self.assertIs(status, TransactionSyncStatus.NEW)
        self.assertEqual(item.in_stock, -3)

    @patch("pos.transaction_service.resolve_transaction_request")
    @patch("pos.transaction_service.TransactionDetail.objects")
    @patch("pos.transaction_service.Transactions.objects")
    @patch("pos.transaction_service.Item.objects")
    def test_zero_stock_is_allowed_and_goes_negative(
        self,
        mock_items,
        mock_transactions,
        mock_details,
        mock_resolve,
    ):
        mock_resolve.return_value = (
            TransactionSyncStatus.NEW,
            None,
        )

        item = MagicMock()
        item.item_name = "Test Item"
        item.variant = "Default"
        item.in_stock = 0
        item.min_price = None
        item.max_price = None

        (
            mock_items
            .select_for_update.return_value
            .get.return_value
        ) = item

        # Keputusan pemilik (dikunci, diterapkan 2026-10-17): stok boleh
        # minus; penjualan tidak ditolak karena stok habis.
        persist_transaction(
            self.transaction_data,
            "W001",
        )

        jumlah = sum(i["quantity"] for i in self.transaction_data["items"])
        self.assertEqual(item.in_stock, 0 - jumlah)
        mock_transactions.create.assert_called_once()
        item.save.assert_called_once_with(update_fields=["in_stock"])

    @patch("pos.transaction_service.resolve_transaction_request")
    @patch("pos.transaction_service.TransactionDetail.objects")
    @patch("pos.transaction_service.Transactions.objects")
    @patch("pos.transaction_service.Item.objects")
    def test_negative_stock_is_allowed_and_goes_negative(
        self,
        mock_items,
        mock_transactions,
        mock_details,
        mock_resolve,
    ):
        mock_resolve.return_value = (
            TransactionSyncStatus.NEW,
            None,
        )

        item = MagicMock()
        item.item_name = "Test Item"
        item.variant = "Default"
        item.in_stock = -1
        item.min_price = None
        item.max_price = None

        (
            mock_items
            .select_for_update.return_value
            .get.return_value
        ) = item

        # Keputusan pemilik (dikunci, diterapkan 2026-10-17): stok boleh
        # minus; penjualan tidak ditolak karena stok habis.
        persist_transaction(
            self.transaction_data,
            "W001",
        )

        jumlah = sum(i["quantity"] for i in self.transaction_data["items"])
        self.assertEqual(item.in_stock, -1 - jumlah)
        mock_transactions.create.assert_called_once()
        item.save.assert_called_once_with(update_fields=["in_stock"])

    @patch("pos.transaction_service.resolve_transaction_request")
    @patch("pos.transaction_service.TransactionDetail.objects")
    @patch("pos.transaction_service.Transactions.objects")
    @patch("pos.transaction_service.Item.objects")
    def test_inventory_failure_does_not_create_transaction(
        self,
        mock_items,
        mock_transactions,
        mock_details,
        mock_resolve,
    ):
        mock_resolve.return_value = (
            TransactionSyncStatus.NEW,
            None,
        )

        item = MagicMock()
        item.item_name = "Test Item"
        item.variant = "Default"
        item.in_stock = 5
        item.min_price = 0
        item.max_price = 100000

        (
            mock_items
            .select_for_update.return_value
            .get.return_value
        ) = item

        mock_details.create.side_effect = IntegrityError(
            "detail creation failed"
        )

        with self.assertRaises(IntegrityError):
            persist_transaction(
                self.transaction_data,
                "W001",
            )

        mock_transactions.create.assert_called_once()
        mock_details.create.assert_called_once()
        item.save.assert_called_once()

    @patch("pos.transaction_service.resolve_transaction_request")
    @patch("pos.transaction_service.TransactionDetail.objects")
    @patch("pos.transaction_service.Transactions.objects")
    @patch("pos.transaction_service.Item.objects")
    def test_items_are_locked_in_deterministic_order(
        self,
        mock_items,
        mock_transactions,
        mock_details,
        mock_resolve,
    ):
        mock_resolve.return_value = (
            TransactionSyncStatus.NEW,
            None,
        )

        item_a = MagicMock()
        item_a.item_name = "Apple"
        item_a.variant = "Default"
        item_a.in_stock = 10
        item_a.min_price = 0
        item_a.max_price = 100000

        item_b = MagicMock()
        item_b.item_name = "Banana"
        item_b.variant = "Default"
        item_b.in_stock = 10
        item_b.min_price = 0
        item_b.max_price = 100000

        mock_items.select_for_update.return_value.get.side_effect = [
            item_a,
            item_b,
        ]

        transaction_record = MagicMock()
        transaction_record.sync_id = self.sync_id
        transaction_record.transaction_id = self.transaction_id
        mock_transactions.create.return_value = transaction_record

        self.transaction_data["items"] = [
            {
                "item_name": "Banana",
                "variant": "Default",
                "quantity": 1,
                "unit_price": 50000,
                "discount": 0,
                "subtotal": 50000,
            },
            {
                "item_name": "Apple",
                "variant": "Default",
                "quantity": 1,
                "unit_price": 45000,
                "discount": 0,
                "subtotal": 45000,
            },
        ]

        status, _ = persist_transaction(
            self.transaction_data,
            "W001",
        )

        self.assertIs(status, TransactionSyncStatus.NEW)

        get_calls = (
            mock_items
            .select_for_update.return_value
            .get.call_args_list
        )

        self.assertEqual(
            get_calls,
            [
                unittest.mock.call(
                    item_name="Apple",
                    variant="Default",
                ),
                unittest.mock.call(
                    item_name="Banana",
                    variant="Default",
                ),
            ],
        )


class ItemPriceCeilingTests(APITestCase):
    """Batas atas harga item yang pricelist-nya belum diisi.

    Permintaan pemilik 2026-10-07: `max_price` = 0 berarti pricelist item itu
    belum diisi, bukan harga jual Rp 0. Batas atasnya karena itu `min_price`,
    sama dengan katalog di perangkat
    (pos-frontend/src/utils/priceRange.js `resolveItemPrices`). Tanpa aturan
    ini setiap nota untuk item seperti itu -- data nyata "Aries Pintu - Abu"
    (min 245.000, max 0) -- ditolak "Harga barang di atas harga pricelist.".
    """

    def setUp(self):
        self.transaction_data = {
            "sync_id": uuid4(),
            "transaction_id": "T01-000001",
            "transaction_time": datetime.now(timezone.utc),
            "item_amount": 245000,
            "shipping_amount": 0,
            "total_amount": 245000,
            "payment_method": "cash",
            "payment_amount": 245000,
            "change_amount": 0,
            "items": [],
        }

    def _persist(self, item, unit_price, quantity=1):
        """Jalankan penjualan satu item lewat service dengan tabel item ditiru.

        Tabel `item` dikelola Inventory dan tidak ada di SQLite, jadi barisnya
        ditiru seperti TransactionInventoryTests; yang diuji di sini hanya
        aturan harganya.
        """
        data = dict(self.transaction_data)
        data["items"] = [
            {
                "item_name": item.item_name,
                "variant": item.variant,
                "quantity": quantity,
                "unit_price": unit_price,
                "discount": 0,
                "subtotal": unit_price * quantity,
            }
        ]
        data["item_amount"] = unit_price * quantity
        data["total_amount"] = data["item_amount"]
        data["payment_amount"] = data["total_amount"]

        with (
            patch("pos.transaction_service.resolve_transaction_request") as resolve,
            patch("pos.transaction_service.TransactionDetail.objects"),
            patch("pos.transaction_service.Transactions.objects") as transactions,
            patch("pos.transaction_service.Item.objects") as items,
        ):
            resolve.return_value = (TransactionSyncStatus.NEW, None)
            items.select_for_update.return_value.get.return_value = item
            record = MagicMock()
            record.sync_id = data["sync_id"]
            transactions.create.return_value = record

            return persist_transaction(data, "W001")

    @staticmethod
    def _item(max_price, min_price):
        item = MagicMock()
        item.item_name = "Aries Pintu"
        item.variant = "Abu"
        item.in_stock = 5
        item.max_price = max_price
        item.min_price = min_price

        return item

    def test_pricelist_zero_accepts_the_floor_price_as_the_ceiling(self):
        item = self._item(max_price=0, min_price=245000)

        status, _ = self._persist(item, unit_price=245000)

        self.assertIs(status, TransactionSyncStatus.NEW)

    def test_pricelist_zero_still_rejects_below_the_floor(self):
        item = self._item(max_price=0, min_price=245000)

        with self.assertRaises(TransactionValidationError) as context:
            self._persist(item, unit_price=240000)

        self.assertEqual(
            str(context.exception),
            "Harga barang di bawah harga floor.",
        )

    def test_pricelist_zero_rejects_above_the_floor_price(self):
        item = self._item(max_price=0, min_price=245000)

        with self.assertRaises(TransactionValidationError) as context:
            self._persist(item, unit_price=300000)

        self.assertEqual(
            str(context.exception),
            "Harga barang di atas harga pricelist.",
        )

    def test_filled_pricelist_keeps_its_own_ceiling(self):
        item = self._item(max_price=300000, min_price=200000)

        status, _ = self._persist(item, unit_price=300000)

        self.assertIs(status, TransactionSyncStatus.NEW)

        with self.assertRaises(TransactionValidationError) as context:
            self._persist(item, unit_price=300001)

        self.assertEqual(
            str(context.exception),
            "Harga barang di atas harga pricelist.",
        )

    def test_missing_pricelist_keeps_only_the_floor(self):
        item = self._item(max_price=None, min_price=200000)

        status, _ = self._persist(item, unit_price=250000)

        self.assertIs(status, TransactionSyncStatus.NEW)

        with self.assertRaises(TransactionValidationError) as context:
            self._persist(item, unit_price=150000)

        self.assertEqual(
            str(context.exception),
            "Harga barang di bawah harga floor.",
        )

def _lepas_auth_item_list(test):
    from .views import ItemListAPIView

    for nama in ("authentication_classes", "permission_classes"):
        tambalan = patch.object(ItemListAPIView, nama, [])
        tambalan.start()
        test.addCleanup(tambalan.stop)


class ExternalPostgresTableMixin:
    """Shared setup for tests that need the externally managed tables.

    These tests require PostgreSQL on a disposable test database (TEMP TABLE
    ... ON COMMIT PRESERVE ROWS, pg_temp.*). On any other engine they skip
    with an explicit reason instead of failing: SQLite cannot prove trigram
    ranking, row locking, or TEMP-table behaviour. Run them via
    tools/run_pg_tests.py with POS_TEST_DATABASE_URL.
    """

    @classmethod
    def setUpClass(cls):
        if connection.vendor != "postgresql":
            raise unittest.SkipTest(
                "Membutuhkan database uji PostgreSQL disposable "
                "(POS_TEST_DATABASE_URL); SQLite tidak membuktikan perilaku "
                "trigram/locking/TEMP-table PostgreSQL."
            )
        super().setUpClass()

    @classmethod
    def _ensure_external_tables(cls):
        """Create the externally managed tables as session temp tables.

        They cannot live in setUpClass: Django closes the database
        connection between TransactionTestCase tests, and PostgreSQL
        drops session temp tables when the connection ends. The schema is
        therefore (re)created and emptied for every test.
        """
        with connection.cursor() as cursor:
            cls._create_external_temp_tables(cursor)

            cursor.execute(
                """
                TRUNCATE TABLE
                    pg_temp.transaction_detail,
                    pg_temp.transactions,
                    pg_temp.item,
                    pg_temp.item_alias,
                    pg_temp.worker,
                    pg_temp.refund_event_detail,
                    pg_temp.refund_event;
                """
            )

    @classmethod
    def _create_external_temp_tables(cls, cursor):
        cursor.execute(
            """
            CREATE TEMPORARY TABLE IF NOT EXISTS worker (
                id VARCHAR(5) PRIMARY KEY,
                name TEXT NOT NULL,
                pin TEXT NOT NULL
            ) ON COMMIT PRESERVE ROWS;
            """
        )

        cursor.execute(
            """
            CREATE TEMPORARY TABLE IF NOT EXISTS item (
                item_name VARCHAR NOT NULL,
                variant VARCHAR NOT NULL,
                brand VARCHAR NOT NULL,
                category VARCHAR NOT NULL,
                cost_amount INTEGER NOT NULL,
                ecommerce_item BOOLEAN NOT NULL,
                in_stock INTEGER NOT NULL,
                max_price INTEGER NOT NULL,
                min_price INTEGER NOT NULL,
                moka_id VARCHAR NOT NULL,
                sku VARCHAR NOT NULL,
                stock_alert INTEGER NULL,
                track_alert BOOLEAN NOT NULL,
                track_stock BOOLEAN NOT NULL,
                PRIMARY KEY (item_name, variant)
            ) ON COMMIT PRESERVE ROWS;
            """
        )

        # Tabel alias (frasa -> item_name). Tanpa FOREIGN KEY: data alias
        # nyata memuat nama yang belum ada di item (dilaporkan tools/
        # import_item_alias.py), dan alias harus bisa dimuat lebih dulu.
        cursor.execute(
            """
            CREATE TEMPORARY TABLE IF NOT EXISTS item_alias (
                frasa VARCHAR NOT NULL,
                item_name VARCHAR NOT NULL,
                PRIMARY KEY (frasa, item_name)
            ) ON COMMIT PRESERVE ROWS;
            """
        )

        # Tabel refund (REFUND_DESIGN.md): event append-only void/return/
        # refund. Tanpa FOREIGN KEY ke transactions seperti pola item_alias:
        # tabel harus bisa dibuat/dikosongkan dalam urutan apa pun.
        cursor.execute(
            """
            CREATE TEMPORARY TABLE IF NOT EXISTS refund_event (
                refund_id UUID PRIMARY KEY,
                sync_id UUID NOT NULL,
                transaction_id VARCHAR(10) NULL,
                event_type VARCHAR(10) NOT NULL,
                reason TEXT NOT NULL,
                refund_method VARCHAR(12) NOT NULL,
                refund_amount INTEGER NOT NULL,
                approver_worker_id VARCHAR(5) NOT NULL,
                device_code VARCHAR(3) NOT NULL,
                created_at TIMESTAMPTZ NOT NULL
            ) ON COMMIT PRESERVE ROWS;
            """
        )

        cursor.execute(
            """
            CREATE TEMPORARY TABLE IF NOT EXISTS refund_event_detail (
                refund_id UUID NOT NULL,
                item_name VARCHAR NOT NULL,
                variant VARCHAR NOT NULL,
                quantity INTEGER NOT NULL,
                unit_price INTEGER NOT NULL,
                subtotal INTEGER NOT NULL,
                PRIMARY KEY (refund_id, item_name, variant)
            ) ON COMMIT PRESERVE ROWS;
            """
        )

        cursor.execute(
            """
            CREATE TEMPORARY TABLE IF NOT EXISTS transactions (
                sync_id UUID PRIMARY KEY,
                transaction_id VARCHAR(10) UNIQUE NULL,
                worker_id VARCHAR(5) NOT NULL,
                transaction_time TIMESTAMPTZ NOT NULL,
                item_amount INTEGER NOT NULL,
                shipping_amount INTEGER NULL,
                total_amount INTEGER NOT NULL,
                payment_method VARCHAR NOT NULL,
                payment_amount INTEGER NOT NULL,
                change_amount INTEGER NOT NULL,
                cust_name TEXT NULL,
                phone_no VARCHAR(20) NULL,
                FOREIGN KEY (worker_id)
                    REFERENCES worker(id)
            ) ON COMMIT PRESERVE ROWS;
            """
        )

        cursor.execute(
            """
            CREATE TEMPORARY TABLE IF NOT EXISTS transaction_detail (
                sync_id UUID NOT NULL,
                item_name VARCHAR NOT NULL,
                variant VARCHAR NOT NULL,
                discount INTEGER NOT NULL,
                quantity INTEGER NOT NULL,
                subtotal INTEGER NOT NULL,
                unit_price INTEGER NOT NULL,
                PRIMARY KEY (sync_id, item_name, variant),
                FOREIGN KEY (sync_id)
                    REFERENCES transactions(sync_id),
                FOREIGN KEY (item_name, variant)
                    REFERENCES item(item_name, variant),
                CONSTRAINT ck_test_detail_subtotal
                    CHECK (subtotal >= 0)
            ) ON COMMIT PRESERVE ROWS;
            """
        )


class TransactionPersistencePostgreSQLTests(
    ExternalPostgresTableMixin,
    TransactionTestCase,
):
    reset_sequences = False

    # _ensure_external_tables diwarisi dari ExternalPostgresTableMixin
    # (satu definisi; duplikasi yang identik dihapus agar skema TEMP-table
    # tidak bisa drift diam-diam).

    @classmethod
    def tearDownClass(cls):
        with connection.cursor() as cursor:
            cursor.execute(
                "DROP TABLE IF EXISTS pg_temp.transaction_detail;"
            )
            cursor.execute(
                "DROP TABLE IF EXISTS pg_temp.transactions;"
            )
            cursor.execute(
                "DROP TABLE IF EXISTS pg_temp.item;"
            )
            cursor.execute(
                "DROP TABLE IF EXISTS pg_temp.worker;"
            )

        super().tearDownClass()

    def setUp(self):
        self.sync_id = uuid4()
        self.transaction_id = "T01-000001"

        self._ensure_external_tables()

        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO worker (
                    id,
                    name,
                    pin
                )
                VALUES (%s, %s, %s);
                """,
                [
                    "W001",
                    "Integration Test Worker",
                    "123456",
                ],
            )

            cursor.execute(
                """
                INSERT INTO item (
                    item_name,
                    variant,
                    brand,
                    category,
                    cost_amount,
                    ecommerce_item,
                    in_stock,
                    max_price,
                    min_price,
                    moka_id,
                    sku,
                    stock_alert,
                    track_alert,
                    track_stock
                )
                VALUES
                    (
                        'Item A',
                        'Default',
                        'Test',
                        'Test',
                        10000,
                        FALSE,
                        10,
                        20000,
                        15000,
                        'MOKA-A',
                        'SKU-A',
                        NULL,
                        FALSE,
                        TRUE
                    ),
                    (
                        'Item B',
                        'Default',
                        'Test',
                        'Test',
                        10000,
                        FALSE,
                        20,
                        30000,
                        25000,
                        'MOKA-B',
                        'SKU-B',
                        NULL,
                        FALSE,
                        TRUE
                    );
                """
            )

    def test_transaction_rolls_back_inventory_and_details_when_detail_creation_fails(
        self,
    ):
        transaction_data = {
            "sync_id": self.sync_id,
            "transaction_id": self.transaction_id,
            "transaction_time": datetime.now(timezone.utc),
            "item_amount": 150,
            "shipping_amount": None,
            "total_amount": 150,
            "payment_method": "cash",
            "payment_amount": 150,
            "change_amount": 0,
            "items": [
                {
                    "item_name": "Item A",
                    "variant": "Default",
                    "quantity": 1,
                    "unit_price": 20000,
                    "discount": 0,
                    "subtotal": 20000,
                },
                {
                    "item_name": "Item B",
                    "variant": "Default",
                    "quantity": 1,
                    "unit_price": 30000,
                    "discount": 40000,
                    "subtotal": -10000,
                },
            ],
        }

        # lewati_harga: diskon kini ditolak di validasi harga (QA 18 Okt 2026),
        # jadi kegagalan baris detail dipicu lewat jalur ACC pemilik yang
        # tidak memeriksa harga ulang. Yang diuji tetap rollback-nya.
        with self.assertRaises(IntegrityError):
            persist_transaction(
                transaction_data,
                "W001",
                lewati_harga=True,
            )

        item_a = Item.objects.get(
            item_name="Item A",
            variant="Default",
        )
        item_b = Item.objects.get(
            item_name="Item B",
            variant="Default",
        )

        self.assertEqual(item_a.in_stock, 10)
        self.assertEqual(item_b.in_stock, 20)

        self.assertFalse(
            Transactions.objects.filter(
                sync_id=self.sync_id
            ).exists()
        )

        self.assertFalse(
            TransactionDetail.objects.filter(
                sync_id=self.sync_id
            ).exists()
        )

    def test_price_below_floor_is_rejected(self):
        transaction_data = {
            "sync_id": self.sync_id,
            "transaction_id": self.transaction_id,
            "transaction_time": datetime.now(timezone.utc),
            "item_amount": 14000,
            "shipping_amount": None,
            "total_amount": 14000,
            "payment_method": "cash",
            "payment_amount": 14000,
            "change_amount": 0,
            "items": [
                {
                    "item_name": "Item A",
                    "variant": "Default",
                    "quantity": 1,
                    "unit_price": 14000,
                    "discount": 0,
                    "subtotal": 14000,
                }
            ],
        }

        with self.assertRaises(TransactionValidationError) as context:
            persist_transaction(
                transaction_data,
                "W001",
            )

        self.assertEqual(
            str(context.exception),
            "Harga barang di bawah harga floor.",
        )

        # Negosiasi di bawah floor ditolak sebelum stok tersentuh.
        item_a = Item.objects.get(
            item_name="Item A",
            variant="Default",
        )
        self.assertEqual(item_a.in_stock, 10)

        self.assertFalse(
            Transactions.objects.filter(
                sync_id=self.sync_id
            ).exists()
        )

    def test_price_above_ceiling_is_rejected(self):
        transaction_data = {
            "sync_id": self.sync_id,
            "transaction_id": self.transaction_id,
            "transaction_time": datetime.now(timezone.utc),
            "item_amount": 25000,
            "shipping_amount": None,
            "total_amount": 25000,
            "payment_method": "cash",
            "payment_amount": 25000,
            "change_amount": 0,
            "items": [
                {
                    "item_name": "Item A",
                    "variant": "Default",
                    "quantity": 1,
                    "unit_price": 25000,
                    "discount": 0,
                    "subtotal": 25000,
                }
            ],
        }

        with self.assertRaises(TransactionValidationError) as context:
            persist_transaction(
                transaction_data,
                "W001",
            )

        self.assertEqual(
            str(context.exception),
            "Harga barang di atas harga pricelist.",
        )

        item_a = Item.objects.get(
            item_name="Item A",
            variant="Default",
        )
        self.assertEqual(item_a.in_stock, 10)

        self.assertFalse(
            Transactions.objects.filter(
                sync_id=self.sync_id
            ).exists()
        )

    def test_idempotent_retry_after_race_does_not_double_deduct_stock(self):
        first_data = {
            "sync_id": self.sync_id,
            "transaction_id": self.transaction_id,
            "transaction_time": datetime.now(timezone.utc),
            "item_amount": 100,
            "shipping_amount": None,
            "total_amount": 100,
            "payment_method": "cash",
            "payment_amount": 100,
            "change_amount": 0,
            "items": [
                {
                    "item_name": "Item A",
                    "variant": "Default",
                    "quantity": 1,
                    "unit_price": 20000,
                    "discount": 0,
                    "subtotal": 20000,
                }
            ],
        }

        status, _ = persist_transaction(first_data, "W001")

        self.assertIs(status, TransactionSyncStatus.NEW)

        # Simulate a concurrent duplicate: this request locks the item
        # rows and deducts stock AFTER the first submission already
        # committed, then hits the unique constraint on the header
        # INSERT. The retry must return idempotent while its own stock
        # deduction is rolled back, not committed.
        with patch(
            "pos.transaction_service.resolve_transaction_request"
        ) as mock_resolve:
            def resolve_then_idempotent(sync_id, transaction_id):
                # First call (pre-INSERT): identity not visible yet -> NEW.
                mock_resolve.side_effect = None
                mock_resolve.return_value = (
                    TransactionSyncStatus.IDEMPOTENT,
                    Transactions.objects.get(sync_id=self.sync_id),
                )
                return TransactionSyncStatus.NEW, None

            mock_resolve.side_effect = resolve_then_idempotent

            status, record = persist_transaction(first_data, "W001")

        self.assertIs(status, TransactionSyncStatus.IDEMPOTENT)
        self.assertEqual(record.sync_id, self.sync_id)

        # Exactly one deduction must have survived: 10 - 1 = 9.
        # Without the rollback fix this commit would make it 8.
        item_a = Item.objects.get(
            item_name="Item A",
            variant="Default",
        )
        self.assertEqual(item_a.in_stock, 9)

        self.assertEqual(
            TransactionDetail.objects.filter(
                sync_id=self.sync_id
            ).count(),
            1,
        )

class ItemListEndpointTests(ExternalPostgresTableMixin, TransactionTestCase):
    """GET /api/items/ lewat endpoint nyata dengan tabel item Postgres.

    Endpoint yang paling dipakai frontend: ranking pencarian, paginasi
    (default 30, opsional sampai 200 untuk sync katalog tablet),
    dan jaminan data modal tidak pernah ikut terkirim.
    """

    pg_trgm_available = False

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        # Database produksi punya pg_trgm (dipakai TrigramSimilarity);
        # test database mungkin belum. Coba pasang; kalau tidak berizin,
        # test pencarian di-skip dengan alasan yang jelas.
        with connection.cursor() as cursor:
            try:
                cursor.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")
                cls.pg_trgm_available = True
            except Exception:
                cls.pg_trgm_available = False

    @classmethod
    def tearDownClass(cls):
        with connection.cursor() as cursor:
            cursor.execute("DROP TABLE IF EXISTS pg_temp.item;")

        super().tearDownClass()

    def setUp(self):
        self.url = "/api/items/"
        # Sejak 17 Okt 2026 /api/items/ wajib token tablet; uji pencarian di
        # sini fokus ke hasil kueri, jadi autentikasinya dilepas.
        _lepas_auth_item_list(self)

        self._ensure_external_tables()

        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO item (
                    item_name, variant, brand, category, cost_amount,
                    ecommerce_item, in_stock, max_price, min_price,
                    moka_id, sku, stock_alert, track_alert, track_stock
                )
                VALUES
                    (
                        'Body Lidah', 'Sapi', 'BrandA', 'Daging', 50000,
                        FALSE, 5, 120000, 95000,
                        'M1', 'SKU1', 2, FALSE, TRUE
                    ),
                    (
                        'Ayam', 'Utuh', 'BrandB', 'Unggas', 20000,
                        FALSE, 0, 45000, 35000,
                        'M2', 'SKU2', 5, FALSE, TRUE
                    ),
                    (
                        'Ayam', 'Panen', 'BrandB', 'Unggas', 21000,
                        FALSE, 8, 47000, 36000,
                        'M3', 'SKU3', NULL, FALSE, TRUE
                    ),
                    (
                        'Bawang Merah', '1 kg', 'BrandC', 'Sayur', 15000,
                        FALSE, 100, 28000, 22000,
                        'M4', 'SKU4', 10, FALSE, TRUE
                    );
                """
            )

    def test_item_list_returns_floor_price_and_hides_cost(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)

        results = response.data["results"]

        bawang = next(
            item
            for item in results
            if item["item_name"] == "Bawang Merah"
        )

        self.assertEqual(bawang["price"], 28000)
        self.assertEqual(bawang["floor_price"], 22000)
        self.assertEqual(bawang["stock"], 100)

        # Aturan wajib gagal: data modal tidak boleh sampai ke tab.
        self.assertNotIn("cost_amount", bawang)
        self.assertNotIn("min_price", bawang)
        self.assertNotIn("brand", bawang)
        self.assertNotIn("moka_id", bawang)
        self.assertNotIn("sku", bawang)

    def test_out_of_stock_item_still_shows_price(self):
        response = self.client.get(self.url)

        results = response.data["results"]

        ayam_utuh = next(
            item
            for item in results
            if item["item_name"] == "Ayam"
            and item["variant"] == "Utuh"
        )

        self.assertEqual(ayam_utuh["stock"], 0)
        self.assertEqual(ayam_utuh["price"], 45000)

    def test_pagination_page_size_is_30(self):
        with connection.cursor() as cursor:
            for number in range(35):
                cursor.execute(
                    """
                    INSERT INTO item (
                        item_name, variant, brand, category, cost_amount,
                        ecommerce_item, in_stock, max_price, min_price,
                        moka_id, sku, stock_alert, track_alert, track_stock
                    )
                    VALUES (
                        %s, 'Default', 'B', 'C', 1000,
                        FALSE, 1, 2000, 1500,
                        'MK', 'S', NULL, FALSE, FALSE
                    );
                    """,
                    [f"Bulk Item {number:02d}"],
                )

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["results"]), 30)
        self.assertIn("next", response.data)

        second_page = self.client.get(self.url, {"page": 2})

        self.assertEqual(second_page.status_code, 200)
        self.assertEqual(len(second_page.data["results"]), 9)

    def test_pagination_page_size_param_up_to_200(self):
        with connection.cursor() as cursor:
            for number in range(210):
                cursor.execute(
                    """
                    INSERT INTO item (
                        item_name, variant, brand, category, cost_amount,
                        ecommerce_item, in_stock, max_price, min_price,
                        moka_id, sku, stock_alert, track_alert, track_stock
                    )
                    VALUES (
                        %s, 'Default', 'B', 'C', 1000,
                        FALSE, 1, 2000, 1500,
                        'MK', 'S', NULL, FALSE, FALSE
                    );
                    """,
                    [f"Bulk Item {number:03d}"],
                )

        # Sync katalog tablet: 214 baris dalam 2 request, bukan 8.
        big_page = self.client.get(self.url, {"page_size": 200})

        self.assertEqual(big_page.status_code, 200)
        self.assertEqual(len(big_page.data["results"]), 200)
        self.assertIsNotNone(big_page.data["next"])

        tail = self.client.get(self.url, {"page": 2, "page_size": 200})

        self.assertEqual(tail.status_code, 200)
        self.assertEqual(len(tail.data["results"]), 14)
        self.assertIsNone(tail.data["next"])

        # Di atas 200 dipangkas ke 200, bukan ditolak dan bukan raksasa.
        capped = self.client.get(self.url, {"page_size": 500})

        self.assertEqual(capped.status_code, 200)
        self.assertEqual(len(capped.data["results"]), 200)

    def test_alias_search_finds_item_by_variant(self):
        if not self.pg_trgm_available:
            self.skipTest(
                "pg_trgm extension is not available on the test database."
            )

        # "body lidah" = alias yang dikenal kasir; hasil membawa harga,
        # stok, dan tetap satu kelompok per item.
        response = self.client.get(
            self.url,
            {"search": "body lidah"},
        )

        self.assertEqual(response.status_code, 200)

        names = [item["item_name"] for item in response.data["results"]]

        self.assertIn("Body Lidah", names)

    def test_search_ranks_exact_match_first(self):
        if not self.pg_trgm_available:
            self.skipTest(
                "pg_trgm extension is not available on the test database."
            )

        response = self.client.get(
            self.url,
            {"search": "bawang"},
        )

        self.assertEqual(response.status_code, 200)

        results = response.data["results"]

        self.assertTrue(results)
        self.assertEqual(results[0]["item_name"], "Bawang Merah")


class ItemAliasSearchTests(ExternalPostgresTableMixin, TransactionTestCase):
    """Perilaku default pencarian alias dan fallback bila tabel tidak tersedia.

    Tes mengunci bahwa alias otomatis dipakai, kueri nama tetap sama pada
    halaman pertama, dan tabel alias yang hilang membuat fallback ke kueri lama.
    """

    pg_trgm_available = False

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        with connection.cursor() as cursor:
            try:
                cursor.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")
                cls.pg_trgm_available = True
            except Exception:
                cls.pg_trgm_available = False

    @classmethod
    def tearDownClass(cls):
        with connection.cursor() as cursor:
            cursor.execute("DROP TABLE IF EXISTS pg_temp.item_alias;")
            cursor.execute("DROP TABLE IF EXISTS pg_temp.item;")

        super().tearDownClass()

    def setUp(self):
        self.url = "/api/items/"
        # Sejak 17 Okt 2026 /api/items/ wajib token tablet; uji pencarian di
        # sini fokus ke hasil kueri, jadi autentikasinya dilepas.
        _lepas_auth_item_list(self)

        # Deteksi tabel di-cache per proses; di sini tabel TEMP dibuat ulang
        # setiap tes, jadi cache itu harus dilupakan dulu.
        reset_alias_table_cache()

        self._ensure_external_tables()

        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO item (
                    item_name, variant, brand, category, cost_amount,
                    ecommerce_item, in_stock, max_price, min_price,
                    moka_id, sku, stock_alert, track_alert, track_stock
                )
                VALUES
                    (
                        'Washtafel Grand', 'Putih', 'BrandA', 'Sanitair', 70000,
                        FALSE, 4, 120000, 95000,
                        'M1', 'SKU1', 2, FALSE, TRUE
                    ),
                    (
                        'Bak Cuci Piring', 'Stainless', 'BrandA', 'Sanitair', 90000,
                        FALSE, 6, 150000, 130000,
                        'M2', 'SKU2', 1, FALSE, TRUE
                    ),
                    (
                        'Keran Tembok', '1/2', 'BrandB', 'Sanitair', 30000,
                        FALSE, 12, 55000, 45000,
                        'M3', 'SKU3', NULL, FALSE, TRUE
                    );
                """
            )

            cursor.execute(
                """
                INSERT INTO item_alias (frasa, item_name)
                VALUES
                    ('zink', 'Washtafel Grand'),
                    ('wastafel', 'Washtafel Grand'),
                    ('bak cuci piring', 'Bak Cuci Piring'),
                    ('kitchen sink', 'Bak Cuci Piring');
                """
            )

    def _search(self, term):
        response = self.client.get(self.url, {"search": term})
        self.assertEqual(response.status_code, 200)
        return [
            (item["item_name"], item["variant"])
            for item in response.data["results"]
        ]

    def test_alias_phrase_finds_item_only_via_alias(self):
        if not self.pg_trgm_available:
            self.skipTest(
                "pg_trgm extension is not available on the test database."
            )

        # Frasa "zink" tidak ada di nama/varian dan kemiripannya di bawah
        # 0.3; hasilnya hanya dapat ditemukan lewat tabel item_alias.
        found = self._search("zink")

        self.assertIn(("Washtafel Grand", "Putih"), found)

    def test_alias_path_returns_correct_price_floor_and_stock(self):
        if not self.pg_trgm_available:
            self.skipTest(
                "pg_trgm extension is not available on the test database."
            )

        # "zink" hanya cocok lewat tabel alias, jadi hasilnya pasti dari
        # jalur AliasSearchResults (bukan fallback ORM). Nilai ketiga kolom
        # dibuat saling beda (stok 4, ceiling 120000, floor 95000) supaya
        # pergeseran posisional langsung ketahuan: dulu harga tampil =
        # floor, floor = stok, stok = ceiling.
        response = self.client.get(self.url, {"search": "zink"})

        self.assertEqual(response.status_code, 200)

        row = next(
            item
            for item in response.data["results"]
            if item["item_name"] == "Washtafel Grand"
        )

        self.assertEqual(row["price"], 120000)
        self.assertEqual(row["floor_price"], 95000)
        self.assertEqual(row["stock"], 4)

    def test_alias_path_keeps_legacy_results_for_name_queries(self):
        if not self.pg_trgm_available:
            self.skipTest(
                "pg_trgm extension is not available on the test database."
            )

        # Kueri nama harus menghasilkan daftar dan urutan halaman pertama
        # yang sama seperti jalur lama; paginator lama tetap menghitung total.
        with patch("pos.views.search_items", return_value=None):
            legacy = self.client.get(self.url, {"search": "keran tembok"})
        alias_path = self.client.get(self.url, {"search": "keran tembok"})

        self.assertEqual(legacy.status_code, 200)
        self.assertEqual(alias_path.status_code, 200)
        self.assertEqual(legacy.data["count"], alias_path.data["count"])
        self.assertEqual(legacy.data["results"], alias_path.data["results"])

    def test_item_name_and_variant_outrank_alias_only_matches(self):
        """Permintaan pemilik 2026-10-17: alias bukan bobot setara nama.

        Kueri "pintu" harus menaruh barang yang namanya memuat "pintu" di atas
        barang yang hanya cocok lewat alias "pintu", walau alias itu persis
        sama dengan kueri.
        """
        if not self.pg_trgm_available:
            self.skipTest(
                "pg_trgm extension is not available on the test database."
            )

        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO item (
                    item_name, variant, brand, category, cost_amount,
                    ecommerce_item, in_stock, max_price, min_price,
                    moka_id, sku, stock_alert, track_alert, track_stock
                ) VALUES
                    ('Pintu Besi', 'Default', 'B', 'C', 1000,
                        FALSE, 1, 2000, 1500, 'MK1', 'S1', NULL, FALSE, TRUE),
                    ('Grendel Alias', 'Default', 'B', 'C', 1000,
                        FALSE, 1, 2000, 1500, 'MK2', 'S2', NULL, FALSE, TRUE);
                """
            )
            cursor.execute(
                """
                INSERT INTO item_alias (frasa, item_name)
                VALUES ('pintu', 'Grendel Alias');
                """
            )

        found = self._search("pintu")

        name_position = found.index(("Pintu Besi", "Default"))
        alias_position = found.index(("Grendel Alias", "Default"))

        self.assertLess(name_position, alias_position)

    def test_missing_alias_table_falls_back_to_legacy_search(self):
        if not self.pg_trgm_available:
            self.skipTest(
                "pg_trgm extension is not available on the test database."
            )

        with connection.cursor() as cursor:
            cursor.execute("DROP TABLE IF EXISTS pg_temp.item_alias;")

        reset_alias_table_cache()

        found = self._search("keran tembok")

        # Tidak error, dan hasilnya hasil jalur lama.
        self.assertIn(("Keran Tembok", "1/2"), found)

    def test_alias_query_failure_falls_back_to_legacy_search(self):
        if not self.pg_trgm_available:
            self.skipTest(
                "pg_trgm extension is not available on the test database."
            )

        from django.db import DatabaseError

        from . import item_search
        original_execute = item_search._execute_search_sql
        with patch(
            "pos.item_search._execute_search_sql",
            side_effect=[DatabaseError("simulated"), original_execute],
        ), patch("pos.pagination.logger.warning") as mock_warning:
            response = self.client.get(self.url, {"search": "keran tembok"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 3)
        self.assertIn(
            ("Keran Tembok", "1/2"),
            [
                (item["item_name"], item["variant"])
                for item in response.data["results"]
            ],
        )
        mock_warning.assert_called_once()

    def test_alias_pagination_returns_exact_total_beyond_old_candidate_cap(self):
        if not self.pg_trgm_available:
            self.skipTest(
                "pg_trgm extension is not available on the test database."
            )

        with connection.cursor() as cursor:
            for number in range(170):
                item_name = f"Alias Item {number:03d}"
                cursor.execute(
                    """
                    INSERT INTO item (
                        item_name, variant, brand, category, cost_amount,
                        ecommerce_item, in_stock, max_price, min_price,
                        moka_id, sku, stock_alert, track_alert, track_stock
                    ) VALUES (
                        %s, 'Default', 'B', 'C', 1000,
                        FALSE, 1, 2000, 1500,
                        'MK', 'S', NULL, FALSE, FALSE
                    )
                    """,
                    [item_name],
                )
                cursor.execute(
                    "INSERT INTO item_alias (frasa, item_name) VALUES ('zink', %s)",
                    [item_name],
                )

        first_page = self.client.get(self.url, {"search": "zink", "page": 1})
        sixth_page = self.client.get(self.url, {"search": "zink", "page": 6})

        self.assertEqual(first_page.status_code, 200)
        self.assertEqual(sixth_page.status_code, 200)
        self.assertEqual(first_page.data["count"], 171)
        self.assertEqual(sixth_page.data["count"], 171)
        self.assertEqual(len(first_page.data["results"]), 30)
        self.assertEqual(len(sixth_page.data["results"]), 21)
        self.assertIsNone(sixth_page.data["next"])


class DeviceAuthenticationTests(TestCase):
    def setUp(self):
        self.device = MagicMock()
        self.device.id = uuid4()
        self.device.device_code = "T01"

    @override_settings(
        DEVICE_AUTH_SIGNING_KEY="test-device-signing-key"
    )
    def test_create_and_decode_device_token(self):
        token = create_device_token(self.device)

        device_id, device_code = _decode_device_token(token)

        self.assertEqual(device_id, str(self.device.id))
        self.assertEqual(device_code, "T01")

    @override_settings(
        DEVICE_AUTH_SIGNING_KEY="test-device-signing-key"
    )
    def test_tampered_device_token_is_rejected(self):
        token = create_device_token(self.device)

        tampered_token = token[:-1] + (
            "a" if token[-1] != "a" else "b"
        )

        with self.assertRaises(AuthenticationFailed):
            _decode_device_token(tampered_token)

    @override_settings(
        DEVICE_AUTH_SIGNING_KEY="test-device-signing-key"
    )
    def test_invalid_device_token_is_rejected(self):
        with self.assertRaises(AuthenticationFailed):
            _decode_device_token("invalid-token")

    @override_settings(
        DEVICE_AUTH_SIGNING_KEY="test-device-signing-key"
    )
    @patch("pos.device_authentication.Device.objects.get")
    def test_device_token_for_nonexistent_device_is_rejected(
        self,
        mock_device_get,
    ):
        token = create_device_token(self.device)

        mock_device_get.side_effect = Device.DoesNotExist

        request = APIRequestFactory().get(
            "/api/items/",
            HTTP_AUTHORIZATION=f"Device {token}",
        )

        authentication = DeviceTokenAuthentication()

        with self.assertRaises(AuthenticationFailed):
            authentication.authenticate(request)

        mock_device_get.assert_called_once_with(
            id=str(self.device.id),
        )

    @override_settings(
        DEVICE_AUTH_SIGNING_KEY="test-device-signing-key"
    )
    def test_missing_authorization_header_returns_none(self):
        request = APIRequestFactory().get("/api/items/")

        authentication = DeviceTokenAuthentication()

        self.assertIsNone(
            authentication.authenticate(request)
        )

    @override_settings(
        DEVICE_AUTH_SIGNING_KEY="test-device-signing-key"
    )
    def test_valid_authorization_header_returns_authenticated_device(
        self,
    ):
        token = create_device_token(self.device)

        with patch(
            "pos.device_authentication.Device.objects.get",
            return_value=self.device,
        ):
            request = APIRequestFactory().get(
                "/api/items/",
                HTTP_AUTHORIZATION=f"Device {token}",
            )

            authentication = DeviceTokenAuthentication()

            authenticated_device, returned_token = (
                authentication.authenticate(request)
            )

        self.assertIs(
            authenticated_device.device,
            self.device,
        )
        self.assertEqual(returned_token, token)
        self.assertTrue(
            authenticated_device.is_authenticated
        )

    @override_settings(
        DEVICE_AUTH_SIGNING_KEY="test-device-signing-key"
    )
    def test_device_code_mismatch_is_rejected(self):
        token = create_device_token(self.device)

        mismatched_device = MagicMock()
        mismatched_device.id = self.device.id
        mismatched_device.device_code = "T02"

        with patch(
            "pos.device_authentication.Device.objects.get",
            return_value=mismatched_device,
        ):
            request = APIRequestFactory().get(
                "/api/items/",
                HTTP_AUTHORIZATION=f"Device {token}",
            )

            authentication = DeviceTokenAuthentication()

            with self.assertRaises(AuthenticationFailed):
                authentication.authenticate(request)


class ItemSerializerTests(SimpleTestCase):
    def test_floor_price_is_exposed_without_cost_data(self):
        item = Item(
            item_name="Item A",
            variant="Default",
            brand="Test",
            category="Test",
            cost_amount=10000,
            ecommerce_item=False,
            in_stock=3,
            max_price=20000,
            min_price=15000,
            moka_id="MOKA-A",
            sku="SKU-A",
            stock_alert=None,
            track_alert=False,
            track_stock=True,
        )

        data = ItemSerializer(item).data

        # Kasir perlu harga floor untuk nego tanpa bertanya pemilik,
        # tetapi data modal tidak boleh ikut terkirim.
        self.assertEqual(data["price"], 20000)
        self.assertEqual(data["floor_price"], 15000)
        self.assertEqual(data["stock"], 3)

        self.assertNotIn("cost_amount", data)
        self.assertNotIn("min_price", data)
        self.assertNotIn("brand", data)
        self.assertNotIn("moka_id", data)


@override_settings(DEVICE_AUTH_SIGNING_KEY="test-device-signing-key")
class TransactionBatchSyncAPITests(APITestCase):
    """Batch sync: satu PIN per batch, hasil per transaksi."""

    def setUp(self):
        self.url = "/api/transactions/sync/"

        self.device = MagicMock()
        self.device.id = uuid4()
        self.device.device_code = "T01"

        self.device_headers = {
            "HTTP_AUTHORIZATION": f"Device {create_device_token(self.device)}",
        }

        self.queued_transaction = {
            "sync_id": str(uuid4()),
            "transaction_id": "T01-000001",
            "transaction_time": datetime.now(timezone.utc).isoformat(),
            "item_amount": 95000,
            "shipping_amount": 10000,
            "total_amount": 105000,
            "payment_method": "cash",
            "payment_amount": 110000,
            "change_amount": 5000,
            "items": [
                {
                    "item_name": "Test Item",
                    "variant": "Default",
                    "quantity": 2,
                    "unit_price": 47500,
                    # Toko tidak memakai diskon per baris (QA 18 Okt 2026):
                    # harga nego langsung di unit_price.
                    "discount": 0,
                    "subtotal": 95000,
                }
            ],
        }

        self.payload = {
            "pin": "123456",
            "transactions": [self.queued_transaction],
        }

    def _post_batch(self, payload):
        with patch(
            "pos.device_authentication.Device.objects.get",
            return_value=self.device,
        ):
            return self.client.post(
                self.url,
                payload,
                format="json",
                **self.device_headers,
            )

    def test_missing_device_token_is_rejected(self):
        response = self.client.post(
            self.url,
            self.payload,
            format="json",
        )

        self.assertEqual(response.status_code, 401)

    @patch("pos.views.persist_transaction")
    @patch(
        "pos.transaction_request.verify_worker_pin",
        side_effect=InvalidWorkerCredentials(
            "PIN kasir tidak cocok."
        ),
    )
    def test_invalid_worker_credentials_reject_whole_batch(
        self,
        mock_verify_pin,
        mock_persist,
    ):
        response = self._post_batch(self.payload)

        self.assertEqual(response.status_code, 400)
        self.assertIn("pin", response.data)
        mock_persist.assert_not_called()

    @patch("pos.views.persist_transaction")
    @patch(
        "pos.transaction_request.verify_worker_pin",
        return_value="W001",
    )
    def test_batch_pin_worker_is_used_for_persisting(
        self,
        mock_verify_pin,
        mock_persist,
    ):
        transaction_record = MagicMock()
        transaction_record.sync_id = self.queued_transaction["sync_id"]
        transaction_record.transaction_id = "T01-000001"

        mock_persist.return_value = (
            TransactionSyncStatus.NEW,
            transaction_record,
        )

        response = self._post_batch(self.payload)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["worker_id"], "W001")
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(response.data["results"][0]["status"], "new")
        self.assertEqual(
            response.data["results"][0]["transaction_id"],
            "T01-000001",
        )

        mock_verify_pin.assert_called_once()
        self.assertEqual(mock_verify_pin.call_args.args[0], "123456")

        _, worker_id, _device_code = mock_persist.call_args.args

        self.assertEqual(worker_id, "W001")

    @patch("pos.views.persist_transaction")
    @patch(
        "pos.transaction_request.verify_worker_pin",
        return_value="W001",
    )
    def test_mixed_outcomes_are_reported_per_transaction(
        self,
        mock_verify_pin,
        mock_persist,
    ):
        accepted = MagicMock()
        accepted.sync_id = uuid4()
        accepted.transaction_id = "T01-000001"

        mock_persist.side_effect = [
            (TransactionSyncStatus.NEW, accepted),
            TransactionValidationError("Stok barang habis."),
            TransactionSyncConflict("Identitas nota bentrok."),
        ]

        second = {
            **self.queued_transaction,
            "sync_id": str(uuid4()),
            "transaction_id": "T01-000002",
        }
        third = {
            **self.queued_transaction,
            "sync_id": str(uuid4()),
            "transaction_id": "T01-000003",
        }

        payload = {
            "pin": "123456",
            "transactions": [self.queued_transaction, second, third],
        }

        # Setiap penolakan wajib meninggalkan jejak di log server
        # (keputusan 2026-09-26: tanpa tabel penolakan).
        with self.assertLogs("pos.views", level="WARNING"):
            response = self._post_batch(payload)

        self.assertEqual(response.status_code, 200)

        results = response.data["results"]

        self.assertEqual(
            [result["status"] for result in results],
            ["new", "rejected", "conflict"],
        )
        self.assertEqual(results[1]["detail"], "Stok barang habis.")
        self.assertEqual(
            results[2]["detail"],
            "Identitas nota bentrok.",
        )

    @patch("pos.views.persist_transaction")
    @patch(
        "pos.transaction_request.verify_worker_pin",
        return_value="W001",
    )
    def test_invalid_queued_payload_is_rejected_without_persisting(
        self,
        mock_verify_pin,
        mock_persist,
    ):
        broken = {
            **self.queued_transaction,
            "payment_amount": 100000,
        }

        response = self._post_batch(
            {"pin": "123456", "transactions": [broken]}
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["results"][0]["status"], "rejected")
        self.assertIn(
            "payment_amount",
            response.data["results"][0]["detail"],
        )
        mock_persist.assert_not_called()

    @patch("pos.views.persist_transaction")
    @patch(
        "pos.transaction_request.verify_worker_pin",
        return_value="W001",
    )
    def test_offline_nota_without_number_is_accepted(
        self,
        mock_verify_pin,
        mock_persist,
    ):
        transaction_record = MagicMock()
        transaction_record.sync_id = uuid4()
        transaction_record.transaction_id = "T01-000009"

        mock_persist.return_value = (
            TransactionSyncStatus.NEW,
            transaction_record,
        )

        queued = {
            **self.queued_transaction,
            "transaction_id": None,
        }

        response = self._post_batch(
            {"pin": "123456", "transactions": [queued]}
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data["results"][0]["transaction_id"],
            "T01-000009",
        )

    @patch("pos.views.persist_transaction")
    @patch(
        "pos.transaction_request.verify_worker_pin",
        return_value="W001",
    )
    def test_empty_queue_is_rejected(
        self,
        mock_verify_pin,
        mock_persist,
    ):
        response = self._post_batch(
            {"pin": "123456", "transactions": []}
        )

        self.assertEqual(response.status_code, 400)
        mock_persist.assert_not_called()

    @patch("pos.views.persist_transaction")
    @patch(
        "pos.transaction_request.verify_worker_pin",
        return_value="W001",
    )
    def test_oversized_batch_is_rejected(
        self,
        mock_verify_pin,
        mock_persist,
    ):
        payload = {
            "pin": "123456",
            "transactions": [self.queued_transaction]
            * (MAX_BATCH_SYNC_TRANSACTIONS + 1),
        }

        response = self._post_batch(payload)

        self.assertEqual(response.status_code, 400)
        mock_persist.assert_not_called()

    def _per_nota_member(self, pin, transaction_id, **overrides):
        member = {
            **self.queued_transaction,
            "sync_id": str(uuid4()),
            "transaction_id": transaction_id,
        }
        member.update(overrides)

        if pin is not None:
            member["pin"] = pin

        return member

    def _per_nota_record(self, member):
        record = MagicMock()
        record.sync_id = member["sync_id"]
        record.transaction_id = member["transaction_id"]
        return record

    @patch("pos.views.persist_transaction")
    @patch(
        "pos.transaction_request.verify_worker_pin",
        side_effect=["W001", "W002"],
    )
    def test_per_nota_pins_attribute_each_nota_to_its_own_worker(
        self,
        mock_verify_pin,
        mock_persist,
    ):
        first = self._per_nota_member("111111", "T01-000001")
        second = self._per_nota_member("222222", "T01-000002")

        mock_persist.side_effect = [
            (TransactionSyncStatus.NEW, self._per_nota_record(first)),
            (TransactionSyncStatus.NEW, self._per_nota_record(second)),
        ]

        response = self._post_batch({"transactions": [first, second]})

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("worker_id", response.data)
        self.assertEqual(
            [result["status"] for result in response.data["results"]],
            ["new", "new"],
        )
        self.assertEqual(mock_verify_pin.call_count, 2)
        self.assertEqual(
            [call.args[1] for call in mock_persist.call_args_list],
            ["W001", "W002"],
        )

    @patch("pos.views.persist_transaction")
    @patch("pos.transaction_request.verify_worker_pin")
    def test_per_nota_bad_pin_rejects_only_that_nota(
        self,
        mock_verify_pin,
        mock_persist,
    ):
        def resolve_pin(pin, **_kwargs):
            if pin == "111111":
                return "W001"
            raise InvalidWorkerCredentials("PIN kasir tidak cocok.")

        mock_verify_pin.side_effect = resolve_pin

        good = self._per_nota_member("111111", "T01-000001")
        bad = self._per_nota_member("999999", "T01-000002")
        bad_again = self._per_nota_member("999999", "T01-000003")

        mock_persist.return_value = (
            TransactionSyncStatus.NEW,
            self._per_nota_record(good),
        )

        with self.assertLogs("pos.views", level="WARNING"):
            response = self._post_batch(
                {"transactions": [good, bad, bad_again]}
            )

        self.assertEqual(response.status_code, 200)
        results = response.data["results"]
        self.assertEqual(
            [result["status"] for result in results],
            ["new", "rejected", "rejected"],
        )
        self.assertIn("PIN kasir tidak cocok", results[1]["detail"])
        # PIN buruk yang sama diverifikasi sekali, bukan per nota.
        self.assertEqual(mock_verify_pin.call_count, 2)
        mock_persist.assert_called_once()

    @patch("pos.views.persist_transaction")
    @patch(
        "pos.transaction_request.verify_worker_pin",
        side_effect=["W001", "W002"],
    )
    def test_per_nota_resent_batch_is_idempotent(
        self,
        mock_verify_pin,
        mock_persist,
    ):
        first = self._per_nota_member("111111", "T01-000001")
        second = self._per_nota_member("222222", "T01-000002")

        mock_persist.side_effect = [
            (TransactionSyncStatus.IDEMPOTENT, self._per_nota_record(first)),
            (TransactionSyncStatus.IDEMPOTENT, self._per_nota_record(second)),
        ]

        response = self._post_batch({"transactions": [first, second]})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [result["status"] for result in response.data["results"]],
            ["idempotent", "idempotent"],
        )

    @patch("pos.views.persist_transaction")
    @patch(
        "pos.transaction_request.verify_worker_pin",
        return_value="W002",
    )
    def test_per_nota_worker_mismatch_is_conflict(
        self,
        mock_verify_pin,
        mock_persist,
    ):
        member = self._per_nota_member("222222", "T01-000001")

        mock_persist.side_effect = TransactionSyncConflict(
            "Identitas nota bentrok."
        )

        with self.assertLogs("pos.views", level="WARNING"):
            response = self._post_batch({"transactions": [member]})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data["results"][0]["status"],
            "conflict",
        )
        self.assertIn(
            "Identitas nota bentrok",
            response.data["results"][0]["detail"],
        )

    @patch("pos.views.persist_transaction")
    @patch(
        "pos.transaction_request.verify_worker_pin",
        return_value="W001",
    )
    def test_mixed_top_level_and_member_pin_is_rejected_whole(
        self,
        mock_verify_pin,
        mock_persist,
    ):
        member = self._per_nota_member("123456", "T01-000001")

        response = self._post_batch(
            {"pin": "123456", "transactions": [member]}
        )

        self.assertEqual(response.status_code, 400)
        mock_verify_pin.assert_not_called()
        mock_persist.assert_not_called()

    @patch("pos.views.persist_transaction")
    @patch(
        "pos.transaction_request.verify_worker_pin",
        return_value="W001",
    )
    def test_per_nota_member_without_pin_is_rejected_only(
        self,
        mock_verify_pin,
        mock_persist,
    ):
        missing = self._per_nota_member(None, "T01-000001")
        good = self._per_nota_member("111111", "T01-000002")

        mock_persist.return_value = (
            TransactionSyncStatus.NEW,
            self._per_nota_record(good),
        )

        with self.assertLogs("pos.views", level="WARNING"):
            response = self._post_batch({"transactions": [missing, good]})

        self.assertEqual(response.status_code, 200)
        results = response.data["results"]
        self.assertEqual(
            [result["status"] for result in results],
            ["rejected", "new"],
        )
        self.assertIn("PIN wajib", results[0]["detail"])
        mock_persist.assert_called_once()

    @patch("pos.views.persist_transaction")
    @patch(
        "pos.transaction_request.verify_worker_pin",
        return_value="W001",
    )
    def test_per_nota_oversized_batch_is_rejected(
        self,
        mock_verify_pin,
        mock_persist,
    ):
        payload = {
            "transactions": [
                self._per_nota_member("111111", f"T01-{index:06d}")
                for index in range(1, MAX_BATCH_SYNC_TRANSACTIONS + 2)
            ],
        }

        response = self._post_batch(payload)

        self.assertEqual(response.status_code, 400)
        mock_persist.assert_not_called()

    @patch("pos.views.persist_transaction")
    @patch("pos.views.check_pin_allowed", side_effect=PinThrottled(42))
    def test_per_nota_pre_locked_throttle_rejects_whole_request(
        self,
        mock_check_allowed,
        mock_persist,
    ):
        member = self._per_nota_member("111111", "T01-000001")

        response = self._post_batch({"transactions": [member]})

        self.assertEqual(response.status_code, 429)
        self.assertEqual(response["Retry-After"], "42")
        mock_persist.assert_not_called()

    @patch(
        "pos.transaction_request.verify_worker_pin",
        return_value="W001",
    )
    def test_foreign_device_prefix_is_rejected(
        self,
        mock_verify_pin,
    ):
        # Nota ber-prefix device lain tidak boleh disinkronkan dari device
        # ini: prefix nomor nota harus cocok dengan device token. Cek ini
        # berjalan sebelum persist menyentuh DB, jadi persist asli boleh
        # dipanggil (tanpa mock) dan penolakannya tertangkap rapi.
        queued = {
            **self.queued_transaction,
            "transaction_id": "T02-000001",
        }

        response = self._post_batch(
            {"pin": "123456", "transactions": [queued]}
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["results"][0]["status"], "rejected")
        self.assertIn(
            "bukan milik perangkat ini",
            response.data["results"][0]["detail"],
        )


class PinThrottleTests(SimpleTestCase):
    """Throttle PIN tanpa Redis: hitungan di cache bersama."""

    def setUp(self):
        from django.core.cache import cache

        cache.clear()
        self.identifier = "ip:1.2.3.4"

    def tearDown(self):
        from django.core.cache import cache

        cache.clear()

    def test_lockout_after_max_failures(self):
        for _ in range(MAX_PIN_ATTEMPTS - 1):
            register_pin_failure(self.identifier)

        # Belum terkunci pada percobaan ke-9.
        check_pin_allowed(self.identifier)

        register_pin_failure(self.identifier)

        with self.assertRaises(PinThrottled) as context:
            check_pin_allowed(self.identifier)

        self.assertGreater(context.exception.retry_after_seconds, 0)
        self.assertLessEqual(
            context.exception.retry_after_seconds,
            LOCKOUT_SECONDS,
        )

    def test_successful_verification_clears_counter(self):
        for _ in range(MAX_PIN_ATTEMPTS - 1):
            register_pin_failure(self.identifier)

        clear_pin_attempts(self.identifier)

        # Satu kegagalan lagi setelah reset tidak boleh mengunci.
        register_pin_failure(self.identifier)

        check_pin_allowed(self.identifier)

    def test_throttle_key_uses_device_when_authenticated(self):
        from rest_framework.test import APIRequestFactory

        from .transaction_request import pin_throttle_identifier

        device = MagicMock()
        device.device_code = "T07"

        authenticated = MagicMock()
        authenticated.device = device

        request = APIRequestFactory().post("/api/transactions/")
        request.user = authenticated

        self.assertEqual(
            pin_throttle_identifier(request),
            "device:T07",
        )

    def test_throttle_key_falls_back_to_ip(self):
        from rest_framework.test import APIRequestFactory

        from .transaction_request import pin_throttle_identifier

        request = APIRequestFactory().post(
            "/api/transactions/",
            HTTP_X_FORWARDED_FOR="9.9.9.9, 10.0.0.1",
        )
        request.user = MagicMock()
        del request.user.device

        self.assertEqual(
            pin_throttle_identifier(request),
            "ip:9.9.9.9",
        )


class DeviceTokenExpiryTests(TestCase):
    """Device token kedaluwarsa setelah DEVICE_TOKEN_MAX_AGE_SECONDS."""

    @override_settings(
        DEVICE_AUTH_SIGNING_KEY="test-device-signing-key",
    )
    def test_expired_token_is_rejected(self):
        device = MagicMock()
        device.id = uuid4()
        device.device_code = "T01"

        token = create_device_token(device)

        with patch(
            "pos.device_authentication.DEVICE_TOKEN_MAX_AGE_SECONDS",
            -1,
        ):
            with self.assertRaises(AuthenticationFailed) as context:
                _decode_device_token(token)

        self.assertIn("kedaluwarsa", str(context.exception))

    @override_settings(
        DEVICE_AUTH_SIGNING_KEY="test-device-signing-key",
    )
    def test_old_token_outside_renewal_window_is_rejected(self):
        device = MagicMock()
        device.id = uuid4()
        device.device_code = "T01"
        token = create_device_token(device)

        with self.assertRaises(AuthenticationFailed) as context:
            _decode_device_token(token, max_age=-1)

        self.assertIn("masa perpanjangan", str(context.exception))

    @override_settings(
        DEVICE_AUTH_SIGNING_KEY="test-device-signing-key",
    )
    @patch("pos.device_authentication.Device.objects.get")
    def test_renewal_token_resolves_to_the_signed_device(self, mock_get):
        device = MagicMock()
        device.id = uuid4()
        device.device_code = "T07"
        mock_get.return_value = device
        token = create_device_token(device)

        result = get_device_from_token(
            token,
            max_age=DEVICE_TOKEN_RENEWAL_MAX_AGE_SECONDS,
        )

        self.assertIs(result, device)
        mock_get.assert_called_once_with(id=str(device.id))


class TransactionCreateDeviceAuthTests(APITestCase):
    """Endpoint transaksi online kini mewajibkan device token."""

    def setUp(self):
        self.url = "/api/transactions/"
        self.payload = {
            "pin": "123456",
            "transaction": {
                "sync_id": str(uuid4()),
                "transaction_id": "T01-000001",
                "transaction_time": datetime.now(timezone.utc).isoformat(),
                "item_amount": 1000,
                "total_amount": 1000,
                "payment_method": "cash",
                "payment_amount": 1000,
                "change_amount": 0,
                "items": [
                    {
                        "item_name": "Test Item",
                        "variant": "Default",
                        "quantity": 1,
                        "unit_price": 1000,
                        "discount": 0,
                        "subtotal": 1000,
                    }
                ],
            },
        }

    def test_missing_device_token_returns_401(self):
        response = self.client.post(
            self.url,
            self.payload,
            format="json",
        )

        self.assertEqual(response.status_code, 401)

    @patch(
        "pos.transaction_request.verify_worker_pin",
        return_value="W001",
    )
    def test_device_prefix_mismatch_is_rejected_before_persist(
        self,
        mock_verify_pin,
    ):
        device = MagicMock()
        device.id = uuid4()
        device.device_code = "T01"

        # Payload memakai nomor milik T02, sementara token adalah T01.
        self.payload["transaction"]["transaction_id"] = "T02-000001"

        with patch(
            "pos.device_authentication.Device.objects.get",
            return_value=device,
        ):
            response = self.client.post(
                self.url,
                self.payload,
                format="json",
                HTTP_AUTHORIZATION=(
                    f"Device {create_device_token(device)}"
                ),
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data["detail"],
            "Nomor nota bukan milik perangkat ini.",
        )

    @override_settings(
        CACHES={
            "default": {
                "BACKEND":
                    "django.core.cache.backends.locmem.LocMemCache",
                "LOCATION": "pin-throttle-429-test",
            }
        },
    )
    @patch("pos.transaction_request.verify_worker_pin")
    @patch("pos.views.persist_transaction")
    def test_pin_throttle_returns_429_with_retry_after(
        self,
        mock_persist,
        mock_verify_pin,
    ):
        from django.core.cache import cache

        cache.clear()

        device = MagicMock()
        device.id = uuid4()
        device.device_code = "T01"

        # Kunci sumber sebelum request: view harus membalas 429 + Retry-After
        # tanpa menyentuh verifikasi PIN maupun persist.
        register_pin_failure("device:T01")
        register_pin_failure("device:T01")
        register_pin_failure("device:T01")
        register_pin_failure("device:T01")
        register_pin_failure("device:T01")
        register_pin_failure("device:T01")
        register_pin_failure("device:T01")
        register_pin_failure("device:T01")
        register_pin_failure("device:T01")
        register_pin_failure("device:T01")

        with patch(
            "pos.device_authentication.Device.objects.get",
            return_value=device,
        ):
            response = self.client.post(
                self.url,
                self.payload,
                format="json",
                HTTP_AUTHORIZATION=(
                    f"Device {create_device_token(device)}"
                ),
            )

        self.assertEqual(response.status_code, 429)
        self.assertIn("Retry-After", response)

        mock_verify_pin.assert_not_called()
        mock_persist.assert_not_called()

        cache.clear()

    @override_settings(
        CACHES={
            "default": {
                "BACKEND":
                    "django.core.cache.backends.locmem.LocMemCache",
                "LOCATION": "pin-throttle-test-create",
            }
        },
    )
    @patch("pos.views.persist_transaction")
    @patch(
        "pos.transaction_request.verify_worker_pin",
        side_effect=InvalidWorkerCredentials(
            "PIN kasir tidak cocok.",
        ),
    )
    def test_repeated_pin_failures_lock_out_endpoint(self,
        mock_verify_pin,
        mock_persist,
    ):
        from django.core.cache import cache

        device = MagicMock()
        device.id = uuid4()
        device.device_code = "T01"

        headers = {
            "HTTP_AUTHORIZATION": f"Device {create_device_token(device)}",
        }

        with patch(
            "pos.device_authentication.Device.objects.get",
            return_value=device,
        ):
            for attempt in range(MAX_PIN_ATTEMPTS):
                response = self.client.post(
                    self.url,
                    self.payload,
                    format="json",
                    **headers,
                )

                self.assertEqual(response.status_code, 400)

            # Percobaan berikutnya terkunci dengan 429.
            response = self.client.post(
                self.url,
                self.payload,
                format="json",
                **headers,
            )

            self.assertEqual(response.status_code, 429)
            self.assertIn("Retry-After", response)

        cache.clear()
        mock_persist.assert_not_called()

class TransactionHistoryEndpointTests(
    ExternalPostgresTableMixin,
    TransactionTestCase,
):
    """GET riwayat nota per device (keputusan visibilitas 2026-09-26).

    Riwayat hanya boleh menampilkan nota milik device/tab pemanggil,
    dibatasi lewat prefix nomor nota Txx- yang sudah diverifikasi saat
    penulisan. Nota tanpa nomor belum bisa discoping (belum ada kolom
    device pada transactions) dan sengaja tidak ditampilkan.
    """

    @classmethod
    def tearDownClass(cls):
        with connection.cursor() as cursor:
            for table in (
                "refund_event_detail",
                "refund_event",
                "transaction_detail",
                "transactions",
                "item",
                "worker",
            ):
                cursor.execute(
                    f"DROP TABLE IF EXISTS pg_temp.{table};"
                )

        super().tearDownClass()

    def setUp(self):
        self.list_url = "/api/transactions/history/"

        self._ensure_external_tables()

        self.device = MagicMock()
        self.device.id = uuid4()
        self.device.device_code = "T01"

        self.headers = {
            "HTTP_AUTHORIZATION": (
                f"Device {create_device_token(self.device)}"
            ),
        }

        device_patcher = patch(
            "pos.device_authentication.Device.objects.get",
            return_value=self.device,
        )
        device_patcher.start()
        self.addCleanup(device_patcher.stop)

        self.own_time = datetime(2026, 9, 27, 8, 0, tzinfo=timezone.utc)
        self.own_time_newer = datetime(
            2026, 9, 27, 9, 0, tzinfo=timezone.utc
        )

        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO worker (id, name, pin)
                VALUES (%s, %s, %s);
                """,
                ["W001", "Integration Test Worker", "123456"],
            )
            cursor.execute(
                """
                INSERT INTO item (
                    item_name, variant, brand, category, cost_amount,
                    ecommerce_item, in_stock, max_price, min_price,
                    moka_id, sku, stock_alert, track_alert, track_stock
                ) VALUES
                    (
                        'Item A', 'Default', 'Test', 'Test', 10000,
                        FALSE, 10, 20000, 15000,
                        'MOKA-A', 'SKU-A', NULL, FALSE, TRUE
                    ),
                    (
                        'Item B', 'Default', 'Test', 'Test', 10000,
                        FALSE, 20, 30000, 25000,
                        'MOKA-B', 'SKU-B', NULL, FALSE, TRUE
                    );
                """
            )
            cursor.execute(
                """
                INSERT INTO transactions (
                    sync_id, transaction_id, worker_id, transaction_time,
                    item_amount, shipping_amount, total_amount,
                    payment_method, payment_amount, change_amount
                ) VALUES
                    (
                        %s, 'T01-000001', 'W001', %s,
                        20000, NULL, 20000,
                        'cash', 20000, 0
                    ),
                    (
                        %s, 'T01-000002', 'W001', %s,
                        30000, 5000, 35000,
                        'edc', 35000, 0
                    ),
                    (
                        %s, 'T02-000001', 'W001', %s,
                        99000, NULL, 99000,
                        'cash', 99000, 0
                    ),
                    (
                        %s, NULL, 'W001', %s,
                        7000, NULL, 7000,
                        'cash', 7000, 0
                    );
                """,
                [
                    uuid4(), self.own_time,
                    uuid4(), self.own_time_newer,
                    uuid4(), self.own_time,
                    uuid4(), self.own_time_newer,
                ],
            )
            cursor.execute(
                """
                INSERT INTO transaction_detail (
                    sync_id, item_name, variant, discount,
                    quantity, subtotal, unit_price
                )
                SELECT
                    sync_id, 'Item A', 'Default', 0,
                    1, 20000, 20000
                FROM transactions
                WHERE transaction_id = 'T01-000001';
                """
            )
            cursor.execute(
                """
                INSERT INTO transaction_detail (
                    sync_id, item_name, variant, discount,
                    quantity, subtotal, unit_price
                )
                SELECT
                    sync_id, 'Item B', 'Default', 5000,
                    1, 25000, 30000
                FROM transactions
                WHERE transaction_id = 'T01-000001';
                """
            )

    def test_history_requires_device_token(self):
        response = self.client.get(self.list_url)

        self.assertEqual(response.status_code, 401)

    def test_history_lists_only_own_device_notes_newest_first(self):
        response = self.client.get(self.list_url, **self.headers)

        self.assertEqual(response.status_code, 200)

        results = response.data["results"]
        listed_ids = [
            row["transaction_id"] for row in results
        ]

        # Hanya nota T01, terbaru dulu. Nota T02 (device/tab lain) dan
        # nota tanpa nomor tidak boleh muncul.
        self.assertEqual(
            listed_ids,
            ["T01-000002", "T01-000001"],
        )
        self.assertEqual(response.data["count"], 2)

    def test_history_detail_returns_items_and_worker_name(self):
        response = self.client.get(
            f"{self.list_url}T01-000001/",
            **self.headers,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["transaction_id"], "T01-000001")
        self.assertEqual(response.data["total_amount"], 20000)
        self.assertEqual(
            response.data["worker_name"],
            "Integration Test Worker",
        )

        items = response.data["items"]
        self.assertEqual(len(items), 2)
        self.assertEqual(
            [
                (row["item_name"], row["variant"])
                for row in items
            ],
            [("Item A", "Default"), ("Item B", "Default")],
        )
        self.assertEqual(items[1]["discount"], 5000)
        self.assertEqual(items[1]["subtotal"], 25000)

    def test_history_detail_of_other_device_note_returns_404(self):
        # Tanpa PIN kasir: 400 (PIN wajib), bukan 404 maupun 403 —
        # keberadaan nota tab lain tetap tidak bocor dari status ini.
        response = self.client.get(
            f"{self.list_url}T02-000001/",
            **self.headers,
        )

        self.assertEqual(response.status_code, 400)

    def test_history_detail_of_other_device_note_opens_with_person_pin(self):
        # Dengan PIN kasir (keputusan refund 2026-10-05): satu nota lintas
        # device boleh dibaca untuk keperluan refund.
        response = self.client.get(
            f"{self.list_url}T02-000001/",
            **{**self.headers, "HTTP_X_APPROVER_PIN": "123456"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["transaction_id"], "T02-000001")
        self.assertEqual(response.data["device_code"], "T02")
        self.assertEqual(response.data["refund_status"], "none")
        self.assertEqual(response.data["refunds"], [])

    def test_history_detail_unknown_note_returns_404(self):
        response = self.client.get(
            f"{self.list_url}T01-999999/",
            **self.headers,
        )

        self.assertEqual(response.status_code, 404)

    def test_history_detail_rejects_malformed_number(self):
        # Nomor sementara offline (T01-TMP-0007) tidak pernah disimpan ke
        # database; endpoint juga tidak boleh menerimanya sebagai masukan.
        for malformed in ("T01-TMP-0007", "T01-000001-extra"):
            response = self.client.get(
                f"{self.list_url}{malformed}/",
                **self.headers,
            )

            self.assertEqual(
                response.status_code,
                404,
                msg=f"malformed number accepted: {malformed}",
            )

        # Tanpa prefix device sendiri, gerbang PIN yang menjawab dulu.
        no_pin = self.client.get(
            f"{self.list_url}evil/",
            **self.headers,
        )
        self.assertEqual(no_pin.status_code, 400)

        with_pin = self.client.get(
            f"{self.list_url}evil/",
            **{**self.headers, "HTTP_X_APPROVER_PIN": "123456"},
        )
        self.assertEqual(with_pin.status_code, 404)

    def test_history_filters_by_local_day(self):
        """`?date=` membatasi riwayat ke satu hari lokal toko.

        Nota contoh disimpan 2026-09-27 08:00 dan 09:00 UTC = 27 September
        15:00 dan 16:00 WIB (TIME_ZONE Asia/Jakarta), jadi keduanya milik
        tanggal lokal 2026-09-27.
        """
        on_day = self.client.get(
            f"{self.list_url}?date=2026-09-27",
            **self.headers,
        )

        self.assertEqual(on_day.status_code, 200)
        self.assertEqual(
            [row["transaction_id"] for row in on_day.data["results"]],
            ["T01-000002", "T01-000001"],
        )

        for empty_day in ("2026-09-26", "2026-09-28"):
            response = self.client.get(
                f"{self.list_url}?date={empty_day}",
                **self.headers,
            )

            self.assertEqual(response.status_code, 200)
            self.assertEqual(
                response.data["results"],
                [],
                msg=f"day {empty_day} should have no note",
            )
            self.assertEqual(response.data["count"], 0)

    def test_history_day_boundary_uses_store_timezone(self):
        """Nota dini hari WIB tidak boleh jatuh ke tanggal UTC sebelumnya.

        2026-09-26 18:30 UTC adalah 2026-09-27 01:30 WIB. Filter tanggal
        tanpa konversi zona waktu akan menempatkannya di tanggal 26 dan
        menghilangkannya dari riwayat hari ini.
        """
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO transactions (
                    sync_id, transaction_id, worker_id, transaction_time,
                    item_amount, shipping_amount, total_amount,
                    payment_method, payment_amount, change_amount
                ) VALUES (
                    %s, 'T01-000003', 'W001', %s,
                    10000, NULL, 10000,
                    'cash', 10000, 0
                );
                """,
                [
                    uuid4(),
                    datetime(2026, 9, 26, 18, 30, tzinfo=timezone.utc),
                ],
            )

        on_day = self.client.get(
            f"{self.list_url}?date=2026-09-27",
            **self.headers,
        )
        previous_day = self.client.get(
            f"{self.list_url}?date=2026-09-26",
            **self.headers,
        )

        # Urutan tetap terbaru-dulu menurut transaction_time: 000003 memang
        # milik tanggal 27 (01:30 WIB), tetapi jamnya paling pagi.
        self.assertEqual(
            [row["transaction_id"] for row in on_day.data["results"]],
            ["T01-000002", "T01-000001", "T01-000003"],
        )
        self.assertEqual(previous_day.data["results"], [])

    def test_history_rejects_malformed_date(self):
        """Tanggal yang tidak terbaca dibalas 400, bukan diabaikan.

        Filter yang diam-diam tidak jalan membuat kasir melihat nota hari lain
        dan mengira riwayatnya bocor.
        """
        for malformed in ("27-09-2026", "2026-9", "hari-ini", "2026-13-01"):
            response = self.client.get(
                f"{self.list_url}?date={malformed}",
                **self.headers,
            )

            self.assertEqual(
                response.status_code,
                400,
                msg=f"malformed date accepted: {malformed}",
            )

    def test_history_never_exposes_restricted_fields(self):
        for url in (self.list_url, f"{self.list_url}T01-000001/"):
            response = self.client.get(url, **self.headers)

            self.assertEqual(response.status_code, 200)

            content = response.content.decode()

            # Aturan wajib gagal: data modal/margin/PIN tidak pernah
            # dikirim ke tab POS.
            for forbidden in (
                "cost_amount",
                "modal",
                "margin",
                "min_price",
                "moka_id",
                "sku",
                "pin",
            ):
                # Tanda kutip memastikan key JSON "pin", bukan substring
                # "shipping_amount" yang sah ada di response.
                self.assertNotIn(f'"{forbidden}"', content)


# ---------------------------------------------------------------------------
# Bukti transfer (PAYMENT_PROOF_DESIGN.md)
# ---------------------------------------------------------------------------

# JPEG minimal: magic bytes yang dikenali + isi netral. Cukup untuk lolos
# deteksi tipe, jauh di bawah batas ukuran.
JPEG_BODY = b"\xff\xd8\xff\xe0" + b"\x00" * 60


def _jpeg_upload(content=None, name="proof.jpg"):
    """Berkas JPEG minimal: magic bytes + isi netral."""
    return SimpleUploadedFile(
        name,
        JPEG_BODY if content is None else content,
        content_type="image/jpeg",
    )


class PaymentProofContentTypeTests(SimpleTestCase):
    """Tipe berkas ditentukan dari isi, bukan dari header kiriman klien."""

    def test_recognised_image_signatures(self):
        cases = (
            ("image/jpeg", JPEG_BODY),
            ("image/png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 24),
            (
                "image/webp",
                b"RIFF"
                + (12).to_bytes(4, "little")
                + b"WEBP"
                + b"\x00" * 20,
            ),
        )

        for expected, header in cases:
            with self.subTest(expected=expected):
                self.assertEqual(detect_content_type(header), expected)

    def test_svg_is_rejected_however_the_client_labels_it(self):
        # SVG dapat memuat skrip dan berkas ini kelak ditampilkan kembali di
        # browser, jadi label "image/svg+xml" dari klien tidak boleh dipercaya.
        for payload in (
            b'<svg xmlns="http://www.w3.org/2000/svg"><script/></svg>',
            b'<?xml version="1.0"?><svg/>',
            b"GIF89a" + b"\x00" * 26,
            b"",
        ):
            with self.subTest(payload=payload[:12]):
                self.assertIsNone(detect_content_type(payload))


class PaymentProofObjectKeyTests(SimpleTestCase):
    """Key bersifat relatif dan dibuat server, bukan URL."""

    def test_key_shape(self):
        proof_id = uuid4()
        moment = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)

        self.assertEqual(
            build_object_key(proof_id, "image/jpeg", moment),
            f"payment_proofs/2026/09/{proof_id}.jpg",
        )

    def test_key_has_no_per_note_subfolder(self):
        """Folder bulan langsung berisi gambar; nota tidak jadi nama folder."""
        key = build_object_key(
            uuid4(),
            "image/jpeg",
            datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc),
        )

        # payment_proofs / tahun / bulan / berkas
        self.assertEqual(len(key.split("/")), 4)

    def test_two_proofs_for_one_note_get_different_keys(self):
        """Bukti pengganti untuk nota yang sama tidak boleh menabrak berkas lama.

        Ini alasan key memakai `proof_id` dan bukan `sync_id`: jalur 409 membuat
        bukti baru dengan `proof_id` baru untuk `sync_id` yang sama (design
        bagian 7.12), dan storage menolak key yang sudah terpakai.
        """
        moment = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)

        first = build_object_key(uuid4(), "image/jpeg", moment)
        second = build_object_key(uuid4(), "image/jpeg", moment)

        self.assertNotEqual(first, second)

    def test_key_never_contains_a_scheme(self):
        key = build_object_key(
            uuid4(),
            "image/png",
            datetime.now(timezone.utc),
        )

        # Kalau yang tersimpan adalah URL, memindahkan storage ke object
        # storage nanti menuntut migrasi data seluruh bukti (design §3).
        self.assertNotIn("://", key)
        self.assertFalse(key.startswith("http"))
        self.assertTrue(key.endswith(".png"))


class PaymentProofServiceTests(SimpleTestCase):
    """Aturan penyimpanan bukti, diuji tanpa HTTP dan tanpa menyentuh DB."""

    def setUp(self):
        self.proof_id = uuid4()
        self.sync_id = uuid4()
        self.device_code = "T01"
        self.upload = _jpeg_upload()
        self.content = self.upload.read()
        self.digest = sha256_hexdigest(self.content)
        self.upload.seek(0)

    def _store(self, **overrides):
        self.upload.seek(0)

        params = {
            "proof_id": self.proof_id,
            "sync_id": self.sync_id,
            "uploaded_file": self.upload,
            "device_code": self.device_code,
        }
        params.update(overrides)

        return store_payment_proof(**params)

    @staticmethod
    def _given_transaction(mock_transactions, transaction_id="T01-000001"):
        mock_transactions.objects.filter.return_value.first.return_value = (
            MagicMock(transaction_id=transaction_id)
        )

    @patch("pos.payment_proof_service.save_proof_file")
    @patch("pos.payment_proof_service.proof_file_exists")
    @patch("pos.payment_proof_service.PaymentProof")
    @patch("pos.payment_proof_service.Transactions")
    def test_new_proof_is_stored_under_a_relative_key(
        self, mock_transactions, mock_proof, mock_exists, mock_save
    ):
        self._given_transaction(mock_transactions)
        mock_proof.objects.filter.return_value.first.return_value = None
        mock_exists.return_value = False
        mock_save.return_value = "payment_proofs/2026/09/key-dari-storage.jpg"

        proof, created = self._store()

        self.assertTrue(created)
        self.assertIs(proof, mock_proof.objects.create.return_value)

        saved_key, saved_content = mock_save.call_args.args
        self.assertEqual(saved_content, self.content)
        # Key dibuat server dan relatif: tidak ada nama berkas kiriman klien.
        self.assertNotIn("://", saved_key)
        self.assertNotIn("proof.jpg", saved_key)
        # Nama berkas adalah `proof_id`, bukan `sync_id`, dan tanpa subfolder
        # per nota (design bagian 3): folder bulan langsung berisi gambar.
        self.assertEqual(saved_key.count("/"), 3)
        self.assertTrue(saved_key.endswith(f"{self.proof_id}.jpg"))

        kwargs = mock_proof.objects.create.call_args.kwargs
        self.assertEqual(kwargs["object_key"], mock_save.return_value)
        self.assertEqual(kwargs["sha256"], self.digest)
        self.assertEqual(kwargs["content_type"], "image/jpeg")
        self.assertEqual(kwargs["byte_size"], len(self.content))
        self.assertEqual(kwargs["device_code"], "T01")
        # Regresi "kolom NOT NULL diisi NULL": uploaded_at wajib eksplisit,
        # persis pola yang dulu membuat registrasi device gagal 500.
        self.assertIsNotNone(kwargs["uploaded_at"])

    @patch("pos.payment_proof_service.save_proof_file")
    @patch("pos.payment_proof_service.proof_file_exists")
    @patch("pos.payment_proof_service.PaymentProof")
    @patch("pos.payment_proof_service.Transactions")
    def test_resending_identical_content_is_idempotent(
        self, mock_transactions, mock_proof, mock_exists, mock_save
    ):
        self._given_transaction(mock_transactions)
        existing = MagicMock(sha256=self.digest)
        mock_proof.objects.filter.return_value.first.return_value = existing

        proof, created = self._store()

        # Retry klien tidak boleh menghasilkan bukti ganda maupun 500.
        self.assertFalse(created)
        self.assertIs(proof, existing)
        mock_save.assert_not_called()
        mock_proof.objects.create.assert_not_called()

    @patch("pos.payment_proof_service.save_proof_file")
    @patch("pos.payment_proof_service.proof_file_exists")
    @patch("pos.payment_proof_service.PaymentProof")
    @patch("pos.payment_proof_service.Transactions")
    def test_same_proof_id_with_different_content_conflicts(
        self, mock_transactions, mock_proof, mock_exists, mock_save
    ):
        self._given_transaction(mock_transactions)
        mock_proof.objects.filter.return_value.first.return_value = (
            MagicMock(sha256="f" * 64)
        )

        with self.assertRaises(PaymentProofConflict):
            self._store()

        mock_save.assert_not_called()

    @patch("pos.payment_proof_service.save_proof_file")
    @patch("pos.payment_proof_service.proof_file_exists")
    @patch("pos.payment_proof_service.PaymentProof")
    @patch("pos.payment_proof_service.Transactions")
    def test_orphan_file_under_the_same_key_conflicts(
        self, mock_transactions, mock_proof, mock_exists, mock_save
    ):
        self._given_transaction(mock_transactions)
        mock_proof.objects.filter.return_value.first.return_value = None
        mock_exists.return_value = True

        with self.assertRaises(PaymentProofConflict):
            self._store()

        mock_save.assert_not_called()

    @patch("pos.payment_proof_service.save_proof_file")
    @patch("pos.payment_proof_service.proof_file_exists")
    @patch("pos.payment_proof_service.PaymentProof")
    @patch("pos.payment_proof_service.Transactions")
    def test_unknown_transaction_is_not_found(
        self, mock_transactions, mock_proof, mock_exists, mock_save
    ):
        mock_transactions.objects.filter.return_value.first.return_value = None

        with self.assertRaises(PaymentProofNotFound):
            self._store()

        mock_save.assert_not_called()

    @patch("pos.payment_proof_service.save_proof_file")
    @patch("pos.payment_proof_service.proof_file_exists")
    @patch("pos.payment_proof_service.PaymentProof")
    @patch("pos.payment_proof_service.Transactions")
    def test_note_of_another_device_is_not_found(
        self, mock_transactions, mock_proof, mock_exists, mock_save
    ):
        # Nota T02 diminta device T01: dibalas 404 supaya keberadaan nota
        # tab lain tidak bocor, sama seperti endpoint riwayat.
        self._given_transaction(mock_transactions, "T02-000001")

        with self.assertRaises(PaymentProofNotFound):
            self._store()

        mock_save.assert_not_called()

    @patch("pos.payment_proof_service.save_proof_file")
    @patch("pos.payment_proof_service.proof_file_exists")
    @patch("pos.payment_proof_service.PaymentProof")
    @patch("pos.payment_proof_service.Transactions")
    def test_note_without_a_number_is_accepted(
        self, mock_transactions, mock_proof, mock_exists, mock_save
    ):
        # Nota offline yang kehabisan jatah nomor belum bisa diatribusikan ke
        # device (transactions belum punya kolom device_id), jadi tidak boleh
        # ditolak hanya karena itu.
        self._given_transaction(mock_transactions, transaction_id=None)
        mock_proof.objects.filter.return_value.first.return_value = None
        mock_exists.return_value = False

        _, created = self._store()

        self.assertTrue(created)

    @patch("pos.payment_proof_service.save_proof_file")
    @patch("pos.payment_proof_service.proof_file_exists")
    @patch("pos.payment_proof_service.PaymentProof")
    @patch("pos.payment_proof_service.Transactions")
    def test_svg_upload_is_rejected(
        self, mock_transactions, mock_proof, mock_exists, mock_save
    ):
        self._given_transaction(mock_transactions)
        svg = SimpleUploadedFile(
            "proof.svg",
            b'<svg xmlns="http://www.w3.org/2000/svg"><script/></svg>',
            content_type="image/svg+xml",
        )

        with self.assertRaises(InvalidPaymentProofFile):
            self._store(uploaded_file=svg)

        mock_save.assert_not_called()

    @patch("pos.payment_proof_service.save_proof_file")
    @patch("pos.payment_proof_service.proof_file_exists")
    @patch("pos.payment_proof_service.PaymentProof")
    @patch("pos.payment_proof_service.Transactions")
    def test_oversized_upload_is_rejected(
        self, mock_transactions, mock_proof, mock_exists, mock_save
    ):
        self._given_transaction(mock_transactions)
        big = _jpeg_upload(JPEG_BODY + b"\x00" * MAX_PROOF_BYTES)

        with self.assertRaises(PaymentProofTooLarge):
            self._store(uploaded_file=big)

        mock_save.assert_not_called()

    @patch("pos.payment_proof_service.delete_proof_file")
    @patch("pos.payment_proof_service.save_proof_file")
    @patch("pos.payment_proof_service.proof_file_exists")
    @patch("pos.payment_proof_service.PaymentProof")
    @patch("pos.payment_proof_service.Transactions")
    def test_failed_insert_removes_the_file_it_wrote(
        self,
        mock_transactions,
        mock_proof,
        mock_exists,
        mock_save,
        mock_delete,
    ):
        self._given_transaction(mock_transactions)
        mock_proof.objects.filter.return_value.first.return_value = None
        mock_exists.return_value = False
        mock_save.return_value = "payment_proofs/2026/09/yatim.jpg"
        mock_proof.objects.create.side_effect = IntegrityError("boom")

        with self.assertRaises(IntegrityError):
            self._store()

        # Bila insert gagal, berkasnya tidak boleh tertinggal di storage.
        mock_delete.assert_called_once_with(mock_save.return_value)

    def test_image_formats_map_to_the_right_extension(self):
        payloads = (
            (b"\x89PNG\r\n\x1a\n" + b"\x00" * 24, "image/png", "png"),
            (
                b"RIFF" + (12).to_bytes(4, "little") + b"WEBP"
                + b"\x00" * 20,
                "image/webp",
                "webp",
            ),
        )

        for content, content_type, extension in payloads:
            with self.subTest(content_type=content_type):
                key = build_object_key(
                    uuid4(),
                    detect_content_type(content),
                    datetime.now(timezone.utc),
                )

                self.assertTrue(key.endswith(f".{extension}"))


class PaymentProofUploadAPITests(APITestCase):
    """Kontrak HTTP endpoint unggah bukti transfer (design §6)."""

    def setUp(self):
        self.url = "/api/payment-proofs/"
        self.proof_id = uuid4()
        self.sync_id = uuid4()

        # Device auth sungguhan dipakai (bukan di-bypass), supaya jalur
        # `request.user.device.device_code` juga teruji.
        self.device = MagicMock()
        self.device.id = uuid4()
        self.device.device_code = "T01"

        device_patcher = patch(
            "pos.device_authentication.Device.objects.get",
            return_value=self.device,
        )
        device_patcher.start()
        self.addCleanup(device_patcher.stop)

        self.headers = {
            "HTTP_AUTHORIZATION": (
                f"Device {create_device_token(self.device)}"
            ),
        }

    def _proof_row(self):
        return MagicMock(
            proof_id=self.proof_id,
            sync_id=self.sync_id,
            content_type="image/jpeg",
            byte_size=len(JPEG_BODY),
            sha256="a" * 64,
            uploaded_at=datetime.now(timezone.utc),
        )

    def _payload(self, **overrides):
        data = {
            "proof_id": str(self.proof_id),
            "sync_id": str(self.sync_id),
            "file": _jpeg_upload(),
        }
        data.update(overrides)

        return data

    def _post(self, **overrides):
        return self.client.post(
            self.url,
            self._payload(**overrides),
            format="multipart",
            **self.headers,
        )

    @patch("pos.views.store_payment_proof")
    def test_new_proof_returns_201(self, mock_store):
        mock_store.return_value = (self._proof_row(), True)

        response = self._post()

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["proof_id"], str(self.proof_id))
        self.assertEqual(response.data["sync_id"], str(self.sync_id))
        self.assertEqual(response.data["content_type"], "image/jpeg")
        self.assertEqual(response.data["byte_size"], len(JPEG_BODY))
        # Detail storage tidak pernah bocor ke klien: kontraknya key milik
        # server, bukan URL yang bisa di-hardcode klien.
        self.assertNotIn("object_key", response.data)

        self.assertEqual(mock_store.call_args.kwargs["device_code"], "T01")

    @patch("pos.views.store_payment_proof")
    def test_resend_returns_200_without_creating_a_duplicate(
        self, mock_store
    ):
        mock_store.return_value = (self._proof_row(), False)

        response = self._post()

        # 200, bukan 201: retry berarti \"sudah ada\", bukan kegagalan.
        self.assertEqual(response.status_code, 200)

    @patch("pos.views.store_payment_proof")
    def test_unknown_transaction_returns_404(self, mock_store):
        mock_store.side_effect = PaymentProofNotFound("Nota tidak ditemukan.")

        self.assertEqual(self._post().status_code, 404)

    @patch("pos.views.store_payment_proof")
    def test_oversized_file_returns_413(self, mock_store):
        mock_store.side_effect = PaymentProofTooLarge("too large")

        self.assertEqual(self._post().status_code, 413)

    @patch("pos.views.store_payment_proof")
    def test_unrecognised_content_returns_400(self, mock_store):
        mock_store.side_effect = InvalidPaymentProofFile("not an image")

        self.assertEqual(self._post().status_code, 400)

    @patch("pos.views.store_payment_proof")
    def test_conflicting_proof_id_returns_409(self, mock_store):
        mock_store.side_effect = PaymentProofConflict("conflict")

        self.assertEqual(self._post().status_code, 409)

    @patch("pos.views.store_payment_proof")
    def test_missing_file_is_rejected_before_storage(self, mock_store):
        payload = self._payload()
        del payload["file"]

        response = self.client.post(
            self.url, payload, format="multipart", **self.headers
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("file", response.data)
        mock_store.assert_not_called()

    @patch("pos.views.store_payment_proof")
    def test_non_uuid_proof_id_is_rejected_before_storage(self, mock_store):
        response = self._post(proof_id="T01-TMP-0007")

        self.assertEqual(response.status_code, 400)
        mock_store.assert_not_called()

    def test_missing_device_token_returns_401(self):
        response = self.client.post(
            self.url, self._payload(), format="multipart"
        )

        # Tanpa device token tidak ada satu pun byte yang ditulis.
        self.assertEqual(response.status_code, 401)

    def test_invalid_device_token_returns_401(self):
        response = self.client.post(
            self.url,
            self._payload(),
            format="multipart",
            HTTP_AUTHORIZATION="Device not.a.valid.token",
        )

        self.assertEqual(response.status_code, 401)


class PinFormatDescriptionTests(SimpleTestCase):
    """Tablet hanya boleh mencoba mencocokkan format yang ia mengerti."""

    def test_raw_bcrypt_hash_is_labelled_bcrypt(self):
        stored = bcrypt.hashpw(b"123456", bcrypt.gensalt(rounds=6)).decode("utf-8")

        self.assertEqual(describe_pin_format(stored), "bcrypt")

    def test_legacy_text_pin_is_labelled_plaintext(self):
        self.assertEqual(describe_pin_format("123456"), "plaintext")

    def test_django_encoded_hash_is_labelled_other(self):
        # Browser tidak punya hasher berformat Django; baris seperti ini harus
        # diabaikan tablet, bukan dicocokkan dengan asumsi.
        self.assertEqual(describe_pin_format(make_password("123456")), "other")

    def test_empty_value_is_not_offered_as_a_matchable_format(self):
        self.assertEqual(describe_pin_format(""), "other")


class WorkerOfflineListDeviceAuthTests(APITestCase):
    """Daftar worker memuat nilai PIN tersimpan, jadi device token wajib."""

    def setUp(self):
        self.url = "/api/workers/offline-list/"

    def test_missing_device_token_returns_401(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 401)

    def test_invalid_device_token_returns_401(self):
        response = self.client.get(
            self.url,
            HTTP_AUTHORIZATION="Device not.a.valid.token",
        )

        self.assertEqual(response.status_code, 401)


class WorkerOfflineListResponseTests(APITestCase):
    """Bentuk respons daftar worker offline (OFFLINE_SYNC_DESIGN.md §7)."""

    def setUp(self):
        self.url = "/api/workers/offline-list/"

        # Auth diuji di WorkerOfflineListDeviceAuthTests; di sini yang diperiksa
        # isi responsnya.
        auth_patcher = patch.object(
            WorkerOfflineListAPIView,
            "authentication_classes",
            [],
        )
        auth_patcher.start()
        self.addCleanup(auth_patcher.stop)

        permission_patcher = patch.object(
            WorkerOfflineListAPIView,
            "permission_classes",
            [],
        )
        permission_patcher.start()
        self.addCleanup(permission_patcher.stop)

    @patch("pos.views.Worker.objects")
    def test_each_worker_carries_name_and_stored_pin_value(self, worker_objects):
        bcrypt_pin = bcrypt.hashpw(b"123456", bcrypt.gensalt(rounds=6)).decode("utf-8")
        siti = MagicMock()
        siti.name = "SITI"
        siti.pin = bcrypt_pin
        ani = MagicMock()
        ani.name = "ANI"
        ani.pin = "654321"

        worker_objects.all.return_value.only.return_value.order_by.return_value = [
            ani,
            siti,
        ]
        worker_objects.exclude.return_value.only.return_value.order_by.return_value = [
            ani,
            siti,
        ]

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data["workers"],
            [
                {"name": "ANI", "pin_stored": "654321", "pin_format": "plaintext"},
                {"name": "SITI", "pin_stored": bcrypt_pin, "pin_format": "bcrypt"},
            ],
        )
        self.assertIn("generated_at", response.data)

    @patch("pos.views.Worker.objects")
    def test_worker_id_is_never_sent_to_the_device(self, worker_objects):
        worker = MagicMock()
        worker.name = "SITI"
        worker.pin = "123456"
        worker.id = "W001"

        worker_objects.all.return_value.only.return_value.order_by.return_value = [worker]
        worker_objects.exclude.return_value.only.return_value.order_by.return_value = [worker]

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("id", response.data["workers"][0])
        self.assertNotIn("worker_id", response.data["workers"][0])


class RefundEndpointTests(ExternalPostgresTableMixin, TransactionTestCase):
    """Refund/retur/void + riwayat scope=all (keputusan pemilik 2026-10-05).

    Hanya kasir nota asal (PIN) yang boleh mencatat event; scope=all wajib
    PIN kasir; POS hanya mencatat tanpa menulis stok. `client_class =
    APIClient` karena payload bersarang wajib JSON asli (client Django
    mengabaikan `format="json"` dan me-repr struktur menjadi string).
    """

    client_class = APIClient

    @classmethod
    def tearDownClass(cls):
        with connection.cursor() as cursor:
            for table in (
                "refund_event_detail",
                "refund_event",
                "transaction_detail",
                "transactions",
                "item",
                "worker",
            ):
                cursor.execute(
                    f"DROP TABLE IF EXISTS pg_temp.{table};"
                )

        super().tearDownClass()

    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        reset_alias_table_cache()

        self.list_url = "/api/transactions/history/"
        self.refund_url = "/api/refunds/"

        self._ensure_external_tables()

        self.device = MagicMock()
        self.device.id = uuid4()
        self.device.device_code = "T01"

        self.headers = {
            "HTTP_AUTHORIZATION": (
                f"Device {create_device_token(self.device)}"
            ),
        }

        device_patcher = patch(
            "pos.device_authentication.Device.objects.get",
            return_value=self.device,
        )
        device_patcher.start()
        self.addCleanup(device_patcher.stop)

        self.sale_time = datetime(2026, 10, 5, 7, 0, tzinfo=timezone.utc)
        self.sale_sync_id = uuid4()
        self.other_sync_id = uuid4()
        # Nota berongkir: ongkir ikut bagian dari total nota yang boleh
        # dikembalikan tanpa barang kembali.
        self.shipping_sync_id = uuid4()
        # Nota dengan baris berdiskon: plafon kembali baris = yang benar-
        # benar dibayar (subtotal), bukan harga sebelum diskon.
        self.discount_sync_id = uuid4()

        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO worker (id, name, pin) VALUES (%s, %s, %s);",
                ["W001", "Kasir Asli", "123456"],
            )
            cursor.execute(
                "INSERT INTO worker (id, name, pin) VALUES (%s, %s, %s);",
                ["W002", "Kasir Lain", "654321"],
            )
            cursor.execute(
                """
                INSERT INTO item (
                    item_name, variant, brand, category, cost_amount,
                    ecommerce_item, in_stock, max_price, min_price,
                    moka_id, sku, stock_alert, track_alert, track_stock
                ) VALUES
                    ('Item A', 'Default', 'Test', 'Test', 10000,
                        FALSE, 10, 20000, 15000,
                        'MOKA-A', 'SKU-A', NULL, FALSE, TRUE),
                    ('Item B', 'Default', 'Test', 'Test', 10000,
                        FALSE, 10, 20000, 15000,
                        'MOKA-B', 'SKU-B', NULL, FALSE, TRUE);
                """
            )
            cursor.execute(
                """
                INSERT INTO transactions (
                    sync_id, transaction_id, worker_id, transaction_time,
                    item_amount, shipping_amount, total_amount,
                    payment_method, payment_amount, change_amount
                ) VALUES
                    (%s, 'T01-000010', 'W001', %s,
                        40000, NULL, 40000, 'cash', 50000, 10000),
                    (%s, 'T02-000010', 'W002', %s,
                        99000, NULL, 99000, 'cash', 99000, 0),
                    (%s, 'T01-000011', 'W001', %s,
                        40000, 10000, 50000, 'cash', 50000, 0),
                    (%s, 'T01-000012', 'W001', %s,
                        30000, NULL, 30000, 'cash', 30000, 0);
                """,
                [
                    self.sale_sync_id, self.sale_time,
                    self.other_sync_id, self.sale_time,
                    self.shipping_sync_id, self.sale_time,
                    self.discount_sync_id, self.sale_time,
                ],
            )
            cursor.execute(
                """
                INSERT INTO transaction_detail (
                    sync_id, item_name, variant, discount,
                    quantity, subtotal, unit_price
                ) VALUES
                    (%s, 'Item A', 'Default', 0, 2, 40000, 20000),
                    (%s, 'Item A', 'Default', 0, 2, 40000, 20000),
                    (%s, 'Item A', 'Default', 0, 2, 40000, 20000),
                    (%s, 'Item B', 'Default', 10000, 2, 30000, 20000);
                """,
                [
                    self.sale_sync_id,
                    self.other_sync_id,
                    self.shipping_sync_id,
                    self.discount_sync_id,
                ],
            )

    def _refund(self, payload, headers=None):
        return self.client.post(
            self.refund_url,
            payload,
            format="json",
            **(headers if headers is not None else self.headers),
        )

    def _base_payload(self, **overrides):
        payload = {
            "refund_id": str(uuid4()),
            "sync_id": str(self.sale_sync_id),
            "approver_pin": "123456",
            "event_type": "return",
            "reason": "Barang cacat produksi",
            "refund_method": "cash",
            "refund_amount": 20000,
            "items": [
                {
                    "item_name": "Item A",
                    "variant": "Default",
                    "quantity": 1,
                    "unit_price": 20000,
                    "subtotal": 20000,
                },
            ],
        }
        payload.update(overrides)
        return payload

    def test_full_return_with_money(self):
        response = self._refund(self._base_payload(refund_amount=40000, items=[{
            "item_name": "Item A", "variant": "Default",
            "quantity": 2, "unit_price": 20000, "subtotal": 40000,
        }]))

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["status"], "recorded")
        self.assertEqual(response.data["transaction_id"], "T01-000010")
        self.assertTrue(response.data["self_approval"])
        self.assertFalse(response.data["cross_device"])

    def test_refund_id_is_idempotent(self):
        payload = self._base_payload()

        first = self._refund(payload)
        second = self._refund(payload)

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.data["status"], "idempotent")

    def test_wrong_pin_is_rejected(self):
        response = self._refund(self._base_payload(approver_pin="000000"))

        self.assertEqual(response.status_code, 401)

    def test_other_cashier_cannot_approve(self):
        """Keputusan pemilik: retur hanya transaksi sendiri + ACC master.

        PIN sah milik kasir lain ditolak 403 dan tidak ada event yang
        tercatat — termasuk saat kasir nota asal sudah pulang (butuh peran
        master di sistem, yang belum ada di POS)."""
        response = self._refund(self._base_payload(approver_pin="654321"))

        self.assertEqual(response.status_code, 403)
        self.assertIn("kasir nota asal", response.data["detail"])

        detail = self.client.get(
            f"{self.list_url}T01-000010/", **self.headers,
        )
        self.assertEqual(detail.data["refund_status"], "none")
        self.assertEqual(detail.data["refunds"], [])

    def test_refund_id_reused_with_different_content_conflicts(self):
        payload = self._base_payload()
        self.assertEqual(self._refund(payload).status_code, 201)

        changed = dict(payload, refund_amount=1000, items=[])
        response = self._refund(changed)

        self.assertEqual(response.status_code, 409)
        self.assertIn("isi berbeda", response.data["detail"])

    def test_return_more_than_sold_is_rejected(self):
        payload = self._base_payload(items=[{
            "item_name": "Item A", "variant": "Default",
            "quantity": 3, "unit_price": 20000, "subtotal": 60000,
        }])

        response = self._refund(payload)

        self.assertEqual(response.status_code, 400)

    def test_second_return_consumes_remaining_budget(self):
        self.assertEqual(self._refund(self._base_payload()).status_code, 201)

        # Sisa 1 x Item A dan Rp 20.000 dari total Rp 40.000.
        second = self._base_payload(refund_amount=20000, items=[{
            "item_name": "Item A", "variant": "Default",
            "quantity": 1, "unit_price": 20000, "subtotal": 20000,
        }])
        self.assertEqual(self._refund(second).status_code, 201)

        # Jatah uang habis: kumulatif sudah sama dengan total nota.
        third = self._base_payload(
            event_type="refund", refund_amount=1000, items=[],
        )
        response = self._refund(third)

        self.assertEqual(response.status_code, 400)
        self.assertIn("total nota", response.data["detail"])

    def test_void_then_anything_conflicts(self):
        void = self._base_payload(
            event_type="void", refund_amount=0, items=[],
            reason="Salah input kasir",
        )
        self.assertEqual(self._refund(void).status_code, 201)

        response = self._refund(self._base_payload())

        self.assertEqual(response.status_code, 409)

    def test_history_scope_all_needs_person_pin(self):
        denied = self.client.get(
            self.list_url, {"scope": "all"}, **self.headers,
        )

        self.assertEqual(denied.status_code, 400)

        allowed = self.client.get(
            self.list_url,
            {"scope": "all"},
            **{**self.headers, "HTTP_X_APPROVER_PIN": "123456"},
        )

        self.assertEqual(allowed.status_code, 200)
        ids = [row["transaction_id"] for row in allowed.data["results"]]
        self.assertIn("T01-000010", ids)
        self.assertIn("T02-000010", ids)

        codes = {
            row["transaction_id"]: row["device_code"]
            for row in allowed.data["results"]
        }
        self.assertEqual(codes["T01-000010"], "T01")
        self.assertEqual(codes["T02-000010"], "T02")

    def test_history_self_scope_hides_other_device(self):
        response = self.client.get(self.list_url, **self.headers)

        self.assertEqual(response.status_code, 200)
        ids = [row["transaction_id"] for row in response.data["results"]]
        self.assertIn("T01-000010", ids)
        self.assertNotIn("T02-000010", ids)

    def test_history_row_shows_refund_status(self):
        self.assertEqual(self._refund(self._base_payload()).status_code, 201)

        response = self.client.get(self.list_url, **self.headers)

        row = next(
            item for item in response.data["results"]
            if item["transaction_id"] == "T01-000010"
        )
        self.assertEqual(row["refund_status"], "returned")
        self.assertEqual(row["device_code"], "T01")

    def test_detail_lists_refund_events(self):
        self.assertEqual(self._refund(self._base_payload()).status_code, 201)

        response = self.client.get(
            f"{self.list_url}T01-000010/", **self.headers,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["refunds"]), 1)
        self.assertEqual(
            response.data["refunds"][0]["approver_name"], "Kasir Asli",
        )
        self.assertTrue(response.data["refunds"][0]["self_approval"])
        self.assertFalse(response.data["refunds"][0]["cross_device"])
        self.assertEqual(response.data["device_code"], "T01")

    # ==================================
    # VOID
    # ==================================

    def test_void_records_returned_money_and_is_terminal(self):
        """Batal jual: uang pelanggan boleh dicatat penuh, lalu nota selesai."""
        void = self._base_payload(
            event_type="void", refund_amount=40000, items=[],
            reason="Pelanggan membatalkan pesanan",
        )
        response = self._refund(void)

        self.assertEqual(response.status_code, 201)

        detail = self.client.get(
            f"{self.list_url}T01-000010/", **self.headers,
        )
        self.assertEqual(detail.data["refund_status"], "void")
        self.assertEqual(detail.data["refunds"][0]["refund_amount"], 40000)

        # Terminal: tidak ada event lanjutan sesudah void.
        self.assertEqual(self._refund(self._base_payload()).status_code, 409)

    def test_void_carries_no_items(self):
        response = self._refund(self._base_payload(
            event_type="void", refund_amount=0,
            reason="Salah input kasir",
        ))

        self.assertEqual(response.status_code, 400)

    def test_void_above_total_is_rejected(self):
        response = self._refund(self._base_payload(
            event_type="void", refund_amount=50000, items=[],
            reason="Salah input kasir",
        ))

        self.assertEqual(response.status_code, 400)
        self.assertIn("total", response.data["detail"])

    def test_void_after_return_conflicts(self):
        """Barang sudah kembali: void nota utuh akan menghitung ganda."""
        self.assertEqual(self._refund(self._base_payload()).status_code, 201)

        void = self._base_payload(
            event_type="void", refund_amount=0, items=[],
            reason="Ternyata salah input",
        )
        response = self._refund(void)

        self.assertEqual(response.status_code, 409)
        self.assertIn("tidak bisa di-void", response.data["detail"])

    # ==================================
    # REFUND (UANG TANPA BARANG)
    # ==================================

    def test_refund_with_items_is_rejected(self):
        response = self._refund(self._base_payload(event_type="refund"))

        self.assertEqual(response.status_code, 400)
        self.assertIn("hanya berisi uang", response.data["detail"])

    def test_money_refund_up_to_total_needs_no_returned_goods(self):
        """Keputusan pemilik 2026-10-17: plafon uang = total nota.

        Pelanggan yang membayar penuh boleh menerima uangnya kembali tanpa
        mencatat barang kembali lebih dulu.
        """
        response = self._refund(self._base_payload(
            event_type="refund", refund_amount=40000, items=[],
        ))

        self.assertEqual(response.status_code, 201)

    def test_money_refund_above_transaction_total_is_rejected(self):
        response = self._refund(self._base_payload(
            event_type="refund", refund_amount=40001, items=[],
        ))

        self.assertEqual(response.status_code, 400)
        self.assertIn("total nota", response.data["detail"])

    def test_money_cannot_exceed_transaction_total(self):
        """Uang sebelumnya + uang event ini tidak boleh melebihi total nota."""
        self.assertEqual(
            self._refund(self._base_payload(
                event_type="refund", refund_amount=20000, items=[],
            )).status_code,
            201,
        )

        response = self._refund(self._base_payload(refund_amount=40000))

        self.assertEqual(response.status_code, 400)
        self.assertIn("total nota", response.data["detail"])

    def test_shipping_may_be_refunded_without_goods(self):
        """Ongkir yang tidak jadi dikirim adalah uang kembali tanpa barang."""
        payload = self._base_payload(
            sync_id=str(self.shipping_sync_id),
            event_type="refund", refund_amount=10000, items=[],
        )
        self.assertEqual(self._refund(payload).status_code, 201)

        listed = self.client.get(self.list_url, **self.headers)
        row = next(
            item for item in listed.data["results"]
            if item["transaction_id"] == "T01-000011"
        )
        self.assertEqual(row["refund_status"], "refunded")

        # Sisa plafon = Rp 40.000 dari total nota Rp 50.000.
        again = self._base_payload(
            sync_id=str(self.shipping_sync_id),
            event_type="refund", refund_amount=40001, items=[],
        )
        response = self._refund(again)

        self.assertEqual(response.status_code, 400)

    def test_discounted_line_cannot_be_refunded_twice(self):
        """Baris 2 x Rp 20.000 diskon Rp 10.000 hanya membayar Rp 30.000."""
        first = self._base_payload(
            sync_id=str(self.discount_sync_id),
            items=[{
                "item_name": "Item B", "variant": "Default",
                "quantity": 1, "unit_price": 20000, "subtotal": 20000,
            }],
            refund_amount=20000,
        )
        self.assertEqual(self._refund(first).status_code, 201)

        second = self._base_payload(
            sync_id=str(self.discount_sync_id),
            items=[{
                "item_name": "Item B", "variant": "Default",
                "quantity": 1, "unit_price": 20000, "subtotal": 20000,
            }],
            refund_amount=20000,
        )
        over = self._refund(second)

        self.assertEqual(over.status_code, 400)
        self.assertIn("benar-benar dibayar", over.data["detail"])

        # Sisa yang benar-benar dibayar (Rp 10.000) masih boleh kembali.
        third = self._base_payload(
            sync_id=str(self.discount_sync_id),
            items=[{
                "item_name": "Item B", "variant": "Default",
                "quantity": 1, "unit_price": 10000, "subtotal": 10000,
            }],
            refund_amount=10000,
        )
        self.assertEqual(self._refund(third).status_code, 201)

    # ==================================
    # LINTAS DEVICE + FILTER STATUS
    # ==================================

    def test_cross_device_event_is_flagged(self):
        """Nota device lain diselesaikan dari tab ini oleh kasirnya sendiri."""
        payload = self._base_payload(
            sync_id=str(self.other_sync_id),
            approver_pin="654321",
            event_type="return", refund_amount=20000,
        )
        response = self._refund(payload)

        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.data["cross_device"])
        self.assertTrue(response.data["self_approval"])

        detail = self.client.get(
            f"{self.list_url}T02-000010/",
            **{**self.headers, "HTTP_X_APPROVER_PIN": "654321"},
        )
        self.assertEqual(detail.status_code, 200)
        self.assertTrue(detail.data["refunds"][0]["cross_device"])

    def test_history_can_be_filtered_by_refund_status(self):
        self.assertEqual(self._refund(self._base_payload()).status_code, 201)

        returned = self.client.get(
            self.list_url, {"refund_status": "returned"}, **self.headers,
        )
        self.assertEqual(returned.status_code, 200)
        self.assertEqual(
            [row["transaction_id"] for row in returned.data["results"]],
            ["T01-000010"],
        )

        untouched = self.client.get(
            self.list_url, {"refund_status": "none"}, **self.headers,
        )
        ids = {row["transaction_id"] for row in untouched.data["results"]}
        self.assertNotIn("T01-000010", ids)
        self.assertIn("T01-000011", ids)

    def test_history_refund_status_filter_rejects_unknown_value(self):
        response = self.client.get(
            self.list_url, {"refund_status": "hilang"}, **self.headers,
        )

        self.assertEqual(response.status_code, 400)



class IndonesianErrorMessageTests(APITestCase):
    """Warning yang dibaca kasir berbahasa Indonesia (permintaan pemilik 2026-10-17).

    Pesan domain sudah Indonesia di sumbernya; kelas ini menjaga jaring
    pengaman untuk pesan bawaan DRF yang tidak lewat jalur domain.
    """

    def test_default_drf_messages_are_translated(self):
        from .messages_id import _translate_detail

        self.assertEqual(
            _translate_detail(
                {"detail": "Authentication credentials were not provided."}
            )["detail"],
            "Perangkat belum terautentikasi.",
        )
        self.assertEqual(
            _translate_detail({"pin": ["This field is required."]})["pin"][0],
            "Kolom ini wajib diisi.",
        )
        self.assertEqual(
            _translate_detail('Method "GET" not allowed.'),
            "Metode GET tidak diizinkan.",
        )
        self.assertEqual(
            _translate_detail("Ensure this value has at most 20 characters."),
            "Maksimal 20 karakter.",
        )
        self.assertEqual(
            _translate_detail('"x" is not a valid choice.'),
            "Pilihan tidak valid.",
        )

    def test_domain_messages_stay_untouched(self):
        from .messages_id import _translate_detail

        self.assertEqual(
            _translate_detail({"detail": "Uang kembali melebihi total nota."}),
            {"detail": "Uang kembali melebihi total nota."},
        )

    def test_missing_device_token_is_rejected_in_indonesian(self):
        response = self.client.post("/api/transactions/", {}, format="json")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(
            response.data["detail"],
            "Perangkat belum terautentikasi.",
        )
