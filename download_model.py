"""Download and verify the official Argos English→Chinese 1.9 model."""
import hashlib
from pathlib import Path
import urllib.request
import zipfile

URL = "https://argos-net.com/v1/translate-en_zh-1_9.argosmodel"
SHA256 = "433e7c4f034d87fbe2353161e05f18646d7999452f801a4e1f0378522b9850ab"
root = Path(__file__).parent / "models"
root.mkdir(exist_ok=True)
archive = root / "en_zh.argosmodel"
if not archive.exists():
    temporary = archive.with_suffix(".download")
    urllib.request.urlretrieve(URL, temporary)
    if hashlib.sha256(temporary.read_bytes()).hexdigest() != SHA256:
        raise RuntimeError("Model checksum mismatch")
    temporary.replace(archive)
if hashlib.sha256(archive.read_bytes()).hexdigest() != SHA256:
    raise RuntimeError("Model checksum mismatch")
with zipfile.ZipFile(archive) as model:
    if model.testzip():
        raise RuntimeError("Model ZIP failed integrity verification")
    for item in model.infolist():
        if not (root / item.filename).resolve().is_relative_to(root.resolve()):
            raise RuntimeError("Unsafe archive path")
    model.extractall(root)
print("Verified English→Chinese model installed in", root)
