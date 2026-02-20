# Configuration Files

This directory contains configuration files for the Repository Scanner.

## Files

### config.yaml
Main configuration file for the scanner. Contains:
- `prohibited_words_file`: Path to the file containing prohibited words
- `prohibited_words`: Alternative inline list of words (if not using file)
- `case_sensitive`: Whether to match words case-sensitively (default: false)
- `max_file_size_mb`: Maximum file size to scan in MB (default: 10)

Example:
```yaml
prohibited_words_file: config/prohibited_words.txt
case_sensitive: false
max_file_size_mb: 10
```

### prohibited_words.txt
List of prohibited words to search for (one per line).
Lines starting with `#` are treated as comments and ignored.

Example:
```
# Security-related
password
secret
api_key

# Development markers
TODO
FIXME
```

## Usage

When running the scanner, reference the config file:

```bash
./run-cli.py --config config/config.yaml --repo /path/to/scan
```

## Customization

1. Copy `config.yaml` to create your own config (e.g., `config/custom.yaml`)
2. Modify the prohibited words list as needed
3. Adjust case sensitivity and file size limits
4. Run scanner with your custom config:
   ```bash
   ./run-cli.py --config config/custom.yaml --repo /path/to/scan
   ```
