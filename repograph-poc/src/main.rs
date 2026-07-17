use repograph_poc::{RepoAnalyzer, Result};
use clap::Parser;
use std::path::{Path, PathBuf};

#[derive(Parser)]
#[command(name = "repograph-poc")]
#[command(about = "RepoGraph Rust POC - Full repository analysis with graph storage")]
struct Cli {
    #[arg(short, long)]
    repo_path: PathBuf,

    #[arg(short, long, default_value = "false")]
    benchmark: bool,

    #[arg(short, long, default_value = "false")]
    parallel: bool,

    #[arg(long, default_value = ".repograph_db")]
    db_path: PathBuf,

    #[arg(long)]
    search: Option<String>,

    #[arg(long)]
    blast_radius: Option<String>,
}

#[tokio::main]
async fn main() -> Result<()> {
    let cli = Cli::parse();

    let mut analyzer = RepoAnalyzer::new(&cli.db_path)?;

    println!("RepoGraph Rust POC - Full Repository Analysis");
    println!("Repository: {}", cli.repo_path.display());
    println!("Database: {}", cli.db_path.display());
    println!("Parallel: {}", cli.parallel);

    // If search query provided, search existing data
    if let Some(query) = cli.search {
        println!("\nSearching for: {}", query);
        let symbols = analyzer.search_symbols(&query, 20)?;

        println!("Found {} symbols:", symbols.len());
        for symbol in symbols {
            println!("  {} [{}] in {} (risk: {:?})",
                symbol.name, symbol.kind,
                Path::new(&symbol.file_path).file_name()
                    .unwrap_or_default().to_string_lossy(),
                symbol.risk_level);
        }
        return Ok(());
    }

    // If blast radius analysis requested
    if let Some(symbol_id) = cli.blast_radius {
        println!("\nAnalyzing blast radius for: {}", symbol_id);
        let affected = analyzer.get_blast_radius(&symbol_id)?;

        println!("Blast radius: {} symbols affected", affected.len());
        for symbol in affected.iter().take(10) {
            println!("  {} [{}] in {}",
                symbol.name, symbol.kind,
                Path::new(&symbol.file_path).file_name()
                    .unwrap_or_default().to_string_lossy());
        }
        return Ok(());
    }

    // Full repository analysis
    let result = analyzer.analyze_repository(&cli.repo_path, cli.parallel)?;

    // Print results
    println!("\nAnalysis Results:");
    println!("Files processed: {}", result.parse_result.file_count);
    println!("Symbols found: {}", result.parse_result.symbols.len());
    println!("Parse time: {}ms", result.parse_result.parse_time_ms);
    println!("Analysis time: {}ms", result.analysis_time_ms);

    // Graph statistics
    println!("\nGraph Statistics:");
    println!("Stored symbols: {}", result.graph_stats.total_symbols);
    println!("Relationships: {}", result.graph_stats.total_relationships);
    println!("Files indexed: {}", result.graph_stats.total_files);
    println!("Graph nodes: {}", result.graph_stats.graph_nodes);
    println!("Graph edges: {}", result.graph_stats.graph_edges);

    if cli.benchmark {
        println!("\nPerformance Metrics:");
        println!("Files per second: {:.2}",
            result.parse_result.file_count as f64 / (result.parse_result.parse_time_ms as f64 / 1000.0));
        println!("Symbols per second: {:.2}",
            result.parse_result.symbols.len() as f64 / (result.parse_result.parse_time_ms as f64 / 1000.0));
        println!("Total throughput: {:.2} symbols/sec",
            result.parse_result.symbols.len() as f64 / (result.analysis_time_ms as f64 / 1000.0));
    }

    // Sample of found symbols with enrichment
    println!("\nEnriched Symbol Sample (first 5):");
    for symbol in result.parse_result.symbols.iter().take(5) {
        if let Ok(graph_symbols) = analyzer.search_symbols(&symbol.name, 1) {
            if let Some(enriched) = graph_symbols.first() {
                println!("  {} [{}] - Risk: {:?}, Service: {:?}, Test: {}",
                    enriched.name, enriched.kind, enriched.risk_level,
                    enriched.service, enriched.is_test);
            }
        }
    }

    // Save results for comparison
    let output_file = "rust_analysis_results.json";
    std::fs::write(output_file, serde_json::to_string_pretty(&result)?)?;
    println!("\nResults saved to: {}", output_file);

    // Usage examples
    println!("\nUsage examples:");
    println!("  Search: cargo run -- -r {} --search \"function_name\"", cli.repo_path.display());
    println!("  Blast radius: cargo run -- -r {} --blast-radius \"symbol_id\"", cli.repo_path.display());

    Ok(())
}
