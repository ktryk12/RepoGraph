# BabyAI Test Suite

This directory contains end-to-end and integration tests for the BabyAI microservices platform.

## Test Organization

### Unit Tests
Unit tests are co-located with each service in their respective directories:
- `services/{service-name}/tests/` - Unit tests for individual services
- Focus: Testing individual service functionality in isolation

### Integration Tests  
- `tests/integration/` - Cross-service integration tests
- Focus: Testing communication between services via Kafka, HTTP APIs

### End-to-End Tests
- `tests/e2e/` - Full user workflow tests
- Focus: Complete user journeys across multiple services

### Performance Tests
- `tests/performance/` - Load testing and performance benchmarks
- Focus: System performance under realistic loads

## Test Execution

### Local Development
```bash
# Run all tests
pytest

# Run specific test category
pytest tests/e2e/
pytest tests/integration/

# Run service-specific unit tests
cd services/trust-api && pytest tests/
```

### CI/CD Pipeline
Tests are executed in this order:
1. Unit tests (parallel per service)
2. Integration tests
3. End-to-end tests
4. Performance tests (on staging)

## Service Test Standards

Each service should include:
- Unit tests with >80% coverage
- Integration tests for external dependencies
- HTTP API contract tests
- Kafka message contract tests

## Test Data Management

- Use `tests/fixtures/` for shared test data
- Use `tests/factories/` for test data generation
- Clean test data between test runs