import json

from scripts.export_openapi import export_openapi


def test_export_openapi_contains_fullstack_contract(tmp_path):
    output_path = tmp_path / "openapi.json"

    export_openapi(output_path)

    schema = json.loads(output_path.read_text(encoding="utf-8"))
    assert "/chat/prompt" in schema["paths"]
    assert "/retrieval/search" in schema["paths"]
    assert "/knowledge/items" in schema["paths"]
