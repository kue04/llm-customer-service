from dataclasses import replace

import pytest

from services.ingestion.parse_quality import evaluate
from services.ingestion.parsers.base import Block, ParsedDocument


def document(*blocks):
    return ParsedDocument(title="quality", blocks=tuple(replace(b, order=i) for i, b in enumerate(blocks)))


@pytest.mark.parametrize(("text", "reason"), [
    (" ", "empty_text"), ("�" * 30, "garbled_text"),
    ("中文正文" + "\x00" * 20, "garbled_text"),
    ("Ã©" * 30, "garbled_text"), ("!!!", "no_meaningful_text"),
    (("这是一段被错误重复提取的售后流程说明，请联系官方客服办理。\n") * 6, "repeated_content"),
])
def test_bad_text(text, reason):
    quality = evaluate(document(Block("paragraph", text)))
    assert quality["status"] == "requires_review"
    assert reason in quality["reasons"]


@pytest.mark.parametrize("blocks", [
    [Block("heading", "标题", heading_path=("标题",))],
    [Block("paragraph", "第一页", page=1), Block("paragraph", "第三页", page=3)],
    [Block("paragraph", "第一页", page=1), Block("paragraph", "错误页", page=0)],
    [Block("table", "a b c", table_json={"columns": ["a", "b"], "rows": [["c"]]})],
    [Block("heading", "跳级", heading_path=("a", "b", "c")), Block("paragraph", "正常正文")],
])
def test_structure_anomalies(blocks):
    assert "structure_anomaly" in evaluate(document(*blocks))["reasons"]


@pytest.mark.parametrize("text", ["退款请到订单详情页的售后进度查看。", "OK", "正常中文、English, café。", "代码：a = 1; b = 2"])
def test_normal_including_short_text_is_not_quarantined(text):
    assert evaluate(document(Block("paragraph", text)))["status"] == "passed"


def test_bad_block_order():
    parsed = ParsedDocument(title="x", blocks=(Block("paragraph", "正文", order=8),))
    assert "block_order" in evaluate(parsed)["metrics"]["structure_issues"]
