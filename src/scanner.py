"""
Repository Scanner - Core scanning functionality
Supports searching through code repositories including compressed/archived files
"""
import os
import re
import zipfile
import tarfile
import gzip
import tempfile
import shutil
from pathlib import Path
from typing import List, Dict, Set, Tuple
import yaml
import json


class ArchiveExtractor:
    """Handles extraction of various archive formats"""
    
    @staticmethod
    def extract_zip(archive_path: str, extract_dir: str) -> None:
        """Extract ZIP files"""
        with zipfile.ZipFile(archive_path, 'r') as zip_ref:
            zip_ref.extractall(extract_dir)
    
    @staticmethod
    def extract_tar(archive_path: str, extract_dir: str) -> None:
        """Extract TAR files (including .tar.gz, .tar.bz2)"""
        with tarfile.open(archive_path, 'r:*') as tar_ref:
            tar_ref.extractall(extract_dir)
    
    @staticmethod
    def extract_rpm(archive_path: str, extract_dir: str) -> None:
        """Extract RPM files using rpm2cpio and cpio"""
        import subprocess
        try:
            # Try rpm2cpio approach
            result = subprocess.run(
                f"cd {extract_dir} && rpm2cpio {archive_path} | cpio -idmv 2>/dev/null",
                shell=True,
                capture_output=True
            )
            if result.returncode != 0:
                print(f"Warning: Could not extract RPM {archive_path}")
        except Exception as e:
            print(f"Error extracting RPM {archive_path}: {e}")
    
    @staticmethod
    def extract_docker_image(image_path: str, extract_dir: str) -> None:
        """Extract Docker image tar file"""
        import subprocess
        try:
            # Docker images are typically tar files
            if tarfile.is_tarfile(image_path):
                with tarfile.open(image_path, 'r') as tar:
                    tar.extractall(extract_dir)
                
                # Extract layer tar files
                for root, dirs, files in os.walk(extract_dir):
                    for file in files:
                        if file.endswith('.tar'):
                            layer_path = os.path.join(root, file)
                            layer_extract = os.path.join(root, file.replace('.tar', '_layer'))
                            os.makedirs(layer_extract, exist_ok=True)
                            try:
                                with tarfile.open(layer_path, 'r') as layer_tar:
                                    layer_tar.extractall(layer_extract)
                            except:
                                pass
        except Exception as e:
            print(f"Error extracting Docker image {image_path}: {e}")


