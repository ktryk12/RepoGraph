use anyhow::{Context, Result};
use dashmap::DashMap;
use petgraph::graph::{DiGraph, NodeIndex};
use petgraph::Direction;
use serde::{Deserialize, Serialize};
use sled::{Db, Tree};
use std::collections::HashSet;
use std::path::Path;
use uuid::Uuid;

use crate::Symbol;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GraphSymbol {
    pub id: String,
    pub name: String,
    pub kind: String,
    pub file_path: String,
    pub start_line: usize,
    pub end_line: usize,
    pub start_byte: usize,
    pub end_byte: usize,
    pub signature: Option<String>,
    pub risk_level: RiskLevel,
    pub service: Option<String>,
    pub is_test: bool,
    pub is_entrypoint: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum RiskLevel {
    Low,
    Medium,
    High,
    Critical,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Relationship {
    pub id: String,
    pub from_symbol: String,
    pub to_symbol: String,
    pub relationship_type: RelationshipType,
    pub file_path: String,
    pub line_number: usize,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum RelationshipType {
    Calls,
    Imports,
    Defines,
    Inherits,
    Implements,
    Uses,
    Contains,
}

#[derive(Debug)]
pub struct GraphStore {
    // Persistent storage
    db: Db,
    symbols_tree: Tree,
    relationships_tree: Tree,
    files_tree: Tree,

    // In-memory graph for fast traversal
    graph: DiGraph<String, RelationshipType>,
    symbol_to_node: DashMap<String, NodeIndex>,
    node_to_symbol: DashMap<NodeIndex, String>,

    // Caches
    symbol_cache: DashMap<String, GraphSymbol>,
    file_symbols_cache: DashMap<String, Vec<String>>,
}

impl GraphStore {
    pub fn new(db_path: &Path) -> Result<Self> {
        let db = sled::open(db_path)
            .with_context(|| format!("Failed to open graph database at {}", db_path.display()))?;

        let symbols_tree = db.open_tree("symbols")?;
        let relationships_tree = db.open_tree("relationships")?;
        let files_tree = db.open_tree("files")?;

        Ok(GraphStore {
            db,
            symbols_tree,
            relationships_tree,
            files_tree,
            graph: DiGraph::new(),
            symbol_to_node: DashMap::new(),
            node_to_symbol: DashMap::new(),
            symbol_cache: DashMap::new(),
            file_symbols_cache: DashMap::new(),
        })
    }

    pub fn insert_symbol(&mut self, symbol: Symbol) -> Result<GraphSymbol> {
        let graph_symbol = self.enrich_symbol(symbol)?;

        // Store in persistent storage
        let key = graph_symbol.id.as_bytes();
        let value = bincode::serialize(&graph_symbol)?;
        self.symbols_tree.insert(key, value)?;

        // Add to in-memory graph
        let node_idx = self.graph.add_node(graph_symbol.id.clone());
        self.symbol_to_node.insert(graph_symbol.id.clone(), node_idx);
        self.node_to_symbol.insert(node_idx, graph_symbol.id.clone());

        // Cache the symbol
        self.symbol_cache.insert(graph_symbol.id.clone(), graph_symbol.clone());

        // Update file index
        self.add_symbol_to_file_index(&graph_symbol.file_path, &graph_symbol.id)?;

        Ok(graph_symbol)
    }

    fn enrich_symbol(&self, symbol: Symbol) -> Result<GraphSymbol> {
        let id = Uuid::new_v4().to_string();

        // Extract signature from symbol name and kind
        let signature = match symbol.kind.as_str() {
            "function" => Some(format!("{}()", symbol.name)),
            "class" => Some(format!("class {}", symbol.name)),
            "struct" => Some(format!("struct {}", symbol.name)),
            "impl" => Some(format!("impl {}", symbol.name)),
            _ => None,
        };

        // Determine risk level based on patterns
        let risk_level = self.assess_risk_level(&symbol);

        // Detect if it's a test
        let is_test = self.is_test_symbol(&symbol);

        // Detect if it's an entrypoint
        let is_entrypoint = self.is_entrypoint_symbol(&symbol);

        // Extract service from file path
        let service = self.extract_service(&symbol.file_path);

        Ok(GraphSymbol {
            id,
            name: symbol.name,
            kind: symbol.kind,
            file_path: symbol.file_path,
            start_line: symbol.start_line,
            end_line: symbol.end_line,
            start_byte: symbol.start_byte,
            end_byte: symbol.end_byte,
            signature,
            risk_level,
            service,
            is_test,
            is_entrypoint,
        })
    }

    fn assess_risk_level(&self, symbol: &Symbol) -> RiskLevel {
        let name_lower = symbol.name.to_lowercase();
        let file_lower = symbol.file_path.to_lowercase();

        if name_lower.contains("unsafe") ||
           name_lower.contains("delete") ||
           name_lower.contains("drop") ||
           file_lower.contains("security") ||
           file_lower.contains("auth") {
            RiskLevel::High
        } else if name_lower.contains("execute") ||
                  name_lower.contains("run") ||
                  name_lower.contains("process") ||
                  symbol.kind == "class" {
            RiskLevel::Medium
        } else {
            RiskLevel::Low
        }
    }

    fn is_test_symbol(&self, symbol: &Symbol) -> bool {
        let name_lower = symbol.name.to_lowercase();
        let file_lower = symbol.file_path.to_lowercase();

        name_lower.starts_with("test_") ||
        name_lower.starts_with("test") ||
        file_lower.contains("test") ||
        file_lower.contains("spec")
    }

    fn is_entrypoint_symbol(&self, symbol: &Symbol) -> bool {
        let name_lower = symbol.name.to_lowercase();

        name_lower == "main" ||
        name_lower == "init" ||
        name_lower == "setup" ||
        symbol.name == "__init__"
    }

    fn extract_service(&self, file_path: &str) -> Option<String> {
        // Extract service name from file path patterns
        let path = Path::new(file_path);

        // Look for service directories
        for component in path.components() {
            if let std::path::Component::Normal(name) = component {
                if let Some(name_str) = name.to_str() {
                    if name_str.ends_with("_service") ||
                       name_str.ends_with("_api") ||
                       name_str == "api" ||
                       name_str == "core" ||
                       name_str == "shared" {
                        return Some(name_str.to_string());
                    }
                }
            }
        }

        None
    }

    fn add_symbol_to_file_index(&self, file_path: &str, symbol_id: &str) -> Result<()> {
        // Update in-memory cache
        self.file_symbols_cache
            .entry(file_path.to_string())
            .or_insert_with(Vec::new)
            .push(symbol_id.to_string());

        // Update persistent storage
        let file_symbols = self.get_file_symbols(file_path)?;
        let mut symbols = file_symbols.unwrap_or_default();
        symbols.push(symbol_id.to_string());

        let key = file_path.as_bytes();
        let value = bincode::serialize(&symbols)?;
        self.files_tree.insert(key, value)?;

        Ok(())
    }

    pub fn add_relationship(&mut self, relationship: Relationship) -> Result<()> {
        // Store in persistent storage
        let key = relationship.id.as_bytes();
        let value = bincode::serialize(&relationship)?;
        self.relationships_tree.insert(key, value)?;

        // Add edge to in-memory graph
        if let (Some(from_node), Some(to_node)) = (
            self.symbol_to_node.get(&relationship.from_symbol),
            self.symbol_to_node.get(&relationship.to_symbol)
        ) {
            self.graph.add_edge(*from_node, *to_node, relationship.relationship_type);
        }

        Ok(())
    }

    pub fn get_symbol(&self, symbol_id: &str) -> Result<Option<GraphSymbol>> {
        // Check cache first
        if let Some(symbol) = self.symbol_cache.get(symbol_id) {
            return Ok(Some(symbol.clone()));
        }

        // Load from persistent storage
        if let Some(data) = self.symbols_tree.get(symbol_id)? {
            let symbol: GraphSymbol = bincode::deserialize(&data)?;
            self.symbol_cache.insert(symbol_id.to_string(), symbol.clone());
            Ok(Some(symbol))
        } else {
            Ok(None)
        }
    }

    pub fn get_file_symbols(&self, file_path: &str) -> Result<Option<Vec<String>>> {
        // Check cache first
        if let Some(symbols) = self.file_symbols_cache.get(file_path) {
            return Ok(Some(symbols.clone()));
        }

        // Load from persistent storage
        if let Some(data) = self.files_tree.get(file_path)? {
            let symbols: Vec<String> = bincode::deserialize(&data)?;
            self.file_symbols_cache.insert(file_path.to_string(), symbols.clone());
            Ok(Some(symbols))
        } else {
            Ok(None)
        }
    }

    pub fn search_symbols(&self, query: &str, limit: usize) -> Result<Vec<GraphSymbol>> {
        let query_lower = query.to_lowercase();
        let mut results = Vec::new();

        for item in self.symbols_tree.iter() {
            let (_, value) = item?;
            let symbol: GraphSymbol = bincode::deserialize(&value)?;

            if symbol.name.to_lowercase().contains(&query_lower) && results.len() < limit {
                results.push(symbol);
            }
        }

        Ok(results)
    }

    pub fn blast_radius(&self, symbol_id: &str) -> Result<HashSet<String>> {
        let mut visited = HashSet::new();
        let mut to_visit = vec![symbol_id.to_string()];

        while let Some(current_id) = to_visit.pop() {
            if visited.contains(&current_id) {
                continue;
            }
            visited.insert(current_id.clone());

            // Find all symbols that depend on this one
            if let Some(node_idx) = self.symbol_to_node.get(&current_id) {
                let neighbors: Vec<_> = self.graph
                    .neighbors_directed(*node_idx, Direction::Incoming)
                    .collect();

                for neighbor_idx in neighbors {
                    if let Some(neighbor_id) = self.node_to_symbol.get(&neighbor_idx) {
                        to_visit.push(neighbor_id.clone());
                    }
                }
            }
        }

        visited.remove(symbol_id); // Remove the starting symbol
        Ok(visited)
    }

    pub fn get_dependencies(&self, symbol_id: &str) -> Result<Vec<String>> {
        let mut dependencies = Vec::new();

        if let Some(node_idx) = self.symbol_to_node.get(symbol_id) {
            let neighbors: Vec<_> = self.graph
                .neighbors_directed(*node_idx, Direction::Outgoing)
                .collect();

            for neighbor_idx in neighbors {
                if let Some(neighbor_id) = self.node_to_symbol.get(&neighbor_idx) {
                    dependencies.push(neighbor_id.clone());
                }
            }
        }

        Ok(dependencies)
    }

    pub fn get_dependents(&self, symbol_id: &str) -> Result<Vec<String>> {
        let mut dependents = Vec::new();

        if let Some(node_idx) = self.symbol_to_node.get(symbol_id) {
            let neighbors: Vec<_> = self.graph
                .neighbors_directed(*node_idx, Direction::Incoming)
                .collect();

            for neighbor_idx in neighbors {
                if let Some(neighbor_id) = self.node_to_symbol.get(&neighbor_idx) {
                    dependents.push(neighbor_id.clone());
                }
            }
        }

        Ok(dependents)
    }

    pub fn flush(&self) -> Result<()> {
        self.db.flush()?;
        Ok(())
    }

    pub fn stats(&self) -> GraphStats {
        GraphStats {
            total_symbols: self.symbols_tree.len(),
            total_relationships: self.relationships_tree.len(),
            total_files: self.files_tree.len(),
            graph_nodes: self.graph.node_count(),
            graph_edges: self.graph.edge_count(),
        }
    }
}

#[derive(Debug, Serialize)]
pub struct GraphStats {
    pub total_symbols: usize,
    pub total_relationships: usize,
    pub total_files: usize,
    pub graph_nodes: usize,
    pub graph_edges: usize,
}