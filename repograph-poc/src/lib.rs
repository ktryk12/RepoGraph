pub use anyhow::{Context, Result};
pub use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::path::Path;
use std::time::Instant;
use tree_sitter::{Language, Query, QueryCursor};
use walkdir::WalkDir;
use rayon::prelude::*;

pub mod graph_store;
pub mod api_server;
pub mod mcp_server;

pub use graph_store::{GraphStore, GraphSymbol, Relationship, RelationshipType, RiskLevel};
pub use api_server::{start_server, create_router};
pub use mcp_server::McpServer;

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct Symbol {
    pub name: String,
    pub kind: String,
    pub start_byte: usize,
    pub end_byte: usize,
    pub start_line: usize,
    pub end_line: usize,
    pub file_path: String,
}

#[derive(Debug, Serialize)]
pub struct ParseResult {
    pub symbols: Vec<Symbol>,
    pub file_count: usize,
    pub parse_time_ms: u64,
    pub language: String,
}

pub struct RepoParser {
    languages: HashMap<String, Language>,
    symbol_queries: HashMap<String, Query>,
}

impl RepoParser {
    pub fn new() -> Result<Self> {
        let mut languages = HashMap::new();
        let mut symbol_queries = HashMap::new();

        // Python setup
        languages.insert("py".to_string(), tree_sitter_python::language());
        symbol_queries.insert(
            "py".to_string(),
            Query::new(
                tree_sitter_python::language(),
                r#"
                (function_definition) @function
                (class_definition) @class
                "#,
            )?,
        );

        // JavaScript setup
        languages.insert("js".to_string(), tree_sitter_javascript::language());
        symbol_queries.insert(
            "js".to_string(),
            Query::new(
                tree_sitter_javascript::language(),
                r#"
                (function_declaration) @function
                (class_declaration) @class
                "#,
            )?,
        );

        // TypeScript setup
        languages.insert("ts".to_string(), tree_sitter_typescript::language_typescript());
        symbol_queries.insert(
            "ts".to_string(),
            Query::new(
                tree_sitter_typescript::language_typescript(),
                r#"
                (function_declaration) @function
                (class_declaration) @class
                "#,
            )?,
        );

        // Rust setup
        languages.insert("rs".to_string(), tree_sitter_rust::language());
        symbol_queries.insert(
            "rs".to_string(),
            Query::new(
                tree_sitter_rust::language(),
                r#"
                (function_item) @function
                (struct_item) @struct
                (impl_item) @impl
                "#,
            )?,
        );

        // Go setup
        languages.insert("go".to_string(), tree_sitter_go::language());
        symbol_queries.insert(
            "go".to_string(),
            Query::new(
                tree_sitter_go::language(),
                r#"
                (function_declaration) @function
                (type_declaration) @type
                "#,
            )?,
        );

        Ok(RepoParser {
            languages,
            symbol_queries,
        })
    }

    pub fn parse_file(&self, path: &Path) -> Result<Vec<Symbol>> {
        let extension = path
            .extension()
            .and_then(|ext| ext.to_str())
            .unwrap_or("");

        let lang_key = match extension {
            "py" => "py",
            "js" | "jsx" => "js",
            "ts" | "tsx" => "ts",
            "rs" => "rs",
            "go" => "go",
            _ => return Ok(vec![]), // Skip unsupported files
        };

        let language = self.languages.get(lang_key)
            .ok_or_else(|| anyhow::anyhow!("Language not supported: {}", lang_key))?;

        let query = self.symbol_queries.get(lang_key)
            .ok_or_else(|| anyhow::anyhow!("Query not found for: {}", lang_key))?;

        let source_code = std::fs::read_to_string(path)
            .with_context(|| format!("Failed to read file: {}", path.display()))?;

        let mut parser = tree_sitter::Parser::new();
        parser.set_language(*language)
            .with_context(|| format!("Failed to set language for: {}", lang_key))?;

        let tree = parser.parse(&source_code, None)
            .ok_or_else(|| anyhow::anyhow!("Failed to parse file: {}", path.display()))?;

        self.extract_symbols(&tree, query, &source_code, path)
    }

