# from __future__ import annotations

# import json
# from pathlib import Path
# from typing import Any, Dict, List, Optional

# from models import KnowledgeStats, KnowledgeSection


# class KnowledgeManager:
#     """
#     Loads and manages the Singapore Real Estate Knowledge Base.

#     Expected structure:

#         rtd_knowledge/
#         ├── geography/
#         ├── property/
#         ├── hdb/
#         ├── private_property/
#         ├── finance/
#         ├── transaction/
#         ├── regulations/
#         ├── planning/
#         ├── terminology/
#         ├── intents/
#         ├── inference/
#         ├── rtd/
#         ├── metadata/
#         └── data_policy/

#     The manager is responsible for:
#         - loading knowledge files
#         - deterministic ordering
#         - category/file retrieval
#         - LLM context generation
#         - provenance preservation
#         - validation
#         - reload support
#     """

#     SUPPORTED_EXTENSIONS = {
#         ".json",
#         ".md",
#         ".txt",
#     }

#     def __init__(
#         self,
#         knowledge_dir: str = "rtd_knowledge",
#     ):
#         self.knowledge_dir = Path(knowledge_dir)

#         self.sections: List[KnowledgeSection] = []

#         self._context_cache: Optional[str] = None

#         self._loaded = False

#         self._stats = KnowledgeStats(
#             total_files=0,
#             json_files=0,
#             markdown_files=0,
#             text_files=0,
#             categories=0,
#         )

#     # ============================================================
#     # PUBLIC API
#     # ============================================================

#     def load(self) -> None:
#         """
#         Load all supported knowledge files recursively.
#         """

#         if not self.knowledge_dir.exists():
#             raise FileNotFoundError(
#                 f"Knowledge directory does not exist: "
#                 f"{self.knowledge_dir.resolve()}"
#             )

#         if not self.knowledge_dir.is_dir():
#             raise NotADirectoryError(
#                 f"Knowledge path is not a directory: "
#                 f"{self.knowledge_dir.resolve()}"
#             )

#         self.sections.clear()
#         self._context_cache = None

#         json_count = 0
#         markdown_count = 0
#         text_count = 0

#         files = sorted(
#             [
#                 path
#                 for path in self.knowledge_dir.rglob("*")
#                 if path.is_file()
#                 and path.suffix.lower() in self.SUPPORTED_EXTENSIONS
#             ],
#             key=lambda p: str(p.relative_to(self.knowledge_dir)).lower(),
#         )

#         for file_path in files:

#             relative_path = file_path.relative_to(
#                 self.knowledge_dir
#             )

#             category = (
#                 relative_path.parts[0]
#                 if len(relative_path.parts) > 1
#                 else "root"
#             )

#             suffix = file_path.suffix.lower()

#             try:
#                 content = self._load_file(
#                     file_path=file_path,
#                     suffix=suffix,
#                 )

#             except Exception as exc:
#                 raise RuntimeError(
#                     f"Failed to load knowledge file "
#                     f"'{relative_path}': {exc}"
#                 ) from exc

#             self.sections.append(
#                 KnowledgeSection(
#                     category=category,
#                     file_name=file_path.name,
#                     relative_path=str(relative_path),
#                     content=content,
#                 )
#             )

#             if suffix == ".json":
#                 json_count += 1

#             elif suffix == ".md":
#                 markdown_count += 1

#             elif suffix == ".txt":
#                 text_count += 1

#         categories = {
#             section.category
#             for section in self.sections
#         }

#         self._stats = KnowledgeStats(
#             total_files=len(self.sections),
#             json_files=json_count,
#             markdown_files=markdown_count,
#             text_files=text_count,
#             categories=len(categories),
#         )

#         self._loaded = True

#         print(
#             "[KNOWLEDGE] Loaded "
#             f"{self._stats.total_files} files "
#             f"across {self._stats.categories} categories"
#         )

#     def reload(self) -> None:
#         """
#         Reload the knowledge base from disk.
#         """

#         self._loaded = False
#         self.load()

#     def is_loaded(self) -> bool:
#         """
#         Return whether the knowledge base has been loaded.
#         """

#         return self._loaded

#     def get_llm_context(
#         self,
#         categories: Optional[List[str]] = None,
#         files: Optional[List[str]] = None,
#     ) -> str:
#         """
#         Return deterministic knowledge context for LLM injection.

#         If categories/files are omitted, all loaded knowledge is returned.

#         Prefer filtered context in production when possible.
#         """

#         if not self._loaded:
#             self.load()

#         selected_sections = self._select_sections(
#             categories=categories,
#             files=files,
#         )

#         if (
#             categories is None
#             and files is None
#             and self._context_cache is not None
#         ):
#             return self._context_cache

#         context_parts: List[str] = []

#         for section in selected_sections:

#             context_parts.append(
#                 self._format_section(section)
#             )

#         context = "\n\n".join(context_parts)

#         if categories is None and files is None:
#             self._context_cache = context

