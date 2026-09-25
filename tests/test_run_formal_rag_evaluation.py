from unittest.mock import patch
from scripts.run_formal_rag_evaluation import run_formal_evaluation

def test_run_formal_evaluation_combines_sections():
    with patch("scripts.run_formal_rag_evaluation.describe_chunk_index", return_value={"index_name":"document_chunks","index_version":4}), patch("scripts.run_formal_rag_evaluation.evaluate_dataset", return_value=({}, [], {"dense": [], "sparse": [], "hybrid": []})), patch("scripts.run_formal_rag_evaluation.load_formal_chunk_grounding_cases", return_value=[]), patch("scripts.run_formal_rag_evaluation.summarize_grounding_reports", return_value={"route_counts": {}}):
        report = run_formal_evaluation("tenant-alpha")
    assert report["retrieval_path"] == "chunk-index"
    assert report["index"]["index_version"] == 4
