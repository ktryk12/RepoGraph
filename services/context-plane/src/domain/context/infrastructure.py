"""
Context Plane Infrastructure

Implements the infrastructure components that were previously in aesa.infrastructure.
This provides repository indexing, context storage, and related infrastructure for context-plane.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import json
import hashlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Union, Tuple
from dataclasses import dataclass, asdict
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


@dataclass
class IndexedFile:
    """Represents an indexed file."""

    path: str
    content: str
    file_type: str
    size: int
    hash: str
    last_modified: datetime
    metadata: Dict[str, Any]


@dataclass
class RepositoryIndex:
    """Represents a repository index."""

    repository_path: str
    indexed_files: List[IndexedFile]
    total_files: int
    total_size: int
    last_indexed: datetime
    metadata: Dict[str, Any]


class ExpertServingSummaryEngine:
    """Engine for creating expert serving summaries."""

    def __init__(self):
        logger.info("ExpertServingSummaryEngine initialized")

    def create_summary(self, content: str, max_length: int = 500) -> str:
        """Create a summary of content for expert serving."""
        try:
            if len(content) <= max_length:
                return content

            # Simple summarization - take first and last portions
            first_part = content[:max_length // 2]
            last_part = content[-(max_length // 2):]

            return f"{first_part}...\n...\n{last_part}"

        except Exception as e:
            logger.error(f"Error creating summary: {e}")
            return content[:max_length] if len(content) > max_length else content

    def extract_key_information(self, content: str, file_type: str) -> Dict[str, Any]:
        """Extract key information from content based on file type."""
        try:
            info = {
                "file_type": file_type,
                "length": len(content),
                "lines": content.count('\n') + 1 if content else 0
            }

            if file_type in ['python', 'py']:
                info.update(self._extract_python_info(content))
            elif file_type in ['javascript', 'js', 'ts']:
                info.update(self._extract_js_info(content))
            elif file_type in ['markdown', 'md']:
                info.update(self._extract_markdown_info(content))

            return info

        except Exception as e:
            logger.error(f"Error extracting key information: {e}")
            return {"file_type": file_type, "length": len(content)}

    def _extract_python_info(self, content: str) -> Dict[str, Any]:
        """Extract Python-specific information."""
        info = {}

        # Count functions and classes
        info['functions'] = content.count('def ')
        info['classes'] = content.count('class ')
        info['imports'] = content.count('import ') + content.count('from ')

        return info

    def _extract_js_info(self, content: str) -> Dict[str, Any]:
        """Extract JavaScript-specific information."""
        info = {}

        # Count functions and exports
        info['functions'] = content.count('function ') + content.count('=>')
        info['exports'] = content.count('export ') + content.count('module.exports')
        info['requires'] = content.count('require(') + content.count('import ')

        return info

    def _extract_markdown_info(self, content: str) -> Dict[str, Any]:
        """Extract Markdown-specific information."""
        info = {}

        # Count headers and links
        info['headers'] = content.count('#')
        info['links'] = content.count('[')
        info['code_blocks'] = content.count('```')

        return info


class SQLiteContextStorePortAdapter:
    """SQLite adapter for context storage."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._initialize_database()
        logger.info(f"SQLiteContextStorePortAdapter initialized with database: {db_path}")

    def _initialize_database(self):
        """Initialize the SQLite database with required tables."""
        try:
            os.makedirs(Path(self.db_path).parent, exist_ok=True)

            with sqlite3.connect(self.db_path) as conn:
                conn.execute('''
                    CREATE TABLE IF NOT EXISTS context_index (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        repository_path TEXT NOT NULL,
                        file_path TEXT NOT NULL,
                        content TEXT NOT NULL,
                        file_type TEXT NOT NULL,
                        file_size INTEGER NOT NULL,
                        file_hash TEXT NOT NULL,
                        last_modified TEXT NOT NULL,
                        metadata TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        UNIQUE(repository_path, file_path)
                    )
                ''')

                conn.execute('''
                    CREATE INDEX IF NOT EXISTS idx_repository_path
                    ON context_index(repository_path)
                ''')

                conn.execute('''
                    CREATE INDEX IF NOT EXISTS idx_file_type
                    ON context_index(file_type)
                ''')

                conn.execute('''
                    CREATE INDEX IF NOT EXISTS idx_file_hash
                    ON context_index(file_hash)
                ''')

                conn.commit()
                logger.info("Database initialized successfully")

        except Exception as e:
            logger.error(f"Failed to initialize database: {e}")
            raise

    async def store_context(self, repository_path: str, indexed_file: IndexedFile) -> bool:
        """Store context in the database."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute('''
                    INSERT OR REPLACE INTO context_index
                    (repository_path, file_path, content, file_type, file_size,
                     file_hash, last_modified, metadata, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    repository_path,
                    indexed_file.path,
                    indexed_file.content,
                    indexed_file.file_type,
                    indexed_file.size,
                    indexed_file.hash,
                    indexed_file.last_modified.isoformat(),
                    json.dumps(indexed_file.metadata),
                    datetime.now(timezone.utc).isoformat()
                ))
                conn.commit()
                return True

        except Exception as e:
            logger.error(f"Failed to store context: {e}")
            return False

    async def retrieve_context(self, query: str, repository_path: Optional[str] = None,
                             max_results: int = 20) -> List[Dict[str, Any]]:
        """Retrieve context from the database."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row

                if repository_path:
                    cursor = conn.execute('''
                        SELECT * FROM context_index
                        WHERE repository_path = ? AND (
                            content LIKE ? OR file_path LIKE ?
                        )
                        ORDER BY created_at DESC
                        LIMIT ?
                    ''', (repository_path, f'%{query}%', f'%{query}%', max_results))
                else:
                    cursor = conn.execute('''
                        SELECT * FROM context_index
                        WHERE content LIKE ? OR file_path LIKE ?
                        ORDER BY created_at DESC
                        LIMIT ?
                    ''', (f'%{query}%', f'%{query}%', max_results))

                results = []
                for row in cursor.fetchall():
                    result = {
                        'repository_path': row['repository_path'],
                        'file_path': row['file_path'],
                        'content': row['content'],
                        'file_type': row['file_type'],
                        'file_size': row['file_size'],
                        'file_hash': row['file_hash'],
                        'last_modified': row['last_modified'],
                        'metadata': json.loads(row['metadata']),
                        'created_at': row['created_at']
                    }
                    results.append(result)

                return results

        except Exception as e:
            logger.error(f"Failed to retrieve context: {e}")
            return []

    async def get_repository_stats(self, repository_path: str) -> Dict[str, Any]:
        """Get statistics for a repository."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute('''
                    SELECT
                        COUNT(*) as total_files,
                        SUM(file_size) as total_size,
                        COUNT(DISTINCT file_type) as unique_types,
                        MAX(created_at) as last_indexed
                    FROM context_index
                    WHERE repository_path = ?
                ''', (repository_path,))

                row = cursor.fetchone()
                return {
                    'repository_path': repository_path,
                    'total_files': row[0] or 0,
                    'total_size': row[1] or 0,
                    'unique_types': row[2] or 0,
                    'last_indexed': row[3]
                }

        except Exception as e:
            logger.error(f"Failed to get repository stats: {e}")
            return {'repository_path': repository_path, 'total_files': 0}


def estimate_repository_files(repository_path: str,
                            include_patterns: Optional[List[str]] = None,
                            exclude_patterns: Optional[List[str]] = None) -> int:
    """Estimate the number of files in a repository."""
    try:
        if not os.path.exists(repository_path):
            logger.warning(f"Repository path does not exist: {repository_path}")
            return 0

        file_count = 0
        exclude_patterns = exclude_patterns or ['.git', '__pycache__', 'node_modules', '.venv']

        for root, dirs, files in os.walk(repository_path):
            # Skip excluded directories
            dirs[:] = [d for d in dirs if not any(pattern in d for pattern in exclude_patterns)]

            for file in files:
                # Skip excluded files
                if any(pattern in file for pattern in exclude_patterns):
                    continue

                # Check include patterns if specified
                if include_patterns:
                    if any(pattern in file for pattern in include_patterns):
                        file_count += 1
                else:
                    file_count += 1

        logger.info(f"Estimated {file_count} files in {repository_path}")
        return file_count

    except Exception as e:
        logger.error(f"Failed to estimate repository files: {e}")
        return 0


async def index_repository(repository_path: str,
                          context_store: Optional[SQLiteContextStorePortAdapter] = None,
                          include_patterns: Optional[List[str]] = None,
                          exclude_patterns: Optional[List[str]] = None,
                          force_reindex: bool = False) -> RepositoryIndex:
    """Index a repository for context retrieval."""
    try:
        logger.info(f"Starting repository indexing: {repository_path}")

        if not os.path.exists(repository_path):
            raise FileNotFoundError(f"Repository path does not exist: {repository_path}")

        indexed_files = []
        total_size = 0
        exclude_patterns = exclude_patterns or ['.git', '__pycache__', 'node_modules', '.venv']

        summary_engine = ExpertServingSummaryEngine()

        for root, dirs, files in os.walk(repository_path):
            # Skip excluded directories
            dirs[:] = [d for d in dirs if not any(pattern in d for pattern in exclude_patterns)]

            for file in files:
                try:
                    file_path = os.path.join(root, file)
                    relative_path = os.path.relpath(file_path, repository_path)

                    # Skip excluded files
                    if any(pattern in file for pattern in exclude_patterns):
                        continue

                    # Check include patterns if specified
                    if include_patterns and not any(pattern in file for pattern in include_patterns):
                        continue

                    # Get file info
                    file_stat = os.stat(file_path)
                    file_size = file_stat.st_size

                    # Skip very large files (>10MB)
                    if file_size > 10 * 1024 * 1024:
                        logger.warning(f"Skipping large file: {relative_path} ({file_size} bytes)")
                        continue

                    # Read file content
                    try:
                        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                            content = f.read()
                    except Exception as e:
                        logger.warning(f"Could not read file {relative_path}: {e}")
                        continue

                    # Create file hash
                    file_hash = hashlib.sha256(content.encode('utf-8')).hexdigest()

                    # Determine file type
                    file_extension = os.path.splitext(file)[1].lower()
                    file_type_map = {
                        '.py': 'python',
                        '.js': 'javascript',
                        '.ts': 'typescript',
                        '.md': 'markdown',
                        '.txt': 'text',
                        '.json': 'json',
                        '.yaml': 'yaml',
                        '.yml': 'yaml'
                    }
                    file_type = file_type_map.get(file_extension, 'unknown')

                    # Extract metadata
                    metadata = summary_engine.extract_key_information(content, file_type)
                    metadata['relative_path'] = relative_path
                    metadata['file_extension'] = file_extension

                    # Create indexed file
                    indexed_file = IndexedFile(
                        path=relative_path,
                        content=content,
                        file_type=file_type,
                        size=file_size,
                        hash=file_hash,
                        last_modified=datetime.fromtimestamp(file_stat.st_mtime, tz=timezone.utc),
                        metadata=metadata
                    )

                    indexed_files.append(indexed_file)
                    total_size += file_size

                    # Store in context store if provided
                    if context_store:
                        await context_store.store_context(repository_path, indexed_file)

                except Exception as e:
                    logger.warning(f"Error indexing file {file}: {e}")
                    continue

        # Create repository index
        repository_index = RepositoryIndex(
            repository_path=repository_path,
            indexed_files=indexed_files,
            total_files=len(indexed_files),
            total_size=total_size,
            last_indexed=datetime.now(timezone.utc),
            metadata={
                'include_patterns': include_patterns,
                'exclude_patterns': exclude_patterns,
                'force_reindex': force_reindex
            }
        )

        logger.info(f"Repository indexing completed: {len(indexed_files)} files, {total_size} bytes")
        return repository_index

    except Exception as e:
        logger.error(f"Failed to index repository: {e}")
        raise