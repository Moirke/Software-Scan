#!/usr/bin/env python3
"""
Command Line Interface for Repository Scanner
"""
import argparse
import sys
from src.scanner import ProhibitedWordScanner


def main():
    parser = argparse.ArgumentParser(
        description='Scan code repositories for prohibited words',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --config config.yaml --repo /path/to/repo
  %(prog)s -c config.yaml -r /path/to/repo --output results.txt
  %(prog)s -c config.yaml -r /path/to/repo --no-recursive
        """
    )
    
    parser.add_argument(
        '-c', '--config',
        required=True,
        help='Path to configuration file (YAML or JSON)'
    )
    
    parser.add_argument(
        '-r', '--repo',
        required=True,
        help='Path to repository to scan'
    )
    
    parser.add_argument(
        '-o', '--output',
        help='Output file for results (default: print to stdout)'
    )
    
    parser.add_argument(
        '--no-recursive',
        action='store_true',
        help='Do not scan subdirectories recursively'
    )
    
    parser.add_argument(
        '-v', '--verbose',
        action='store_true',
        help='Verbose output'
    )
    
    args = parser.parse_args()
    
    try:
        if args.verbose:
            print(f"Loading configuration from: {args.config}")
            print(f"Scanning repository: {args.repo}")
        
        scanner = ProhibitedWordScanner(args.config)
        
        if args.verbose:
            print(f"Prohibited words loaded: {len(scanner.prohibited_words)}")
            print("Starting scan...")
        
        results = scanner.scan_directory(args.repo, recursive=not args.no_recursive)
        
        output = scanner.format_results(results)
        
        if args.output:
            with open(args.output, 'w') as f:
                f.write(output)
            print(f"Results written to: {args.output}")
        else:
            print(output)
        
        scanner.cleanup()
        
        # Exit with error code if violations found
        sys.exit(1 if results else 0)
        
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(2)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        if args.verbose:
            import traceback
            traceback.print_exc()
        sys.exit(2)


if __name__ == '__main__':
    main()
