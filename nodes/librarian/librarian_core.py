# nodes/librarian/librarian_core.py
"""
Lightweight core for the Librarian node.
Wraps: env setup, doc loading, FAISS index build/load, summaries & searches.

Usage (from anywhere):
    from librarian_core import Librarian
    lib = Librarian()                               # auto-detect LIBRARIAN_ROOT / .env
    lib.ensure_index()                              # builds if missing
    txt = lib.summarize_all(recache=False, mode="tree_summarize")
    hits = lib.quick_search("some query", top_k=5)
"""

from __future__ import annotations
import os, json, re, shutil
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

# LlamaIndex imports
from llama_index.core import (
    Settings, SimpleDirectoryReader, StorageContext, VectorStoreIndex, load_index_from_storage,
    PromptHelper, Document
)
from llama_index.embeddings.huggingface import HuggingFaceEmbedding
from llama_index.llms.ollama import Ollama
from llama_index.vector_stores.faiss import FaissVectorStore

# FAISS
import faiss

# ---- helpers -----------------------------------------------------------------

def _sanitize_base(raw: Optional[str]) -> Path:
    s = (raw or str(Path.cwd())).replace("\r", "").replace("\n", "").strip().strip('"').strip("'")
    return Path(s).resolve()

def _load_env_dotenv(base: Path) -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv(base / ".env")
    except Exception:
        # dotenv is optional; silently ignore if missing
        pass

def _clean_text(s: str) -> str:
    s = re.sub(r"(?i)^<think>\s*\n?", "", s)
    s = re.sub(r"<.*?>", "", s, flags=re.DOTALL)
    return s.strip()

# ---- dataclass for config -----------------------------------------------------

@dataclass
class LibrarianConfig:
    base: Path                    # LIBRARIAN_ROOT
    docs_dir: Path                # base/docs
    storage_dir: Path             # base/storage
    cache_file: Path              # base/query_cache.json
    obsidian_dir: Optional[Path]  # external vault folder (optional)

# ---- main class ---------------------------------------------------------------