    fn extract_symbols(
        &self,
        tree: &tree_sitter::Tree,
        query: &Query,
        source_code: &str,
        file_path: &Path,
    ) -> Result<Vec<Symbol>> {
        let mut cursor = QueryCursor::new();
        let captures = cursor.captures(query, tree.root_node(), source_code.as_bytes());
        let mut symbols = Vec::new();

        for (match_, _) in captures {
            for capture in match_.captures {
                let node = capture.node;
                let capture_name = &query.capture_names()[capture.index as usize];

                // Extract symbol name from the node
                let name = self.extract_symbol_name(&node, source_code.as_bytes())
                    .unwrap_or_else(|| format!("<unnamed_{}>", capture_name));

                symbols.push(Symbol {
                    name,
                    kind: capture_name.to_string(),
                    start_byte: node.start_byte(),
                    end_byte: node.end_byte(),
                    start_line: node.start_position().row,
                    end_line: node.end_position().row,
                    file_path: file_path.to_string_lossy().to_string(),
                });
            }
        }

        Ok(symbols)
    }

    fn extract_symbol_name(&self, node: &tree_sitter::Node, source_code: &[u8]) -> Option<String> {
        // Try to find name in child nodes
        for child in node.children(&mut node.walk()) {
            if child.kind() == "identifier" || child.kind() == "type_identifier" {
                if let Ok(name) = child.utf8_text(source_code) {
                    return Some(name.to_string());
                }
            }
        }

        // Fallback: use the first few words of the node text
        if let Ok(text) = node.utf8_text(source_code) {
            let first_line = text.lines().next().unwrap_or(text);
            let words: Vec<&str> = first_line.split_whitespace().take(3).collect();
            if !words.is_empty() {
                return Some(words.join(" "));
            }
        }

        None
    }

    pub fn parse_repo(&self, repo_path: &Path, parallel: bool) -> Result<ParseResult> {
        let start_time = Instant::now();

        // Collect all source files
        let files: Vec<_> = WalkDir::new(repo_path)
            .into_iter()
            .filter_map(|entry| entry.ok())
            .filter(|entry| entry.file_type().is_file())
            .filter(|entry| {
                if let Some(ext) = entry.path().extension().and_then(|s| s.to_str()) {
                    matches!(ext, "py" | "js" | "jsx" | "ts" | "tsx" | "rs" | "go")
                } else {
                    false
                }
            })
            .map(|entry| entry.path().to_owned())
            .collect();

        // Parse files (parallel vs sequential)
        let all_symbols: Vec<Symbol> = if parallel {
            files
                .par_iter()
                .flat_map(|path| {
                    match self.parse_file(path) {
                        Ok(symbols) => symbols,
                        Err(_) => vec![]
                    }
                })
                .collect()
        } else {
            files
                .iter()
                .flat_map(|path| {
                    match self.parse_file(path) {
                        Ok(symbols) => symbols,
                        Err(_) => vec![]
                    }
                })
                .collect()
        };

        let parse_time = start_time.elapsed();

        Ok(ParseResult {
            symbols: all_symbols,
            file_count: files.len(),
            parse_time_ms: parse_time.as_millis() as u64,
            language: "multi".to_string(),
        })
    }
}

/// Integrated repository analyzer with parsing and graph storage
pub struct RepoAnalyzer {
    parser: RepoParser,
    graph_store: GraphStore,
}

#[derive(Debug, Serialize)]
pub struct AnalysisResult {
    pub parse_result: ParseResult,
    pub graph_stats: graph_store::GraphStats,
    pub analysis_time_ms: u64,
}

