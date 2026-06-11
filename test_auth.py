import urllib.request
import urllib.error
import json

BASE_URL = 'http://127.0.0.1:8000/api/users'

def post(endpoint, data):
    req = urllib.request.Request(f"{BASE_URL}{endpoint}", data=json.dumps(data).encode('utf-8'), headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req) as response:
            return response.status, json.loads(response.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except:
            return e.code, e.read().decode()
    except Exception as e:
        return 0, str(e)

print("--- Test 1: Contraseña Débil ---")
status, resp = post('/auth/register/', {'username': 'testuser1', 'email': 'test1@demo.com', 'password': '123'})
print(f"Status: {status} | Respuesta: {resp}")

print("\n--- Test 2: Registro Exitoso ---")
status, resp = post('/auth/register/', {'username': 'testuser1', 'email': 'test1@demo.com', 'password': 'StrongPassword1'})
print(f"Status: {status} | Respuesta: {resp}")

print("\n--- Test 3: Email Duplicado (Case-Insensitive) ---")
status, resp = post('/auth/register/', {'username': 'testuser2', 'email': 'TEST1@demo.com', 'password': 'StrongPassword1'})
print(f"Status: {status} | Respuesta: {resp}")

print("\n--- Test 4: Username Duplicado (Case-Insensitive) ---")
status, resp = post('/auth/register/', {'username': 'TESTUSER1', 'email': 'test2@demo.com', 'password': 'StrongPassword1'})
print(f"Status: {status} | Respuesta: {resp}")

print("\n--- Test 5: Login con cuenta no verificada (is_active=False) ---")
status, resp = post('/auth/login/', {'username': 'testuser1', 'password': 'StrongPassword1'})
print(f"Status: {status} | Respuesta: {resp}")

print("\n--- Test 6: Fuerza Bruta (Rate Limiting) ---")
for i in range(1, 8):
    status, resp = post('/auth/login/', {'username': 'admin', 'password': 'wrongpassword'})
    print(f"Intento {i}: Status {status}")
    if status == 429 or status == 403:
        print(f"Bloqueado en intento {i} por Rate Limit.")
        break
