"""Uji: passkey (Face ID / sidik jari) dan kunci otomatis aplikasi HP, dengan authenticator tiruan."""
import hashlib
import json
import struct
import time

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from django.urls import reverse

from master.layanan.passkey import b64u, cbor_baca, unb64u
from master.models import PasskeyHp
from master.tests.dasar import Dasar
from master.tests.test_hp import masuk_hp

ASAL = "http://testserver"


def cbor(v):
    def kepala(jenis, n):
        if n < 24:
            return bytes([jenis << 5 | n])
        for info, lebar in ((24, 1), (25, 2), (26, 4), (27, 8)):
            if n < 1 << (8 * lebar):
                return bytes([jenis << 5 | info]) + n.to_bytes(lebar, "big")
    if v is None:
        return b"\xf6"
    if isinstance(v, bool):
        return b"\xf5" if v else b"\xf4"
    if isinstance(v, int):
        return kepala(0, v) if v >= 0 else kepala(1, -1 - v)
    if isinstance(v, bytes):
        return kepala(2, len(v)) + v
    if isinstance(v, str):
        b = v.encode()
        return kepala(3, len(b)) + b
    if isinstance(v, list):
        return kepala(4, len(v)) + b"".join(cbor(x) for x in v)
    if isinstance(v, dict):
        return kepala(5, len(v)) + b"".join(cbor(k) + cbor(x) for k, x in v.items())
    raise TypeError(v)


class Perangkat:
    """Authenticator tiruan: ES256, mendukung UV (Face ID / sidik jari)."""
    def __init__(self, rp="testserver"):
        self.kunci = ec.generate_private_key(ec.SECP256R1())
        self.cid = b"cred-" + struct.pack(">d", time.time())
        self.rp = rp
        self.hitungan = 0

    def cose(self):
        n = self.kunci.public_key().public_numbers()
        return cbor({1: 2, 3: -7, -1: 1, -2: n.x.to_bytes(32, "big"), -3: n.y.to_bytes(32, "big")})

    def buat(self, challenge, *, asal=ASAL):
        client = json.dumps({"type": "webauthn.create", "challenge": challenge, "origin": asal}).encode()
        ad = (hashlib.sha256(self.rp.encode()).digest() + bytes([0x45]) + struct.pack(">I", 0) + bytes(16)
              + struct.pack(">H", len(self.cid)) + self.cid + self.cose())
        return {"id": b64u(self.cid), "rawId": b64u(self.cid), "type": "public-key", "response": {
            "clientDataJSON": b64u(client), "attestationObject": b64u(cbor({"fmt": "none", "attStmt": {}, "authData": ad}))}}

    def ambil(self, challenge, *, asal=ASAL, bendera=0x05, rusak=False):
        client = json.dumps({"type": "webauthn.get", "challenge": challenge, "origin": asal}).encode()
        ad = hashlib.sha256(self.rp.encode()).digest() + bytes([bendera]) + struct.pack(">I", self.hitungan)
        tanda = self.kunci.sign(ad + hashlib.sha256(client).digest(), ec.ECDSA(hashes.SHA256()))
        if rusak:
            tanda = tanda[:-1] + bytes([tanda[-1] ^ 1])
        return {"id": b64u(self.cid), "rawId": b64u(self.cid), "type": "public-key", "response": {
            "clientDataJSON": b64u(client), "authenticatorData": b64u(ad), "signature": b64u(tanda)}}


