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

import json as _json  # keep both json and _json if you prefer

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
_SETTINGS_FILE = "settings.json"  # lives under LIBRARIAN_ROOT (nodes/librarian)

def _load_settings(base: Path) -> dict:
    try:
        p = base / _SETTINGS_FILE
        if p.exists():
            return _json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}

def _save_settings(base: Path, data: dict) -> None:
    try:
        p = base / _SETTINGS_FILE
        p.write_text(_json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass

def _resolve_docs_dir(base: Path) -> Path:
    """
    Precedence:
      1) env LIBRARIAN_DOCS_DIR (graph/node override)
      2) settings.json: {"docs_dir": "..."}
      3) <base>/docs  (default)
    """
    # 1) env override (from node / launcher)
    env_dir = os.environ.get("LIBRARIAN_DOCS_DIR", "").strip().strip('"').strip("'")
    if env_dir:
        return Path(env_dir).expanduser().resolve()

    # 2) persisted setting
    st = _load_settings(base)
    cfg_dir = (st.get("docs_dir") or "").strip()
    if cfg_dir:
        return Path(cfg_dir).expanduser().resolve()

    # 3) default
    return (base / "docs").resolve()


def _sanitize_base(raw: Optional[str | Path]) -> Path:
    # Always work with a string before doing .replace/.strip operations
    s = str(raw or Path.cwd())
    s = s.replace("\r", "").replace("\n", "").strip().strip('"').strip("'")
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


def analyze_with_sources(self, question: str, top_k: int = 8, max_context_chars: int = 4000) -> tuple[str, list[dict]]:
    """
    Retrieve top_k relevant chunks for `question`, run an LLM analysis over a compact context,
    and return (answer_text, sources_list). Each source = {idx, path, score, snippet}.
    """
    if not question or not question.strip():
        raise ValueError("analysis question is empty")

    # Ensure index
    if self._index is None:
        self.ensure_index()

    # 1) Retrieve relevant nodes (no LLM)
    retriever = self._index.as_retriever(similarity_top_k=max(1, int(top_k)))
    results = retriever.retrieve(question)

    # 2) Build a compact context from snippets (trim to avoid giant prompts)
    pieces = []
    sources = []
    for i, nws in enumerate(results, 1):
        node = nws.node
        score = getattr(nws, "score", None)
        path = node.metadata.get("file_path") or node.metadata.get("source") or "<unknown>"
        text = (node.get_content() or "").replace("\r", " ").replace("\n", " ")
        snip = text[:400]
        pieces.append(f"[{i}] {snip}")
        sources.append({
            "idx": i,
            "path": str(path),
            "score": float(score) if score is not None else None,
            "snippet": snip
        })

    context = "\n\n".join(pieces)
    if len(context) > max_context_chars:
        context = context[:max_context_chars] + "..."

    # 3) Ask the LLM. We do NOT want chain-of-thought; just final answer + bracket refs.
    prompt = (
        "You are analyzing the user's question using the provided source snippets.\n"
        "Write a clear, concise answer. If you cite, use bracket numbers like [1], [2], matching the snippets.\n"
        "Do NOT include any internal reasoning tags like <think>.\n\n"
        f"Question:\n{question}\n\n"
        "Relevant snippets (numbered):\n"
        f"{context}\n\n"
        "Answer (use [n] to reference snippets when appropriate):\n"
    )

    llm = Settings.llm
    try:
        resp = llm.complete(prompt)
        answer = getattr(resp, "text", str(resp))
    except Exception:
        answer = str(llm.complete(prompt))

    return (answer.strip(), sources)


# ---- dataclass for config -----------------------------------------------------

@dataclass
class LibrarianConfig:
    base: Path                    # LIBRARIAN_ROOT
    docs_dir: Path                # base/docs (overridable)
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

        # load persisted settings (for docs_dir, etc.)
        self._settings = _load_settings(self.base)

        # Paths (docs_dir now resolved via precedence helper)
        resolved_docs = _resolve_docs_dir(self.base)
        self.cfg = LibrarianConfig(
            base=self.base,
            docs_dir=resolved_docs,
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

    # ----- public docs-dir API -------------------------------------------------
    def get_docs_dir(self) -> Path:
        """Return the active documents directory."""
        return self.cfg.docs_dir

    def set_docs_dir(self, path: Path | str, persist: bool = True) -> Path:
        """
        Update the active documents directory (creates it if missing).
        If persist=True, writes settings.json so the next launch recalls it.
        Note: if LIBRARIAN_DOCS_DIR env is set by a node/graph, that will
        override on next process start, regardless of persisted value.
        """
        p = Path(str(path)).expanduser().resolve()
        p.mkdir(parents=True, exist_ok=True)
        self.cfg.docs_dir = p
        self._docs_mem = None  # force reload on next load_documents
        if persist:
            self._settings["docs_dir"] = str(p)
            _save_settings(self.base, self._settings)
        return p

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

    # in librarian_core.py (near _reader_for_plain), add:
    # inside class Librarian, near _reader_for_plain
    def _reader_for_html(self):
        from llama_index.core.readers.base import BaseReader
        try:
            from bs4 import BeautifulSoup
            def strip_html(s):
                return BeautifulSoup(s, "html.parser").get_text(separator=" ", strip=True)
        except Exception:
            import re
            def strip_html(s):
                return re.sub(r"<[^>]+>", " ", s)

        class _HTMLReader(BaseReader):
            def load_data(self, file_path, extra_info=None):
                with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    html = f.read()
                text = strip_html(html)
                from llama_index.core import Document
                return [Document(text=text, extra_info=extra_info or {})]
        return _HTMLReader()


    def load_documents(self, verbose: bool = True) -> List[Document]:
        plain = self._reader_for_plain()
        htmlr = self._reader_for_html()
        file_extractor = {
            ".txt": plain, ".md": plain, ".mb": plain, ".canvas": plain,
            ".html": htmlr, ".htm": htmlr,
        }
        required_exts = [".txt", ".md", ".mb", ".canvas", ".pdf", ".html", ".htm"]
        exclude_patterns = ["**/.obsidian/**"]

        docs: List[Document] = []

        # base/docs (optional if empty) — now whatever cfg.docs_dir points to
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

    def analysis_from_summary(self, summary_text: str, business_prompt: Optional[str] = None) -> str:
        """
        Run a focused analysis using the configured Ollama LLM.
        """
        p = business_prompt or (
            "Find me the Business Plan for a Windows macro app comparable to Stream Deck "
            "and provide a detailed analysis without including internal reasoning tags like <think>.\n\n"
        )
        llm = Settings.llm
        prompt = p + summary_text
        try:
            resp = llm.complete(prompt)
            text = getattr(resp, "text", str(resp))
        except Exception:
            text = str(llm.complete(prompt))
        return _clean_text(text)

Librarian.analyze_with_sources = analyze_with_sources

# ---- simple CLI (optional) ----------------------------------------------------

if __name__ == "__main__":
    lib = Librarian()
    print("[librarian] base:", lib.cfg.base)
    print("[librarian] docs:", lib.get_docs_dir())
    lib.load_documents()
    lib.ensure_index()
    print(lib.summarize_all()[:1000])
