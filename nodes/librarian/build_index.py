# build_index.py — tiny smoke test to (re)build or load the FAISS index
import os
from pathlib import Path
from dotenv import load_dotenv

# --- resolve base + env -------------------------------------------------------
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

# --- deps ---------------------------------------------------------------------
from llama_index.core import (
    Settings, SimpleDirectoryReader, VectorStoreIndex,
    StorageContext, load_index_from_storage, PromptHelper, Document
)
from llama_index.embeddings.huggingface import HuggingFaceEmbedding
from llama_index.llms.ollama import Ollama
from llama_index.vector_stores.faiss import FaissVectorStore
from llama_index.core.readers.base import BaseReader
import faiss

# --- model settings -----------------------------------------------------------
Settings.embed_model = HuggingFaceEmbedding(model_name="BAAI/bge-small-en-v1.5")
Settings.llm = Ollama(model="deepseek-r1:14b", base_url="http://localhost:11434", request_timeout=600.0)
Settings.prompt_helper = PromptHelper(
    context_window=2048, num_output=512, chunk_size_limit=1024, chunk_overlap_ratio=0.0
)

# --- minimal plain-text reader for odd extensions -----------------------------
class PlainTextReader(BaseReader):
    def load_data(self, file_path, extra_info=None):
        with open(file_path, "r", encoding="utf-8") as f:
            text = f.read()
        return [Document(text=text, extra_info=extra_info or {})]

file_extractor = {
    ".txt": PlainTextReader(),
    ".md": PlainTextReader(),
    ".mb": PlainTextReader(),
    ".canvas": PlainTextReader(),
}

required_exts = [".txt", ".md", ".mb", ".canvas", ".pdf"]
exclude = ["**/.obsidian/**"]

def load_dir(p: Path):
    if not p or not p.is_dir():
        print(f"[librarian] WARN: missing dir {p}")
        return []
    try:
        return SimpleDirectoryReader(
            input_dir=str(p),
            recursive=True,
            required_exts=required_exts,
            file_extractor=file_extractor,
            exclude=exclude,
        ).load_data()
    except ValueError as e:
        # LlamaIndex raises ValueError("No files found") for empty dirs — treat as 0 docs.
        if "No files found" in str(e):
            print(f"[librarian] INFO: no files found in {p} (skipping).")
            return []
        raise

# --- load docs (Obsidian + local docs) ---------------------------------------
DOCS_DIR.mkdir(parents=True, exist_ok=True)
STORAGE_DIR.mkdir(parents=True, exist_ok=True)

docs = []
docs += load_dir(DOCS_DIR)
docs += load_dir(OBSIDIAN_DIR)

print(f"[librarian] loaded documents: {len(docs)}")

# --- build or load index ------------------------------------------------------
def _has_persist(dir_: Path) -> bool:
    # any of the common persistence markers is enough
    return any((dir_ / name).exists() for name in [
        "default__vector_store.json", "faiss.index", "ivfpq.index",
        "docstore.json", "index_store.json"
    ])

if _has_persist(STORAGE_DIR):
    print("[librarian] loading existing index …")
    vector_store = FaissVectorStore.from_persist_dir(str(STORAGE_DIR))
    storage_ctx  = StorageContext.from_defaults(vector_store=vector_store, persist_dir=str(STORAGE_DIR))
    index        = load_index_from_storage(storage_ctx)
else:
    if not docs:
        print("[librarian] WARN: no documents found; building an empty-capable index anyway.")
    print("[librarian] building fresh index …")
    dim = len(Settings.embed_model.get_text_embedding("probe"))
    faiss_idx = faiss.IndexFlatL2(dim)
    vector_store = FaissVectorStore(faiss_index=faiss_idx)
    storage_ctx  = StorageContext.from_defaults(vector_store=vector_store)
    index        = VectorStoreIndex.from_documents(docs, storage_context=storage_ctx)
    index.storage_context.persist(persist_dir=str(STORAGE_DIR))

print("[librarian] OK.")