class ProhibitedWordScanner:
    """Main scanner class for finding prohibited words in repositories"""
    
    ARCHIVE_EXTENSIONS = {
        '.zip': 'extract_zip',
        '.tar': 'extract_tar',
        '.tar.gz': 'extract_tar',
        '.tgz': 'extract_tar',
        '.tar.bz2': 'extract_tar',
        '.tbz2': 'extract_tar',
        '.tar.xz': 'extract_tar',
        '.rpm': 'extract_rpm',
    }
    
    BINARY_EXTENSIONS = {
        '.exe', '.dll', '.so', '.dylib', '.bin', '.class', 
        '.pyc', '.pyo', '.o', '.a', '.lib', '.obj',
        '.jpg', '.jpeg', '.png', '.gif', '.bmp', '.ico',
        '.mp3', '.mp4', '.avi', '.mov', '.wav',
        '.pdf', '.doc', '.docx', '.xls', '.xlsx'
    }
    
    def __init__(self, config_path: str):
        """Initialize scanner with configuration file"""
        self.config = self._load_config(config_path)
        self.case_sensitive = self.config.get('case_sensitive', False)
        self.max_file_size = self.config.get('max_file_size_mb', 10) * 1024 * 1024
        self.prohibited_words = self._load_prohibited_words()
        self.temp_dirs = []
        
    def _load_config(self, config_path: str) -> Dict:
        """Load configuration from YAML or JSON file"""
        with open(config_path, 'r') as f:
            if config_path.endswith('.yaml') or config_path.endswith('.yml'):
                return yaml.safe_load(f)
            elif config_path.endswith('.json'):
                return json.load(f)
            else:
                raise ValueError("Config file must be .yaml, .yml, or .json")
    
    def _load_prohibited_words(self) -> List[str]:
        """Load prohibited words from config"""
        words_file = self.config.get('prohibited_words_file')
        if words_file:
            with open(words_file, 'r') as f:
                words = [line.strip() for line in f if line.strip() and not line.startswith('#')]
        else:
            words = self.config.get('prohibited_words', [])
        
        if not self.case_sensitive:
            words = [w.lower() for w in words]
        
        return words
    
    def _is_binary_file(self, filepath: str) -> bool:
        """Check if file is likely binary"""
        ext = Path(filepath).suffix.lower()
        if ext in self.BINARY_EXTENSIONS:
            return True
        
        try:
            with open(filepath, 'rb') as f:
                chunk = f.read(8192)
                if b'\x00' in chunk:  # Null bytes indicate binary
                    return True
        except:
            return True
        
        return False
    
    def _is_archive(self, filepath: str) -> Tuple[bool, str]:
        """Check if file is an archive and return extraction method"""
        filepath_lower = filepath.lower()
        
        # Check for docker image naming patterns
        if 'docker' in filepath_lower and filepath_lower.endswith('.tar'):
            return True, 'extract_docker_image'
        
        for ext, method in self.ARCHIVE_EXTENSIONS.items():
            if filepath_lower.endswith(ext):
                return True, method
        
        return False, None
    
    def _extract_archive(self, archive_path: str) -> str:
        """Extract archive to temporary directory"""
        temp_dir = tempfile.mkdtemp(prefix='repo_scanner_')
        self.temp_dirs.append(temp_dir)
        
        is_archive, method = self._is_archive(archive_path)
        if is_archive:
            extractor = ArchiveExtractor()
            extract_method = getattr(extractor, method)
            extract_method(archive_path, temp_dir)
        
        return temp_dir
    
    def _search_in_file(self, filepath: str) -> List[Dict]:
        """Search for prohibited words in a single file"""
        results = []
        
        if self._is_binary_file(filepath):
            return results
        
        try:
            file_size = os.path.getsize(filepath)
            if file_size > self.max_file_size:
                print(f"Skipping large file: {filepath} ({file_size / 1024 / 1024:.2f} MB)")
                return results
            
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                for line_num, line in enumerate(f, 1):
                    search_line = line if self.case_sensitive else line.lower()
                    
                    for word in self.prohibited_words:
                        # Use word boundary matching for whole words
                        pattern = r'\b' + re.escape(word) + r'\b'
                        matches = re.finditer(pattern, search_line)
                        
                        for match in matches:
                            results.append({
                                'file': filepath,
                                'line_number': line_num,
                                'line_content': line.strip(),
                                'prohibited_word': word,
                                'position': match.start()
                            })
        except Exception as e:
            print(f"Error reading file {filepath}: {e}")
        
        return results
    
    def scan_directory(self, repo_path: str, recursive: bool = True) -> List[Dict]:
        """Scan directory for prohibited words"""
        all_results = []
        scanned_files = set()
        
        def scan_path(path: str, is_extracted: bool = False):
            """Recursively scan a path"""
            if os.path.isfile(path):
                if path in scanned_files:
                    return
                scanned_files.add(path)
                
                # Check if it's an archive
                is_archive, _ = self._is_archive(path)
                if is_archive:
                    print(f"Extracting archive: {path}")
                    extract_dir = self._extract_archive(path)
                    scan_path(extract_dir, is_extracted=True)
                else:
                    # Search in file
                    results = self._search_in_file(path)
                    all_results.extend(results)
            
            elif os.path.isdir(path):
                for root, dirs, files in os.walk(path):
                    for file in files:
                        filepath = os.path.join(root, file)
                        scan_path(filepath, is_extracted)
                    
                    if not recursive and not is_extracted:
                        break
        
        scan_path(repo_path)
        return all_results
    
    def cleanup(self):
        """Clean up temporary directories"""
        for temp_dir in self.temp_dirs:
            try:
                shutil.rmtree(temp_dir)
            except Exception as e:
                print(f"Error cleaning up {temp_dir}: {e}")
        self.temp_dirs = []
    
    def format_results(self, results: List[Dict]) -> str:
        """Format results as readable text"""
        if not results:
            return "No prohibited words found!"
        
        output = [f"\n{'='*80}"]
        output.append(f"SCAN RESULTS: Found {len(results)} violation(s)")
        output.append('='*80 + '\n')
        
        # Group by file
        by_file = {}
        for result in results:
            file = result['file']
            if file not in by_file:
                by_file[file] = []
            by_file[file].append(result)
        
        for file, violations in by_file.items():
            output.append(f"\nFile: {file}")
            output.append(f"Violations: {len(violations)}")
            output.append("-" * 80)
            
            for v in violations:
                output.append(f"  Line {v['line_number']}: Found '{v['prohibited_word']}'")
                output.append(f"    {v['line_content']}")
                output.append("")
        
        return '\n'.join(output)
