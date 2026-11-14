import io
from unittest.mock import patch

from PIL import Image
from werkzeug.datastructures import FileStorage

from utils.file_handlers import save_file


class _FakeBucket:
    def __init__(self, owner):
        self.owner = owner

    def upload(self, path, file, file_options=None):
        self.owner.uploads.append(
            {
                "path": path,
                "data": bytes(file),
                "content_type": (file_options or {}).get("content-type"),
            }
        )
        if self.owner.fail_upload:
            raise RuntimeError("upload failed")
        return {"path": path}


class _FakeStorage:
    def __init__(self, owner):
        self.owner = owner

    def from_(self, bucket):
        self.owner.buckets.append(bucket)
        return _FakeBucket(self.owner)


class _FakeSupabase:
    def __init__(self, fail_upload=False):
        self.uploads = []
        self.buckets = []
        self.fail_upload = fail_upload
        self.storage = _FakeStorage(self)


def _image_bytes(fmt, size=(16, 16)):
    buffer = io.BytesIO()
    Image.new("RGB", size, color=(200, 30, 30)).save(buffer, format=fmt)
    return buffer.getvalue()


def _pdf_bytes():
    return b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF\n"


def _file(data, filename, content_type="application/octet-stream"):
    return FileStorage(stream=io.BytesIO(data), filename=filename, content_type=content_type)


def _save(file, fake, subfolder="comprobantes"):
    with patch("utils.file_handlers.supabase", fake):
        return save_file(file, subfolder)


def test_valid_jpeg_is_detected_and_converted(test_client):
    fake = _FakeSupabase()
    key = _save(_file(_image_bytes("JPEG"), "foto.jpg", "image/jpeg"), fake)

    assert key is not None
    assert key.startswith("comprobantes/")
    assert key.endswith(".webp")
    assert len(fake.uploads) == 1
    assert fake.uploads[0]["content_type"] == "image/webp"
    assert fake.uploads[0]["data"].startswith(b"RIFF")


def test_valid_png_is_detected_and_converted(test_client):
    fake = _FakeSupabase()
    key = _save(_file(_image_bytes("PNG"), "foto.png", "image/png"), fake)

    assert key is not None
    assert key.endswith(".webp")
    assert len(fake.uploads) == 1
    assert fake.uploads[0]["content_type"] == "image/webp"


def test_valid_pdf_is_uploaded_without_decoding(test_client):
    fake = _FakeSupabase()
    pdf = _pdf_bytes()
    key = _save(_file(pdf, "recibo.pdf", "application/pdf"), fake)

    assert key is not None
    assert key.endswith(".pdf")
    assert len(fake.uploads) == 1
    assert fake.uploads[0]["data"] == pdf
    assert fake.uploads[0]["content_type"] == "application/pdf"


def test_psd_renamed_as_jpeg_is_rejected_without_autodetection(test_client):
    fake = _FakeSupabase()
    psd_like = b"8BPS\x00\x01" + b"\x00" * 32
    key = _save(_file(psd_like, "foto.jpg", "image/jpeg"), fake)

    assert key is None
    assert fake.uploads == []


def test_corrupt_jpeg_is_rejected(test_client):
    fake = _FakeSupabase()
    key = _save(_file(b"\xff\xd8\xff\xe0" + b"not-a-jpeg", "roto.jpg", "image/jpeg"), fake)

    assert key is None
    assert fake.uploads == []


def test_extension_content_mismatch_is_rejected(test_client):
    fake = _FakeSupabase()
    key = _save(_file(_image_bytes("JPEG"), "foto.png", "image/png"), fake)

    assert key is None
    assert fake.uploads == []


def test_declared_mime_is_not_trusted(test_client):
    fake = _FakeSupabase()
    key = _save(_file(_image_bytes("PNG"), "foto.png", "application/pdf"), fake)

    assert key is not None
    assert key.endswith(".webp")
    assert fake.uploads[0]["content_type"] == "image/webp"


def test_image_byte_limit_is_enforced(test_client, monkeypatch):
    fake = _FakeSupabase()
    monkeypatch.setitem(test_client.application.config, "MAX_IMAGE_UPLOAD_BYTES", 32)
    key = _save(_file(_image_bytes("PNG"), "grande.png", "image/png"), fake)

    assert key is None
    assert fake.uploads == []


def test_image_pixel_limit_is_enforced(test_client, monkeypatch):
    fake = _FakeSupabase()
    monkeypatch.setitem(test_client.application.config, "MAX_IMAGE_PIXELS", 50)
    key = _save(_file(_image_bytes("PNG", size=(16, 16)), "grande.png", "image/png"), fake)

    assert key is None
    assert fake.uploads == []


def test_truncated_pdf_is_rejected(test_client):
    fake = _FakeSupabase()
    key = _save(_file(b"%PDF-1.4\nsin fin", "roto.pdf", "application/pdf"), fake)

    assert key is None
    assert fake.uploads == []


def test_upload_failure_does_not_report_a_key(test_client):
    fake = _FakeSupabase(fail_upload=True)
    key = _save(_file(_pdf_bytes(), "recibo.pdf", "application/pdf"), fake)

    assert key is None
    assert len(fake.uploads) == 1
