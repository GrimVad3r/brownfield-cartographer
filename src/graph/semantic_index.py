"""
src/graph/semantic_index.py
---------------------------------
Semantic index builder for module/function purpose statements.

Uses ChromaDB with SentenceTransformer embeddings when available.
Falls back to a JSONL index if dependencies are missing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.graph.knowledge_graph import KnowledgeGraph
from src.models.nodes import FunctionNode, ModuleNode
from src.utils.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class SemanticDocument:
    doc_id: str
    text: str
    metadata: dict[str, Any]


class SemanticIndex:
    """
    Build and persist a vector index for semantic search.
    Output directory: <output_dir>/semantic_index
    """

    def __init__(self, output_dir: Path) -> None:
        self._index_dir = output_dir / "semantic_index"
        self._index_dir.mkdir(parents=True, exist_ok=True)

    def build(self, graph: KnowledgeGraph) -> Path:
        docs = self._collect_documents(graph)
        if not docs:
            logger.info("semantic_index_empty")
            return self._index_dir

        try:
            import chromadb
            from sentence_transformers import SentenceTransformer

            model = SentenceTransformer("all-MiniLM-L6-v2")
            texts = [d.text for d in docs]
            embeddings = model.encode(texts, show_progress_bar=False)

            client = chromadb.PersistentClient(path=str(self._index_dir))
            collection = client.get_or_create_collection("cartographer")
            collection.upsert(
                ids=[d.doc_id for d in docs],
                embeddings=[e.tolist() for e in embeddings],
                documents=texts,
                metadatas=[d.metadata for d in docs],
            )
            logger.info("semantic_index_built", count=len(docs), path=str(self._index_dir))
            return self._index_dir

        except Exception as exc:  # noqa: BLE001
            logger.warning("semantic_index_chroma_unavailable_fallback", error=str(exc))
            self._write_fallback(docs)
            return self._index_dir

    def _collect_documents(self, graph: KnowledgeGraph) -> list[SemanticDocument]:
        docs: list[SemanticDocument] = []

        for module in graph.all_nodes_of_type(ModuleNode):
            text = module.purpose_statement or (
                f"Module {module.path} ({module.language.value}). "
                f"Exports: {', '.join(module.exported_symbols[:5]) or 'none'}."
            )
            docs.append(SemanticDocument(
                doc_id=f"module:{module.node_id}",
                text=text,
                metadata={
                    "type": "module",
                    "path": module.path,
                    "language": module.language.value,
                },
            ))

        for fn in graph.all_nodes_of_type(FunctionNode):
            text = fn.purpose_statement or f"Function {fn.qualified_name} in {fn.parent_module}."
            docs.append(SemanticDocument(
                doc_id=f"function:{fn.node_id}",
                text=text,
                metadata={
                    "type": "function",
                    "qualified_name": fn.qualified_name,
                    "parent_module": fn.parent_module,
                },
            ))

        return docs

    def _write_fallback(self, docs: list[SemanticDocument]) -> None:
        path = self._index_dir / "index.jsonl"
        with path.open("w", encoding="utf-8") as fh:
            for d in docs:
                fh.write(
                    json.dumps(
                        {"id": d.doc_id, "text": d.text, "metadata": d.metadata},
                        ensure_ascii=True,
                    )
                    + "\n"
                )
        logger.info("semantic_index_fallback_written", path=str(path), count=len(docs))
