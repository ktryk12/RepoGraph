use criterion::{black_box, criterion_group, criterion_main, Criterion, BenchmarkId};
use repograph_poc::{RepoParser, ParseResult};
use std::path::Path;
use tempfile::TempDir;
use std::fs;

// Sample Python code for benchmarking
const PYTHON_SAMPLE: &str = r#"
import os
import sys
from typing import List, Dict, Optional

class DataProcessor:
    """A sample data processing class."""

    def __init__(self, name: str):
        self.name = name
        self.data = []

    def process_data(self, input_data: List[Dict[str, any]]) -> Optional[Dict]:
        """Process the input data and return results."""
        results = {}

        for item in input_data:
            if self.validate_item(item):
                processed = self.transform_item(item)
                results[processed['id']] = processed

        return results if results else None

    def validate_item(self, item: Dict) -> bool:
        """Validate a single data item."""
        required_fields = ['id', 'name', 'value']
        return all(field in item for field in required_fields)

    def transform_item(self, item: Dict) -> Dict:
        """Transform a single data item."""
        return {
            'id': str(item['id']),
            'name': item['name'].strip().lower(),
            'value': float(item['value']),
            'processed': True
        }

def main():
    processor = DataProcessor("benchmark")
    sample_data = [
        {'id': 1, 'name': 'Test Item', 'value': 42.0},
        {'id': 2, 'name': 'Another Item', 'value': 100.5},
    ]
    result = processor.process_data(sample_data)
    print(f"Processed {len(result)} items")

if __name__ == "__main__":
    main()
"#;

// JavaScript sample
const JAVASCRIPT_SAMPLE: &str = r#"
class ApiClient {
    constructor(baseUrl, apiKey) {
        this.baseUrl = baseUrl;
        this.apiKey = apiKey;
        this.cache = new Map();
    }

    async fetchData(endpoint) {
        const cacheKey = `${this.baseUrl}${endpoint}`;

        if (this.cache.has(cacheKey)) {
            return this.cache.get(cacheKey);
        }

        try {
            const response = await fetch(`${this.baseUrl}${endpoint}`, {
                headers: {
                    'Authorization': `Bearer ${this.apiKey}`,
                    'Content-Type': 'application/json'
                }
            });

            if (!response.ok) {
                throw new Error(`HTTP ${response.status}: ${response.statusText}`);
            }

            const data = await response.json();
            this.cache.set(cacheKey, data);
            return data;

        } catch (error) {
            console.error('Fetch failed:', error);
            throw error;
        }
    }

    invalidateCache(pattern) {
        for (const key of this.cache.keys()) {
            if (key.includes(pattern)) {
                this.cache.delete(key);
            }
        }
    }
}

export { ApiClient };
"#;

fn create_test_files(size: usize) -> TempDir {
    let temp_dir = TempDir::new().unwrap();

    // Create multiple files of the specified complexity
    for i in 0..size {
        // Python files
        fs::write(
            temp_dir.path().join(format!("sample_{}.py", i)),
            PYTHON_SAMPLE
        ).unwrap();

        // JavaScript files
        fs::write(
            temp_dir.path().join(format!("client_{}.js", i)),
            JAVASCRIPT_SAMPLE
        ).unwrap();
    }

    temp_dir
}

fn bench_single_file_parsing(c: &mut Criterion) {
    let parser = RepoParser::new().unwrap();
    let temp_dir = create_test_files(1);
    let python_file = temp_dir.path().join("sample_0.py");

    c.bench_function("parse_single_python_file", |b| {
        b.iter(|| parser.parse_file(black_box(&python_file)))
    });
}

fn bench_multi_file_parsing(c: &mut Criterion) {
    let parser = RepoParser::new().unwrap();
    let mut group = c.benchmark_group("multi_file_parsing");

    for file_count in [1, 5, 10, 25, 50].iter() {
        let temp_dir = create_test_files(*file_count);

        group.bench_with_input(
            BenchmarkId::new("sequential", file_count),
            file_count,
            |b, _| {
                b.iter(|| parser.parse_repo(black_box(temp_dir.path()), false))
            }
        );

        group.bench_with_input(
            BenchmarkId::new("parallel", file_count),
            file_count,
            |b, _| {
                b.iter(|| parser.parse_repo(black_box(temp_dir.path()), true))
            }
        );
    }

    group.finish();
}

fn bench_symbol_extraction(c: &mut Criterion) {
    let parser = RepoParser::new().unwrap();
    let temp_dir = create_test_files(10);

    c.bench_function("extract_all_symbols", |b| {
        b.iter(|| {
            let result = parser.parse_repo(black_box(temp_dir.path()), true).unwrap();
            black_box(result.symbols.len())
        })
    });
}

criterion_group!(
    benches,
    bench_single_file_parsing,
    bench_multi_file_parsing,
    bench_symbol_extraction
);
criterion_main!(benches);