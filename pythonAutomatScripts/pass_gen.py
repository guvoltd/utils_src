import hashlib, base64, os

username = "@Dream5"
password = b"@Dream5"
salt = os.urandom(16)

m = hashlib.sha512()
m.update(password)
m.update(salt)
dg = m.digest()

encoded_salt = base64.b64encode(salt).decode()
encoded_hash = base64.b64encode(dg).decode()

print(f"{username}:$6${encoded_salt}${encoded_hash}")

