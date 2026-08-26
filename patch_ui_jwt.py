# patch_ui_jwt.py
import json
import base64

src = open("ui/app.py", encoding="utf-8").read()

# Build a valid unsigned/dev JWT format (header.payload.sig)
header = base64.urlsafe_b64encode(json.dumps({"alg": "none", "typ": "JWT"}).encode()).decode().rstrip("=")
payload = base64.urlsafe_b64encode(json.dumps({
    "sub": "dev-admin",
    "email": "admin@vibeforge.local",
    "tenant_id": "00000000-0000-0000-0000-000000000001",
    "roles": ["admin", "developer"],
    "exp": 9999999999
}).encode()).decode().rstrip("=")
dev_jwt = f"{header}.{payload}."

OLD = 'return "dev-bearer-token"'
NEW = f'return "{dev_jwt}"'

if OLD in src:
    src = src.replace(OLD, NEW)
    open("ui/app.py", "w", encoding="utf-8").write(src)
    print("OK: Replaced with valid dev JWT structure")
else:
    print("WARN: String not found or already patched")