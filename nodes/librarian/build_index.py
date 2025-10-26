# build_index.py — tiny smoke test to (re)build or load the FAISS index
import os
from pathlib import Path
from dotenv import load_dotenv

# --- resolve base + env
BASE = Path(__file__).parent.resolve()
load_dotenv(BASE / ".env")

LIBRARIAN_ROOT = Path(os.getenv("LIBRARIAN_ROOT", BASE))
OBSIDIAN_DIR   = Path(os.getenv("OBSIDIAN_DIR", BASE / "obsidian_vault"))
DOCS_DIR       = BASE / "docs"
STORAGE_DIR    = BASE / "storage"

print(f"[librarian] BASE:        {BASE}")
print(f"[librarian] LIB_ROOT:    {LIBRARIAN_ROOT}")
print(f"[librarian] OBSIDIAN:    {OBSIDIAN_DIR}")
print(f"[librarian] DOCS:        {DOCS_DIR}")
print(f"[librarian] STORAGE:     {STORAGE_DIR}")

# --- deps
from llama_index.core import (
    Settings, SimpleDirectoryReader, VectorStoreIndex,
    StorageContext, load_index_from_storage, PromptHelper
)
from llama_index.embeddings.huggingface import HuggingFaceEmbedding
from llama_index.llms.ollama import Ollama
from llama_index.vector_stores.faiss import FaissVectorStore
import faiss

# --- model settings
Settings.embed_model = HuggingFaceEmbedding(model_name="BAAI/bge-small-en-v1.5")
Settings.llm = Ollama(model="deepseek-r1:14b", base_url="http://localhost:11434", request_timeout=600.0)
Settings.prompt_helper = PromptHelper(context_window=2048, num_output=512, chunk_size_limit=1024, chunk_overlap_ratio=0.0)

# --- load docs (Obsidian + local docs)
class PlainTextReader(SimpleDirectoryReader._doc_reader_cls):  # simple passthrough
    pass

file_extractor = {ext: PlainTextReader() for ext in [".txt",".md",".mb",".canvas"]}

def load_dir(p: Path):
    if not p.is_dir():
        print(f"[librarian] WARN: missing dir {p}")
        return []
    return SimpleDirectoryReader(
        input_dir=str(p), recursive=True,
        required_exts=[".txt",".md",".mb",".canvas",".pdf"],
        file_extractor=file_extractor,
        exclude=["**/.obsidian/**"]
    ).load_data()

docs = []
docs += load_dir(DOCS_DIR)
docs += load_dir(OBSIDIAN_DIR)

print(f"[librarian] loaded documents: {len(docs)}")

# --- build or load index
STORAGE_DIR.mkdir(parents=True, exist_ok=True)

if (STORAGE_DIR / "docstore.json").exists() or (STORAGE_DIR / "faiss.index").exists():
    print("[librarian] loading existing index …")
    vector_store = FaissVectorStore.from_persist_dir(str(STORAGE_DIR))
    storage_ctx  = StorageContext.from_defaults(vector_store=vector_store, persist_dir=str(STORAGE_DIR))
    index        = load_index_from_storage(storage_ctx)
else:
    print("[librarian] building fresh index …")
    dim = len(Settings.embed_model.get_text_embedding("probe"))
    faiss_idx = faiss.IndexFlatL2(dim)
    vector_store = FaissVectorStore(faiss_index=faiss_idx)
    storage_ctx  = StorageContext.from_defaults(vector_store=vector_store)
    index        = VectorStoreIndex.from_documents(docs, storage_context=storage_ctx)
    index.storage_context.persist(persist_dir=str(STORAGE_DIR))

print("[librarian] OK.")
