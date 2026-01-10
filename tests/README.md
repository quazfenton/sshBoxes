# sshBox Tests

This directory contains tests for the sshBox system.
 sshBox system.

## Test Organization

- `test_sshbox.py` - Unit tests for core functionality using unittest
- `test_api.py` - API tests using pytest for FastAPI endpoints

## Running Tests

### Unit Tests
```bash
python -m pytest tests/test_api.py -v
```

```bash
python -m unittest tests/test_sshbox.py -v
```

### All Tests
```bash
python -m pytest tests/ -v
```

Or:

```bash
python -m unittest discover tests/ -v
```

## Test Coverage

The tests cover:

- Token creation and validation
- Session recording functionality
- API endpoints (with mocking where necessary)
- Quality assurance checks
- Script existence and executability
- Documentation completeness

## Quality Assurance Checks

The test suite includes quality assurance checks for:

- Properly pinned dependencies in requirements.txt
- Existence of important documentation files
- Executability of shell scripts
- Docker Compose configuration
- Basic functionality of core modules