class PasskeyTest(Dasar):
    def json(self, nama, data):
        return self.client.post(reverse(nama), json.dumps(data), content_type="application/json")

    def daftar(self, user=None):
        masuk_hp(self.client, user or self.admin)
        hp = Perangkat()
        o = self.client.get(reverse("hp:passkey_opsi"), HTTP_ACCEPT="application/json").json()
        self.assertEqual((o["rp"]["id"], o["authenticatorSelection"]["userVerification"]), ("testserver", "required"))
        r = self.json("hp:passkey_daftar", {"kredensial": hp.buat(o["challenge"])})
        self.assertTrue(r.json()["ok"], r.json())
        return hp

    def test_cbor_baca(self):
        nilai = {"a": [1, -7, b"\x01\x02", True, None], 3: {"x": "yz"}}
        self.assertEqual(cbor_baca(cbor(nilai))[0], nilai)

    def test_daftar_lalu_masuk_dengan_passkey(self):
        hp = self.daftar()
        p = PasskeyHp.objects.get()
        self.assertEqual((p.user, p.credential_id), (self.admin, b64u(hp.cid)))
        self.client.logout()
        self.assertEqual(self.client.get(reverse("hp:beranda")).status_code, 302)
        o = self.client.get(reverse("hp:masuk_opsi")).json()
        self.assertNotIn("allowCredentials", o)                          # passkey dipilih perangkat (tanpa nama pengguna)
        hp.hitungan = 1
        r = self.json("hp:masuk_passkey", {"kredensial": hp.ambil(o["challenge"]), "next": "/hp/transaksi/"})
        self.assertEqual(r.json(), {"ok": True, "pesan": "Masuk.", "next": "/hp/transaksi/"})
        self.assertEqual(self.client.get(reverse("hp:beranda")).status_code, 200)   # langsung terbuka
        self.assertIsNotNone(PasskeyHp.objects.get().terakhir)

    def test_tanda_tangan_salah_challenge_lama_asal_lain_dan_tanpa_uv_ditolak(self):
        hp = self.daftar()
        self.client.logout()
        o = self.client.get(reverse("hp:masuk_opsi")).json()
        self.assertFalse(self.json("hp:masuk_passkey", {"kredensial": hp.ambil(o["challenge"], rusak=True)}).json()["ok"])
        # challenge sekali pakai
        self.assertIn("kedaluwarsa", self.json("hp:masuk_passkey", {"kredensial": hp.ambil(o["challenge"])}).json()["pesan"])
        o = self.client.get(reverse("hp:masuk_opsi")).json()
        self.assertFalse(self.json("hp:masuk_passkey", {"kredensial": hp.ambil(o["challenge"], asal="https://jahat.example")}).json()["ok"])
        o = self.client.get(reverse("hp:masuk_opsi")).json()
        r = self.json("hp:masuk_passkey", {"kredensial": hp.ambil(o["challenge"], bendera=0x01)})   # tanpa Face ID
        self.assertIn("diverifikasi", r.json()["pesan"])
        o = self.client.get(reverse("hp:masuk_opsi")).json()
        lain = Perangkat()
        self.assertIn("tidak terdaftar", self.json("hp:masuk_passkey", {"kredensial": lain.ambil(o["challenge"])}).json()["pesan"])
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_kunci_otomatis_dan_buka(self):
        hp = self.daftar()
        sesi = self.client.session
        sesi["hp_aktif"] = int(time.time()) - 6 * 60
        sesi.save()
        r = self.client.get(reverse("hp:barang") + "?q=palu")
        self.assertRedirects(r, reverse("hp:kunci") + "?next=/hp/barang/%3Fq%3Dpalu", fetch_redirect_response=False)
        self.assertEqual(self.json("hp:acc", {"jenis": "dus"}).status_code, 423)
        self.assertEqual(self.client.get(reverse("master:dashboard")).status_code, 302)   # sesi HP: master ikut terkunci
        self.assertContains(self.client.get(reverse("hp:kunci")), "Buka dengan")
        # kata sandi salah, lalu buka dengan passkey
        self.assertContains(self.client.post(reverse("hp:kunci"), {"password": "salah"}), "Kata sandi salah")
        o = self.client.get(reverse("hp:kunci_opsi")).json()
        self.assertEqual([c["id"] for c in o["allowCredentials"]], [b64u(hp.cid)])
        r = self.json("hp:kunci_buka", {"kredensial": hp.ambil(o["challenge"]), "next": "/hp/barang/"})
        self.assertTrue(r.json()["ok"], r.json())
        self.assertEqual(self.client.get(reverse("hp:barang")).status_code, 200)
        self.assertEqual(self.client.get(reverse("master:dashboard")).status_code, 200)

    def test_buka_dengan_kata_sandi_dan_passkey_orang_lain_ditolak(self):
        hp_admin = self.daftar()
        masuk_hp(self.client, self.finance)
        sesi = self.client.session
        sesi["hp_aktif"] = 0
        sesi.save()
        o = self.client.get(reverse("hp:kunci_opsi")).json()
        self.assertEqual(o["allowCredentials"], [])
        r = self.json("hp:kunci_buka", {"kredensial": hp_admin.ambil(o["challenge"])})
        self.assertFalse(r.json()["ok"])
        r = self.client.post(reverse("hp:kunci"), {"password": "rahasia-123", "next": "/hp/setelan/"})
        self.assertRedirects(r, "/hp/setelan/", fetch_redirect_response=False)
        r = self.client.post(reverse("hp:kunci"), {"password": "x", "next": "https://jahat.example/"})
        self.assertEqual(r.status_code, 200)

    def test_sesi_master_di_pc_tidak_terkunci(self):
        self.client.force_login(self.admin)            # masuk lewat master, tidak pernah membuka /hp/
        self.assertEqual(self.client.get(reverse("master:dashboard")).status_code, 200)
        self.assertEqual(self.client.get(reverse("hp:beranda")).status_code, 302)   # tapi /hp/ minta dibuka dulu

    def test_hapus_passkey_hanya_milik_sendiri(self):
        self.daftar()
        p = PasskeyHp.objects.get()
        masuk_hp(self.client, self.finance)
        self.json("hp:passkey_hapus", {"id": p.id})
        self.assertTrue(PasskeyHp.objects.exists())
        masuk_hp(self.client, self.admin)
        self.assertContains(self.client.get(reverse("hp:setelan")), "Hapus")
        self.json("hp:passkey_hapus", {"id": p.id})
        self.assertFalse(PasskeyHp.objects.exists())
        self.assertEqual(unb64u(b64u(b"\x00\xff")), b"\x00\xff")
