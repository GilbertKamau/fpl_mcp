import asyncio

from starlette.testclient import TestClient

from fpl_mcp import server


def test_health_is_open_and_token_is_required():
    with TestClient(server.http_app(token="s3cret")) as client:
        assert client.get("/health").status_code == 200
        assert client.post("/mcp", json={}).status_code == 401
        assert client.post("/mcp", json={}, headers={"Authorization": "Bearer wrong"}).status_code == 401
        # The right token gets past the guard (the MCP layer then rejects the empty body).
        assert client.post("/mcp", json={}, headers={"Authorization": "Bearer s3cret"}).status_code != 401


def test_hosted_server_hides_refresh_tool():
    server.http_app(token=None)
    server.http_app(token=None)  # safe to build twice
    names = [t.name for t in asyncio.run(server.mcp.list_tools())]
    assert "refresh_data" not in names
    assert "predict_points" in names
