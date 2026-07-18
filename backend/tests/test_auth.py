def test_login_nonexistent_user(client):
    resp = client.post(
        "/api/auth/login",
        json={"email": "no-existe@example.com", "password": "cualquiera"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Usuario o contraseña incorrectos"
    assert "X-Attempts-Remaining" not in resp.headers
