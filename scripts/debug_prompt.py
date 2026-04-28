from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from models.prompt import create_prompt
from utils.retriever import retrieve_documents


query = "会员退款怎么办？"
documents = retrieve_documents(query, limit=3)
prompt = create_prompt(query, documents)

print(prompt)
