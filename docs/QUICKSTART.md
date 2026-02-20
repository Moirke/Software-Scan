# Quick Start Guide

## Prerequisites

- Python 3.8+
- Flask 3.0.0
- PyYAML 6.0.1
- `rpm2cpio` and `cpio` system utilities (only required for RPM archive scanning)

## Installation

```bash
pip3 install -r requirements.txt
```

## Basic Usage

### 1. Command Line

Scan a repository:
```bash
python3 run-cli.py -c config.yaml -r /path/to/repository
```

Scan and save results:
```bash
python3 run-cli.py -c config.yaml -r /path/to/repository -o results.txt
```

### 2. Web Interface

Start the server:
```bash
python3 run-web.py
```

Open browser to: http://localhost:5000

## Configuration Files

### config.yaml
```yaml
prohibited_words_file: prohibited_words.txt
case_sensitive: false
max_file_size_mb: 10
```

### prohibited_words.txt
```
password
secret
api_key
TODO
FIXME
```

## Supported Archive Formats

- ✅ ZIP (.zip)
- ✅ TAR (.tar, .tar.gz, .tgz, .tar.bz2, .tar.xz)
- ✅ RPM (.rpm) - requires rpm2cpio
- ✅ Docker images (.tar with "docker" in filename)

## Exit Codes (CLI)

- `0` - No violations found
- `1` - Violations found
- `2` - Error occurred

## Examples

### Scan current directory
```bash
python3 run-cli.py -c config.yaml -r .
```

### Scan ZIP archive
```bash
python3 run-cli.py -c config.yaml -r backup.zip
```

### Scan with custom words
Create custom_words.txt:
```
my_custom_word
another_word
```

Update config.yaml:
```yaml
prohibited_words_file: custom_words.txt
case_sensitive: true
max_file_size_mb: 5
```

Run scan:
```bash
python3 run-cli.py -c config.yaml -r /path/to/code
```

### Use in CI/CD
```bash
#!/bin/bash
python3 run-cli.py -c config.yaml -r .
if [ $? -eq 1 ]; then
  echo "Prohibited words found!"
  exit 1
fi
```

## Common Issues

**"rpm2cpio not found"**
```bash
sudo apt-get install rpm2cpio cpio
```

**"Module not found"**
```bash
pip3 install -r requirements.txt --break-system-packages
```

**Web UI not accessible**
- Check firewall: allow port 5000
- Try: `python3 run-web.py` and check for errors