class Librarian:
    def __init__(
        self,
        base: Optional[Path | str] = None,
        model_name: str = "deepseek-r1:14b",
        embed_model: str = "BAAI/bge-small-en-v1.5",
        ollama_url: str = "http://localhost:11434",
    ):
        # Resolve base & .env
        env_root = os.environ.get("LIBRARIAN_ROOT")
        self.base = _sanitize_base(base if base is not None else env_root)
        _load_env_dotenv(self.base)

        # Paths
        self.cfg = LibrarianConfig(
            base=self.base,
            docs_dir=self.base / "docs",
            storage_dir=self.base / "storage",
            cache_file=self.base / "query_cache.json",
            obsidian_dir=Path(os.environ["OBSIDIAN_DIR"]).resolve()
                if os.environ.get("OBSIDIAN_DIR") else None,
        )

        # Dirs
        self.cfg.storage_dir.mkdir(parents=True, exist_ok=True)
        self.cfg.docs_dir.mkdir(parents=True, exist_ok=True)

        # Cache
        if self.cfg.cache_file.exists():
            try:
                self._cache = json.loads(self.cfg.cache_file.read_text(encoding="utf-8"))
            except Exception:
                self._cache = {}
        else:
            self._cache = {}

        # Configure LlamaIndex (global Settings)
        Settings.embed_model = HuggingFaceEmbedding(model_name=embed_model)
        Settings.llm = Ollama(model=model_name, base_url=ollama_url, request_timeout=600.0)
        Settings.prompt_helper = PromptHelper(
            context_window=2048, num_output=512, chunk_size_limit=1024, chunk_overlap_ratio=0.0
        )

        self._index: Optional[VectorStoreIndex] = None
        self._docs_mem: Optional[List[Document]] = None

    # ----- document loading ----------------------------------------------------

    def _reader_for_plain(self):
        # tiny inline plain-text reader for odd extensions
        from llama_index.core.readers.base import BaseReader

        class _Plain(BaseReader):
            def load_data(self, file_path, extra_info=None):
                with open(file_path, "r", encoding="utf-8") as f:
                    text = f.read()
                return [Document(text=text, extra_info=extra_info or {})]

        return _Plain()

    def load_documents(self, verbose: bool = True) -> List[Document]:
        plain = self._reader_for_plain()
        file_extractor = {
            ".txt": plain, ".md": plain, ".mb": plain, ".canvas": plain
        }
        required_exts = [".txt", ".md", ".mb", ".canvas", ".pdf"]
        exclude_patterns = ["**/.obsidian/**"]

        docs: List[Document] = []

        # base/docs (optional if empty)
        if self.cfg.docs_dir.exists():
            try:
                d1 = SimpleDirectoryReader(
                    input_dir=str(self.cfg.docs_dir),
                    recursive=True,
                    required_exts=required_exts,
                    file_extractor=file_extractor,
                    exclude=exclude_patterns,
                ).load_data()
            except ValueError:
                d1 = []
            docs.extend(d1)
            if verbose:
                msg = "No files found" if not d1 else f"Loaded {len(d1)} docs"
                print(f"[librarian] docs: {msg} from {self.cfg.docs_dir}")

        # external Obsidian vault (optional)
        if self.cfg.obsidian_dir and self.cfg.obsidian_dir.is_dir():
            try:
                d2 = SimpleDirectoryReader(
                    input_dir=str(self.cfg.obsidian_dir),
                    recursive=True,
                    required_exts=required_exts,
                    file_extractor=file_extractor,
                    exclude=exclude_patterns,
                ).load_data()
            except ValueError:
                d2 = []
            docs.extend(d2)
            if verbose:
                msg = "No files found" if not d2 else f"Loaded {len(d2)} docs"
                print(f"[librarian] vault: {msg} from {self.cfg.obsidian_dir}")
        else:
            if verbose:
                print("[librarian] vault: skipped (OBSIDIAN_DIR not set or missing)")

        self._docs_mem = docs
        if verbose:
            print(f"[librarian] total docs in memory: {len(docs)}")
        return docs

    # ----- index build/load ----------------------------------------------------

    def ensure_index(self, force_rebuild: bool = False, verbose: bool = True) -> VectorStoreIndex:
        if force_rebuild and self.cfg.storage_dir.exists():
            if verbose:
                print(f"[librarian] removing old storage: {self.cfg.storage_dir}")
            shutil.rmtree(self.cfg.storage_dir)
            self._index = None

        # try loading persisted FAISS
        default_store_json = self.cfg.storage_dir / "default__vector_store.json"
        if self.cfg.storage_dir.exists() and default_store_json.exists():
            if verbose:
                print(f"[librarian] loading FAISS store from {self.cfg.storage_dir}")
            vstore = FaissVectorStore.from_persist_dir(str(self.cfg.storage_dir))
            sctx = StorageContext.from_defaults(vector_store=vstore, persist_dir=str(self.cfg.storage_dir))
            self._index = load_index_from_storage(sctx)
            return self._index

        # else build fresh
        docs = self._docs_mem if self._docs_mem is not None else self.load_documents(verbose=verbose)
        if not docs:
            raise RuntimeError("No documents available to build the index. Add files to docs/ or set OBSIDIAN_DIR.")

        if verbose:
            print("[librarian] building FAISS index ...")
        dim = len(Settings.embed_model.get_text_embedding("probe"))
        faiss_idx = faiss.IndexFlatL2(dim)
        vstore = FaissVectorStore(faiss_index=faiss_idx)
        sctx = StorageContext.from_defaults(vector_store=vstore)
        self._index = VectorStoreIndex.from_documents(docs, storage_context=sctx)
        self._index.storage_context.persist(persist_dir=str(self.cfg.storage_dir))
        if verbose:
            print(f"[librarian] persisted index to {self.cfg.storage_dir}")
        return self._index

    # ----- caching -------------------------------------------------------------

    def _cache_get(self, key: str) -> Optional[str]:
        return self._cache.get(key)

    def _cache_put(self, key: str, value: str) -> None:
        self._cache[key] = value
        self.cfg.cache_file.write_text(json.dumps(self._cache, ensure_ascii=False, indent=2), encoding="utf-8")

    # ----- operations ----------------------------------------------------------

    def summarize_all(self, prompt: Optional[str] = None, recache: bool = False,
                      mode: str = "tree_summarize", top_k: int = 4) -> str:
        """
        Summarize your whole corpus via the current index.
        """
        if self._index is None:
            self.ensure_index()

        query = prompt or (
            "Summarize the key points of all documents in the folder. "
            "Do NOT include any internal reasoning or <think> tags. Only give clean summary points."
        )

        if not recache:
            hit = self._cache_get(query)
            if hit is not None:
                return hit

        qe = self._index.as_query_engine(
            response_mode=mode,
            similarity_top_k=max(1, int(top_k)),
            verbose=False,
        )
        resp = qe.query(query)
        cleaned = _clean_text(str(resp))
        self._cache_put(query, cleaned)
        return cleaned

    def quick_search(self, text_query: str, top_k: int = 5) -> List[Tuple[str, str]]:
        """
        Pure retrieval (no LLM): returns [(path, snippet), ...]
        """
        if self._index is None:
            self.ensure_index()

        retriever = self._index.as_retriever(similarity_top_k=max(1, int(top_k)))
        results = retriever.retrieve(text_query)
        out = []
        for nws in results:
            node = nws.node
            path = node.metadata.get("file_path") or node.metadata.get("source") or "<unknown>"
            snippet = (node.get_content() or "").replace("\n", " ")[:200]
            out.append((str(path), snippet))
        return out

    def analysis_from_summary(self, summary_text: str, business_prompt: Optional=str) -> str:
        """
        Run a focused analysis using the configured Ollama LLM.
        """
        p = business_prompt or (
            "Find me the Business Plan for a Windows macro app comparable to Stream Deck "
            "and provide a detailed analysis without including internal reasoning tags like <think>.\n\n"
        )
        # Using Settings.llm directly to keep deps simple (LangChain optional)
        llm = Settings.llm
        prompt = p + summary_text
        try:
            # llama_index.llms.ollama.Ollama implements .complete()
            resp = llm.complete(prompt)
            text = getattr(resp, "text", str(resp))
        except Exception:
            # fallback to plain call if needed
            text = str(llm.complete(prompt))
        return _clean_text(text)

# ---- simple CLI (optional) ----------------------------------------------------

if __name__ == "__main__":
    lib = Librarian()
    print("[librarian] base:", lib.cfg.base)
    lib.load_documents()
    lib.ensure_index()
    print(lib.summarize_all()[:1000])
