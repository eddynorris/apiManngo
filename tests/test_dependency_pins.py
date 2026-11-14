from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _requirements():
    pins = {}
    for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if "==" in line:
            name, version = line.split("==", 1)
            pins[name.strip().lower()] = version.strip()
    return pins


def test_audited_dependency_pins_are_kept():
    pins = _requirements()
    expected = {
        "flask": "3.1.3",
        "flask-cors": "6.0.5",
        "flask-jwt-extended": "4.7.4",
        "flask-limiter": "4.1.1",
        "flask-marshmallow": "1.3.0",
        "flask-sqlalchemy": "3.1.1",
        "werkzeug": "3.1.9",
        "pillow": "13.0.0",
        "gunicorn": "26.2.0",
        "cryptography": "50.0.2",
        "pyjwt": "2.15.1",
        "python-dotenv": "1.2.4",
        "requests": "2.34.2",
        "jinja2": "3.1.6",
        "setuptools": "84.2.1",
        "marshmallow": "3.26.2",
        "pyyaml": "6.0.3",
        "openpyxl": "3.1.5",
        "lxml": "6.1.3",
        "signxml": "5.2.0",
    }
    for name, version in expected.items():
        assert pins.get(name) == version, f"{name} debe quedar en {version}"


def test_dockerfile_uses_the_tested_python_version():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert dockerfile.count("FROM python:3.12-slim") == 2
    assert "python:3.11" not in dockerfile
