from pathlib import Path
import os
from dotenv import load_dotenv

BASE = Path(__file__).parent.resolve()
load_dotenv(BASE / ".env")

from llama_index.core import StorageContext, load_index_from_storage
from llama_index.vector_stores.faiss import FaissVectorStore

STORAGE = BASE / "storage"
assert STORAGE.exists(), f"Storage missing: {STORAGE}. Run build_index.py first."

faiss_store = FaissVectorStore.from_persist_dir(persist_dir=str(STORAGE))
storage_ctx = StorageContext.from_defaults(persist_dir=str(STORAGE), vector_store=faiss_store)
index = load_index_from_storage(storage_ctx)

retriever = index.as_retriever(similarity_top_k=5)
query = "Summarize the key points of all documents in the folder."
results = retriever.retrieve(query)

print(f"Top {len(results)} hits for: {query!r}\n")
for i, nws in enumerate(results, 1):
    node = nws.node
    path = node.metadata.get("file_path") or node.metadata.get("source") or "<unknown>"
    snippet = (node.get_content() or "").replace("\n", " ")[:200]
    print(f"{i}. ({path})\n   {snippet}...\n")

print("\nOK.")