impl RepoAnalyzer {
    pub fn new(graph_db_path: &Path) -> Result<Self> {
        let parser = RepoParser::new()?;
        let graph_store = GraphStore::new(graph_db_path)?;

        Ok(RepoAnalyzer {
            parser,
            graph_store,
        })
    }

    pub fn analyze_repository(&mut self, repo_path: &Path, parallel: bool) -> Result<AnalysisResult> {
        let start_time = Instant::now();

        // Parse repository and get symbols
        let parse_result = self.parser.parse_repo(repo_path, parallel)?;

        // Store symbols in graph store
        for symbol in &parse_result.symbols {
            let graph_symbol = self.graph_store.insert_symbol(symbol.clone())?;

            // Extract relationships from symbol context
            self.extract_relationships(&graph_symbol)?;
        }

        // Flush changes to disk
        self.graph_store.flush()?;

        let analysis_time = start_time.elapsed();

        Ok(AnalysisResult {
            parse_result,
            graph_stats: self.graph_store.stats(),
            analysis_time_ms: analysis_time.as_millis() as u64,
        })
    }

    fn extract_relationships(&mut self, symbol: &GraphSymbol) -> Result<()> {
        // Basic relationship extraction based on symbol kind and context
        // This is a simplified implementation - in practice would parse AST more deeply

        match symbol.kind.as_str() {
            "function" => {
                // Functions can call other functions
                // In a full implementation, we'd parse the function body
                // For now, just create placeholder relationships
                if symbol.name.contains("call") || symbol.name.contains("invoke") {
                    // This would be extracted from actual function calls in AST
                }
            }
            "class" => {
                // Classes can inherit from other classes
                // Would extract from class definition AST
            }
            "import" => {
                // Imports create dependency relationships
                // Would extract from import statements
            }
            _ => {}
        }

        Ok(())
    }

    pub fn get_symbol(&self, symbol_id: &str) -> Result<Option<GraphSymbol>> {
        self.graph_store.get_symbol(symbol_id)
    }

    pub fn search_symbols(&self, query: &str, limit: usize) -> Result<Vec<GraphSymbol>> {
        self.graph_store.search_symbols(query, limit)
    }

    pub fn get_blast_radius(&self, symbol_id: &str) -> Result<Vec<GraphSymbol>> {
        let symbol_ids = self.graph_store.blast_radius(symbol_id)?;
        let mut symbols = Vec::new();

        for id in symbol_ids {
            if let Some(symbol) = self.graph_store.get_symbol(&id)? {
                symbols.push(symbol);
            }
        }

        Ok(symbols)
    }

    pub fn get_file_symbols(&self, file_path: &str) -> Result<Vec<GraphSymbol>> {
        let symbol_ids = self.graph_store.get_file_symbols(file_path)?;
        let mut symbols = Vec::new();

        if let Some(ids) = symbol_ids {
            for id in ids {
                if let Some(symbol) = self.graph_store.get_symbol(&id)? {
                    symbols.push(symbol);
                }
            }
        }

        Ok(symbols)
    }

    pub fn get_dependencies(&self, symbol_id: &str) -> Result<Vec<GraphSymbol>> {
        let dep_ids = self.graph_store.get_dependencies(symbol_id)?;
        let mut dependencies = Vec::new();

        for id in dep_ids {
            if let Some(symbol) = self.graph_store.get_symbol(&id)? {
                dependencies.push(symbol);
            }
        }

        Ok(dependencies)
    }

    pub fn get_dependents(&self, symbol_id: &str) -> Result<Vec<GraphSymbol>> {
        let dependent_ids = self.graph_store.get_dependents(symbol_id)?;
        let mut dependents = Vec::new();

        for id in dependent_ids {
            if let Some(symbol) = self.graph_store.get_symbol(&id)? {
                dependents.push(symbol);
            }
        }

        Ok(dependents)
    }

    pub fn stats(&self) -> graph_store::GraphStats {
        self.graph_store.stats()
    }
}