#         return context

#     def get_category(
#         self,
#         category: str,
#     ) -> List[KnowledgeSection]:
#         """
#         Return all knowledge files in a category.
#         """

#         if not self._loaded:
#             self.load()

#         category = category.strip().lower()

#         return [
#             section
#             for section in self.sections
#             if section.category.lower() == category
#         ]

#     def get_file(
#         self,
#         file_name: str,
#     ) -> Optional[KnowledgeSection]:
#         """
#         Find a knowledge file by file name or relative path.
#         """

#         if not self._loaded:
#             self.load()

#         normalized = file_name.replace("\\", "/").lower()

#         for section in self.sections:

#             if (
#                 section.file_name.lower() == normalized
#                 or section.relative_path.lower() == normalized
#             ):
#                 return section

#         return None

#     def get_stats(self) -> KnowledgeStats:
#         """
#         Return knowledge-base statistics.
#         """

#         return self._stats

#     def validate(self) -> Dict[str, Any]:
#         """
#         Validate the knowledge-base structure and files.

#         Returns a validation report rather than silently ignoring
#         problems.
#         """

#         if not self.knowledge_dir.exists():
#             return {
#                 "valid": False,
#                 "errors": [
#                     f"Knowledge directory does not exist: "
#                     f"{self.knowledge_dir}"
#                 ],
#             }

#         errors: List[str] = []
#         warnings: List[str] = []

#         if not self._loaded:
#             self.load()

#         if not self.sections:
#             errors.append(
#                 "No supported knowledge files were found."
#             )

#         required_categories = {
#             "geography",
#             "property",
#             "hdb",
#             "private_property",
#             "finance",
#             "transaction",
#             "regulations",
#             "planning",
#             "terminology",
#             "intents",
#             "inference",
#             "rtd",
#         }

#         loaded_categories = {
#             section.category
#             for section in self.sections
#         }

#         for category in sorted(required_categories):

#             if category not in loaded_categories:
#                 warnings.append(
#                     f"Expected category missing: {category}"
#                 )

#         return {
#             "valid": len(errors) == 0,
#             "errors": errors,
#             "warnings": warnings,
#             "stats": {
#                 "total_files": self._stats.total_files,
#                 "json_files": self._stats.json_files,
#                 "markdown_files": self._stats.markdown_files,
#                 "text_files": self._stats.text_files,
#                 "categories": self._stats.categories,
#             },
#         }

#     # ============================================================
#     # INTERNAL FILE LOADING
#     # ============================================================

#     def _load_file(
#         self,
#         file_path: Path,
#         suffix: str,
#     ) -> Any:

#         if suffix == ".json":

#             with file_path.open(
#                 "r",
#                 encoding="utf-8",
#             ) as file:

#                 return json.load(file)

#         return file_path.read_text(
#             encoding="utf-8"
#         )

#     # ============================================================
#     # SECTION SELECTION
#     # ============================================================

#     def _select_sections(
#         self,
#         categories: Optional[List[str]],
#         files: Optional[List[str]],
#     ) -> List[KnowledgeSection]:

#         selected = self.sections

#         if categories:

#             normalized_categories = {
#                 category.strip().lower()
#                 for category in categories
#             }

#             selected = [
#                 section
#                 for section in selected
#                 if section.category.lower()
#                 in normalized_categories
#             ]

#         if files:

#             normalized_files = {
#                 file.replace("\\", "/").lower()
#                 for file in files
#             }

#             selected = [
#                 section
#                 for section in selected
#                 if (
#                     section.file_name.lower()
#                     in normalized_files
#                     or section.relative_path.lower()
#                     in normalized_files
#                 )
#             ]

#         return selected

#     # ============================================================
#     # LLM FORMATTING
#     # ============================================================

#     def _format_section(
#         self,
#         section: KnowledgeSection,
#     ) -> str:

#         content = self._serialize_content(
#             section.content
#         )

#         return (
#             "\n"
#             "============================================================\n"
#             f"KNOWLEDGE CATEGORY: {section.category}\n"
#             f"KNOWLEDGE FILE: {section.relative_path}\n"
#             "============================================================\n"
#             f"{content}\n"
#         )

#     def _serialize_content(
#         self,
#         content: Any,
#     ) -> str:

#         if isinstance(content, str):
#             return content

#         return json.dumps(
#             content,
#             indent=2,
#             ensure_ascii=False,
#             sort_keys=True,
#         )


# # ================================================================
# # GLOBAL KNOWLEDGE MANAGER
# # ================================================================

# knowledge_manager = KnowledgeManager(
#     knowledge_dir=r"D:\RTD AI Workflow\singapore_real_estate_knowledge_base"
# )

# import markdown


class Knowledge:
    def __init__(self):
        self.path = "knowledge.md"

    def load(self):
        with open(self.path, 'r', encoding='utf-8') as f:
            data = f.read()
        
        return data

knowledge_manager = Knowledge()