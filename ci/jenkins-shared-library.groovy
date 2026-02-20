// vars/scanProhibitedWords.groovy
// Jenkins Shared Library for Repository Scanner

def call(Map config = [:]) {
    // Default configuration
    def defaults = [
        configFile: 'config/config.yaml',
        scanPath: '.',
        outputFile: "scan_results_${env.BUILD_NUMBER}.txt",
        failOnViolations: true,
        archiveResults: true,
        verbose: true
    ]
    
    // Merge user config with defaults
    def cfg = defaults + config
    
    pipeline {
        agent any
        
        stages {
            stage('Setup Repository Scanner') {
                steps {
                    script {
                        echo "Installing Repository Scanner dependencies..."
                        sh '''
                            pip3 install -r requirements.txt --break-system-packages || \
                            pip3 install PyYAML Flask --break-system-packages
                            chmod +x run-cli.py src/cli.py
                        '''
                    }
                }
            }
            
            stage('Execute Scan') {
                steps {
                    script {
                        echo "Scanning ${cfg.scanPath} for prohibited words..."
                        
                        def verboseFlag = cfg.verbose ? '-v' : ''
                        def scanCmd = "./run-cli.py -c ${cfg.configFile} -r ${cfg.scanPath} -o ${cfg.outputFile} ${verboseFlag}"
                        
                        def exitCode = sh(
                            script: scanCmd,
                            returnStatus: true
                        )
                        
                        // Store results
                        env.SCAN_EXIT_CODE = exitCode
                        env.SCAN_OUTPUT_FILE = cfg.outputFile
                        
                        // Parse results
                        if (fileExists(cfg.outputFile)) {
                            def results = readFile(cfg.outputFile)
                            def matcher = (results =~ /Found (\d+) violation/)
                            env.VIOLATION_COUNT = matcher ? matcher[0][1] : '0'
                        }
                    }
                }
            }
            
            stage('Archive Results') {
                when {
                    expression { cfg.archiveResults }
                }
                steps {
                    archiveArtifacts(
                        artifacts: "${cfg.outputFile}",
                        allowEmptyArchive: true
                    )
                }
            }
            
            stage('Evaluate Results') {
                steps {
                    script {
                        if (env.SCAN_EXIT_CODE == '1') {
                            def message = "Found ${env.VIOLATION_COUNT} prohibited word violation(s)"
                            
                            if (cfg.failOnViolations) {
                                error(message)
                            } else {
                                unstable(message: message)
                                echo "⚠ WARNING: ${message}"
                            }
                        } else if (env.SCAN_EXIT_CODE == '0') {
                            echo "✓ No prohibited words found"
                        } else {
                            error("Scanner encountered an error")
                        }
                    }
                }
            }
        }
        
        post {
            always {
                script {
                    // Display summary
                    echo """
                        ========================================
                        Repository Scanner Summary
                        ========================================
                        Exit Code: ${env.SCAN_EXIT_CODE}
                        Violations: ${env.VIOLATION_COUNT ?: 'N/A'}
                        Results File: ${cfg.outputFile}
                        ========================================
                    """
                }
            }
        }
    }
}
